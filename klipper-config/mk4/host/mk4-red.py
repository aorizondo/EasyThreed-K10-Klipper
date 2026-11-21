#!/usr/bin/env python3
# mk4-red — aplica /boot/firmware/mk4-red.txt a NetworkManager en cada arranque.
#
# El archivo vive en la partición FAT (bootfs): se edita desde cualquier PC
# metiendo la microSD. Formato en mk4-red.txt (plantilla en este directorio).
#
# - [mk4] pais = XX  -> cfg80211.ieee80211_regdom en cmdline.txt (reinicia 1 vez).
# - [<SSID>] clave/prioridad -> conexión NM "mk4-wifi:<SSID>". Las mk4-wifi:*
#   que ya no estén en el archivo se borran. El resto de conexiones no se toca.
import configparser
import re
import subprocess
import sys

CONF = "/boot/firmware/mk4-red.txt"
CMDLINE = "/boot/firmware/cmdline.txt"
PREFIX = "mk4-wifi:"
# brcmfmac (BCM43455) rechaza países que no conoce y se queda en canales 1-11
# (pasó con CU y el hotspot en el canal 13).
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def log(msg):
    print(msg, flush=True)


def nmcli(*args, check=True):
    r = subprocess.run(["nmcli", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("nmcli %s: %s" % (" ".join(args[:3]), r.stderr.strip()))
    return r.stdout


def set_country(country):
    """Devuelve True si cmdline.txt cambió (hace falta reiniciar)."""
    if not COUNTRY_RE.match(country):
        log("WARN: invalid country %r, ignored" % country)
        return False
    with open(CMDLINE) as f:
        line = f.read().strip()
    param = "cfg80211.ieee80211_regdom=" + country
    if re.search(r"(^| )%s( |$)" % re.escape(param), line):
        return False
    if "cfg80211.ieee80211_regdom=" in line:
        new = re.sub(r"cfg80211\.ieee80211_regdom=\S*", param, line)
    else:
        new = line + " " + param
    with open(CMDLINE, "w") as f:
        f.write(new + "\n")
    log("country -> %s (cmdline.txt updated)" % country)
    return True


def existing_wifi():
    out = nmcli("-t", "-f", "NAME,TYPE", "con", "show")
    names = []
    for row in out.splitlines():
        # -t escapa ':' del nombre como '\:'
        name, _, ctype = row.rpartition(":")
        name = name.replace("\\:", ":")
        if ctype == "802-11-wireless" and name.startswith(PREFIX):
            names.append(name)
    return names


def apply_wifi(ssid, psk, prio, present):
    name = PREFIX + ssid
    if name in present:
        cur = nmcli("-s", "-g", "802-11-wireless-security.psk,connection.autoconnect-priority",
                    "con", "show", name).splitlines()
        cur_psk = cur[0] if cur else ""
        cur_prio = cur[1] if len(cur) > 1 else ""
        if cur_psk == psk and cur_prio == str(prio):
            return
        nmcli("con", "delete", name)
        log("wifi %r: updated" % ssid)
    else:
        log("wifi %r: added" % ssid)
    args = ["con", "add", "type", "wifi", "ifname", "wlan0", "con-name", name,
            "ssid", ssid, "connection.autoconnect", "yes",
            "connection.autoconnect-priority", str(prio),
            "ipv4.method", "auto", "ipv6.method", "auto"]
    if psk:
        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk]
    nmcli(*args)


def main():
    cp = configparser.ConfigParser(interpolation=None, default_section="__none__")
    cp.optionxform = str.lower
    try:
        with open(CONF, encoding="utf-8-sig") as f:
            cp.read_file(f)
    except FileNotFoundError:
        log("no %s, nothing to do" % CONF)
        return 0
    except configparser.Error as e:
        log("ERROR: %s malformed: %s" % (CONF, e))
        return 1

    reboot = False
    if cp.has_section("mk4"):
        country = cp.get("mk4", "pais", fallback=cp.get("mk4", "país", fallback="")).strip().upper()
        if country:
            reboot = set_country(country)

    present = existing_wifi()
    wanted = set()
    for ssid in cp.sections():
        if ssid == "mk4":
            continue
        psk = cp.get(ssid, "clave", fallback="").strip()
        if psk and not 8 <= len(psk) <= 63:
            log("WARN: wifi %r: WPA key must be 8-63 chars, skipped" % ssid)
            continue
        try:
            prio = int(cp.get(ssid, "prioridad", fallback="0"))
        except ValueError:
            prio = 0
        wanted.add(PREFIX + ssid)
        try:
            apply_wifi(ssid, psk, prio, present)
        except RuntimeError as e:
            log("ERROR: wifi %r: %s" % (ssid, e))

    for name in present:
        if name not in wanted:
            nmcli("con", "delete", name, check=False)
            log("wifi %r: removed" % name[len(PREFIX):])

    if reboot:
        log("rebooting to apply Wi-Fi country")
        subprocess.run(["systemctl", "--no-block", "reboot"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
