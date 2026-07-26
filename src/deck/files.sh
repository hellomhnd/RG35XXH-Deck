#!/bin/bash
# Drop into a shell for browsing and reading. No custom browser -- the
# firmware already ships vi, less and the usual coreutils.

progdir="$(cd "$(dirname "$0")" && pwd)"

start_dir="$(cd "$progdir" && python3 -c 'import config; print(config.FILES_START_DIR)')"
cd "${start_dir:-/mnt/mmc}" 2>/dev/null || cd /

cat <<'EOF'
Files
-----
  X = show/hide keyboard   d-pad = pick key   A = press
  START = enter            L1 = shift         MENU = quit

  ls           list          less <f>   read a file
  cd <d>       change dir    vi <f>     edit a file
  exit         back to Deck

EOF

exec /bin/bash --norc -i
