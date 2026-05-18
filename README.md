# Flying Probe Backup Agent

Watches your Flying Probe programs folder for changes, commits every save to a local git repository, and pushes to GitHub when the network is available. If someone overwrites a file by mistake, you can recover any previous version without touching the current programs.

---

## How it works

```
Someone saves dummy123.job
        ↓
backup_agent.py sees the change, waits 10 seconds
(batches rapid saves into one commit)
        ↓
git commit — "Auto-backup 2024-03-15 09:32
              Changed files:
                - dummy123.job"
        ↓
Internet available?
  YES → push to GitHub immediately
  NO  → save locally, retry every 5 minutes
```

The programs folder is **never modified** — the agent only reads it and stores history in the backup repo.

---

## Two programs

### `backup_agent.py` — runs silently in the background 24/7

- Watches your programs folder for any file saves or new files
- Waits 10 seconds after a change (to batch rapid saves into one commit)
- Commits only the files that actually changed, with a message listing each one
- Pushes to GitHub if internet is available, otherwise saves locally and retries

### `recover.py` — run this only when you need to recover something

- You type part of a filename (e.g. `dummy123`)
- It shows only the saves of that specific file, most recent first
- You pick the version you want
- The file is copied to `C:\FlyingProbeRecovered\` and Windows Explorer opens it automatically
- **The programs folder is never touched**

---

## What lives where on the PC

| Folder | What it is |
|---|---|
| `C:\FlyingProbePrograms\` | Your actual work — the agent watches this |
| `C:\FlyingProbeBackup\` | The git vault — full history of every backed up file |
| `C:\FlyingProbeRecovered\` | Recovered files land here — safe to delete anytime |
| `C:\BackupAgent\` | The scripts: backup_agent.py, recover.py, config.json |

`FlyingProbeBackup` is the bank vault. All history is locked in there safely.
`FlyingProbeRecovered` is the teller window. When you need something, it hands you a copy. The vault never changes.

---

## Why git only stores changed files

Each commit only contains the files that actually changed, not the entire folder:

```
Commit #1  →  dummy123.job + boardXYZ.job + testABC.job   (all new)
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
5. File is saved to C:\FlyingProbeRecovered\dummy123_2024-03-14\
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

**3. Copy this project** to `C:\BackupAgent\`

**4. Edit `config.json`**

```json
"repo_path":          "C:/FlyingProbeBackup",
"watch_paths":        ["C:/FlyingProbePrograms"],
"github_remote_url":  "https://github.com/YOUR_USERNAME/YOUR_REPO.git",
"recovered_path":     "C:/FlyingProbeRecovered"
```

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
| `repo_path` | Where git stores the backup history |
| `watch_paths` | Folder(s) to watch for changes |
| `file_extensions` | Only back up files with these extensions |
| `debounce_seconds` | How long to wait after a save before committing (default: 10) |
| `github_remote_url` | Your GitHub repo URL |
| `recovered_path` | Where recovered files are saved |
| `commit_author_name` | Name shown on git commits |
| `commit_author_email` | Email shown on git commits |
