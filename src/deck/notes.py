"""Saving and git-sync for the native note editor.

Pure Python so the editor never has to shell out to a terminal. The note is
written to disk before git is touched, so a failed push can never lose it.
"""

import datetime
import os
import subprocess

import config


def add_note(text):
    """Prepend a timestamped entry to the notes file so the newest note is at
    the top. Returns the stamp, or None if the note was blank."""
    text = text.strip()
    if not text:
        return None
    os.makedirs(os.path.dirname(config.NOTES_FILE), exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = "## %s\n\n%s\n" % (stamp, text)
    try:
        with open(config.NOTES_FILE, encoding="utf-8") as f:
            existing = f.read()
    except OSError:
        existing = ""
    with open(config.NOTES_FILE, "w", encoding="utf-8") as f:
        f.write(entry + ("\n" + existing if existing.strip() else ""))
    try:
        os.sync()
    except OSError:
        pass
    return stamp


# Backwards-compatible alias.
append_note = add_note


def sync(stamp):
    """Best-effort two-way git sync: commit, pull remote changes, push.
    Returns (ok, short message). The note is already on disk, so any failure
    here is non-fatal.

    Notes are append-only, so `notes.md` is merged with git's built-in `union`
    driver (via .gitattributes) -- both sides' lines are kept, never a conflict.
    That lets edits made on a laptop and on the device coexist."""
    remote = config.GIT_REMOTE
    if not remote:
        return True, "saved (local only)"

    work = os.path.dirname(config.NOTES_FILE)
    branch = config.GIT_BRANCH

    def git(*args, timeout=45):
        return subprocess.run(
            ["git", *args],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        fresh = not os.path.isdir(os.path.join(work, ".git"))
        if fresh:
            git("init", "-q")
            git("remote", "add", "origin", remote)
            git("branch", "-M", branch)

        # Ensure the union merge driver applies to markdown.
        attrs = os.path.join(work, ".gitattributes")
        if not os.path.exists(attrs):
            with open(attrs, "w", encoding="utf-8") as f:
                f.write("*.md merge=union\n")

        git("add", "-A")
        git(
            "-c", "user.name=deck", "-c", "user.email=deck@localhost",
            "commit", "-q", "-m", "notes: %s" % stamp,
        )

        # Pull remote (laptop) changes, then push. Fetch/merge are best-effort.
        merged = True
        if git("fetch", "-q", "origin", branch).returncode == 0 and \
                git("rev-parse", "--verify", "-q",
                    "origin/" + branch).returncode == 0:
            merge_args = ["merge", "--no-edit", "-q", "origin/" + branch]
            if fresh:
                merge_args.insert(1, "--allow-unrelated-histories")
            if git(*merge_args).returncode != 0:
                git("merge", "--abort")
                merged = False

        pushed = git("push", "-q", "origin", branch)
        if pushed.returncode == 0:
            return True, "saved & synced" if merged else "saved & pushed"
        return False, "saved locally (push failed)"
    except subprocess.TimeoutExpired:
        return False, "saved locally (sync timed out)"
    except OSError:
        return False, "saved locally (git error)"
