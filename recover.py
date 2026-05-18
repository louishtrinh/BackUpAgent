"""
Flying Probe Recovery Tool
Extracts a previous version of any program file into a separate Recovered folder.
The original programs folder is NEVER touched.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def load_config(config_path="config.json"):
    with open(config_path, "r") as f:
        return json.load(f)


def run_git(args, cwd):
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def list_commits(repo_path, max_commits=50):
    """Return list of (short_hash, timestamp, subject) for recent commits."""
    code, out, err = run_git(
        ["log", f"--max-count={max_commits}", "--format=%h|%ci|%s"],
        cwd=repo_path,
    )
    if code != 0 or not out:
        return []
    commits = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append((parts[0], parts[1][:19], parts[2]))
    return commits


def files_in_commit(repo_path, commit_hash):
    """Return list of files that were changed in a given commit."""
    code, out, _ = run_git(
        ["diff-tree", "--no-commit-id", "-r", "--name-only", commit_hash],
        cwd=repo_path,
    )
    if code != 0:
        return []
    return [f for f in out.splitlines() if f]


def extract_file(repo_path, commit_hash, file_path, output_dir):
    """
    Extract one file from a commit and save it to output_dir.
    Returns the full path of the saved file, or None on failure.
    """
    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, Path(file_path).name)

    code, content, err = run_git(
        ["show", f"{commit_hash}:{file_path}"],
        cwd=repo_path,
    )
    if code != 0:
        print(f"  [ERROR] Could not extract {file_path}: {err}")
        return None

    with open(dest, "w", encoding="utf-8", errors="replace") as f:
        f.write(content)
    return dest


def pick(prompt, items, display_fn):
    """Ask the user to pick one item from a numbered list. Returns the item."""
    print()
    for i, item in enumerate(items, 1):
        print(f"  {i:>3}.  {display_fn(item)}")
    print()
    while True:
        raw = input(f"{prompt} (1-{len(items)}, or 0 to cancel): ").strip()
        if raw == "0":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        print("       Invalid choice, try again.")


def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")

    if not os.path.exists(config_path):
        print("[ERROR] config.json not found. Run this from the BackupAgent folder.")
        input("Press Enter to exit.")
        sys.exit(1)

    config = load_config(config_path)
    repo_path = config["repo_path"]
    recovered_root = config.get("recovered_path", os.path.join(os.path.dirname(repo_path), "FlyingProbeRecovered"))

    print("=" * 60)
    print("  Flying Probe Recovery Tool")
    print("  Files are saved to:", recovered_root)
    print("  The programs folder is NEVER modified.")
    print("=" * 60)

    if not os.path.isdir(repo_path):
        print(f"\n[ERROR] Backup repo not found at: {repo_path}")
        print("        Has the backup agent run at least once?")
        input("Press Enter to exit.")
        sys.exit(1)

    # ── Step 1: pick a commit ────────────────────────────────────
    print("\nLoading commit history...")
    commits = list_commits(repo_path)
    if not commits:
        print("[ERROR] No commits found in the backup repository.")
        input("Press Enter to exit.")
        sys.exit(1)

    print(f"\nFound {len(commits)} backup snapshots. Pick the one you want to recover from:\n")
    chosen_commit = pick(
        "Enter snapshot number",
        commits,
        lambda c: f"{c[1]}  |  {c[2][:70]}",
    )
    if chosen_commit is None:
        print("Cancelled.")
        input("Press Enter to exit.")
        return

    commit_hash, commit_time, commit_subject = chosen_commit

    # ── Step 2: pick a file ──────────────────────────────────────
    files = files_in_commit(repo_path, commit_hash)
    if not files:
        print("[ERROR] No files found in that snapshot.")
        input("Press Enter to exit.")
        sys.exit(1)

    print(f"\nSnapshot: {commit_time} — {commit_subject}")
    print("Which file do you want to recover?\n")
    files_with_all = ["** Recover ALL files listed above **"] + files
    chosen_file = pick(
        "Enter file number",
        files_with_all,
        lambda f: f,
    )
    if chosen_file is None:
        print("Cancelled.")
        input("Press Enter to exit.")
        return

    # ── Step 3: extract to Recovered folder ─────────────────────
    safe_time = commit_time.replace(":", "-").replace(" ", "_")
    output_dir = os.path.join(recovered_root, f"{safe_time}_{commit_hash}")

    if chosen_file == files_with_all[0]:
        targets = files
    else:
        targets = [chosen_file]

    print(f"\nExtracting to: {output_dir}\n")
    saved = []
    for f in targets:
        dest = extract_file(repo_path, commit_hash, f, output_dir)
        if dest:
            print(f"  [OK] {Path(f).name}  ->  {dest}")
            saved.append(dest)

    print()
    if saved:
        print(f"Done! {len(saved)} file(s) saved to:")
        print(f"  {output_dir}")
        print()
        print("Copy the file(s) you need from there. The programs folder was NOT changed.")

        # Open the folder in Windows Explorer automatically
        try:
            subprocess.Popen(["explorer", output_dir])
        except Exception:
            pass
    else:
        print("No files were recovered.")

    print()
    input("Press Enter to exit.")


if __name__ == "__main__":
    main()
