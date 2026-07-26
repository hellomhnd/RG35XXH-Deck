#!/usr/bin/env python3
"""Deck menu.

Screens that only show state and flip switches (SSH, later Timer) are drawn
here natively. Screens that need typing or browsing (Notes, Files) are handed
to SimpleTerminal instead: main.py writes the chosen action to `selection` and
exits, and Deck.sh dispatches it.

Exiting before handing over matters -- main.py is SDL2 and SimpleTerminal is
SDL 1.2, and they cannot both hold the framebuffer.
"""

import datetime
import math
import os
import select
import struct
import threading
import time

import sdl2
from PIL import Image, ImageDraw, ImageFont

import importlib

import config
import maps
import net


def _load_plugins():
    """Import the optional plug-in modules named in config.PLUGINS (empty by
    default). Missing or broken modules are skipped silently."""
    mods = []
    for name in getattr(config, "PLUGINS", []):
        try:
            mods.append(importlib.import_module(name))
        except Exception:
            pass
    return mods


PLUGINS = _load_plugins()
import notes
import predict
import sound
import tools
import weather

DIR = os.path.dirname(os.path.abspath(__file__))
SELECTION = os.path.join(DIR, "selection")

WIDTH, HEIGHT = 640, 480

# Two palettes. Render code reads the module-level colour names below, so
# switching theme is just reassigning them (see apply_theme) -- no drawing code
# changes. accent/good/bad/warn are chosen to read on either background.
PALETTES = {
    "dark": {
        "bg": "#0a0a0a", "fg": "#d0d0d0", "dim": "#4a4a4a", "accent": "#7fffd4",
        "warn": "#e0b060", "good": "#7fffd4", "bad": "#e06060",
        "line": "#1e1e1e", "keybg": "#181818", "keyedge": "#333333",
        "savebg": "#202b28",
    },
    "light": {
        "bg": "#f4f4ef", "fg": "#1a1a1a", "dim": "#8a8a84", "accent": "#0a7d6b",
        "warn": "#a6690a", "good": "#0a7d6b", "bad": "#b00020",
        "line": "#d4d4cc", "keybg": "#e7e7e0", "keyedge": "#c4c4bc",
        "savebg": "#cfe9e1",
    },
    # Minimal, e-ink-like: neutral paper grey, near-black ink, no colour.
    # Emphasis and selection come from black/inverted, not hue.
    "eink": {
        "bg": "#dcdcd7", "fg": "#141414", "dim": "#5a5a55", "accent": "#141414",
        "warn": "#141414", "good": "#141414", "bad": "#141414",
        "line": "#b4b4ae", "keybg": "#cfcfc9", "keyedge": "#9a9a94",
        "savebg": "#bcbcb5",
    },
}

# SELECT cycles through these in order.
THEME_ORDER = ["dark", "light", "eink"]

# Current-theme colour globals (set by apply_theme). Drawing code uses these.
BG = FG = DIM = ACCENT = WARN = GOOD = BAD = "#000000"
C_LINE = C_KEYBG = C_KEYEDGE = C_SAVEBG = "#000000"
THEME_NAME = "dark"


def apply_theme(name):
    global BG, FG, DIM, ACCENT, WARN, GOOD, BAD
    global C_LINE, C_KEYBG, C_KEYEDGE, C_SAVEBG, THEME_NAME
    p = PALETTES.get(name, PALETTES["dark"])
    THEME_NAME = name if name in PALETTES else "dark"
    BG, FG, DIM, ACCENT = p["bg"], p["fg"], p["dim"], p["accent"]
    WARN, GOOD, BAD = p["warn"], p["good"], p["bad"]
    C_LINE, C_KEYBG, C_KEYEDGE, C_SAVEBG = (
        p["line"], p["keybg"], p["keyedge"], p["savebg"]
    )


apply_theme("dark")

ITEMS = [
    ("Notes", "notes"),
    ("SSH", "ssh"),
    ("Timer", "timer"),
    ("Files", "files"),
    ("Tools", "tools"),
    ("Weather", "weather"),
    ("Map", "map"),
]

# Handled in-process. Everything else exits and lets Deck.sh run a terminal.
NATIVE = {"ssh", "timer", "notes", "tools", "weather", "map"}

# On-screen keyboard for the note editor. Each key is (label, kind, value);
# `span` (cells wide) defaults to 1. kind: char/space/back/enter/shift/save.
def _row(chars):
    return [(c, "char", c) for c in chars]

KB_ROWS = [
    _row("1234567890"),
    _row("qwertyuiop"),
    _row("asdfghjkl"),
    _row("zxcvbnm") + [(",", "char", ","), (".", "char", "."),
                        ("'", "char", "'"), ("?", "char", "?")],
    [
        ("shift", "shift", "", 2),
        ("space", "space", " ", 4),
        ("del", "back", "", 2),
        ("new", "enter", "", 1),
        ("SAVE", "save", "", 2),
    ],
]

# Shift turns letters upper-case and gives the number/symbol keys a second,
# useful character each -- so shift is never a wasted press.
SHIFT_MAP = {
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
    "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    ",": ":", ".": "-", "'": '"', "?": "/",
}


def shift_char(ch):
    """The shifted form of a character."""
    if ch.isalpha():
        return ch.upper()
    return SHIFT_MAP.get(ch, ch)

