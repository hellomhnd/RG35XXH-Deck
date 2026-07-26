# Deck

A minimal, distraction-free utility app for the **Anbernic RG35XX H** (a retro
handheld running a modded stock OS). It turns the device into a pocketable notes
and reading companion — deliberately incapable of anything else.

One terminal-styled menu:

```
> Notes      a native on-screen-keyboard editor; syncs to git
  SSH        toggle the ssh server, show the device IP, fix the clock
  Timer      countdown with big digits and an audio alarm
  Files      the stock gamepad file manager (browse & read)
  Tools      passive Wi-Fi / network / Bluetooth scanners for your own kit
  Weather    current conditions + forecast for a city you pick (keyless API)
  Map        pannable slippy-map; caches tiles, so places you've seen work offline
```

Aesthetic: monospace, dark by default, no icons. Three themes — **dark / light /
e-ink** — cycled with SELECT and remembered.

It is **purely additive**: everything lives under `Roms/APPS/`. Delete `Deck.sh`
and `deck/` and the device is exactly as it was — no firmware changes, no reflash.

> Want to run this on a different handheld (another Anbernic, or something else
> entirely)? See **[PORTING.md](PORTING.md)** — it isolates every device-specific
> assumption and shows how to find the right values on your hardware.

## Features

- **Notes** — a full native editor drawn with SDL2 (no terminal). On-screen QWERTY
  keyboard: d-pad / left stick to move, A to type. Auto-capitalises sentence
  starts; **shift** gives every key a second character (`! @ # $ …`, and
  `, . ' ?` → `: - " /`). **Caret editing** — the right stick moves the caret by
  letter and line, so you can go back and fix anything. **Word prediction** — a
  suggestion row (frequency-ranked, tuned for academic writing); L2/R2 scroll it,
  R1 accepts. Each entry is timestamped and written to a plain `notes.md`
  (**newest first**), then committed and two-way synced to a git repo (optional).
  The note hits disk *before* any push, so a failed sync never loses it.
- **SSH** — flips the stock OpenSSH server on/off, shows the device IP and a
  ready-to-paste `ssh` line, and can set the clock from the network (these
  handhelds have no reliable RTC).
- **Timer** — d-pad sets MM:SS, big monospace digits, pause/resume, and an audio
  alarm generated on-device (no shipped sound file).
- **Files** — launches the firmware's DinguxCommander: a dual-pane gamepad file
  manager with built-in text and image viewers. No custom browser, no typing.
- **Tools** — passive scanners for your own surroundings and network, each a
  native scrollable screen (uses `iw`, `ping`, `bluetoothctl` — no installs):
  - **Wi-Fi** — nearby networks with signal bars, channel, and security
  - **Network** — ping-sweep + port-scan of the LAN you're on (live hosts,
    hostnames, open ports) in pure Python
  - **Bluetooth** — nearby BT/BLE devices

  > **Responsible use.** These are read-only discovery tools for networks and
  > devices *you own or are authorised to test*, and for learning — the same
  > category as `nmap` or any Wi-Fi analyzer. Use them accordingly.
- **Weather** — current conditions + a 4-day forecast for a city you pick (type
  the name once; it's remembered). Uses **Open-Meteo** — no API key. Metric by
  default; set `WEATHER_UNITS = "imperial"` in config for °F/mph.
- **Map** — a pannable OpenStreetMap view. Stick pans, L2/R2 zoom, A jumps to a
  city by name (no GPS on this device). R1 re-fetches the tiles on screen (map
  data changes upstream). Tiles are fetched over Wi-Fi and cached to disk, so
  anywhere you've already browsed keeps working **offline**. The
  cache is **size-capped** (150 MB default, `MAP_CACHE_MAX_MB`) with least-
  recently-used eviction, so it never bloats storage; Y clears it. It lives on
  `/mnt/data` when present, off the small ROMs partition. Point `MAP_TILE_URL`
  at another tile provider if you like.

## How it works

`Deck.sh` is a small loop. `deck/main.py` (SDL2 + PySDL2 + Pillow) draws the menu
and the native screens (Notes, SSH, Timer, Tools, Weather, Map), then exits; the shell dispatches Files
to an external program and re-enters the loop. Screens that need free-form text or
browsing use programs that already ship on the device — the app writes almost no
code it doesn't have to.

```
Roms/APPS/
├── Deck.sh              # dispatcher loop
└── deck/
    ├── main.py          # menu + native Notes / SSH / Timer screens
    ├── config.py        # paths, git remote (+ config_local.py override)
    ├── notes.py         # note save + git sync
    ├── weather.py       # Open-Meteo geocode + forecast
    ├── maps.py          # slippy-map tiles + size-capped disk cache
    ├── tools.py         # passive Wi-Fi / LAN / Bluetooth scanners
    ├── net.py           # ssh / network / clock helpers
    ├── sound.py         # timer alarm tone (generated with stdlib `wave`)
    └── files.sh         # shell fallback for Files
```

## Requirements (RG35XX H, modded stock OS)

Already present on the target firmware — nothing to install:

- Python 3 with **PySDL2** and **Pillow**
- A monospace TTF (DejaVu Sans Mono is used if present)
- `git`, `ssh`/`sshd`, `aplay` (for the alarm)
- DinguxCommander (for Files), at `/mnt/vendor/bin/fileM/`

## Install

1. Copy `Deck.sh` and the `deck/` directory into `Roms/APPS/` on the SD card.
2. (Optional) enable Notes sync: copy `deck/config_local.example.py` to
   `deck/config_local.py`, set `GIT_REMOTE`, and add a device deploy key to your
   repo (see [PORTING.md](PORTING.md#notes-sync-git-over-ssh)).
3. Launch **Deck** from the stock APPS menu.

### Developing

`deploy.sh` pushes the local `src/` to the device, over the network once SSH is on:

```sh
./deploy.sh ssh <device-ip>     # copy over Wi-Fi (no card shuffling)
./deploy.sh                     # or copy to a mounted SD card
./deploy.sh log                 # read back the device's deck/log.txt
./deploy.sh eject               # sync + unmount the card
```

## License

[MIT](LICENSE). Note that **SimpleTerminal** and **DinguxCommander** are
third-party programs shipped by the device firmware; they are used, not
redistributed here.
