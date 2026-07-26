"""Passive local-environment scanners for the Tools menu.

Everything here is read-only discovery of your own surroundings and network:
list nearby Wi-Fi/Bluetooth, and map devices on the LAN you're connected to.
Uses iw / ping / bluetoothctl, which ship on the device -- no installs.
"""

import concurrent.futures
import gzip
import json
import os
import re
import socket
import subprocess

_DIR = os.path.dirname(os.path.abspath(__file__))


def _run(cmd, timeout):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None


# --- vendor lookup (MAC -> manufacturer) -----------------------------------

_OUI = None


def _load_oui():
    global _OUI
    if _OUI is not None:
        return _OUI
    _OUI = {}
    try:
        with gzip.open(os.path.join(_DIR, "oui.csv.gz"), "rt",
                       encoding="utf-8") as f:
            for line in f:
                prefix, _, name = line.partition(",")
                name = name.strip()
                if name:
                    _OUI[prefix] = name
    except OSError:
        pass
    return _OUI


def vendor(mac):
    """Manufacturer for a MAC, '(random)' for a privacy/randomised address,
    or '' if unknown."""
    if not mac:
        return ""
    hx = mac.replace(":", "").replace("-", "").upper()
    if len(hx) < 6:
        return ""
    try:
        if int(hx[0:2], 16) & 0x02:   # locally-administered = randomised
            return "(random)"
    except ValueError:
        return ""
    return _load_oui().get(hx[:6], "")


# --- new / gone tracking ----------------------------------------------------

def diff_seen(tool, ids):
    """Compare the current set of identifiers against the last scan of `tool`.
    Returns (new_set, gone_count) and persists the current set."""
    state_dir = os.path.join(_DIR, "tools_state")
    fp = os.path.join(state_dir, tool + ".json")
    prev = set()
    try:
        with open(fp) as f:
            prev = set(json.load(f))
    except (OSError, ValueError):
        pass
    cur = set(ids)
    new = cur - prev
    gone = len(prev - cur)
    # Don't flag "new" on the very first scan (everything would be new).
    if not prev:
        new = set()
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(fp, "w") as f:
            json.dump(sorted(cur), f)
    except OSError:
        pass
    return new, gone


# --- Wi-Fi -----------------------------------------------------------------

def chan_from_freq(freq):
    if not freq:
        return "?"
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2412) // 5 + 1
    if 5000 <= freq <= 5900:
        return (freq - 5000) // 5
    return "?"


def signal_bars(dbm):
    if dbm is None:
        return 0
    if dbm >= -55:
        return 4
    if dbm >= -67:
        return 3
    if dbm >= -75:
        return 2
    return 1


def wifi_scan(iface="wlan0", timeout=12):
    """Nearby networks: list of {ssid, signal, freq, sec}, strongest first.
    De-duplicated by SSID (keeps the strongest BSS).

    A live scan can stall or return busy while the card is associated, so on
    any failure we fall back to the kernel's cached results (`scan dump`),
    which are instant. The user always gets something."""
    r = _run(["iw", "dev", iface, "scan"], timeout)
    if not r or r.returncode != 0:
        r = _run(["iw", "dev", iface, "scan", "dump"], 5)
    if not r or r.returncode != 0:
        return []
    nets, cur = [], None
    for line in r.stdout.splitlines():
        s = line.strip()
        if line.startswith("BSS"):
            if cur:
                nets.append(cur)
            m = re.search(r"([0-9a-fA-F:]{17})", line)
            bssid = m.group(1) if m else ""
            cur = {"ssid": "", "signal": None, "freq": None, "sec": "open",
                   "bssid": bssid, "vendor": vendor(bssid)}
        elif cur is None:
            continue
        elif s.startswith("signal:"):
            m = re.search(r"(-?\d+\.?\d*)", s)
            cur["signal"] = float(m.group(1)) if m else None
        elif s.startswith("freq:"):
            m = re.search(r"(\d+)", s)
            cur["freq"] = int(m.group(1)) if m else None
        elif s.startswith("SSID:"):
            cur["ssid"] = s[5:].strip()
        elif s.startswith("RSN:"):
            cur["sec"] = "WPA2/3"
        elif s.startswith("WPA:") and cur["sec"] == "open":
            cur["sec"] = "WPA"
    if cur:
        nets.append(cur)

    best = {}
    for n in nets:
        key = n["ssid"] or "<hidden>"
        n["ssid"] = key
        if key not in best or (n["signal"] or -999) > (best[key]["signal"] or -999):
            best[key] = n
    return sorted(best.values(), key=lambda n: n["signal"] or -999, reverse=True)