FONT_CANDIDATES = [
    os.path.join(DIR, "font.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/mnt/vendor/bin/default.ttf",
]

# BTN_* / ABS_HAT0* codes as they arrive from ANBERNIC-keys.
BUTTONS = {
    16: "DX", 17: "DY",
    304: "A", 305: "B", 306: "Y", 307: "X",
    308: "L1", 309: "R1", 310: "SELECT", 311: "START",
    312: "MENU", 314: "L2", 315: "R2",
}
EVENT_FORMAT = "llHHi"  # 64-bit userland; signed value (needed for analog axes)
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

# Analog sticks (ANBERNIC-keys ABS axes, ~-4096..4096). Non-standard codes on
# this device. Left stick drives DX/DY (navigation, deck-wide, like the d-pad);
# right stick emits RSX/RSY (used for suggestions/caret/list-scroll where handled).
# If a stick or a direction is wrong on your unit, adjust here (see the capture
# in the notes). code -> nav name.
ANALOG_AXES = {
    2: "DX",    # left stick horizontal -> navigate
    3: "DY",    # left stick vertical   -> navigate
    4: "RSX",   # right stick horizontal -> pick suggestion
    5: "RSY",   # right stick vertical   -> caret between lines
}
ANALOG_DEADZONE = 2200
_axis_state = {}   # code -> current discrete state (-1/0/1)


def decode_event(code, value):
    """Turn one raw input event into a (name, direction) press, or None.
    Buttons/d-pad pass straight through; analog axes emit a single nav press
    each time they cross the dead-zone, and nothing until they re-centre."""
    if code in BUTTONS:
        if value == 0:            # release
            return None
        return BUTTONS[code], (1 if value == 1 else -1)
    if code in ANALOG_AXES:
        state = 1 if value > ANALOG_DEADZONE else (-1 if value < -ANALOG_DEADZONE else 0)
        prev = _axis_state.get(code, 0)
        _axis_state[code] = state
        if state and state != prev:
            return ANALOG_AXES[code], state
    return None


def load_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def gamepad_path(default="/dev/input/event1"):
    """Resolve ANBERNIC-keys by name, since event numbering is not guaranteed."""
    try:
        with open("/proc/bus/input/devices") as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return default
    for block in blocks:
        if "ANBERNIC-keys" not in block:
            continue
        for token in block.split():
            if token.startswith("event"):
                return "/dev/input/" + token
    return default


_header_cache = {"t": 0.0, "text": ""}


def menu_header():
    """Battery / Wi-Fi / clock line for the menu, cached briefly so it does not
    spawn a subprocess on every keypress."""
    now = time.monotonic()
    if now - _header_cache["t"] < 4 and _header_cache["text"]:
        return _header_cache["text"]
    parts = []
    bat = net.battery()
    if bat:
        parts.append("%d%%%s" % (bat[0], "+" if bat[1] else ""))
    parts.append("wifi" if net.device_ip() else "no net")
    parts.append(datetime.datetime.now().strftime("%H:%M"))
    _header_cache["text"] = "   ".join(parts)
    _header_cache["t"] = now
    return _header_cache["text"]


class Screen:
    def __init__(self):
        sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO)
        self.window = sdl2.SDL_CreateWindow(
            b"Deck",
            sdl2.SDL_WINDOWPOS_UNDEFINED,
            sdl2.SDL_WINDOWPOS_UNDEFINED,
            0,
            0,  # ignored in fullscreen
            sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP | sdl2.SDL_WINDOW_SHOWN,
        )
        if not self.window:
            raise RuntimeError(sdl2.SDL_GetError().decode())

        self.renderer = sdl2.SDL_CreateRenderer(
            self.window, -1, sdl2.SDL_RENDERER_ACCELERATED
        ) or sdl2.SDL_CreateRenderer(self.window, -1, sdl2.SDL_RENDERER_SOFTWARE)
        if not self.renderer:
            raise RuntimeError(sdl2.SDL_GetError().decode())

        self.title_font = load_font(20)
        self.item_font = load_font(34)
        self.body_font = load_font(22)
        self.hint_font = load_font(18)
        self.tiny_font = load_font(16)
        self.big_font = load_font(72)
        self.digit_font = load_font(150)

    def _frame(self, title):
        image = Image.new("RGBA", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(image)
        draw.text((40, 44), title, font=self.title_font, fill=DIM)
        draw.line([(40, 76), (WIDTH - 40, 76)], fill=C_LINE, width=1)
        return image, draw

    def render_menu(self, position):
        image, draw = self._frame("deck")
        status = menu_header()
        if status:
            draw.text((WIDTH - 40, 54), status, font=self.hint_font,
                      fill=DIM, anchor="rm")
        top = 118
        step = min(58, (HEIGHT - 70 - top) // max(1, len(ITEMS)))
        for index, (label, _) in enumerate(ITEMS):
            selected = index == position
            draw.text(
                (40, top + index * step),
                ("> " if selected else "  ") + label,
                font=self.item_font,
                fill=ACCENT if selected else DIM,
            )
        draw.text((40, HEIGHT - 56), "A select    B exit", font=self.hint_font, fill=DIM)
        self._present(image)

    def render_rows(self, title, rows, hint, note=""):
        """A label/value screen. rows is a list of (label, value, colour)."""
        image, draw = self._frame(title)
        for index, (label, value, colour) in enumerate(rows):
            y = 130 + index * 42
            draw.text((40, y), label, font=self.body_font, fill=DIM)
            draw.text((220, y), value, font=self.body_font, fill=colour)
        if note:
            draw.text((40, HEIGHT - 96), note, font=self.hint_font, fill=WARN)
        draw.text((40, HEIGHT - 56), hint, font=self.hint_font, fill=DIM)
        self._present(image)

    def render_busy(self, title, message):
        image, draw = self._frame(title)
        draw.text((40, 200), message, font=self.item_font, fill=WARN)
        self._present(image)

    def render_timer(self, minutes, seconds, field, state, hint):
        """Big MM:SS clock. field: 0=minutes 1=seconds (setup only).
        state: 'set' | 'run' | 'pause' | 'done'."""
        image, draw = self._frame("deck / timer")

        text = "%02d:%02d" % (minutes, seconds)
        colour = {
            "set": FG, "run": ACCENT, "pause": WARN, "done": BAD,
        }[state]

        # Centre the digits, and (in setup) underline the field being edited.
        bbox = draw.textbbox((0, 0), text, font=self.digit_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (WIDTH - tw) // 2
        y = 150
        draw.text((x, y), text, font=self.digit_font, fill=colour)

        if state == "set":
            colon = draw.textlength(text[:2] + ":", font=self.digit_font)
            mm_w = draw.textlength(text[:2], font=self.digit_font)
            ss_w = draw.textlength(text[3:], font=self.digit_font)
            if field == 0:
                ux0, ux1 = x, x + mm_w
            else:
                ux0, ux1 = x + colon, x + colon + ss_w
            uy = y + th + 28
            draw.line([(ux0, uy), (ux1, uy)], fill=colour, width=4)

        if state == "done":
            draw.text(
                (WIDTH // 2, 120), "TIME'S UP", font=self.item_font,
                fill=BAD, anchor="mm",
            )

        draw.text((40, HEIGHT - 56), hint, font=self.hint_font, fill=DIM)
        self._present(image)

    def _wrap(self, draw, text, font, max_w):
        """Word-wrap `text` (honouring explicit newlines) to `max_w` pixels."""
        lines = []
        for para in text.split("\n"):
            if para == "":
                lines.append("")
                continue
            cur = ""
            for word in para.split(" "):
                trial = word if not cur else cur + " " + word
                if draw.textlength(trial, font=font) <= max_w:
                    cur = trial
                    continue
                if cur:
                    lines.append(cur)
                    cur = ""
                # A single word longer than the line: hard-break it.
                while draw.textlength(word, font=font) > max_w:
                    i = len(word)
                    while i > 1 and draw.textlength(word[:i], font=font) > max_w:
                        i -= 1
                    lines.append(word[:i])
                    word = word[i:]
                cur = word
            lines.append(cur)
        return lines

    # Keyboard geometry.
    _KB_X, _KB_Y0 = 12, 230
    _KB_STEP, _KB_KEYH, _KB_ROWGAP = 54, 38, 4

    def _key_rects(self, draw):
        """Yield (row, col, x, y, w, h, key) for every keyboard key.
        Each row is centred horizontally (rows differ in width)."""
        for r, row in enumerate(KB_ROWS):
            spans = sum((k[3] if len(k) > 3 else 1) for k in row)
            row_w = spans * self._KB_STEP - 4
            x = (WIDTH - row_w) // 2
            y = self._KB_Y0 + r * (self._KB_KEYH + self._KB_ROWGAP)
            for c, key in enumerate(row):
                span = key[3] if len(key) > 3 else 1
                w = span * self._KB_STEP - 4
                yield r, c, x, y, w, self._KB_KEYH, key
                x += span * self._KB_STEP

    _CARET = "│"   # caret marker inserted into the text at the caret

    def render_editor(self, buffer, caret, sel, shift, suggestions, sug_index,
                      status):
        image, draw = self._frame("deck / new note")

        # --- text area: caret shown in place, view scrolls to keep it visible ---
        max_w = WIDTH - 80
        display = buffer[:caret] + self._CARET + buffer[caret:]
        lines = self._wrap(draw, display, self.body_font, max_w)
        line_h = 25
        area_top = 88
        visible = 4
        caret_line = next((i for i, ln in enumerate(lines)
                           if self._CARET in ln), len(lines) - 1)
        start = max(0, caret_line - visible + 1)
        for i, line in enumerate(lines[start:start + visible]):
            draw.text((40, area_top + i * line_h), line,
                      font=self.body_font, fill=FG)

        # --- suggestion strip (word prediction) ---
        if suggestions:
            x = 44
            draw.text((x, 202), "R1", font=self.tiny_font, fill=DIM)
            x += 34
            for i, word in enumerate(suggestions):
                w = draw.textlength(word, font=self.hint_font)
                if i == sug_index:
                    draw.rounded_rectangle([x - 6, 198, x + w + 6, 224], 4,
                                           fill=C_KEYBG)
                draw.text((x, 200), word, font=self.hint_font,
                          fill=ACCENT if i == sug_index else DIM)
                x += w + 22

        # --- keyboard ---
        for r, c, x, y, w, h, key in self._key_rects(draw):
            label = key[0]
            if key[1] == "char" and shift:
                label = shift_char(key[2])
            selected = (r, c) == sel
            is_save = key[1] == "save"
            fill = ACCENT if selected else (C_SAVEBG if is_save else C_KEYBG)
            draw.rounded_rectangle([x, y, x + w, y + h], 5, fill=fill,
                                   outline=C_KEYEDGE)
            tcol = BG if selected else (ACCENT if is_save else DIM)
            if key[1] == "shift" and shift and not selected:
                tcol = ACCENT
            draw.text((x + w / 2, y + h / 2), label,
                      font=self.hint_font, fill=tcol, anchor="mm")

        bar = status or "A type   R-stick caret   L2/R2 word   START save   X help"
        draw.text((WIDTH / 2, HEIGHT - 14), bar, font=self.tiny_font,
                  fill=DIM, anchor="mm")
        self._present(image)

    def render_help(self, title, pairs):
        """A two-column key/action reference list. Row spacing and font adapt to
        the number of rows so a long list never collides with the footer."""
        image, draw = self._frame(title)
        top, footer_y = 90, HEIGHT - 26
        step = min(34, (footer_y - 14 - top) // max(1, len(pairs)))
        font = self.body_font if step >= 30 else self.hint_font
        for i, (keys, action) in enumerate(pairs):
            y = top + i * step
            draw.text((60, y), keys, font=font, fill=ACCENT)
            draw.text((240, y), action, font=font, fill=FG)
        draw.text((WIDTH / 2, footer_y), "any button to go back",
                  font=self.hint_font, fill=DIM, anchor="mm")
        self._present(image)

    def _draw_keyboard(self, draw, sel, shift):
        for r, c, x, y, w, h, key in self._key_rects(draw):
            label = key[0]
            if key[1] == "char" and shift:
                label = shift_char(key[2])
            selected = (r, c) == sel
            is_save = key[1] == "save"
            fill = ACCENT if selected else (C_SAVEBG if is_save else C_KEYBG)
            draw.rounded_rectangle([x, y, x + w, y + h], 5, fill=fill,
                                   outline=C_KEYEDGE)
            tcol = BG if selected else (ACCENT if is_save else DIM)
            if key[1] == "shift" and shift and not selected:
                tcol = ACCENT
            draw.text((x + w / 2, y + h / 2), label,
                      font=self.hint_font, fill=tcol, anchor="mm")

    def render_text_input(self, title, text, sel, shift):
        """A single-line text entry with the on-screen keyboard."""
        image, draw = self._frame(title)
        draw.rounded_rectangle([30, 92, WIDTH - 30, 134], 5, outline=C_KEYEDGE)
        draw.text((44, 100), (text + "│")[-42:], font=self.body_font, fill=FG)
        self._draw_keyboard(draw, sel, shift)
        draw.text((WIDTH / 2, HEIGHT - 14),
                  "A type   B erase   START ok   MENU cancel",
                  font=self.tiny_font, fill=DIM, anchor="mm")
        self._present(image)

    def render_weather(self, loc, data):
        image, draw = self._frame("deck / weather")
        where = loc["name"] + (", " + loc["country"] if loc.get("country") else "")
        draw.text((40, 92), where[:34], font=self.body_font, fill=FG)
        if not data:
            draw.text((WIDTH / 2, 220), "no data - check Wi-Fi",
                      font=self.item_font, fill=DIM, anchor="mm")
        else:
            draw.text((40, 148), "%d%s" % (round(data["temp"]), data["tunit"]),
                      font=self.big_font, fill=ACCENT)
            draw.text((320, 162), weather.code_desc(data["code"]),
                      font=self.item_font, fill=FG, anchor="lm")
            draw.text((320, 204),
                      "wind %d %s" % (round(data["wind"]), data["wunit"]),
                      font=self.hint_font, fill=DIM, anchor="lm")
            days = data["days"][:4]
            colw = (WIDTH - 80) // len(days)
            for i, day in enumerate(days):
                x = 40 + i * colw
                draw.text((x, 296), weather.day_label(day["date"]),
                          font=self.hint_font, fill=DIM)
                draw.text((x, 324), "%d/%d" % (round(day["hi"]), round(day["lo"])),
                          font=self.body_font, fill=FG)
                draw.text((x, 360), weather.code_desc(day["code"])[:9],
                          font=self.tiny_font, fill=DIM)
        draw.text((40, HEIGHT - 28), "A change city   Y refresh   B back",
                  font=self.hint_font, fill=DIM)
        self._present(image)

    def render_map(self, tiles, hud, hint, message=""):
        """Blit map tiles under a thin HUD. `tiles` is a list of
        (PIL image or None, x, y); a None tile draws a placeholder square."""
        image = Image.new("RGBA", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(image)
        for img, px, py in tiles:
            if img is not None:
                image.paste(img, (px, py))
            else:
                draw.rectangle([px, py, px + maps.TILE_PX, py + maps.TILE_PX],
                               fill=C_KEYBG)
        # centre crosshair (there is no GPS -- this just marks screen centre)
        cx, cy = WIDTH // 2, HEIGHT // 2
        draw.line([(cx - 9, cy), (cx + 9, cy)], fill=BAD, width=2)
        draw.line([(cx, cy - 9), (cx, cy + 9)], fill=BAD, width=2)
        # top / bottom HUD bars, so text stays readable over any imagery
        draw.rectangle([0, 0, WIDTH, 30], fill=BG)
        draw.text((12, 6), "deck / map", font=self.hint_font, fill=DIM)
        draw.text((WIDTH - 12, 6), hud, font=self.hint_font, fill=DIM,
                  anchor="ra")
        draw.rectangle([0, HEIGHT - 28, WIDTH, HEIGHT], fill=BG)
        draw.text((12, HEIGHT - 24), hint, font=self.hint_font, fill=DIM)
        if message:
            draw.text((WIDTH - 12, HEIGHT - 24), message, font=self.hint_font,
                      fill=WARN, anchor="ra")
        self._present(image)

    def render_submenu(self, title, items, sel):
        """A vertical selectable menu (used by Tools)."""
        image, draw = self._frame(title)
        for i, label in enumerate(items):
            draw.text((50, 120 + i * 50), ("> " if i == sel else "  ") + label,
                      font=self.item_font, fill=ACCENT if i == sel else DIM)
        draw.text((40, HEIGHT - 56), "A open    B back",
                  font=self.hint_font, fill=DIM)
        self._present(image)

    LIST_VISIBLE = 8

    def render_list(self, title, lines, cursor, offset, hint, note="", empty=""):
        """A scrollable list with a selection cursor and an optional note."""
        image, draw = self._frame(title)
        if note:
            draw.text((WIDTH - 40, 58), note, font=self.hint_font,
                      fill=WARN, anchor="rm")
        if not lines:
            draw.text((WIDTH / 2, 210), empty or "nothing found",
                      font=self.body_font, fill=DIM, anchor="mm")
        else:
            for i, ln in enumerate(lines[offset:offset + self.LIST_VISIBLE]):
                idx = offset + i
                y = 90 + i * 34
                if idx == cursor:
                    draw.rounded_rectangle([34, y - 2, WIDTH - 34, y + 28], 4,
                                           fill=C_KEYBG)
                draw.text((44, y), ln, font=self.body_font,
                          fill=ACCENT if idx == cursor else FG)
            if len(lines) > self.LIST_VISIBLE:
                draw.text((WIDTH - 40, HEIGHT - 28),
                          "%d/%d" % (cursor + 1, len(lines)),
                          font=self.hint_font, fill=DIM, anchor="rm")
        draw.text((40, HEIGHT - 28), hint, font=self.hint_font, fill=DIM)
        self._present(image)

    def render_hunt(self, label, dbm, prev, scanning=False):
        """Big live signal readout for physically locating one target."""
        image, draw = self._frame("deck / hunt")
        draw.text((40, 96), label[:30], font=self.body_font, fill=FG)
        if scanning:
            draw.text((WIDTH - 40, 96), "scanning...", font=self.hint_font,
                      fill=DIM, anchor="rm")
        if dbm is None:
            draw.text((WIDTH / 2, 230),
                      "scanning..." if scanning else "not seen",
                      font=self.item_font, fill=DIM, anchor="mm")
        else:
            draw.text((WIDTH / 2, 180), "%d dBm" % int(dbm), font=self.big_font,
                      fill=ACCENT, anchor="mm")
            frac = max(0.0, min(1.0, (dbm + 90) / 60.0))
            x0, x1, y = 60, WIDTH - 60, 260
            draw.rounded_rectangle([x0, y, x1, y + 34], 6, outline=C_KEYEDGE)
            draw.rounded_rectangle([x0, y, x0 + int((x1 - x0) * frac), y + 34],
                                   6, fill=ACCENT)
            trend = ""
            if prev is not None:
                if dbm > prev + 1:
                    trend, tcol = "warmer  ^", GOOD
                elif dbm < prev - 1:
                    trend, tcol = "colder  v", BAD
                else:
                    trend, tcol = "steady", DIM
                draw.text((WIDTH / 2, 320), trend, font=self.item_font,
                          fill=tcol, anchor="mm")
        draw.text((40, HEIGHT - 28), "B back", font=self.hint_font, fill=DIM)
        self._present(image)

    def _present(self, image):
        data = image.tobytes()
        surface = sdl2.SDL_CreateRGBSurfaceWithFormatFrom(
            data, WIDTH, HEIGHT, 32, WIDTH * 4, sdl2.SDL_PIXELFORMAT_RGBA32
        )
        texture = sdl2.SDL_CreateTextureFromSurface(self.renderer, surface)
        sdl2.SDL_FreeSurface(surface)

        sdl2.SDL_RenderClear(self.renderer)
        sdl2.SDL_RenderCopy(self.renderer, texture, None, None)
        sdl2.SDL_RenderPresent(self.renderer)
        sdl2.SDL_DestroyTexture(texture)

    def close(self):
        sdl2.SDL_DestroyRenderer(self.renderer)
        sdl2.SDL_DestroyWindow(self.window)
        sdl2.SDL_Quit()


def read_button(device):
    """Block until a press, returning (name, value). Buttons, d-pad, and analog
    sticks all come through here (see decode_event)."""
    while True:
        data = device.read(EVENT_SIZE)
        if not data or len(data) < EVENT_SIZE:
            continue
        _, _, _, code, value = struct.unpack(EVENT_FORMAT, data)
        got = decode_event(code, value)
        if got:
            return got


def poll_button(device, timeout):
    """Like read_button but non-blocking: returns (name, value) or None after
    `timeout` seconds with no press."""
    ready, _, _ = select.select([device], [], [], timeout)
    if not ready:
        return None
    data = device.read(EVENT_SIZE)
    if not data or len(data) < EVENT_SIZE:
        return None
    _, _, _, code, value = struct.unpack(EVENT_FORMAT, data)
    return decode_event(code, value)


def clock_looks_wrong(now):
    """The device has no dependable RTC, so sanity-check rather than trust."""
    return now.year < 2025


def ssh_screen(screen, device):
    """Native SSH screen: toggle the daemon, show how to reach it, fix the clock."""
    note = ""
    while True:
        running = net.sshd_running()
        ip = net.device_ip()
        now = datetime.datetime.now()
        stale = clock_looks_wrong(now)
        has_password = net.root_password_set()

        rows = [
            ("status", "running" if running else "stopped", GOOD if running else DIM),
            ("address", ip or "no network", FG if ip else BAD),
            ("clock", now.strftime("%Y-%m-%d %H:%M") + " UTC", BAD if stale else FG),
        ]
        if running and ip:
            rows.append(("connect", "ssh root@" + ip, ACCENT))
        if has_password is False:
            rows.append(("root pw", "not set - logins refused", BAD))

        if not note and stale:
            note = "Clock looks wrong. Press Y to sync it."

        screen.render_rows(
            "deck / ssh",
            rows,
            "A %s    Y sync clock    B back" % ("stop" if running else "start"),
            note,
        )

        name, _ = read_button(device)
        note = ""

        if name == "A":
            screen.render_busy(
                "deck / ssh", "stopping..." if running else "starting..."
            )
            ok, message = net.stop_sshd() if running else net.start_sshd()
            note = message if ok else "failed: " + message[:48]
        elif name == "Y":
            screen.render_busy("deck / ssh", "syncing clock...")
            ok, message = net.sync_clock()
            note = message if ok else "sync failed: " + message[:40]
        elif name in ("B", "MENU"):
            return


# --- editor text helpers (pure, unit-testable) ---

def kb_move(row, col, name, value):
    """Move the keyboard selection. DY wraps rows, DX wraps within a row."""
    if name == "DY":
        row = (row + value) % len(KB_ROWS)
        col = min(col, len(KB_ROWS[row]) - 1)
    elif name == "DX":
        col = (col + value) % len(KB_ROWS[row])
    return row, col


def at_sentence_start(buffer):
    """True if the next letter should auto-capitalise: start of note, after a
    newline, or after sentence-ending punctuation FOLLOWED BY a space. The space
    requirement avoids capitalising inside abbreviations like 'e.g'."""
    if buffer == "" or buffer.endswith("\n"):
        return True
    stripped = buffer.rstrip(" ")
    if stripped == "":
        return True
    if stripped == buffer:          # no trailing space -> mid-token
        return False
    return stripped[-1] in ".!?"


def insert_at(buffer, caret, s):
    """Insert `s` at the caret, returning (new_buffer, new_caret)."""
    return buffer[:caret] + s + buffer[caret:], caret + len(s)


def type_char(buffer, caret, value, shift):
    """Insert a typed character at the caret, applying shift or automatic
    sentence-start capitalisation (context is the text before the caret)."""
    if shift:
        ch = shift_char(value)
    elif value.isalpha() and at_sentence_start(buffer[:caret]):
        ch = value.upper()
    else:
        ch = value
    return insert_at(buffer, caret, ch)


def word_before(buffer, caret):
    """The run of letters ending at the caret, and its start index."""
    i = caret
    while i > 0 and buffer[i - 1].isalpha():
        i -= 1
    return buffer[i:caret], i


def caret_line_move(buffer, caret, direction):
    """Move the caret up (-1) or down (+1) one line, keeping the column.
    Lines are the newline-separated lines the user made."""
    line_start = buffer.rfind("\n", 0, caret) + 1
    col = caret - line_start
    if direction < 0:
        if line_start == 0:
            return caret
        prev_start = buffer.rfind("\n", 0, line_start - 1) + 1
        return prev_start + min(col, line_start - 1 - prev_start)
    nxt = buffer.find("\n", caret)
    if nxt < 0:
        return caret
    nxt += 1
    nxt_end = buffer.find("\n", nxt)
    if nxt_end < 0:
        nxt_end = len(buffer)
    return nxt + min(col, nxt_end - nxt)


EDITOR_HELP = [
    ("d-pad / L-stick", "move around the keys"),
    ("A", "type the selected key"),
    ("R-stick", "move the caret (letters & lines)"),
    ("L2 / R2", "scroll suggested words"),
    ("R1", "accept suggested word"),
    ("B", "erase (backspace)"),
    ("Y", "space"),
    ("L1", "shift (capitals & symbols)"),
    ("SELECT", "cycle theme"),
    ("START", "save & sync"),
    ("MENU", "cancel"),
    ("X", "this help"),
]


def help_overlay(screen, device):
    """Show the editor's controls; return on any button."""
    screen.render_help("deck / notes — controls", EDITOR_HELP)
    while True:
        got = read_button(device)
        if got and got[1] != 0:
            return


def notes_screen(screen, device):
    """Native on-screen-keyboard editor. No terminal: types into a buffer,
    then saves + git-pushes via notes.py. One move per d-pad press (no
    auto-repeat -- that can run away if a worn d-pad drops its release event).
    Auto-caps handles sentence starts; SELECT flips light/dark; X shows help."""
    buffer = ""
    caret = 0
    row, col = 1, 0          # start on 'q'
    shift = False

    sug_index = 0

    def accept(word, sugs):
        nonlocal buffer, caret
        if not sugs:
            return
        w = sugs[min(sug_index, len(sugs) - 1)]
        if word and word[0].isupper():
            w = w[0].upper() + w[1:]
        start = caret - len(word)
        buffer = buffer[:start] + w + " " + buffer[caret:]
        caret = start + len(w) + 1

    def do_button(name):
        """Handle a typing / action press. Returns 'save', 'quit', or None."""
        nonlocal buffer, caret, shift
        if name == "A":
            _label, kind, value = KB_ROWS[row][col][:3]
            if kind == "char":
                buffer, caret = type_char(buffer, caret, value, shift)
            elif kind == "space":
                buffer, caret = insert_at(buffer, caret, " ")
            elif kind == "back":
                if caret > 0:
                    buffer, caret = buffer[:caret - 1] + buffer[caret:], caret - 1
            elif kind == "enter":
                buffer, caret = insert_at(buffer, caret, "\n")
            elif kind == "shift":
                shift = not shift
            elif kind == "save":
                return "save"
        elif name == "B":
            if caret > 0:
                buffer, caret = buffer[:caret - 1] + buffer[caret:], caret - 1
        elif name == "Y":
            buffer, caret = insert_at(buffer, caret, " ")
        elif name == "L1":
            shift = not shift
        elif name == "X":
            help_overlay(screen, device)
        elif name == "SELECT":
            order = THEME_ORDER
            new = order[(order.index(THEME_NAME) + 1) % len(order)]
            apply_theme(new)
            config.save_theme(new)
        elif name == "START":
            return "save"
        elif name == "MENU":
            if buffer.strip() == "" or confirm_discard(screen, device):
                return "quit"
        return None

    prev_word = None
    while True:
        word, _ = word_before(buffer, caret)
        sugs = predict.suggest(word) if word else []
        if word != prev_word:
            sug_index = 0
            prev_word = word
        if sugs:
            sug_index = max(0, min(sug_index, len(sugs) - 1))
        screen.render_editor(buffer, caret, (row, col), shift, sugs,
                             sug_index, "")
        got = read_button(device)
        if got is None:
            continue
        name, value = got

        if name in ("DX", "DY"):          # d-pad or left stick: navigate keys
            row, col = kb_move(row, col, name, value)
        elif name == "RSX":               # right stick L/R: caret by character
            caret = max(0, min(len(buffer), caret + value))
        elif name == "RSY":               # right stick U/D: caret between lines
            caret = caret_line_move(buffer, caret, value)
        elif name in ("L2", "R2"):        # shoulders: scroll suggestions
            if sugs:
                step = 1 if name == "R2" else -1
                sug_index = (sug_index + step) % len(sugs)
        elif name == "R1":                # accept highlighted suggestion
            accept(word, sugs)
        else:
            result = do_button(name)
            if result == "save":
                break
            if result == "quit":
                return

    # --- save + sync, shown natively ---
    if buffer.strip() == "":
        return
    screen.render_editor(buffer, caret, (row, col), shift, [], 0, "saving...")
    stamp = notes.add_note(buffer)
    if stamp is None:
        return
    screen.render_busy("deck / notes", "syncing...")
    ok, message = notes.sync(stamp)
    screen.render_busy("deck / notes", message)
    # Hold the result briefly so it can be read.
    poll_button(device, 1.5)


def confirm_discard(screen, device):
    """True if the user confirms discarding the note."""
    screen.render_rows(
        "deck / notes",
        [("discard", "this note?", BAD)],
        "A discard    B keep editing",
    )
    while True:
        got = read_button(device)
        if got is None:
            continue
        name, _ = got
        if name == "A":
            return True
        if name in ("B", "MENU"):
            return False


def timer_screen(screen, device):
    """Native countdown. d-pad sets MM:SS, A starts, B backs out.
    Redraws every second while running, both to tick and to keep the
    framebuffer active against blanking."""
    minutes, seconds, field = 5, 0, 0

    # --- setup: choose the duration ---
    while True:
        screen.render_timer(
            minutes, seconds, field, "set",
            "d-pad set    A start    B back",
        )
        press = read_button(device)
        if press is None:
            continue
        name, value = press
        if name == "DX":
            field = (field + value) % 2
        elif name == "DY":
            if field == 0:
                minutes = (minutes - value) % 100     # up increases
            else:
                seconds = (seconds - value) % 60
        elif name == "A":
            total = minutes * 60 + seconds
            if total > 0:
                break
        elif name in ("B", "MENU"):
            return

    # --- run: count down from a monotonic deadline (robust to drift/pauses) ---
    remaining = float(total)
    running = True
    deadline = time.monotonic() + remaining

    while True:
        if running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
        left = int(remaining + 0.999)  # ceil, so it hits 00:00 exactly at zero
        screen.render_timer(
            left // 60, left % 60, 0,
            "run" if running else "pause",
            ("A pause" if running else "A resume") + "    B stop",
        )
        press = poll_button(device, 0.2 if running else None)
        if press is None:
            continue
        name, _ = press
        if name == "A":
            running = not running
            if running:
                deadline = time.monotonic() + remaining
        elif name in ("B", "MENU"):
            return

    # --- done: alarm until acknowledged (capped so it can't blare forever) ---
    alarm(screen, device)


def alarm(screen, device):
    proc = sound.play_once()
    net.vibrate(250)
    started = time.monotonic()
    flash = True
    while True:
        screen.render_timer(
            0, 0, 0, "done" if flash else "set",
            "any button to dismiss",
        )
        flash = not flash

        # Re-trigger tone + buzz until dismissed, but stop after ~30 s unattended.
        if (proc is None or proc.poll() is not None) and \
                time.monotonic() - started < 30:
            proc = sound.play_once()
            net.vibrate(250)

        if poll_button(device, 0.5) is not None:
            break

    if proc is not None and proc.poll() is None:
        proc.terminate()


# --- Tools: passive local-environment scanners --------------------------------
#
# Beyond a phone's built-in scan: every device is vendor-identified from its MAC
# (tools.vendor), "new/gone since last scan" is tracked (tools.diff_seen), Wi-Fi
# has a channel-congestion view and a signal-hunt meter (buzz to physically
# locate an AP). All still passive / read-only, for your own environment.

def _change_note(new_count, gone_count):
    bits = []
    if new_count:
        bits.append("%d new" % new_count)
    if gone_count:
        bits.append("%d gone" % gone_count)
    return "   ".join(bits)


def _collect_wifi():
    nets = tools.wifi_scan()
    new, gone = tools.diff_seen("wifi", [n["bssid"] for n in nets])
    rows = []
    for n in nets:
        bars = tools.signal_bars(n["signal"])
        meter = "#" * bars + " " * (4 - bars)
        tag = " NEW" if n["bssid"] in new else ""
        rows.append("%s c%-3s %-15s %-11s%s" % (
            meter, tools.chan_from_freq(n["freq"]),
            n["ssid"][:15], n["vendor"][:11], tag))
    return nets, rows, _change_note(len(new), gone)


def _collect_lan():
    hosts = tools.lan_scan()
    new, gone = tools.diff_seen("lan", [h["ip"] for h in hosts])
    rows = []
    for h in hosts:
        ports = ",".join(str(p) for p in h["ports"]) or "-"
        who = (h["name"] or h["vendor"] or "?")[:14]
        tag = " NEW" if h["ip"] in new else ""
        rows.append("%-15s %-14s %s%s" % (h["ip"], who, ports, tag))
    return hosts, rows, _change_note(len(new), gone)


def _collect_bt():
    devs = tools.bt_scan(10)
    new, gone = tools.diff_seen("bt", [d["addr"] for d in devs])
    rows = []
    for d in devs:
        tag = " NEW" if d["addr"] in new else ""
        who = d["name"][:20]
        vend = (" " + d["vendor"]) if d["vendor"] and d["vendor"] not in who else ""
        rows.append("%s %s%s%s" % (d["addr"][:17], who, vend, tag))
    return devs, rows, _change_note(len(new), gone)


def _collect_channels():
    nets = tools.wifi_scan()
    counts, best24, best5 = tools.channel_stats(nets)
    rows = []
    for ch in sorted(counts):
        band = "2.4" if ch <= 14 else "5G"
        rows.append("ch%-3s %-3s %-20s %d" % (
            ch, band, "#" * min(counts[ch], 20), counts[ch]))
    note = "best 2.4: ch%s" % best24
    if best5:
        note += "   5G: ch%s" % best5
    return [], rows, note


def hunt_screen(screen, device, label, bssid):
    """Live signal meter for one AP, buzzing stronger as it gets stronger --
    walk around to locate it. Wi-Fi only (BT RSSI isn't reliable here).

    The scan (~4s) runs in a thread so the last reading stays on screen the
    whole time and B exits instantly, instead of flashing a busy screen."""
    prev = dbm = None
    while True:
        result = {}
        worker = threading.Thread(
            target=lambda: result.__setitem__("v", tools.ap_signal(bssid)),
            daemon=True)
        worker.start()
        while worker.is_alive():
            screen.render_hunt(label, dbm, prev, scanning=True)
            got = poll_button(device, 0.2)
            if got and got[0] in ("B", "MENU"):
                return
        new = result.get("v")
        if new is not None:
            prev, dbm = dbm, new
            net.vibrate(60 + tools.signal_bars(dbm) * 55)
        screen.render_hunt(label, dbm, prev, scanning=False)
        got = poll_button(device, 0.7)
        if got and got[0] in ("B", "MENU"):
            return


def scanner_screen(screen, device, title, collect, hint, empty, action=None):
    """Scan, then show a scrollable, cursor-selectable list. Y rescans, B backs
    out, A runs `action(item)` on the selected row if provided."""
    screen.render_busy(title, "scanning...")
    items, rows, note = collect()
    cursor = offset = 0
    while True:
        if cursor < offset:
            offset = cursor
        elif cursor >= offset + screen.LIST_VISIBLE:
            offset = cursor - screen.LIST_VISIBLE + 1
        screen.render_list(title, rows, cursor, offset, hint, note, empty)
        got = read_button(device)
        if got is None:
            continue
        name, value = got
        if name == "DY" and rows:
            cursor = max(0, min(cursor + value, len(rows) - 1))
        elif name == "A" and action and items and cursor < len(items):
            action(items[cursor])
        elif name == "Y":
            screen.render_busy(title, "scanning...")
            items, rows, note = collect()
            cursor = offset = 0
        elif name in ("B", "MENU"):
            return


def tools_screen(screen, device):
    """Submenu of passive scanners for your own network/surroundings."""
    def wifi():
        scanner_screen(
            screen, device, "deck / wi-fi", _collect_wifi,
            "A hunt   Y rescan   B back", "no networks found",
            action=lambda n: hunt_screen(screen, device, n["ssid"], n["bssid"]))

    def channels():
        scanner_screen(screen, device, "deck / channels", _collect_channels,
                       "Y rescan   B back", "no networks found")

    def lan():
        scanner_screen(screen, device, "deck / network", _collect_lan,
                       "Y rescan   B back", "no hosts found")

    def bt():
        scanner_screen(screen, device, "deck / bluetooth", _collect_bt,
                       "Y rescan   B back", "no devices found")

    entries = [("Wi-Fi", wifi), ("Channels", channels),
               ("Network", lan), ("Bluetooth", bt)]
    for mod in PLUGINS:           # optional plug-ins add their own entries
        label, run = getattr(mod, "LABEL", None), getattr(mod, "run", None)
        if label and run:
            entries.append(
                (label, lambda m=mod: m.run(screen, device,
                                            scanner_screen, hunt_screen)))
    sel = 0
    while True:
        screen.render_submenu("deck / tools", [e[0] for e in entries], sel)
        got = read_button(device)
        if got is None:
            continue
        name, value = got
        if name == "DY":
            sel = (sel + value) % len(entries)
        elif name == "A":
            entries[sel][1]()
        elif name in ("B", "MENU"):
            return


# --- Weather -----------------------------------------------------------------

def text_input(screen, device, title, initial=""):
    """Single-line text entry via the on-screen keyboard. Returns the string,
    or None if cancelled with MENU."""
    text = initial
    row, col = 1, 0
    shift = False
    while True:
        screen.render_text_input(title, text, (row, col), shift)
        got = read_button(device)
        if got is None:
            continue
        name, value = got
        if name in ("DX", "DY"):
            row, col = kb_move(row, col, name, value)
        elif name == "A":
            _label, kind, val = KB_ROWS[row][col][:3]
            if kind == "char":
                text += shift_char(val) if shift else val
            elif kind == "space":
                text += " "
            elif kind == "back":
                text = text[:-1]
            elif kind == "shift":
                shift = not shift
            elif kind == "save":
                return text.strip()
        elif name == "B":
            text = text[:-1]
        elif name == "Y":
            text += " "
        elif name == "L1":
            shift = not shift
        elif name == "START":
            return text.strip()
        elif name == "MENU":
            return None


def choose_city(screen, device):
    """Prompt for a city, geocode it, and save. Returns the location or None."""
    name = text_input(screen, device, "deck / weather - city")
    if not name:
        return None
    screen.render_busy("deck / weather", "finding %s..." % name[:20])
    loc = weather.geocode(name)
    if not loc:
        screen.render_busy("deck / weather", "not found: %s" % name[:18])
        poll_button(device, 1.6)
        return None
    weather.save_location(loc)
    return loc


def weather_screen(screen, device):
    """Current conditions + forecast for a saved city (Open-Meteo, keyless)."""
    loc = weather.load_location()
    if not loc:
        loc = choose_city(screen, device)
        if not loc:
            return
    need_fetch, data = True, None
    while True:
        if need_fetch:
            screen.render_busy("deck / weather", "loading...")
            data = weather.fetch(loc)
            need_fetch = False
        screen.render_weather(loc, data)
        got = read_button(device)
        if got is None:
            continue
        name, _ = got
        if name == "A":
            new = choose_city(screen, device)
            if new:
                loc, need_fetch = new, True
        elif name == "Y":
            need_fetch = True
        elif name in ("B", "MENU"):
            return


# --- Map ---------------------------------------------------------------------

MAP_HELP = [
    ("stick / d-pad", "pan the map"),
    ("L2 / R2", "zoom out / in"),
    ("A", "find a city by name"),
    ("R1", "refresh the tiles on screen"),
    ("Y", "clear the tile cache"),
    ("X", "this help"),
    ("B", "back (remembers where you were)"),
]


def _map_search(screen, device):
    """Prompt for a city and geocode it (reuses weather's geocoder)."""
    name = text_input(screen, device, "deck / map - city")
    if not name:
        return None
    screen.render_busy("deck / map", "finding %s..." % name[:20])
    loc = weather.geocode(name)
    if not loc:
        screen.render_busy("deck / map", "not found: %s" % name[:18])
        poll_button(device, 1.6)
        return None
    return loc


def confirm_clear(screen, device):
    """True if the user confirms clearing the cached tiles."""
    screen.render_rows(
        "deck / map",
        [("clear", "cached map tiles?", WARN),
         ("size", maps.cache_summary(), DIM)],
        "A clear    B keep",
    )
    while True:
        got = read_button(device)
        if got is None:
            continue
        name, _ = got
        if name == "A":
            return True
        if name in ("B", "MENU"):
            return False


def map_screen(screen, device):
    """Online slippy-map with an on-disk, size-capped tile cache. Pan with the
    stick, zoom with the shoulders. Tiles you view are cached, so places you
    have already browsed still render with no Wi-Fi. No GPS on this device, so
    A jumps to a city by name; the last view is remembered between runs."""
    view = maps.load_view()
    if view:
        lat, lon, z = view["lat"], view["lon"], int(view["z"])
    else:
        loc = weather.load_location()          # reuse the weather city if set
        if loc:
            lat, lon, z = loc["lat"], loc["lon"], 12
        else:
            lat, lon, z = 20.0, 0.0, 2         # world view; press A to search

    tm = maps.TileManager()
    hint = "stick pan   L2/R2 zoom   A city   R1 refresh   X more   B back"
    try:
        while True:
            n = 1 << z
            cx, cy = maps.deg2num(lat, lon, z)
            origin_x = cx * maps.TILE_PX - WIDTH / 2
            origin_y = cy * maps.TILE_PX - HEIGHT / 2
            tx0 = math.floor(origin_x / maps.TILE_PX)
            tx1 = math.floor((origin_x + WIDTH) / maps.TILE_PX)
            ty0 = math.floor(origin_y / maps.TILE_PX)
            ty1 = math.floor((origin_y + HEIGHT) / maps.TILE_PX)
            tiles, vis_keys, missing = [], [], False
            for tx in range(tx0, tx1 + 1):
                for ty in range(ty0, ty1 + 1):
                    px = int(round(tx * maps.TILE_PX - origin_x))
                    py = int(round(ty * maps.TILE_PX - origin_y))
                    on_map = 0 <= ty < n
                    img = tm.get(z, tx % n, ty) if on_map else None
                    if on_map:
                        vis_keys.append((z, tx % n, ty))
                    if img is None:
                        missing = True
                    tiles.append((img, px, py))

            tm.dirty.clear()
            screen.render_map(tiles, "z%d   %s" % (z, maps.cache_summary()),
                              hint, "" if not missing else "loading...")

            # While tiles are still arriving, wake often to fill them in;
            # once the view is complete, just sleep until the next press.
            got = poll_button(device, 0.25 if missing else 30)
            if got is None:
                continue
            name, value = got
            if name in ("DX", "DY"):
                step = maps.TILE_PX * 0.42
                if name == "DX":
                    lat, lon = maps.pan(lat, lon, z, value * step, 0)
                else:
                    lat, lon = maps.pan(lat, lon, z, 0, value * step)
            elif name == "R2":
                z = min(maps.MAX_Z, z + 1)
            elif name == "L2":
                z = max(maps.MIN_Z, z - 1)
            elif name == "R1":
                tm.refresh(vis_keys)      # re-fetch what's on screen
            elif name == "A":
                new = _map_search(screen, device)
                if new:
                    lat, lon, z = new["lat"], new["lon"], max(z, 12)
            elif name == "Y":
                if confirm_clear(screen, device):
                    tm.clear()
            elif name == "X":
                screen.render_help("deck / map — controls", MAP_HELP)
                while True:
                    g = read_button(device)
                    if g and g[1] != 0:
                        break
            elif name in ("B", "MENU"):
                maps.save_view({"lat": lat, "lon": lon, "z": z})
                return
    finally:
        tm.close()


def main():
    apply_theme(config.load_theme())
    screen = Screen()
    position = config.load_last_position(len(ITEMS))
    try:
        with open(gamepad_path(), "rb") as device:
            while True:
                screen.render_menu(position)
                name, value = read_button(device)

                if name == "DY":
                    position = (position + value) % len(ITEMS)
                elif name == "A":
                    action = ITEMS[position][1]
                    config.save_last_position(position)
                    if action in NATIVE:
                        if action == "ssh":
                            ssh_screen(screen, device)
                        elif action == "timer":
                            timer_screen(screen, device)
                        elif action == "notes":
                            notes_screen(screen, device)
                        elif action == "tools":
                            tools_screen(screen, device)
                        elif action == "weather":
                            weather_screen(screen, device)
                        elif action == "map":
                            map_screen(screen, device)
                    else:
                        # Hand over to Deck.sh, releasing SDL2 on the way out.
                        with open(SELECTION, "w") as f:
                            f.write(action)
                        return
                elif name in ("B", "MENU"):
                    config.save_last_position(position)
                    return
    finally:
        screen.close()


if __name__ == "__main__":
    main()
