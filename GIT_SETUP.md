# Git Setup & Workflow

This document explains how to get set up with this repository and the day-to-day Git commands you'll use as a contributor.

## 1. Install Git

- **Windows**: download from [git-scm.com](https://git-scm.com/download/win) and install with default options.
- **macOS**: `brew install git` (or install Xcode Command Line Tools: `xcode-select --install`).
- **Linux**: `sudo apt install git` (Debian/Ubuntu) or your distro's package manager.

Verify it worked:

```bash
git --version
```

## 2. One-time configuration

Set your identity (used to attribute your commits):

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

## 3. Getting the code

Once the repo has a remote (GitHub/GitLab/etc.), clone it:

```bash
git clone <remote-url>
cd <repo-folder>
```

If you already have a local copy and just need to connect it to the remote:

```bash
git remote add origin <remote-url>
git push -u origin main
```

## 4. Everyday commands

These are the commands you'll use constantly. Run them from inside the project folder.

### Check what's going on

```bash
git status
```

Shows which files are modified, staged, or untracked. Run this often — it's always safe.

```bash
git log --oneline -10
```

Shows the last 10 commits, one line each.

### Stage changes

```bash
git add <file>          # stage one file
git add .                # stage everything changed in the current folder and below
```

Prefer adding specific files by name over `git add .` when you've touched files you don't intend to commit (e.g. local screenshots, `chromedriver`, generated files) — check `git status` first.

### Commit

```bash
git commit -m "Short, descriptive message about the change"
```

A good commit message explains *why* the change was made, not just *what* changed.

### Push (send your commits to the shared remote)

```bash
git push
```

If it's the first push on a new branch:

```bash
git push -u origin <branch-name>
```

### Pull (get everyone else's latest changes)

```bash
git pull
```

Run this before you start new work each day, and before you push, to avoid conflicts.

## 5. Recommended branch workflow

Don't commit directly to `main`. For each piece of work:

```bash
git checkout main
git pull
git checkout -b feature/short-description
```

Make your changes, then:

```bash
git add <files>
git commit -m "Describe the change"
git push -u origin feature/short-description
```

Then open a Pull Request against `main` so the change can be reviewed before merging.

## 6. Handling conflicts

If `git pull` or a merge reports conflicts:

1. Open the affected file(s) — Git marks conflicting sections with `<<<<<<<`, `=======`, `>>>>>>>`.
2. Edit the file to keep the correct content and remove the conflict markers.
3. Stage the resolved file(s): `git add <file>`
4. Continue: `git commit` (for a merge) or `git rebase --continue` (if rebasing).

If you're unsure, stop and ask rather than force-pushing or discarding changes.

## 7. Things to avoid

- Don't `git push --force` on shared branches (like `main`) — it can overwrite teammates' work.
- Don't commit secrets, API keys, or credentials.
- Don't commit generated/local files — check [.gitignore](.gitignore) and add patterns for anything new that shouldn't be tracked (e.g. `chromedriver`, browser profile folders, screenshots).
- Run `git status` before any destructive command (`git checkout --`, `git reset --hard`, `git clean`) — these can discard uncommitted work.
