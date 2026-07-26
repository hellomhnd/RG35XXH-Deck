# Copy this file to config_local.py and fill in your own values.
# config_local.py is git-ignored, so your private settings never get committed
# but still deploy to the device. Anything defined here overrides config.py.

# Your notes repo (SSH URL recommended; set up a device deploy key -- see PORTING.md)
GIT_REMOTE = "git@github.com:you/your-notes.git"

# Optional: override any other config.py value, e.g.
# NOTES_DIR = "/mnt/sdcard/Notes"
# FILES_START_DIR = "/mnt/sdcard"
