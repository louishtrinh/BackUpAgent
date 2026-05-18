"""
Flying Probe Backup Agent
Watches folders for changes, mirrors files into a local git repo,
and pushes to GitHub when the network is available.
"""

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


def get_base_dir():
    """Return the folder containing the exe (frozen) or the script (dev)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_config(config_path=None):
    if config_path is None:
        config_path = os.path.join(get_base_dir(), "config.json")
    with open(config_path, "r") as f:
        return json.load(f)


def setup_logging(log_file):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def is_network_available(host, port, timeout=5):
    try:
        socket.setdefaulttimeout(timeout)
        with socket.create_connection((host, port)):
            return True
    except OSError:
        return False


def run_git(args, cwd, env=None):
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def ensure_repo_initialized(config):
    repo_path = config["repo_path"]
    os.makedirs(repo_path, exist_ok=True)

    code, _, _ = run_git(["rev-parse", "--git-dir"], cwd=repo_path)
    if code != 0:
        logging.info("Initializing new git repository at %s", repo_path)
        run_git(["init", "-b", config["git_branch"]], cwd=repo_path)
        run_git(["config", "user.name", config["commit_author_name"]], cwd=repo_path)
        run_git(["config", "user.email", config["commit_author_email"]], cwd=repo_path)

    code, _, _ = run_git(["remote", "get-url", config["git_remote"]], cwd=repo_path)
    if code != 0:
        run_git(
            ["remote", "add", config["git_remote"], config["github_remote_url"]],
            cwd=repo_path,
        )
        logging.info("Added remote '%s' -> %s", config["git_remote"], config["github_remote_url"])
    else:
        run_git(
            ["remote", "set-url", config["git_remote"], config["github_remote_url"]],
            cwd=repo_path,
        )


def mirror_path(src_file, watch_root, repo_path):
    """
    Given a file path inside watch_root, return where it should live inside repo_path.

    Example:
      src_file   = C:/FlyingProbePrograms/BoardA/dummy123.job
      watch_root = C:/FlyingProbePrograms
      repo_path  = C:/FlyingProbeBackup
      →  returns  C:/FlyingProbeBackup/FlyingProbePrograms/BoardA/dummy123.job
    """
    folder_name = Path(watch_root).name
    rel = os.path.relpath(src_file, watch_root)
    return os.path.join(repo_path, folder_name, rel)


def copy_into_repo(src_file, watch_root, repo_path):
    """Copy src_file into its mirrored location inside the repo. Returns the repo-relative path."""
    dest = mirror_path(src_file, watch_root, repo_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src_file, dest)
    return os.path.relpath(dest, repo_path)


def initial_snapshot(config):
    """
    On first startup, copy every existing file from all watch_paths into the repo
    and make a single 'Initial snapshot' commit. Skips if repo already has commits.
    """
    repo_path = config["repo_path"]
    code, out, _ = run_git(["log", "--oneline", "-1"], cwd=repo_path)
    if code == 0 and out:
        logging.info("Repo already has commits — skipping initial snapshot.")
        return

    extensions = {ext.lower() for ext in config.get("file_extensions", [])}
    copied = []

    for watch_root in config["watch_paths"]:
        if not os.path.isdir(watch_root):
            continue
        for dirpath, _, filenames in os.walk(watch_root):
            for fname in filenames:
                if extensions and Path(fname).suffix.lower() not in extensions:
                    continue
                src = os.path.join(dirpath, fname)
                rel = copy_into_repo(src, watch_root, repo_path)
                copied.append(rel)

    if not copied:
        logging.info("No files found for initial snapshot.")
        return

    run_git(["add", "--all"], cwd=repo_path)
    code, diff, _ = run_git(["diff", "--cached", "--name-only"], cwd=repo_path)
    if not diff:
        logging.info("Initial snapshot: nothing to commit.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_list = "\n".join(f"  - {f}" for f in sorted(diff.splitlines()))
    msg = f"Initial snapshot {timestamp}\n\nFiles:\n{file_list}"
    code, _, err = run_git(["commit", "-m", msg], cwd=repo_path)
    if code == 0:
        logging.info("Initial snapshot committed: %d file(s).", len(diff.splitlines()))
    else:
        logging.error("Initial snapshot commit failed: %s", err)


def stage_and_commit(repo_path, changed_files, watch_root_map, config):
    """
    Copy changed files into the repo mirror, stage, and commit.
    watch_root_map: {src_file: watch_root} so we know which root each file belongs to.
    """
    staged_rel = []

    for src_file in changed_files:
        if not os.path.isfile(src_file):
            continue  # deleted or temp file
        watch_root = watch_root_map.get(src_file)
        if not watch_root:
            continue
        try:
            rel = copy_into_repo(src_file, watch_root, repo_path)
            code, _, err = run_git(["add", rel], cwd=repo_path)
            if code == 0:
                staged_rel.append(rel)
            else:
                logging.warning("Could not stage %s: %s", rel, err)
        except Exception as e:
            logging.warning("Could not copy %s into repo: %s", src_file, e)

    if not staged_rel:
        logging.info("Nothing to stage.")
        return

    code, diff_output, _ = run_git(["diff", "--cached", "--name-only"], cwd=repo_path)
    if not diff_output:
        logging.info("No changes after staging — skipping commit.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_list = "\n".join(f"  - {f}" for f in sorted(diff_output.splitlines()))
    commit_msg = f"Auto-backup {timestamp}\n\nChanged files:\n{file_list}"

    code, _, err = run_git(["commit", "-m", commit_msg], cwd=repo_path)
    if code == 0:
        logging.info("Committed %d file(s):\n%s", len(diff_output.splitlines()), file_list)
    else:
        logging.error("Commit failed: %s", err)
        return

    if is_network_available(config["network_check_host"], config["network_check_port"]):
        push_to_remote(repo_path, config)
    else:
        logging.info("Network not available — commit saved locally, will push later.")


def push_to_remote(repo_path, config):
    remote = config["git_remote"]
    branch = config["git_branch"]
    logging.info("Pushing to %s/%s ...", remote, branch)
    code, _, err = run_git(["push", "-u", remote, branch], cwd=repo_path)
    if code == 0:
        logging.info("Push successful.")
    else:
        logging.warning("Push failed (will retry on next commit): %s", err)


class ProgramChangeHandler(FileSystemEventHandler):
    def __init__(self, config, watch_root, pending_lock, pending_files, watch_root_map, debounce_timer_ref):
        super().__init__()
        self.config = config
        self.watch_root = watch_root
        self.repo_path = config["repo_path"]
        self.extensions = {ext.lower() for ext in config.get("file_extensions", [])}
        self.debounce_seconds = config.get("debounce_seconds", 10)
        self.pending_lock = pending_lock
        self.pending_files = pending_files
        self.watch_root_map = watch_root_map
        self.debounce_timer_ref = debounce_timer_ref

    def _is_relevant(self, path):
        if not self.extensions:
            return True
        return Path(path).suffix.lower() in self.extensions

    def _schedule_commit(self, src_path):
        if not self._is_relevant(src_path):
            return
        with self.pending_lock:
            self.pending_files.add(src_path)
            self.watch_root_map[src_path] = self.watch_root
            # Reset debounce timer so rapid saves are batched into one commit
            if self.debounce_timer_ref[0] is not None:
                self.debounce_timer_ref[0].cancel()
            timer = threading.Timer(self.debounce_seconds, self._flush_pending)
            timer.daemon = True
            timer.start()
            self.debounce_timer_ref[0] = timer

    def _flush_pending(self):
        with self.pending_lock:
            files = set(self.pending_files)
            root_map = dict(self.watch_root_map)
            self.pending_files.clear()
            self.watch_root_map.clear()
            self.debounce_timer_ref[0] = None
        if files:
            logging.info("Detected %d change(s), committing...", len(files))
            stage_and_commit(self.repo_path, files, root_map, self.config)

    def on_created(self, event):
        if not event.is_directory:
            logging.info("New file: %s", event.src_path)
            self._schedule_commit(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            logging.info("Modified: %s", event.src_path)
            self._schedule_commit(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            logging.info("Renamed: %s -> %s", event.src_path, event.dest_path)
            self._schedule_commit(event.dest_path)


def retry_unpushed_commits(repo_path, config, interval=300):
    """Background thread: periodically push commits that failed to push earlier."""
    while True:
        time.sleep(interval)
        code, out, _ = run_git(
            ["log", f"{config['git_remote']}/{config['git_branch']}..HEAD", "--oneline"],
            cwd=repo_path,
        )
        if code == 0 and out:
            logging.info("Found unpushed commits, retrying push...")
            if is_network_available(config["network_check_host"], config["network_check_port"]):
                push_to_remote(repo_path, config)


def main():
    config = load_config()
    log_file = os.path.join(get_base_dir(), config.get("log_file", "backup_agent.log"))
    setup_logging(log_file)

    logging.info("=== Flying Probe Backup Agent starting ===")

    ensure_repo_initialized(config)
    initial_snapshot(config)

    pending_lock = threading.Lock()
    pending_files = set()
    watch_root_map = {}
    debounce_timer_ref = [None]

    observer = Observer()
    for watch_root in config["watch_paths"]:
        if not os.path.isdir(watch_root):
            logging.warning("Watch path does not exist, skipping: %s", watch_root)
            continue
        handler = ProgramChangeHandler(
            config, watch_root, pending_lock, pending_files, watch_root_map, debounce_timer_ref
        )
        observer.schedule(handler, watch_root, recursive=True)
        logging.info("Watching: %s", watch_root)

    observer.start()
    logging.info("Agent is running. Press Ctrl+C to stop.")

    retry_thread = threading.Thread(
        target=retry_unpushed_commits,
        args=(config["repo_path"], config),
        daemon=True,
    )
    retry_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        observer.stop()
    observer.join()
    logging.info("Agent stopped.")


if __name__ == "__main__":
    main()
