# Porting Deck to another device

Deck was built for the Anbernic RG35XX H, but nothing about the *idea* is
specific to it: a small SDL2 program draws a menu and a few native screens, and
delegates anything it can to programs already on the device. This guide isolates
every device-specific assumption so you can move it to another Anbernic handheld
— or something entirely different — with a clear checklist.

The golden rule that keeps it safe and portable: **be additive.** Add files, call
existing binaries, store your own data in your own directory. Don't modify the
launcher, init, or firmware. If uninstalling is just "delete two things," you can
experiment freely.

---

## The five things that are device-specific

Everything you must check on a new device falls into these buckets. The rest of
the code is portable.

| # | Concern | Where it lives | RG35XX H value |
|---|---------|----------------|----------------|
| 1 | How apps are launched | firmware convention | a `.sh` wrapper + payload dir under `Roms/APPS/` |
| 2 | Gamepad input | `main.py` `BUTTONS`, `EVENT_FORMAT`, `gamepad_path()` | `/dev/input/eventX` (by name), 24-byte events, specific button codes |
| 3 | Display | `main.py` `WIDTH/HEIGHT`, `Screen` | 640×480, SDL2 fullscreen |
| 4 | Runtime & fonts | `main.py` `FONT_CANDIDATES`, requirements | Python3 + PySDL2 + Pillow; DejaVu Sans Mono |
| 5 | Delegated programs | `Deck.sh`, `net.py`, `sound.py` | DinguxCommander, sshd, `aplay`, `git` |

Work through them in order; each has a "how to discover it" recipe below.

---

## 1. App-launch convention

On the RG35XX H stock-OS mod, every app is a top-level `Name.sh` plus a payload
directory, both under `Roms/APPS/`. The launcher runs the `.sh` with `sh`, so the
executable bit doesn't matter, and the wrapper returns synchronously.

**On a new device, find out how the front-end launches apps.** Look at an existing
app and copy its wrapper pattern. Common families:

- **Anbernic stock / stock-mod** — the `.sh` + payload convention above.
- **muOS** — apps/ports are launched via a `.sh` in a known directory with a
  specific environment; check `MUOS/application/`.
- **Knulli / ROCKNIX / ArkOS (EmulationStation)** — add a "port" (a `.sh`) that ES
  lists; there's usually a `ports/` collection.
- **A plain Linux box** — there is no launcher; run `Deck.sh` directly or make a
  `.desktop`/service.

Adapt `Deck.sh`'s header (how it finds its own directory, the env it exports) to
match; the loop body is portable.

---

## 2. Gamepad input

Deck reads the joypad straight from the Linux input layer (`evdev`), no SDL input.
Three things must match your device:

**a. The event device.** `gamepad_path()` resolves it *by name* from
`/proc/bus/input/devices` (the RG35XX H calls it `ANBERNIC-keys`) rather than
hard-coding `event1`, because numbering isn't stable. Find yours:

```sh
cat /proc/bus/input/devices          # look for the joypad's Name=
# or, interactively:
evtest                               # pick the device, watch codes/values
```

Change the name string in `gamepad_path()` to match.

**b. The event struct size.** An `input_event` is `struct { timeval time; __u16
type; __u16 code; __s32 value; }`. On a **64-bit** userland the `timeval` is two
64-bit longs → **24 bytes** (`EVENT_FORMAT = "llHHI"`). On a **32-bit** userland
it's two 32-bit longs → **16 bytes** (`"iiHHi"`). Check:

```sh
getconf LONG_BIT        # 32 or 64
file /bin/busybox        # or any binary: "ELF 32-bit" vs "ELF 64-bit"
```

Set `EVENT_FORMAT`/`EVENT_SIZE` accordingly.

**c. The button codes.** `BUTTONS` maps kernel codes to names. The d-pad is
usually two `ABS_HAT0X/Y` axes (codes 16/17, values -1/0/+1); face/shoulder
buttons are `BTN_*` (304+). These vary by device — read them off `evtest` and
update the map. Deck only needs: d-pad, A, B, X, Y, L1, SELECT, START, MENU.

> Tip: the values you'll see are press=1, release=0, and (for some drivers)
> autorepeat=2. Deck acts on presses and ignores releases, which makes it robust
> even on a worn d-pad. Avoid input designs that depend on receiving a clean
> release event.

---

## 3. Display

`main.py` assumes **640×480** and opens one SDL2 fullscreen window, rendering with
Pillow into an RGBA buffer that's blitted as a texture. To port:

