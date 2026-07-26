#!/bin/bash
# Deploy Deck to the RG35XX H, and pull its log back.
#
#   ./deploy.sh            copy src/ to the SD card, sync, and report
#   ./deploy.sh log        print the last run's log from the card
#   ./deploy.sh eject      sync and unmount the card
#   ./deploy.sh ssh <ip>   copy over the network instead (once SSH is on)
#
# The canonical copy of Deck lives here, in src/. The card is only ever a
# deploy target -- never edit it directly, or the next deploy overwrites you.

set -u

here="$(cd "$(dirname "$0")" && pwd)"
src="$here/src"
# SD card mount point. Override for your card: DECK_CARD=/path ./deploy.sh
card="${DECK_CARD:-/run/media/$USER/B56E-47E1}"
apps="$card/Roms/APPS"

die() { echo "error: $*" >&2; exit 1; }

require_card() {
    mountpoint -q "$card" || die "card is not mounted at $card"
    [ -d "$apps" ] || die "$apps does not exist"
}

copy_to() {
    local dest="$1"
    cp    "$src/Deck.sh"   "$dest/Deck.sh"
    mkdir -p "$dest/deck"

    # Drop code that no longer exists in src/. Without this a script that was
    # deleted here lingers on the card and quietly keeps running.
    local name
    for stale in "$dest"/deck/*.py "$dest"/deck/*.sh; do
        [ -e "$stale" ] || continue
        name="$(basename "$stale")"
        [ -e "$src/deck/$name" ] || { echo "pruning stale deck/$name"; rm -f "$stale"; }
    done
    rm -rf "$dest/deck/__pycache__"

    cp    "$src"/deck/*.py "$src"/deck/*.sh "$src"/deck/*.gz "$dest/deck/"
    cp    "$src/deck/SimpleTerminal" "$dest/deck/SimpleTerminal"
    mkdir -p "$dest/Imgs"
    cp    "$src/Deck.png"  "$dest/Imgs/Deck.png"
}

case "${1:-deploy}" in
    deploy)
        require_card
        # Guard against the classic CRLF-shebang failure before it ships.
        if grep -qlU $'\r' "$src/Deck.sh" "$src"/deck/*.sh 2>/dev/null; then
            die "CRLF line endings found in a shell script; run: sed -i 's/\r\$//' <file>"
        fi
        copy_to "$apps"
        sync
        echo "deployed to $apps"
        ;;

    log)
        require_card
        if [ -s "$apps/deck/log.txt" ]; then
            cat "$apps/deck/log.txt"
        else
            echo "(log.txt is empty or missing -- has Deck been run yet?)"
        fi
        ;;

    eject)
        sync
        cd / || exit 1
        # A plain unmount fails whenever any process merely sits in the card as
        # its cwd -- which the agent session itself usually does. --force is a
        # lazy unmount via polkit: no root, no password, and safe because the
        # sync above has already flushed everything.
        disk="$(findmnt -no SOURCE "$card" 2>/dev/null)"
        disk="${disk:-/dev/sda1}"
        base="${disk%[0-9]*}"
        for part in "$base"*; do
            [ -b "$part" ] || continue
            findmnt -no TARGET "$part" >/dev/null 2>&1 || continue
            echo -n "$part: "
            udisksctl unmount -b "$part" --force 2>&1
        done
        if findmnt -no SOURCE | grep -q "^$base"; then
            echo "warning: some partitions are still mounted"
        else
            echo "card fully detached -- safe to remove"
        fi
        ;;

    ssh)
        ip="${2:-}"
        [ -n "$ip" ] || die "usage: $0 ssh <device-ip>"
        ssh "root@$ip" 'mkdir -p /mnt/mmc/Roms/APPS/deck /mnt/mmc/Roms/APPS/Imgs'

        # Prune deck scripts/modules that no longer exist in src/, so a deleted
        # file cannot linger and keep running on the device.
        keep="$(cd "$src/deck" && ls *.py *.sh 2>/dev/null | tr '\n' '|')"
        ssh "root@$ip" "keep='$keep'; cd /mnt/mmc/Roms/APPS/deck || exit 0
            for f in *.py *.sh; do
                [ -e \"\$f\" ] || continue
                case \"|\$keep\" in *\"|\$f|\"*) : ;; *) echo \"pruning \$f\"; rm -f \"\$f\";; esac
            done
            rm -rf __pycache__"

        scp "$src/Deck.sh"                "root@$ip:/mnt/mmc/Roms/APPS/Deck.sh"
        scp "$src"/deck/*.py "$src"/deck/*.sh "$src"/deck/*.gz "root@$ip:/mnt/mmc/Roms/APPS/deck/"
        scp "$src/Deck.png"               "root@$ip:/mnt/mmc/Roms/APPS/Imgs/Deck.png"

        # SimpleTerminal never changes and overwriting the running binary on
        # FAT32 fails, so push it only when it is missing on the device.
        if ! ssh "root@$ip" 'test -s /mnt/mmc/Roms/APPS/deck/SimpleTerminal'; then
            scp "$src/deck/SimpleTerminal" "root@$ip:/mnt/mmc/Roms/APPS/deck/"
            ssh "root@$ip" 'chmod 755 /mnt/mmc/Roms/APPS/deck/SimpleTerminal'
        fi
        ssh "root@$ip" 'sync'
        echo "deployed to $ip"
        ;;

    *)
        die "unknown command: $1"
        ;;
esac
