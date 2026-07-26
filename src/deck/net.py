"""Network and clock helpers for the SSH screen.

Kept out of main.py so the drawing code stays readable. Everything here shells
out to tools that already ship with the firmware -- nothing is installed.
"""

import email.utils
import subprocess
import time
import urllib.request

_BATTERY = "/sys/class/power_supply/axp2202-battery"


def battery():
    """Return (percent:int, charging:bool), or None if unreadable."""
    try:
        with open(_BATTERY + "/capacity") as f:
            pct = int(f.read().strip())
    except (OSError, ValueError):
        return None
    charging = False
    try:
        with open(_BATTERY + "/status") as f:
            charging = f.read().strip() == "Charging"
    except OSError:
        pass
    return pct, charging


def vibrate(ms=150):
    """Pulse the vibration motor for ms milliseconds. Best-effort (needs root,
    which Deck has). Blocks for the duration."""
    moto = _BATTERY + "/moto"
    try:
        with open(moto, "w") as f:
            f.write("1")
        time.sleep(ms / 1000.0)
        with open(moto, "w") as f:
            f.write("0")
        return True
    except OSError:
        return False


def sh(command, timeout=20):
    """Run a shell command, returning (ok, combined output)."""
    try:
        done = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return done.returncode == 0, (done.stdout + done.stderr).strip()
    except Exception as error:  # timeout, missing binary, anything
        return False, str(error)


def sshd_running():
    return sh("pgrep -x sshd")[0]


def device_ip():
    ok, out = sh(
        "ip -4 addr show | "
        "awk '/inet / && $2 !~ /^127\\./ {sub(\"/.*\",\"\",$2); print $2; exit}'"
    )
    return out if ok and out else ""


def start_sshd():
    sh("mkdir -p /run/sshd /var/run/sshd")
    sh("/etc/init.d/ssh start")
    if sshd_running():
        return True, "started"
    # The init script is picky; fall back to the daemon itself.
    ok, out = sh("/usr/sbin/sshd")
    if sshd_running():
        return True, "started"
    return False, out or "failed to start"


def stop_sshd():
    sh("/etc/init.d/ssh stop")
    if not sshd_running():
        return True, "stopped"
    sh("pkill -x sshd")
    if not sshd_running():
        return True, "stopped"
    return False, "still running"


def root_password_set():
    """None if it cannot be determined (not root, no shadow file)."""
    try:
        with open("/etc/shadow") as f:
            for line in f:
                if line.startswith("root:"):
                    field = line.split(":")[1]
                    return bool(field) and not field.startswith(("!", "*"))
    except OSError:
        return None
    return None


def fetch_network_time():
    """Epoch seconds from an HTTP Date header, or None.

    The device has no reliable RTC, so the clock drifts. This needs no NTP
    client -- only python3 stdlib -- and second-level accuracy is far more
    than a note timestamp requires.
    """
    for url in ("http://cloudflare.com", "http://www.google.com", "http://example.com"):
        try:
            request = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(request, timeout=6) as response:
                header = response.headers.get("Date")
            if header:
                return int(email.utils.parsedate_to_datetime(header).timestamp())
        except Exception:
            continue
    return None


def sync_clock():
    """Set the system clock from the network. Returns (ok, message)."""
    epoch = fetch_network_time()
    if epoch is None:
        return False, "no network"
    ok, out = sh("date -s @%d" % epoch)
    if not ok:
        return False, out or "date -s failed"
    sh("hwclock -w")  # persist if this unit actually has an RTC
    return True, "clock synced"
