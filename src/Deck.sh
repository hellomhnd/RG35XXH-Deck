#!/bin/bash
# Deck -- a minimal utility menu.
# Additive only: deleting Deck.sh and deck/ returns the device to stock.

progdir="$(cd "$(dirname "$0")" || exit; pwd)"/deck

export PYSDL2_DLL_PATH="/usr/lib"
export LD_LIBRARY_PATH="/usr/lib32:/usr/lib:/mnt/vendor/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HOME="/root"
export TERM="xterm"

log_file="${progdir}/log.txt"
sel_file="${progdir}/selection"
term="${progdir}/SimpleTerminal"

: > "$log_file"

# SimpleTerminal is SDL 1.2 and main.py is SDL2; they must never hold the
# framebuffer at once. The menu exits before each action and is relaunched
# after, so only one owns the display at a time.
run_term() {
    echo "--- SimpleTerminal -e $* ---" >>"$log_file"
    "$term" -e "$@" >>"$log_file" 2>&1
    echo "--- SimpleTerminal exit=$? ---" >>"$log_file"
}

# Files uses DinguxCommander, a stock gamepad file manager (SDL 1.2, so it
# obeys the same one-owner-at-a-time rule as SimpleTerminal). Its config is
# derived from the stock one at launch so the button mappings always match this
# firmware; only the opening paths are changed. Falls back to a shell if the
# binary is ever missing (e.g. a different firmware).
run_files() {
    local dge="/mnt/vendor/bin/fileM/dinguxCommand_en.dge"
    local stock_cfg="/mnt/vendor/bin/fileM/commander.cfg"
    local my_cfg="${progdir}/commander.cfg"

    if [ ! -x "$dge" ]; then
        run_term /bin/bash "${progdir}/files.sh"
        return
    fi

    if [ -f "$stock_cfg" ]; then
        sed -e 's#^path_default=.*#path_default=/mnt/mmc#' \
            -e 's#^path_default_right=.*#path_default_right=/mnt/sdcard#' \
            -e 's#^path_default_right_fallback=.*#path_default_right_fallback=/mnt/mmc#' \
            -e 's#^res_dir=.*#res_dir=/mnt/vendor/bin/fileM/res/#' \
            "$stock_cfg" > "$my_cfg"
    fi

    echo "--- DinguxCommander ---" >>"$log_file"
    HOME=/root "$dge" --config "$my_cfg" >>"$log_file" 2>&1
    echo "--- DinguxCommander exit=$? ---" >>"$log_file"
}

while true; do
    rm -f "$sel_file"

    python3 "${progdir}/main.py" >>"$log_file" 2>&1

    # No selection file means the menu was exited with B or MENU.
    [ -f "$sel_file" ] || break

    # SSH, Timer and Notes are drawn natively by main.py and never reach here.
    # Only Files still needs an external program.
    case "$(cat "$sel_file")" in
        files) run_files ;;
    esac
done

rm -f "$sel_file"
sync
exit 0
