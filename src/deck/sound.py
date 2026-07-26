"""Alarm tone for the timer.

No vibration motor exists on this unit, so the timer falls back to audio.
The tone is generated once with the stdlib `wave` module -- nothing is shipped
as a binary and no external sound file is needed -- then played with `aplay`,
which is present on the device.
"""

import math
import os
import struct
import subprocess
import wave

DIR = os.path.dirname(os.path.abspath(__file__))
TONE = os.path.join(DIR, "alarm.wav")

_RATE = 22050


def ensure_tone():
    """Create alarm.wav on first use. Two rising beeps, ~1 s total."""
    if os.path.exists(TONE):
        return TONE

    def beep(freq, seconds):
        frames = int(_RATE * seconds)
        out = bytearray()
        for i in range(frames):
            # Sine with a short fade in/out so it does not click.
            fade = min(1.0, i / 400.0, (frames - i) / 400.0)
            sample = int(0.6 * fade * 32767 * math.sin(2 * math.pi * freq * i / _RATE))
            out += struct.pack("<h", sample)
        return bytes(out)

    def silence(seconds):
        return b"\x00\x00" * int(_RATE * seconds)

    data = beep(784, 0.15) + silence(0.08) + beep(1047, 0.22) + silence(0.05)

    try:
        with wave.open(TONE, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(_RATE)
            w.writeframes(data)
    except OSError:
        return None
    return TONE


def play_once():
    """Start one playback, returning the process (or None). Non-blocking."""
    path = ensure_tone()
    if not path:
        return None
    for player in (["aplay", "-q", path], ["mpv", "--really-quiet", path]):
        try:
            return subprocess.Popen(
                player,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            continue
    return None
