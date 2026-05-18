# Flying Probe Backup Agent

Watches your Flying Probe programs folder for changes, mirrors every file into a local git repository, and pushes to GitHub when the network is available. If someone overwrites a file by mistake, you can recover any previous version without touching the current programs.

---

## How it works

```
Someone saves dummy123.job
        ↓
backup_agent.py sees the change, waits 10 seconds
(batches rapid saves into one commit)
        ↓
Copies dummy123.job into the Agent Database (mirrored folder structure)
        ↓
git commit — "Auto-backup 2024-03-15 09:32
              Changed files:
                - Watch Folder/dummy123.job"
        ↓
Internet available?
  YES → push to GitHub immediately
  NO  → save locally, retry every 5 minutes
```

The Watch Folder is **never modified** — the agent copies files out of it into the Agent Database.

On first startup the agent takes a **full snapshot** of every existing file in the watched folders, so nothing is missed even before the first change.

---

## What gets pushed to GitHub

The full contents of every file are stored in the repo — not just diffs. GitHub holds a complete copy of your programs at every point in time. The folder structure is mirrored inside the Agent Database:

```
C:\Agent Database\
  Watch Folder\             ← mirror of C:\Watch Folder
    BoardA\
      dummy123.job
      boardXYZ.job
    Data\                   ← mirror of C:\Watch Folder\Data (if configured)
      fixture001.xml
```

You can back up as many folders as you like — just add them to `watch_paths` in `config.json`.

---

## Two programs

### `backup_agent.py` — runs silently in the background 24/7

- On startup: takes a full snapshot of all files in all watched folders
- Watches for any file saves or new files
- Waits 10 seconds after a change (batches rapid saves into one commit)
- Copies changed files into the Agent Database, commits with a message listing each one
- Pushes to GitHub if internet is available, otherwise saves locally and retries every 5 minutes

### `recover.py` — run this only when you need to recover something

- You type part of a filename (e.g. `dummy123`)
- It shows only the saves of that specific file, most recent first
- You pick the version you want
- The file is copied to `C:\Data Recovery\` and Windows Explorer opens it automatically
- **The Watch Folder is never touched**

---

## What lives where on the PC

| Folder | What it is |
|---|---|
| `C:\Watch Folder\` | Your actual programs — agent watches and copies from here |
| `C:\Agent Database\` | The git vault — full mirrored copy and complete history |
| `C:\Data Recovery\` | Recovered files land here — safe to delete anytime |
| `C:\Agent Core\` | The scripts: backup_agent.py, recover.py, config.json |

`Agent Database` is the bank vault. All history is locked in there safely.
`Data Recovery` is the teller window. When you need something, it hands you a copy. The vault never changes.

---

## Why git only stores changed files

Each commit only contains the files that actually changed, not the entire folder:

```
Commit #1  →  dummy123.job + boardXYZ.job + testABC.job   (initial snapshot)
Commit #2  →  boardXYZ.job                                (only this changed)
Commit #3  →  dummy123.job                                (only this changed — broken)
Commit #4  →  testABC.job                                 (only this changed)
```

The full history of every file is always there. Git reconstructs it by looking back through commits. This means 10 years of programs won't take up nearly as much space as storing full folder copies every time.

---

## Recovering a file

Say someone saved over `dummy123.job` by mistake instead of Save As:

```
1. Double-click recover.bat
2. Type:  dummy123
3. Tool shows only the saves of that file:

     1.  2024-03-15 14:22  <-- MOST RECENT (may be the broken one)
     2.  2024-03-14 09:15
     3.  2024-03-10 11:05

4. Pick #2 (one above the broken version)
5. File is saved to C:\Data Recovery\dummy123_2024-03-14\
6. Windows Explorer opens that folder automatically
7. Copy the file wherever you need it
```

You always pick **one above the broken version** in the file's history — no scrolling through unrelated commits.

---

## One-time setup on the work PC

**1. Install Python**
Download from https://www.python.org/downloads/
Tick **"Add Python to PATH"** during install.

**2. Install Git**
Download from https://git-scm.com/download/win
Use default options.

**3. Copy this project** to `C:\Agent Core\`

**4. Edit `config.json`**

```json
"repo_path":          "C:/Agent Database",
"watch_paths":        ["C:/Watch Folder"],
"github_remote_url":  "https://github.com/YOUR_USERNAME/YOUR_REPO.git",
"recovered_path":     "C:/Data Recovery"
```

Add as many folders to `watch_paths` as you need.

**5. Create a private GitHub repo** and paste its URL into `github_remote_url`

**6. Run `setup_windows.bat`**
Checks Python and Git, installs dependencies, and offers to register the agent to auto-start on every login.

**7. Authenticate git to GitHub once** (in Command Prompt):
```
git config --global credential.helper manager
```
The first push will prompt for your GitHub login — after that it remembers.

---

## config.json reference

| Key | What it does |
|---|---|
| `repo_path` | Where git stores the mirrored files and history (`C:/Agent Database`) |
| `watch_paths` | Folder(s) to watch — all are mirrored into the repo |
| `file_extensions` | Only back up files with these extensions |
| `debounce_seconds` | How long to wait after a save before committing (default: 10) |
| `github_remote_url` | Your GitHub repo URL |
| `recovered_path` | Where recovered files are saved (`C:/Data Recovery`) |
| `commit_author_name` | Name shown on git commits |
| `commit_author_email` | Email shown on git commits |
