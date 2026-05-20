"""
Flying Probe Recovery Tool
Extracts a previous version of any program file into a separate Recovered folder.
The original programs folder is NEVER touched.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


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


def run_git(args, cwd):
    # -c safe.directory=* bypasses git's ownership check which blocks
    # network shares and folders owned by a different user
    result = subprocess.run(
        ["git", "-c", "safe.directory=*"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def all_tracked_files(repo_path):
    """
    Return every file path (relative to repo root) currently tracked by git.
    Uses git ls-files which is simpler and more reliable than log filtering.
    """
    code, out, err = run_git(["ls-files"], cwd=repo_path)
    if code != 0 or not out:
        if err:
            print(f"  [git error] {err}")
        return []
    seen = {}
    for line in out.splitlines():
        line = line.strip()
        if line:
            name = Path(line).name.lower()
            if name not in seen:
                seen[name] = line
    return sorted(seen.values(), key=lambda p: Path(p).name.lower())


def commits_for_file(repo_path, file_path, max_commits=100):
    """
    Return list of (short_hash, timestamp, size_tag, label) for every commit
    that touched this specific file — most recent first.
    size_tag is parsed from the commit body: [new], [size unchanged], [+N bytes], etc.
    """
    filename = Path(file_path).name.lower()
    code, out, _ = run_git(
        ["log", f"--max-count={max_commits}", "--format=##COMMIT##%h|%ci%n%b", "--", file_path],
        cwd=repo_path,
    )
    if code != 0 or not out:
        return []
    commits = []
    for block in out.split("##COMMIT##"):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        parts = lines[0].split("|", 1)
        if len(parts) != 2:
            continue
        short_hash = parts[0].strip()
        timestamp = parts[1].strip()[:19]
        size_tag = ""
        for line in lines[1:]:
            if filename in line.lower():
                m = re.search(r'\[([^\]]+)\]', line)
                if m:
                    size_tag = f"[{m.group(1)}]"
                break
        label = "  <-- MOST RECENT (may be the broken one)" if not commits else ""
        commits.append((short_hash, timestamp, size_tag, label))
    return commits


def extract_file(repo_path, commit_hash, file_path, output_dir):
    """
    Pull one file out of git history and write it to output_dir.
    Returns the destination path, or None on failure.
    The original programs folder is never touched.
    """
    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, Path(file_path).name)

    # git show reads from the object database — it never writes to the work tree
    code, content, err = run_git(
        ["show", f"{commit_hash}:{file_path}"],
        cwd=repo_path,
    )
    if code != 0:
        print(f"\n  [ERROR] Could not read {file_path} from commit {commit_hash}: {err}")
        return None

    with open(dest, "wb") as f:
        f.write(content.encode("utf-8", errors="replace"))
    return dest


def pick(prompt, items, display_fn):
    """Numbered menu — returns the chosen item, or None if user cancels."""
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


def search_files(all_files, query):
    """Return files whose name contains the query string (case-insensitive)."""
    q = query.lower()
    return [f for f in all_files if q in Path(f).name.lower()]


def last_saved_batch(repo_path, file_paths):
    """
    Return {file_path: timestamp} for all files in one git call instead of
    one call per file. Much faster when there are many search results.
    """
    code, out, _ = run_git(
        ["log", "--format=COMMIT %ci", "--name-only", "--diff-filter=AM"],
        cwd=repo_path,
    )
    timestamps = {}
    current_ts = ""
    if code == 0 and out:
        for line in out.splitlines():
            if line.startswith("COMMIT "):
                current_ts = line[7:23]
            elif line.strip():
                f = line.strip()
                if f not in timestamps:
                    timestamps[f] = current_ts
    return {f: timestamps.get(f, "") for f in file_paths}


def pick_grouped(prompt, matches, repo_path):
    """
    Display files grouped by their parent folder with the last-saved timestamp.
    Returns the chosen file path, or None if cancelled.

    Example:
      Machine A\
         1.  ProgramA.job          2024-03-15 14:22
         2.  ProgramB.job          2024-03-14 09:15
      Machine B\
         3.  ProgramC.job          2024-03-13 11:00
    """
    # Group by parent folder (everything above the filename)
    from collections import defaultdict
    groups = defaultdict(list)
    for f in matches:
        folder = str(Path(f).parent)
        groups[folder].append(f)

    timestamps = last_saved_batch(repo_path, matches)

    numbered = []  # flat ordered list for selection
    print()
    for folder in sorted(groups):
        print(f"  {folder}\\")
        for f in sorted(groups[folder], key=lambda x: Path(x).name.lower()):
            ts = timestamps.get(f, "")
            idx = len(numbered) + 1
            numbered.append(f)
            print(f"    {idx:>3}.  {Path(f).name:<40}  {ts}  [{idx}]")
        print()

    while True:
        raw = input(f"{prompt} (1-{len(numbered)}, or 0 to cancel): ").strip()
        if raw == "0":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(numbered):
            return numbered[int(raw) - 1]
        print("       Invalid choice, try again.")


def main():
    config_path = os.path.join(get_base_dir(), "config.json")

    if not os.path.exists(config_path):
        print("[ERROR] config.json not found next to the exe.")
        input("Press Enter to exit.")
        sys.exit(1)

    config = load_config()
    repo_path = config["repo_path"]
    recovered_root = config.get(
        "recovered_path",
        os.path.join(os.path.dirname(repo_path), "Data Recovery"),
    )

    print("=" * 62)
    print("  Flying Probe Recovery Tool")
    print(f"  Recovered files go to: {recovered_root}")
    print("  Your programs folder is NEVER modified.")
    print("=" * 62)

    if not os.path.isdir(repo_path):
        print(f"\n[ERROR] Backup repo not found at: {repo_path}")
        print("        Has the backup agent run at least once?")
        input("Press Enter to exit.")
        sys.exit(1)

    # ── Prefer server folder so history is always up to date ──────
    server_folder = config.get("server_folder", "")
    if server_folder and os.path.isdir(server_folder):
        repo_path = server_folder
        print("\n  Reading history from server.")
    elif server_folder:
        print("\n  Server folder not reachable — using local history.")

    print("\nType part of the filename to search, or press Enter to exit.")

    while True:
        # ── Step 1: search ───────────────────────────────────────
        print("\n" + "-" * 62)
        query = input("Search (or Enter to quit): ").strip()
        if not query:
            print("Goodbye.")
            break

        print("\nSearching backup history...")
        all_files = all_tracked_files(repo_path)
        matches = search_files(all_files, query)

        if not matches:
            print(f"\n  No files matching '{query}' found in backup history.")
            continue

        if len(matches) == 1:
            chosen_file = matches[0]
            ts = last_saved_batch(repo_path, [chosen_file]).get(chosen_file, "")
            print(f"\n  Found: {Path(chosen_file).parent}\\{Path(chosen_file).name}  ({ts})")
        else:
            print(f"\n  Found {len(matches)} matching file(s). Pick one:")
            chosen_file = pick_grouped("Enter file number", matches, repo_path)
            if chosen_file is None:
                continue

        # ── Step 2: version history ──────────────────────────────
        print()
        print("=" * 62)
        print("  STEP 2 — Pick which saved version to recover")
        print(f"  File: {Path(chosen_file).name}")
        print("=" * 62)
        print("\n  Loading save history...")
        commits = commits_for_file(repo_path, chosen_file)

        if not commits:
            print("  No backup history found for this file.")
            continue

        print(f"\n  {len(commits)} saved version(s) found — most recent at the top.")
        print("  If the latest is the broken one, pick the version just below it.")

        chosen_commit = pick(
            "Which version to recover",
            commits,
            lambda c: f"{c[1]}  {c[2]}{c[3]}",
        )
        if chosen_commit is None:
            continue

        commit_hash, commit_time, _, __ = chosen_commit

        # ── Step 3: extract ──────────────────────────────────────
        safe_name = Path(chosen_file).stem
        safe_time = commit_time.replace(":", "-").replace(" ", "_")
        output_dir = os.path.join(recovered_root, f"{safe_name}_{safe_time}_{commit_hash}")

        print(f"\nExtracting to:\n  {output_dir}\n")
        dest = extract_file(repo_path, commit_hash, chosen_file, output_dir)

        if dest:
            print(f"  [OK]  {Path(chosen_file).name}  saved.")
            print()
            print("Done! Copy the file from the folder below into your programs folder.")
            print("Your current programs were NOT changed.")
            print(f"\n  {output_dir}")
            try:
                subprocess.Popen(["explorer", output_dir])
            except Exception:
                pass
        else:
            print("Recovery failed — see error above.")

    input("\nPress Enter to close.")


if __name__ == "__main__":
    main()
