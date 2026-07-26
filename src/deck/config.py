"""Deck configuration. Plain Python, edited by hand. No config UI."""

import os

DIR = os.path.dirname(os.path.abspath(__file__))

# Where notes live. On the FAT32 card so they survive a firmware reflash and
# can be read directly when the card is in a laptop.
NOTES_DIR = "/mnt/mmc/Notes"
NOTES_FILE = os.path.join(NOTES_DIR, "notes.md")

# Leave empty to disable git sync entirely. Notes are always written to disk
# first, so a failed push never loses anything.
#
# To enable sync, set GIT_REMOTE to your repo (an SSH URL is recommended):
#   GIT_REMOTE = "git@github.com:you/your-notes.git"
# Auth is an SSH deploy key generated ON the device and added to that repo with
# write access -- see PORTING.md. Put your personal value in config_local.py
# (git-ignored) so it never ends up in a public commit.
GIT_REMOTE = ""
GIT_BRANCH = "main"

# Where Files opens.
FILES_START_DIR = "/mnt/mmc"

# Optional plug-in modules to load at startup, by import name. Empty by default;
# set the list in config_local.py. A plug-in may expose LABEL (str) and run(...)
# to add an entry to the Tools submenu.
PLUGINS = []

# Map tiles (the Map screen). Fetched on demand over Wi-Fi and cached to disk;
# the cache is size-capped with LRU eviction, so it can never bloat storage.
# It goes on /mnt/data (a roomy, separate partition) when that exists, else
# beside the code. Point MAP_TILE_URL at another provider (some need a key) if
# you prefer; keep a descriptive User-Agent.
MAP_CACHE_DIR = ("/mnt/data/deck-tiles" if os.path.isdir("/mnt/data")
                 else os.path.join(DIR, "tiles"))
MAP_CACHE_MAX_MB = 150          # hard ceiling; oldest tiles deleted past this
MAP_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_USER_AGENT = "RG35XXH-Deck/1.0 (personal handheld map viewer)"


_STATE = os.path.join(DIR, "state")


def load_last_position(count):
    """Restore the cursor so returning from an action lands where you left."""
    try:
        with open(_STATE) as f:
            return int(f.read().strip()) % count
    except (OSError, ValueError):
        return 0


def save_last_position(position):
    try:
        with open(_STATE, "w") as f:
            f.write(str(position))
    except OSError:
        pass


_THEME = os.path.join(DIR, "theme")


def load_theme():
    """Return the saved theme name, defaulting to 'dark'."""
    try:
        with open(_THEME) as f:
            name = f.read().strip()
            return name if name in ("dark", "light", "eink") else "dark"
    except OSError:
        return "dark"


def save_theme(name):
    try:
        with open(_THEME, "w") as f:
            f.write(name)
    except OSError:
        pass


# Private, machine-local overrides (git remote, custom paths). This file is
# git-ignored, so personal values stay out of any public commit while still
# deploying to the device. See config_local.example.py.
try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass
