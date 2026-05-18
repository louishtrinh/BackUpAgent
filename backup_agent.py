"""
Flying Probe Backup Agent
Watches folders for changes, commits to local git repo, and pushes to GitHub when online.
"""

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


def load_config(config_path="config.json"):
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
    """Run a git command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def ensure_repo_initialized(config):
    """Initialize the git repo if it doesn't exist yet, and set the remote."""
    repo_path = config["repo_path"]
    os.makedirs(repo_path, exist_ok=True)

    code, _, _ = run_git(["rev-parse", "--git-dir"], cwd=repo_path)
    if code != 0:
        logging.info("Initializing new git repository at %s", repo_path)
        run_git(["init", "-b", config["git_branch"]], cwd=repo_path)
        run_git(
            ["config", "user.name", config["commit_author_name"]],
            cwd=repo_path,
        )
        run_git(
            ["config", "user.email", config["commit_author_email"]],
            cwd=repo_path,
        )

    # Set or update the remote URL
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


def stage_and_commit(repo_path, changed_files, config):
    """Stage changed files and create a commit listing every changed file."""
    if not changed_files:
        return

    # Stage each file individually so we only commit what actually changed
    staged = []
    for file_path in changed_files:
        # Path relative to repo root for git add
        try:
            rel = os.path.relpath(file_path, repo_path)
        except ValueError:
            # On Windows, relpath fails across drives — use absolute path
            rel = file_path

        code, _, err = run_git(["add", rel], cwd=repo_path)
        if code == 0:
            staged.append(rel)
        else:
            logging.warning("Could not stage %s: %s", rel, err)

    if not staged:
        logging.info("Nothing to stage.")
        return

    # Check if there are actual changes to commit (git add on an unchanged file is a no-op)
    code, diff_output, _ = run_git(["diff", "--cached", "--name-only"], cwd=repo_path)
    if not diff_output:
        logging.info("No changes detected after staging — skipping commit.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_list = "\n".join(f"  - {f}" for f in sorted(diff_output.splitlines()))
    commit_msg = f"Auto-backup {timestamp}\n\nChanged files:\n{file_list}"

    code, out, err = run_git(["commit", "-m", commit_msg], cwd=repo_path)
    if code == 0:
        logging.info("Committed %d file(s):\n%s", len(diff_output.splitlines()), file_list)
    else:
        logging.error("Commit failed: %s", err)
        return

    # Push if network is available
    if is_network_available(config["network_check_host"], config["network_check_port"]):
        push_to_remote(repo_path, config)
    else:
        logging.info("Network not available — commit saved locally, will push later.")


def push_to_remote(repo_path, config):
    remote = config["git_remote"]
    branch = config["git_branch"]
    logging.info("Pushing to %s/%s ...", remote, branch)
    code, out, err = run_git(["push", "-u", remote, branch], cwd=repo_path)
    if code == 0:
        logging.info("Push successful.")
    else:
        logging.warning("Push failed (will retry on next commit): %s", err)


class ProgramChangeHandler(FileSystemEventHandler):
    def __init__(self, config, pending_lock, pending_files, debounce_timer_ref):
        super().__init__()
        self.config = config
        self.repo_path = config["repo_path"]
        self.extensions = {ext.lower() for ext in config.get("file_extensions", [])}
        self.debounce_seconds = config.get("debounce_seconds", 10)
        self.pending_lock = pending_lock
        self.pending_files = pending_files
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
            # Reset the debounce timer
            if self.debounce_timer_ref[0] is not None:
                self.debounce_timer_ref[0].cancel()
            timer = threading.Timer(
                self.debounce_seconds,
                self._flush_pending,
            )
            timer.daemon = True
            timer.start()
            self.debounce_timer_ref[0] = timer

    def _flush_pending(self):
        with self.pending_lock:
            files = set(self.pending_files)
            self.pending_files.clear()
            self.debounce_timer_ref[0] = None
        if files:
            logging.info("Detected %d change(s), committing...", len(files))
            stage_and_commit(self.repo_path, files, self.config)

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
    """Background thread: periodically push any commits that failed earlier."""
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
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    config = load_config(config_path)
    setup_logging(config.get("log_file", "backup_agent.log"))

    logging.info("=== Flying Probe Backup Agent starting ===")

    ensure_repo_initialized(config)

    # Shared state for debouncing across all watched paths
    pending_lock = threading.Lock()
    pending_files = set()
    debounce_timer_ref = [None]

    handler = ProgramChangeHandler(config, pending_lock, pending_files, debounce_timer_ref)

    observer = Observer()
    for watch_path in config["watch_paths"]:
        if not os.path.isdir(watch_path):
            logging.warning("Watch path does not exist, skipping: %s", watch_path)
            continue
        observer.schedule(handler, watch_path, recursive=True)
        logging.info("Watching: %s", watch_path)

    observer.start()
    logging.info("Agent is running. Press Ctrl+C to stop.")

    # Background thread: retry any locally-stored commits when network comes back
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