- Set `WIDTH`/`HEIGHT` to your panel (e.g. 720×720, 1280×720). The layout uses a
  few absolute pixel positions (menu rows, keyboard geometry `_KB_*`, text area) —
  scale those or switch to proportions of `WIDTH`/`HEIGHT`.
- Confirm SDL2 can open the framebuffer. `SDL_WINDOW_FULLSCREEN_DESKTOP` works on
  most of these. If you get a black screen, the SDL video driver may need an env
  hint (`SDL_VIDEODRIVER`, `SDL_FBDEV`).

Nothing else in the render code is resolution-specific.

---

## 4. Runtime & fonts

Deck needs **Python 3, PySDL2, and Pillow** on the device. Check:

```sh
python3 -c "import sdl2, PIL; print('ok')"
```

If PySDL2 is missing you can vendor it (ship a `sdl2/` package and set
`PYSDL2_DLL_PATH` to where `libSDL2` lives). Pillow is heavier to vendor; most of
these firmwares already include it.

Fonts: `FONT_CANDIDATES` lists paths tried in order. Point it at any monospace TTF
on your device (`fc-list | grep -i mono`, or just drop a `font.ttf` into `deck/`).

**No physical keyboard?** Then text entry *must* be an on-screen keyboard — that's
why Notes draws its own. This is true of essentially every handheld; budget for it
rather than assuming a shell/editor will do.

---

## 5. Delegated programs

Deck deliberately shells out for things that already exist, instead of writing
them. Each is optional and swappable:

- **Files → a file manager.** The RG35XX H has DinguxCommander at
  `/mnt/vendor/bin/fileM/`. Find your device's equivalent (or fall back to a
  terminal file manager). `Deck.sh:run_files` launches it; change the path, or the
  fallback in `files.sh`.
- **Timer alarm → audio.** `sound.py` generates a WAV with the stdlib `wave`
  module and plays it with `aplay`. If `aplay` is absent, try `mpv`, `ffplay`, or
  `paplay` (the code already tries a couple). If the device has a **vibration
  motor**, you can buzz it instead — on the RG35XX H that's a sysfs switch
  (`echo 1 > /sys/class/power_supply/axp2202-battery/moto`); yours may differ or
  be a proper force-feedback device.
- **SSH → the stock sshd.** `net.py` calls `/etc/init.d/ssh` and reads the IP with
  `ip`. Adjust if your init system or paths differ.
- **A terminal (optional).** Some firmwares ship a framebuffer terminal with an
  on-screen keyboard (the RG35XX H mod ships `SimpleTerminal`, an `st-sdl` build
  that accepts `-e <command>`). Deck only uses it as the Files fallback now that
  Notes is native. Not required.

---

## Notes sync (git over SSH)

Portable as-is, given `git` and `ssh` on the device:

1. Generate a **deploy key on the device** (not your laptop's key):
   ```sh
   ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_notes_ed25519
   ```
2. Add the **public** half to your notes repo as a **deploy key with write
   access** (GitHub: repo → Settings → Deploy keys).
3. Point git at it via `/root/.ssh/config`:
   ```
   Host github.com
       IdentityFile /root/.ssh/id_notes_ed25519
       IdentitiesOnly yes
   ```
4. Set `GIT_REMOTE` in `config_local.py` (git-ignored) to the SSH URL.

**Gotcha worth knowing:** sshd's `StrictModes` silently refuses key auth if the
home directory is group/other-writable. If a key that should work is rejected with
a bare "Permission denied (publickey)", check `chmod 700 ~`.

Keep private keys **on the device only** — never in the repo. `config_local.py`
(your remote URL) and any key material are git-ignored for exactly this reason.

---

## Porting checklist

- [ ] Launch convention: how does the front-end start an app? Mirror it in `Deck.sh`.
- [ ] `gamepad_path()` device name matches `/proc/bus/input/devices`.
- [ ] `EVENT_FORMAT`/`EVENT_SIZE` match 32- vs 64-bit userland.
- [ ] `BUTTONS` codes match `evtest` output.
- [ ] `WIDTH`/`HEIGHT` and layout positions match the panel.
- [ ] `python3 -c "import sdl2, PIL"` succeeds; `FONT_CANDIDATES` has a real font.
- [ ] Files program path (or fallback) is correct.
- [ ] Alarm: `aplay`/`mpv`/`ffplay` present, or wire up vibration.
- [ ] Notes sync: device deploy key added to the repo; `config_local.py` set.
- [ ] Uninstall test: deleting `Deck.sh` + `deck/` restores the device exactly.

Build in this order and test after each step — a menu entry that draws "hello"
and exits cleanly proves the whole integration before you write any features.
