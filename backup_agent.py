"""
Flying Probe Backup Agent
Watches folders for changes, mirrors files into a local git repo,
and syncs to a server share drive and/or GitHub when available.
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
    """Initialize the local git repo and GitHub remote if enabled."""
    repo_path = config["repo_path"]
    os.makedirs(repo_path, exist_ok=True)

    code, _, _ = run_git(["rev-parse", "--git-dir"], cwd=repo_path)
    if code != 0:
        logging.info("Initializing new git repository at %s", repo_path)
        run_git(["init", "-b", config["git_branch"]], cwd=repo_path)
        run_git(["config", "user.name", config["commit_author_name"]], cwd=repo_path)
        run_git(["config", "user.email", config["commit_author_email"]], cwd=repo_path)

    if config.get("github_enabled", False):
        _set_remote(repo_path, config["git_remote"], config["github_remote_url"])
        logging.info("GitHub sync enabled -> %s", config["github_remote_url"])
    else:
        logging.info("GitHub sync disabled.")

    server_folder = config.get("server_folder", "")
    if server_folder:
        logging.info("Server folder configured -> %s", server_folder)
    else:
        logging.info("Server folder not configured — local only.")


def _set_remote(repo_path, name, url):
    code, _, _ = run_git(["remote", "get-url", name], cwd=repo_path)
    if code != 0:
        run_git(["remote", "add", name, url], cwd=repo_path)
    else:
        run_git(["remote", "set-url", name, url], cwd=repo_path)


def sync_to_server(repo_path, server_folder, retries=3):
    """
    Mirror the local Agent Database to the server folder using robocopy.
    robocopy only copies files that changed, handles network shares reliably,
    and requires only write access — no git ownership needed on the server.
    The server copy is a full git repo so recover.py can read it directly.
    robocopy exit codes 0-7 are all success (bit flags for what was copied).
    """
    logging.info("Syncing to server ...")
    for attempt in range(retries):
        try:
            result = subprocess.run(
                ["robocopy", repo_path, server_folder,
                 "/MIR",       # mirror: copy new/changed, remove deleted
                 "/W:1",       # wait 1s between retries per file
                 "/R:1",       # retry once per file on error
                 "/NP",        # no progress percentage (cleaner logs)
                 "/NFL",       # no file list in output
                 "/NDL",       # no directory list in output
                 "/MT:4"],     # 4 parallel threads for faster copy
                capture_output=True,
                text=True,
                timeout=120,   # give up after 2 minutes
            )
            if result.returncode < 8:
                logging.info("Server sync successful.")
                return
        except subprocess.TimeoutExpired:
            logging.warning("Server sync timed out on attempt %d.", attempt + 1)
        except Exception as e:
            logging.warning("Server sync error on attempt %d: %s", attempt + 1, e)
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    logging.warning("Server sync failed after %d attempts.", retries)


def mirror_path(src_file, watch_root, repo_path):
    folder_name = Path(watch_root).name
    rel = os.path.relpath(src_file, watch_root)
    return os.path.join(repo_path, folder_name, rel)


def copy_into_repo(src_file, watch_root, repo_path, retries=5, delay=0.5):
    """
    Copy src_file into its mirrored location inside the repo.
    Retries with a short delay because the file may still be locked by the
    application that just saved it when the first filesystem event fires.
    Returns (rel_path, size_tag) where size_tag is [new], [size unchanged],
    or [+N bytes]/[-N bytes] based on byte-level size comparison.
    """
    dest = mirror_path(src_file, watch_root, repo_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    src_size = os.path.getsize(src_file)
    if os.path.exists(dest):
        delta = src_size - os.path.getsize(dest)
        if delta == 0:
            size_tag = "[size unchanged]"
        else:
            size_tag = f"[{'+' if delta > 0 else ''}{delta} bytes]"
    else:
        size_tag = "[new]"

    last_err = None
    for attempt in range(retries):
        try:
            shutil.copy2(src_file, dest)
            return os.path.relpath(dest, repo_path), size_tag
        except OSError as e:
            last_err = e
            time.sleep(delay)
    raise last_err


def startup_snapshot(config):
    """
    Runs on every startup. Copies all current files from watch_paths into the
    repo and commits anything that changed while the agent was offline.
    On first run this becomes the initial snapshot; on subsequent runs it
    catches up any missed changes.
    """
    repo_path = config["repo_path"]
    extensions = {ext.lower() for ext in config.get("file_extensions", [])}

    for watch_root in config["watch_paths"]:
        if not os.path.isdir(watch_root):
            continue
        for dirpath, _, filenames in os.walk(watch_root):
            for fname in filenames:
                if extensions and Path(fname).suffix.lower() not in extensions:
                    continue
                src = os.path.join(dirpath, fname)
                try:
                    copy_into_repo(src, watch_root, repo_path)
                except Exception as e:
                    logging.warning("Startup: could not copy %s: %s", src, e)

    run_git(["add", "--all"], cwd=repo_path)
    code, diff, _ = run_git(["diff", "--cached", "--name-only"], cwd=repo_path)
    if not diff:
        logging.info("Startup check: no changes missed while offline.")
        return

    # First ever commit vs catch-up commit
    code_log, out_log, _ = run_git(["log", "--oneline", "-1"], cwd=repo_path)
    is_first = not (code_log == 0 and out_log)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_list = "\n".join(f"  - {f}" for f in sorted(diff.splitlines()))
    if is_first:
        msg = f"Initial snapshot {timestamp}\n\nFiles:\n{file_list}"
    else:
        msg = f"Catch-up {timestamp}\n\nChanged while agent was offline:\n{file_list}"

    code, _, err = run_git(["commit", "-m", msg], cwd=repo_path)
    if code == 0:
        label = "Initial snapshot" if is_first else "Catch-up"
        logging.info("%s committed: %d file(s):\n%s", label, len(diff.splitlines()), file_list)
        push_all(repo_path, config)
    else:
        logging.error("Startup commit failed: %s", err)


def stage_and_commit(repo_path, changed_files, watch_root_map, config):
    staged_tags = {}  # rel_path -> size_tag

    for src_file in changed_files:
        if not os.path.isfile(src_file):
            continue
        watch_root = watch_root_map.get(src_file)
        if not watch_root:
            continue
        try:
            rel, size_tag = copy_into_repo(src_file, watch_root, repo_path)
            code, _, err = run_git(["add", rel], cwd=repo_path)
            if code == 0:
                staged_tags[rel] = size_tag
            else:
                logging.warning("Could not stage %s: %s", rel, err)
        except Exception as e:
            logging.warning("Could not copy %s into repo: %s", src_file, e)

    if not staged_tags:
        logging.info("Nothing to stage.")
        return

    code, diff_output, _ = run_git(["diff", "--cached", "--name-only"], cwd=repo_path)
    if not diff_output:
        logging.info("No changes after staging — skipping commit.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_list = "\n".join(
        f"  - {f}  {staged_tags.get(f, '')}"
        for f in sorted(diff_output.splitlines())
    )
    commit_msg = f"Auto-backup {timestamp}\n\nChanged files:\n{file_list}"

    code, _, err = run_git(["commit", "-m", commit_msg], cwd=repo_path)
    if code == 0:
        logging.info("Committed %d file(s):\n%s", len(staged_tags), file_list)
        push_all(repo_path, config)
    else:
        logging.error("Commit failed: %s", err)


def push_all(repo_path, config):
    """Sync to every configured destination after a commit."""
    branch = config["git_branch"]

    if config.get("github_enabled", False):
        if is_network_available(config["network_check_host"], config["network_check_port"]):
            _github_push(repo_path, config["git_remote"], branch)
        else:
            logging.info("GitHub: network not available — will retry later.")

    server_folder = config.get("server_folder", "")
    if server_folder:
        if os.path.isdir(server_folder):
            sync_to_server(repo_path, server_folder)
        else:
            logging.warning("Server folder not reachable: %s", server_folder)


def _github_push(repo_path, remote, branch, retries=4):
    """Push to GitHub with exponential backoff."""
    logging.info("Pushing to GitHub ...")
    last_err = ""
    for attempt in range(retries):
        code, _, err = run_git(["push", "-u", remote, branch], cwd=repo_path)
        if code == 0:
            logging.info("GitHub push successful.")
            return
        last_err = err
        wait = 2 ** attempt
        logging.debug("GitHub push attempt %d failed, retrying in %ds: %s", attempt + 1, wait, err)
        time.sleep(wait)
    logging.warning("GitHub push failed after %d attempts — git said: %s", retries, last_err)


def retry_sync(repo_path, config, interval=300):
    """Background thread: periodically retry any syncs that failed earlier."""
    while True:
        time.sleep(interval)
        branch = config["git_branch"]

        if config.get("github_enabled", False):
            remote = config["git_remote"]
            code, out, _ = run_git(["log", f"{remote}/{branch}..HEAD", "--oneline"], cwd=repo_path)
            if code == 0 and out:
                logging.info("Found commits not yet on GitHub, retrying...")
                if is_network_available(config["network_check_host"], config["network_check_port"]):
                    _github_push(repo_path, remote, branch)

        server_folder = config.get("server_folder", "")
        if server_folder and os.path.isdir(server_folder):
            sync_to_server(repo_path, server_folder)


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

    def on_deleted(self, event):
        if not event.is_directory and self._is_relevant(event.src_path):
            logging.warning("FILE DELETED: %s", event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            logging.debug("New file: %s", event.src_path)
            self._schedule_commit(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            logging.debug("Modified: %s", event.src_path)
            self._schedule_commit(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            logging.debug("Renamed: %s -> %s", event.src_path, event.dest_path)
            self._schedule_commit(event.dest_path)


def main():
    config = load_config()
    log_file = os.path.join(get_base_dir(), config.get("log_file", "backup_agent.log"))
    setup_logging(log_file)

    logging.info("=== Flying Probe Backup Agent starting ===")

    ensure_repo_initialized(config)
    startup_snapshot(config)

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
        target=retry_sync,
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