def channel_stats(nets):
    """Aggregate scanned APs per channel. Returns ({channel: count}, best_24,
    best_5) where best_* is the least-congested channel in that band."""
    counts = {}
    for n in nets:
        ch = chan_from_freq(n["freq"])
        if ch != "?":
            counts[ch] = counts.get(ch, 0) + 1
    # Recommend among the non-overlapping 2.4GHz channels (1/6/11).
    best_24 = min((1, 6, 11), key=lambda c: counts.get(c, 0))
    five = [c for c in counts if c >= 32]
    best_5 = min(five, key=lambda c: counts.get(c, 0)) if five else None
    return counts, best_24, best_5


def ap_signal(bssid, iface="wlan0"):
    """Current signal (dBm) of one AP by BSSID, from a fresh scan. For hunting.
    Returns float or None."""
    r = _run(["iw", "dev", iface, "scan"], 10)
    if not r or r.returncode != 0:
        r = _run(["iw", "dev", iface, "scan", "dump"], 5)
    if not r:
        return None
    target = bssid.lower()
    cur_match = False
    for line in r.stdout.splitlines():
        if line.startswith("BSS"):
            cur_match = target in line.lower()
        elif cur_match and line.strip().startswith("signal:"):
            m = re.search(r"(-?\d+\.?\d*)", line)
            return float(m.group(1)) if m else None
    return None


# --- LAN -------------------------------------------------------------------

def local_subnet(iface="wlan0"):
    """Return ('192.168.1', my_host_octet) for the interface, or None."""
    r = _run(["ip", "-4", "addr", "show", iface], 5)
    if not r:
        return None
    m = re.search(r"inet (\d+\.\d+\.\d+)\.(\d+)/", r.stdout)
    return (m.group(1), int(m.group(2))) if m else None


def _alive(ip):
    r = _run(["ping", "-c1", "-W1", ip], 2)
    return r is not None and r.returncode == 0


_COMMON_PORTS = (22, 80, 443, 8080, 445, 139, 5900, 3389, 21, 23, 53, 62078)


def _open_ports(ip):
    found = []
    for p in _COMMON_PORTS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.3)
            if s.connect_ex((ip, p)) == 0:
                found.append(p)
            s.close()
        except OSError:
            pass
    return found


def _neigh_map():
    """IP -> MAC from the kernel ARP/neighbour table."""
    r = _run(["ip", "neigh", "show"], 5)
    out = {}
    if r:
        for line in r.stdout.splitlines():
            parts = line.split()
            if "lladdr" in parts:
                out[parts[0]] = parts[parts.index("lladdr") + 1]
    return out


def lan_scan(iface="wlan0"):
    """Ping-sweep the local /24, then port-scan, reverse-resolve, and identify
    the vendor of each live host."""
    sub = local_subnet(iface)
    if not sub:
        return []
    net, mine = sub
    ips = ["%s.%d" % (net, i) for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as ex:
        alive = list(ex.map(_alive, ips))
    live = [ip for ip, up in zip(ips, alive) if up]

    neigh = _neigh_map()  # populated by the ping sweep above

    def probe(ip):
        try:
            name = socket.gethostbyaddr(ip)[0]
        except (OSError, socket.herror):
            name = ""
        mac = neigh.get(ip, "")
        tag = " (this device)" if ip.endswith("." + str(mine)) else ""
        return {"ip": ip, "name": name + tag, "ports": _open_ports(ip),
                "mac": mac, "vendor": vendor(mac)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
        hosts = list(ex.map(probe, live))
    hosts.sort(key=lambda h: tuple(int(x) for x in h["ip"].split(".")))
    return hosts


# --- Bluetooth -------------------------------------------------------------

def bt_scan(seconds=10):
    """Discover nearby Bluetooth/BLE devices: list of {addr, name}."""
    _run(["bluetoothctl", "power", "on"], 5)
    _run(["bluetoothctl", "--timeout", str(seconds), "scan", "on"], seconds + 5)
    r = _run(["bluetoothctl", "devices"], 5)
    devs = []
    if r:
        for line in r.stdout.splitlines():
            m = re.match(r"Device (\S+) (.+)", line.strip())
            if m:
                addr = m.group(1)
                devs.append({"addr": addr, "name": m.group(2),
                             "vendor": vendor(addr)})
    return devs
