# coding=utf-8
"""
Suite de calibración universal de Boost Kit.

Genera patrones de calibración PARAMÉTRICOS en el backend (no arrays hardcodeados),
calculados desde las capacidades REALES de la impresora (volumen de cama y Ø de
nozzle del perfil, velocidades/aceleraciones máximas de M503, límites de cama M211,
topes de temperatura, nº de extrusores) y CONSCIENTES DEL MODO DE EXTRUSIÓN
(volumétrico M200 D>0 → E en mm³; lineal → E en mm de filamento).

Patrones simples pero efectivos (geometrías conocidas de la comunidad), recortados al
área alcanzable y validados. Cada generador: (caps, params) -> gcode (str).

Además, CalibrationController orquesta los tests dirigidos por firmware (PID via M303,
e-steps por medición) reutilizando el patrón de espera de echos de leveling.py, y
persiste presets + histórico (tracking de combinaciones probadas) en JSON.
"""
from __future__ import absolute_import

import json
import math
import os
import re
import threading
import time

from ..common.tracker import _axis


def _gword(cmd):
    m = re.match(r"\s*([GM]\d+)", str(cmd), re.I)
    return m.group(1).upper() if m else ""


# --- Echos de PID (M301/M304) en sus múltiples formatos (Marlin) ---
_PID_RE = re.compile(
    r"p:?\s*(?P<p>[\d.]+)\s+k?i:?\s*(?P<i>[\d.]+)\s+k?d:?\s*(?P<d>[\d.]+)", re.I)
# M92 (steps/mm) reportado por M503/M92
_M92_RE = re.compile(
    r"M92(?:\s+X(?P<x>[\d.]+))?(?:\s+Y(?P<y>[\d.]+))?(?:\s+Z(?P<z>[\d.]+))?"
    r"(?:\s+E(?P<e>[\d.]+))?", re.I)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ============================================================================
# Capacidades resueltas
# ============================================================================
def resolve_caps(profile, limits, fw, temp_max, leveling_caps=None):
    """Construye el dict de capacidades para los generadores.
      profile: perfil de impresora (get_current_or_default) o None
      limits:  self._limits (M201/M203/M205) o None
      fw:      self._fw (incluye M200 volumétrico, M900 K, M425, M593)
      temp_max: {"hotend":.., "bed":..}
      leveling_caps: caps de nivelación (para reach/M211) o None
    """
    bed = {"x": 220.0, "y": 220.0, "z": 250.0}
    nozzle = 0.4
    filament = 1.75
    extruders = 1
    origin_center = False
    try:
        vol = profile.get("volume", {}) if profile else {}
        bed["x"] = float(vol.get("width", bed["x"]))
        bed["y"] = float(vol.get("depth", bed["y"]))
        bed["z"] = float(vol.get("height", bed["z"]))
        origin_center = (vol.get("origin") == "center")
        ext = profile.get("extruder", {}) if profile else {}
        nozzle = float(ext.get("nozzleDiameter", nozzle) or nozzle)
        extruders = int(ext.get("count", 1) or 1)
    except (TypeError, ValueError, AttributeError):
        pass

    lim = limits or {}
    def _axis_lim(d, ax, dflt):
        try:
            return float((d or {}).get(ax))
        except (TypeError, ValueError):
            return dflt
    vmax = {
        "x": _axis_lim(lim.get("M203"), "X", 200.0),
        "y": _axis_lim(lim.get("M203"), "Y", 200.0),
        "z": _axis_lim(lim.get("M203"), "Z", 10.0),
        "e": _axis_lim(lim.get("M203"), "E", 50.0),
    }
    amax = {
        "x": _axis_lim(lim.get("M201"), "X", 1500.0),
        "y": _axis_lim(lim.get("M201"), "Y", 1500.0),
    }
    # Modo de extrusión (M200 D): D>0 -> volumétrico.
    m200 = (fw or {}).get("M200", {})
    d = 0.0
    try:
        d = float(m200.get("D", 0))
    except (TypeError, ValueError):
        d = 0.0
    volumetric = d > 0.0
    if volumetric and d > 0:
        filament = d

    return {
        "bed": bed,
        "origin_center": origin_center,
        "nozzle": nozzle,
        "filament": filament,
        "extruders": extruders,
        "vmax": vmax,
        "amax": amax,
        "temp_max": {
            "hotend": float((temp_max or {}).get("hotend", 280)),
            "bed": float((temp_max or {}).get("bed", 120)),
        },
        "volumetric": volumetric,
        "la_k": (fw or {}).get("M900", {}).get("K"),
        "backlash": (fw or {}).get("M425", {}),
        "shaping": (fw or {}).get("M593", {}),
    }


# ============================================================================
# Constructor de gcode (consciente del modo de extrusión)
# ============================================================================
class GcodeBuilder(object):
    """Genera gcode de patrones centrado en la cama, con E correcta según el modo
    de extrusión (lineal mm vs volumétrico mm³) y recorte al área alcanzable."""

    def __init__(self, caps, layer_height=0.2, line_width=None,
                 print_speed=40, travel_speed=120, hotend=200, bed=60,
                 fan=255, first_layer_speed=20):
        self.caps = caps
        self.lh = float(layer_height)
        self.lw = float(line_width or caps["nozzle"] * 1.1)
        self.fil_area = math.pi * (caps["filament"] / 2.0) ** 2
        self.volumetric = caps["volumetric"]
        self.ps = float(print_speed) * 60.0       # mm/min
        self.ts = float(travel_speed) * 60.0
        self.fls = float(first_layer_speed) * 60.0
        self.hotend = _clamp(float(hotend), 0, caps["temp_max"]["hotend"])
        self.bed = _clamp(float(bed), 0, caps["temp_max"]["bed"])
        self.fan = int(_clamp(fan, 0, 255))
        bx, by = caps["bed"]["x"], caps["bed"]["y"]
        self.cx, self.cy = bx / 2.0, by / 2.0
        self.bedx, self.bedy = bx, by
        self.x = self.cx
        self.y = self.cy
        self.z = 0.0
        self.lines = []
        self.layer = 0

    # -- E para un movimiento de extrusión de longitud `dist` (mm de XY/XYZ) --
    def _e_for(self, dist, lh=None):
        lh = self.lh if lh is None else lh
        volume = dist * lh * self.lw            # mm³ depositados
        if self.volumetric:
            return volume                       # E en mm³
        return volume / self.fil_area           # E en mm lineales de filamento

    def cmd(self, s):
        self.lines.append(s)

    def _emit(self, start):
        """Devuelve (y deja en self.lines) lo emitido desde el índice `start`. Permite
        usar el builder tanto en modo Python (`.result()`) como en plantillas Jinja
        (`{{ g.rect(20,20) }}` imprime lo devuelto)."""
        return "\n".join(self.lines[start:])

    def fit(self, x, y):
        """Recorta un punto al área de cama (con 2 mm de margen)."""
        return (_clamp(x, 2.0, self.bedx - 2.0), _clamp(y, 2.0, self.bedy - 2.0))

    def mcode(self, s):
        """Emite una línea cruda (M-code, comentario, lo que sea) y la devuelve."""
        self.cmd(str(s))
        return str(s)

    def comment(self, s):
        return self.mcode("; " + str(s))

    def band(self, label):
        """Comentario + M117 para etiquetar una banda; lo devuelve."""
        start = len(self.lines)
        self.cmd("; --- " + str(label) + " ---")
        self.cmd("M117 " + str(label))
        return self._emit(start)

    def travel(self, x, y, z=None):
        start = len(self.lines)
        x, y = self.fit(x, y)
        parts = "G0 X{:.3f} Y{:.3f}".format(x, y)
        if z is not None:
            parts += " Z{:.3f}".format(z)
            self.z = z
        parts += " F{:.0f}".format(self.ts)
        self.cmd(parts)
        self.x, self.y = x, y
        return self._emit(start)

    def line_to(self, x, y, speed=None):
        start = len(self.lines)
        x, y = self.fit(x, y)
        dist = math.hypot(x - self.x, y - self.y)
        if dist < 1e-4:
            return ""
        e = self._e_for(dist)
        sp = speed if speed is not None else (self.fls if self.layer == 0 else self.ps)
        self.cmd("G1 X{:.3f} Y{:.3f} E{:.5f} F{:.0f}".format(x, y, e, sp))
        self.x, self.y = x, y
        return self._emit(start)

    def extrude(self, amount, speed=120):
        """Extruye/retrae `amount` mm de filamento (E relativa). Lo devuelve."""
        e = self._e_for(0)  # placeholder; extrusión pura usa el valor crudo
        return self.mcode("G1 E{:.4f} F{:.0f}".format(float(amount), float(speed) * 60))

    def rect(self, w, h, speed=None):
        """Dibuja un rectángulo centrado (perímetro) de w×h en la capa actual."""
        start = len(self.lines)
        x0, y0 = self.cx - w / 2.0, self.cy - h / 2.0
        x1, y1 = self.cx + w / 2.0, self.cy + h / 2.0
        self.travel(x0, y0)
        self.line_to(x1, y0, speed)
        self.line_to(x1, y1, speed)
        self.line_to(x0, y1, speed)
        self.line_to(x0, y0, speed)
        return self._emit(start)

    def next_layer(self):
        start = len(self.lines)
        self.layer += 1
        self.z = round(self.layer * self.lh, 5)
        self.cmd("G1 Z{:.3f} F{:.0f}".format(self.z, self.caps["vmax"]["z"] * 60 or 600))
        return self._emit(start)

    def new_object(self, cx, cy):
        """Empieza un objeto NUEVO lado a lado: lift seguro, viaja al centro y reinicia el
        contador de capa a 0 (la próxima next_layer baja a la primera capa). Clave para que
        cada objeto separado arranque DESDE LA CAMA y no quede impreso en el aire."""
        start = len(self.lines)
        safe = round(max(self.z, self.lh) + 1.0, 5)
        zf = self.caps["vmax"]["z"] * 60 or 600
        self.cmd("G1 Z{:.3f} F{:.0f}".format(safe, zf))   # lift por encima de todo
        self.z = safe
        self.cx, self.cy = cx, cy
        self.travel(cx, cy)                                # viaja en alto (G0)
        self.layer = 0
        self.z = 0.0
        return self._emit(start)

    def preamble(self, home=True):
        start = len(self.lines)
        self.cmd("; Boost Kit calibration pattern")
        self.cmd("G21")
        self.cmd("G90")
        if self.bed > 0:
            self.cmd("M140 S{:.0f}".format(self.bed))
        self.cmd("M104 S{:.0f}".format(self.hotend))
        if home:
            self.cmd("G28")
        if self.bed > 0:
            self.cmd("M190 S{:.0f}".format(self.bed))
        self.cmd("M109 S{:.0f}".format(self.hotend))
        self.cmd("M83")                          # E relativa para el patrón
        self.cmd("M106 S{:d}".format(self.fan))
        # purga: línea de cebado en el borde
        self.z = self.lh
        self.cmd("G1 Z{:.3f} F600".format(self.z))
        self.travel(5, 5)
        self.cmd("G1 X{:.3f} Y5 E{:.4f} F1000".format(
            min(self.bedx - 5, 60), self._e_for(min(self.bedx - 5, 60) - 5)))
        self.layer = 1
        return self._emit(start)

    def postamble(self):
        start = len(self.lines)
        self.cmd("M104 S0")
        self.cmd("M140 S0")
        self.cmd("M107")
        self.cmd("G1 E-3 F2400")
        safe_z = min(self.z + 10, self.caps["bed"]["z"])
        self.cmd("G1 Z{:.3f} F600".format(safe_z))
        self.cmd("G28 X Y")
        self.cmd("M84")
        return self._emit(start)

    def result(self):
        return "\n".join(self.lines) + "\n"


# ============================================================================
# Generador genérico de TORRE (perímetros cuadrados apilados por bandas)
# ============================================================================
def _square_tower(caps, p, band_cb, size=20.0, bands=None):
    """Torre de perímetros cuadrados. `band_cb(builder, i, value)` se invoca al
    inicio de cada banda para inyectar M-codes (M104/M900/M204/M221...) y devolver
    una etiqueta. `bands` = lista de valores (uno por banda)."""
    b = GcodeBuilder(
        caps,
        layer_height=p.get("layer_height", 0.2),
        print_speed=p.get("speed", 40),
        hotend=p.get("hotend", 205),
        bed=p.get("bed", 60),
        fan=p.get("fan", 255),
    )
    band_h = float(p.get("band_height", 8.0))
    size = min(size, caps["bed"]["x"] - 10, caps["bed"]["y"] - 10)
    b.preamble()
    layers_per_band = max(1, int(round(band_h / b.lh)))
    for i, val in enumerate(bands or []):
        lbl = band_cb(b, i, val)
        if lbl:
            b.cmd("; --- banda {}: {} ---".format(i + 1, lbl))
            b.cmd("M117 {}".format(lbl))
        for _ in range(layers_per_band):
            b.rect(size, size)
            b.next_layer()
    b.postamble()
    return b.result()


def _frange(a, b, step):
    out, v = [], a
    n = 0
    while (step > 0 and v <= b + 1e-9) or (step < 0 and v >= b - 1e-9):
        out.append(round(v, 4))
        n += 1
        v = a + n * step
        if n > 200:
            break
    return out


def _axis_values(start, end, step):
    """Lista de valores de un eje variado (elige el signo del paso según start/end)."""
    start, end = float(start), float(end)
    step = abs(float(step)) or 1.0
    if end < start:
        step = -step
    vals = _frange(start, end, step)
    return vals or [start]


# ---- Generadores concretos --------------------------------------------------


def gen_linear_advance(caps, p):
    """Patrón de LÍNEAS de Linear/Pressure Advance (método Marlin K-factor / Ellis): en
    pocas capas, una línea por valor K, cada una con tramos lento–RÁPIDO–lento. Con K bien
    ajustada el ancho es uniforme y las transiciones no tienen bulto (K baja) ni hueco
    (K alta). Se aplica con M900 K<valor> (Klipper: pressure_advance)."""
    k0 = float(p.get("k_start", 0.0))
    k1 = float(p.get("k_end", 1.0))
    step = abs(float(p.get("k_step", 0.1))) or 0.1
    ks = _frange(k0, k1, step)
    slow = float(p.get("slow_speed", 20)) * 60.0
    fast = float(p.get("fast_speed", 100)) * 60.0
    b = GcodeBuilder(caps, layer_height=p.get("layer_height", 0.2),
                     print_speed=p.get("speed", 40), hotend=p.get("hotend", 210),
                     bed=p.get("bed", 60), fan=p.get("fan", 255))
    b.preamble()
    seg_slow, seg_fast = 15.0, 40.0
    total = 2 * seg_slow + seg_fast
    spacing = 5.0
    n = len(ks)
    x0 = b.cx - total / 2.0
    y0 = b.cy - (n - 1) * spacing / 2.0
    for i, k in enumerate(ks):                             # una línea por K (una capa)
        b.cmd("M900 K{:.3f}".format(max(0, k)))
        b.cmd("M117 LA K{:.2f}".format(k))
        yy = y0 + i * spacing
        b.travel(x0, yy)
        b.line_to(x0 + seg_slow, yy, slow)                # extremo lento
        b.line_to(x0 + seg_slow + seg_fast, yy, fast)      # centro RÁPIDO
        b.line_to(x0 + total, yy, slow)                   # extremo lento
    b.postamble()
    return b.result()


def gen_ringing_tower(caps, p):
    """Test de ringing/ghosting (input shaping, Marlin M593 / Klipper). PROVOCA las
    vibraciones con aceleración y jerk ALTOS y esquinas a 90° AFILADAS; las bandas a
    velocidad creciente dejan ecos (ghosting) tras cada esquina sobre la pared siguiente.
    Se mide con calibre la distancia D entre ecos (saltando el primero o dos):
    frecuencia (Hz) = velocidad (mm/s) / D (mm) -> M593 X/Y F<freq>. Si ya conoces la
    frecuencia, ponla en 'Freq' y se emite el M593."""
    v0 = float(p.get("speed_start", 80))
    v1 = float(p.get("speed_end", 200))
    step = abs(float(p.get("speed_step", 20))) or 20
    speeds = _frange(v0, v1, step)
    vmax = min(caps["vmax"]["x"] or 9999, caps["vmax"]["y"] or 9999)
    accel = float(p.get("accel", 5000))
    jerk = float(p.get("jerk", 20))
    freq = float(p.get("shape_freq", 0) or 0)
    size = float(p.get("size", 50))
    band_h = float(p.get("band_height", 5))
    b = GcodeBuilder(caps, layer_height=p.get("layer_height", 0.2),
                     print_speed=p.get("speed", 40), hotend=p.get("hotend", 205),
                     bed=p.get("bed", 60), fan=p.get("fan", 255))
    b.preamble()
    # Provocar ringing: aceleración + jerk ALTOS (sin esto no aparecen los ecos).
    b.cmd("M201 X{:.0f} Y{:.0f}".format(accel, accel))
    b.cmd("M204 P{:.0f} T{:.0f}".format(accel, accel))
    b.cmd("M205 X{:.1f} Y{:.1f}".format(jerk, jerk))   # jerk clásico Marlin
    b.cmd("; Mide D entre ecos tras una esquina (salta el 1o o 2o):")
    b.cmd(";   freq(Hz) = velocidad(mm/s) / D(mm)  ->  M593 X/Y F<freq>")
    if freq > 0:
        b.cmd("M593 X F{:.1f} Y F{:.1f}".format(freq, freq))
    lpb = max(1, int(round(band_h / b.lh)))
    for v in speeds:
        vv = _clamp(v, 10, vmax if vmax > 0 else v)
        b.cmd("M117 Ringing {:.0f}mm/s".format(vv))
        b.ps = vv * 60.0          # velocidad de impresión de la banda
        for _ in range(lpb):
            b.rect(size, size)     # esquinas afiladas a 90°
            b.next_layer()
    b.postamble()
    return b.result()


# Registro de generadores: nombre -> (func, defaults, meta UI)
GENERATORS = {
    "linear_advance": gen_linear_advance,
    "ringing_tower": gen_ringing_tower,
}


# ============================================================================
# MOTOR DE MATRIZ: un test puede variar VARIOS parámetros (producto cartesiano).
#   - temp (slow) -> bandas en Z (sincronizado por capa, compacto).
#   - fast/geom   -> rejilla XY de especímenes (que se envuelve para caber).
# Cada test define un SPEC: footprint(p)->(w,h), base(b,cx,cy,p) [espécimen entero,
# usado en modo SEQ] y, si es "tower", layer(b,cx,cy,li,p) [una capa, usado en SYNC].
# ============================================================================
def _apply_settings(b, p, set_bed=False):
    """Emite los M-codes INSTANTÁNEOS presentes en p (no la temperatura, que la
    gestiona el motor en las bandas). DEDUP: solo emite si el valor cambió respecto al
    último emitido (evita bloat al aplicar por celda/capa en modo SYNC)."""
    mc = getattr(b, "_mc", None)
    if mc is None:
        mc = b._mc = {}

    def once(key, line, val):
        if mc.get(key) != val:
            b.cmd(line); mc[key] = val

    if set_bed and p.get("bed") is not None:
        once("bed", "M140 S{:d}".format(int(float(p["bed"]))), int(float(p["bed"])))
    if p.get("fan") is not None:
        f = int(_clamp(float(p["fan"]), 0, 255))
        once("fan", "M106 S{:d}".format(f) if f > 0 else "M107", f)
    if p.get("flow") is not None:
        v = int(_clamp(float(p["flow"]), 50, 200)); once("flow", "M221 S{:d}".format(v), v)
    if p.get("accel") is not None:
        a = int(float(p["accel"]))
        if mc.get("accel") != a:
            b.cmd("M201 X{0} Y{0}".format(a)); b.cmd("M204 P{0} T{0}".format(a)); mc["accel"] = a
    if p.get("jerk") is not None:
        j = float(p["jerk"]); once("jerk", "M205 X{0:.1f} Y{0:.1f}".format(j), j)
    if p.get("speed") is not None:
        b.ps = float(p["speed"]) * 60.0
    if p.get("k") is not None:
        v = max(0.0, float(p["k"])); once("k", "M900 K{:.3f}".format(v), round(v, 4))
    if p.get("retract") is not None:
        rs = float(p.get("retract_speed", 40)) * 60.0; v = max(0.0, float(p["retract"]))
        once("retract", "M207 S{:.2f} F{:.0f}".format(v, rs), round(v, 3))


def _h_layers(b, p, dflt=8.0):
    return max(1, int(round(float(p.get("height", p.get("band_height", dflt)) or dflt) / b.lh)))


# ---- Especímenes (geometría reutilizada de los gen_*, ahora por celda) ------
def _layer_square(b, cx, cy, li, p):
    s = float(p.get("size", 20)); b.cx, b.cy = cx, cy; b.rect(s, s)

def _base_square(b, cx, cy, p):
    for _ in range(_h_layers(b, p)):
        b.next_layer(); _layer_square(b, cx, cy, 0, p)


def _base_la(b, cx, cy, p):
    slow = float(p.get("slow_speed", 20)) * 60.0; fast = float(p.get("fast_speed", 100)) * 60.0
    ss, sf = 15.0, 40.0; total = 2 * ss + sf; x0 = cx - total / 2.0
    b.next_layer()
    b.travel(x0, cy)
    b.line_to(x0 + ss, cy, slow); b.line_to(x0 + ss + sf, cy, fast); b.line_to(x0 + total, cy, slow)


def _ringing_prelude(b, p):
    freq = float(p.get("shape_freq", 0) or 0)
    b.cmd("; freq(Hz)=velocidad(mm/s)/D(mm) -> M593 X/Y F<freq>")
    if freq > 0:
        b.cmd("M593 X F{0:.1f} Y F{0:.1f}".format(freq))


SPECS = {
    "ringing_tower":{"tower": True,  "footprint": lambda p: (float(p.get("size", 50)),) * 2,
                     "layer": _layer_square, "base": _base_square, "prelude": _ringing_prelude},
    "linear_advance":{"tower": False, "footprint": lambda p: (2 * 15.0 + 40.0 + 4, 4.5), "base": _base_la},
}


def build_matrix(test_id, fixed, axes, caps):
    """Genera la MATRIZ de especímenes. axes=[{k,kind,values}]. Devuelve (gcode, info)
    con info={count, bands, cols, rows, overflow}."""
    spec = SPECS.get(test_id)
    fixed = dict(fixed or {})
    axes = list(axes or [])
    if spec is None:
        return GENERATORS[test_id](caps, fixed), {"count": 1, "bands": 1, "overflow": False}

    temp_axis = next((a for a in axes if a.get("kind") == "temp"), None)
    grid_axes = [a for a in axes if a is not temp_axis]
    temps = [float(v) for v in temp_axis["values"]] if temp_axis else [float(fixed.get("hotend", 200) or 200)]
    nbands = len(temps)

    # Celdas = producto cartesiano de los ejes de rejilla (fast/geom).
    cells = [{}]
    for a in grid_axes:
        cells = [dict(c, **{a["k"]: v}) for c in cells for v in a["values"]]

    # SYNC (temp en bandas Z) solo si los demás ejes son INSTANTÁNEOS. El fan tiene
    # inercia: si se varía junto a la temp, usamos SEQ (cada espécimen entero, fan estable).
    _laggy = {"fan"}
    has_laggy = any(a.get("k") in _laggy for a in grid_axes)
    use_sync = (temp_axis is not None) and spec.get("tower") and not has_laggy
    if temp_axis is not None and not use_sync:
        # Sin banda Z: la temp es un eje más de la rejilla (recalentar entre grupos).
        cells = [dict(c, hotend=t) for t in temps for c in cells]

    def cellp(cell):
        p = dict(fixed); p.update(cell)
        if "hotend" not in p:
            p["hotend"] = temps[0]
        return p

    sample = cellp(cells[0])
    if use_sync:
        sample["hotend"] = temps[0]
    bopts = (spec.get("builder_opts") or (lambda p, c: {}))(sample, caps)
    b = GcodeBuilder(caps, layer_height=float(fixed.get("layer_height", 0.2) or 0.2),
                     print_speed=float(fixed.get("speed", 40) or 40),
                     hotend=temps[0], bed=float(fixed.get("bed", 60) or 60),
                     fan=int(float(fixed.get("fan", 255) or 255)),
                     **bopts)

    fw, fh = spec["footprint"](sample)
    margin, pad = 6.0, 3.0
    bw, bh = b.bedx, b.bedy
    cols = max(1, int((bw - 2 * margin) // max(1.0, fw + pad)))
    n = len(cells)
    rows = int(math.ceil(n / float(cols)))
    # overflow solo tiene sentido con >1 espécimen (n==1 = el patrón ocupa lo que ocupa).
    overflow = n > 1 and ((rows * (fh + pad)) > (bh - 2 * margin) or (fw + pad) > (bw - 2 * margin))
    gridw = cols * (fw + pad) - pad; gridh = rows * (fh + pad) - pad
    ox = bw / 2.0 - gridw / 2.0 + fw / 2.0; oy = bh / 2.0 - gridh / 2.0 + fh / 2.0
    pos = [(ox + (i % cols) * (fw + pad), oy + (i // cols) * (fh + pad)) for i in range(n)]

    b.preamble(home=True)
    if spec.get("prelude"):
        spec["prelude"](b, sample)

    if use_sync:
        band_layers = max(1, int(round(float(fixed.get("band_height", 6.0) or 6.0) / b.lh)))
        total = nbands * band_layers; cur = temps[0]
        for li in range(total):
            t = temps[min(nbands - 1, li // band_layers)]
            b.layer = li + 1; b.z = round((li + 1) * b.lh, 5)
            b.cmd("G1 Z{:.3f} F{:.0f}".format(b.z, b.caps["vmax"]["z"] * 60 or 600))
            if t != cur:
                b.cmd("M104 S{:d}".format(int(t))); b.cmd("M109 S{:d}".format(int(t))); cur = t
            for i, cell in enumerate(cells):
                p = cellp(cell); p["hotend"] = t
                _apply_settings(b, p); spec["layer"](b, pos[i][0], pos[i][1], li, p)
    else:
        cur = temps[0]
        for i, cell in enumerate(cells):
            p = cellp(cell)
            t = float(p.get("hotend", temps[0]))
            if t != cur:
                b.cmd("M104 S{:d}".format(int(t))); b.cmd("M109 S{:d}".format(int(t))); cur = t
            b.new_object(pos[i][0], pos[i][1])
            _apply_settings(b, p)
            spec["base"](b, pos[i][0], pos[i][1], p)

    b.postamble()
    return b.result(), {"count": n, "bands": nbands, "cols": cols, "rows": rows, "overflow": overflow}


def _test_meta(test_def_or_id):
    if isinstance(test_def_or_id, dict):
        return test_def_or_id
    return next((t for t in _TESTS_META if t["id"] == test_def_or_id), None)


def render_matrix(test, fixed, axes_in, caps, test_def=None):
    """Punto de entrada de alto nivel: construye los ejes (kind+valores) desde el esquema
    del test y genera la matriz. `axes_in` = [{k,start,end,step}]. Devuelve (gcode, info)."""
    tdef = test_def or _test_meta(test)
    pmeta = {p["k"]: p for p in (tdef.get("params", []) if tdef else [])}
    axes = []
    for a in (axes_in or []):
        k = a.get("k")
        pm = pmeta.get(k, {})
        kind = pm.get("kind", "fast")
        if kind == "size":   # los estructurales no son ejes
            continue
        vals = _axis_values(a.get("start", pm.get("vstart", 0)),
                            a.get("end", pm.get("vend", 0)),
                            a.get("step", pm.get("vstep", 1)))
        axes.append({"k": k, "kind": kind, "values": vals})
    return build_matrix(test if isinstance(test, str) else tdef["id"], fixed or {}, axes, caps)


def default_args(test_def):
    """fixed+axes por defecto desde el esquema (params con vary:true -> eje)."""
    fixed, axes = {}, []
    for p in test_def.get("params", []):
        if p.get("vary"):
            axes.append({"k": p["k"], "start": p.get("vstart", p["def"]),
                         "end": p.get("vend", p["def"]), "step": p.get("vstep", p["step"])})
        else:
            fixed[p["k"]] = p["def"]
    return fixed, axes


def generate(test, caps, params):
    """Compat: genera con los ejes POR DEFECTO del esquema (o el generador legacy)."""
    tdef = _test_meta(test)
    if tdef is None:
        fn = GENERATORS.get(test)
        if fn is None:
            raise ValueError("test desconocido: %s" % test)
        return fn(caps, params or {})
    fixed, axes = default_args(tdef)
    if params:
        fixed.update({k: v for k, v in params.items()})
    g, _info = render_matrix(test, fixed, axes, caps, test_def=tdef)
    return g


# ============================================================================
# Motor de plantillas Jinja (sandbox) + extracción de trazos para preview
# ============================================================================
def _builder_from_params(caps, params):
    p = params or {}
    return GcodeBuilder(
        caps,
        layer_height=float(p.get("layer_height", 0.2) or 0.2),
        print_speed=float(p.get("speed", 40) or 40),
        hotend=float(p.get("hotend", 205) or 205),
        bed=float(p.get("bed", 60) or 60),
        fan=int(p.get("fan", 255) or 255),
    )


def render_template(template_str, caps, params, builtin_id=None):
    """Renderiza una plantilla Jinja SANDBOXED a gcode. El contexto expone `g`
    (GcodeBuilder, cuyos métodos devuelven gcode), `params`, `caps`, helpers de
    matemáticas, y `builtin()` (el generador Python del test, para extender)."""
    from jinja2.sandbox import SandboxedEnvironment
    env = SandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
                               keep_trailing_newline=True)
    b = _builder_from_params(caps, params)

    def _builtin():
        if builtin_id and builtin_id in GENERATORS:
            return GENERATORS[builtin_id](caps, params or {})
        return ""

    ctx = {
        "g": b, "params": params or {}, "caps": caps, "builtin": _builtin,
        "range": range, "frange": _frange, "len": len,
        "min": min, "max": max, "abs": abs, "round": round, "int": int, "float": float,
        "sin": math.sin, "cos": math.cos, "tan": math.tan, "sqrt": math.sqrt,
        "pi": math.pi, "radians": math.radians, "clamp": _clamp,
    }
    out = env.from_string(template_str).render(**ctx)
    lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
    return "\n".join(lines) + "\n"


def extract_traces(gcode, max_pts=2000):
    """Extrae las polilíneas de EXTRUSIÓN 3D del gcode para el preview ISOMÉTRICO en
    canvas. Cada punto es [x, y, z]. Devuelve {bounds:{minx,miny,maxx,maxy,maxz},
    paths:[[[x,y,z],…],…]}. Ligero, sin raster (el cliente proyecta y dibuja)."""
    x = y = z = 0.0
    abs_xyz = True
    abs_e = True
    last_e = 0.0
    paths = []
    cur = []
    minx = miny = 1e9
    maxx = maxy = -1e9
    maxz = 0.0
    npts = 0
    for raw in gcode.splitlines():
        s = raw.split(";")[0].strip()
        if not s:
            continue
        g = _gword(s)
        if g == "G90":
            abs_xyz = True
        elif g == "G91":
            abs_xyz = False
        elif g == "M82":
            abs_e = True
        elif g == "M83":
            abs_e = False
        elif g == "G92":
            ev = _axis(s, "E")
            if ev is not None:
                last_e = ev
        elif g in ("G0", "G1"):
            nx, ny, nz = _axis(s, "X"), _axis(s, "Y"), _axis(s, "Z")
            px, py, pz = x, y, z
            if nx is not None:
                x = nx if abs_xyz else x + nx
            if ny is not None:
                y = ny if abs_xyz else y + ny
            if nz is not None:
                z = nz if abs_xyz else z + nz
            ev = _axis(s, "E")
            extruding = False
            if ev is not None:
                de = ev if not abs_e else (ev - last_e)
                if abs_e:
                    last_e = ev
                extruding = de > 1e-5
            if extruding:
                if not cur:
                    cur.append([round(px, 2), round(py, 2), round(pz, 2)])
                    minx = min(minx, px); miny = min(miny, py)
                    maxx = max(maxx, px); maxy = max(maxy, py)
                cur.append([round(x, 2), round(y, 2), round(z, 2)])
                minx = min(minx, x); miny = min(miny, y)
                maxx = max(maxx, x); maxy = max(maxy, y)
                maxz = max(maxz, z)            # solo de movimientos que EXTRUYEN
                npts += 1
            else:
                if len(cur) > 1:
                    paths.append(cur)
                cur = []
        if npts >= max_pts:
            break
    if len(cur) > 1:
        paths.append(cur)
    if maxx < minx:
        minx = miny = 0.0
        maxx = maxy = 1.0
    return {"bounds": {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy, "maxz": maxz},
            "paths": paths}


# ============================================================================
# Definiciones de tests (data-driven). Las "complejas" usan {{ builtin() }} (el
# generador Python) y se pueden EXPANDIR a Jinja propio desde el editor.
# ============================================================================
_TPL_TOWER_RAW = (
    "{{ g.preamble() }}\n"
    "{% set lpb = [(params.band_height / g.lh)|round(0,'floor')|int, 1]|max %}\n"
    "{% set _step = (-1 if __DESC__ else 1) * params.__STEP__ %}\n"
    "{% for v in frange(params.__START__, params.__END__, _step) %}\n"
    "{{ g.band('__LABEL__: ' ~ v) }}\n"
    "__MCODES__\n"
    "{% for _ in range(lpb) %}\n"
    "{{ g.rect(params.get('size', 25), params.get('size', 25)) }}\n"
    "{{ g.next_layer() }}\n"
    "{% endfor %}\n"
    "{% endfor %}\n"
    "{{ g.postamble() }}\n"
)


def _tower_tpl(start, end, step, desc, label, mcodes):
    return (_TPL_TOWER_RAW
            .replace("__START__", start).replace("__END__", end)
            .replace("__STEP__", step).replace("__DESC__", "True" if desc else "False")
            .replace("__LABEL__", label).replace("__MCODES__", mcodes))

def _P(k, label, u, mn, mx, st, df, kind="size", vary=False, vs=None, ve=None, vst=None):
    """Crea un parámetro enriquecido. kind: temp(slow→Z) | fast | geom | size(fijo)."""
    return {"k": k, "label": label, "u": u, "min": mn, "max": mx, "step": st, "def": df,
            "kind": kind, "vary": vary,
            "vstart": df if vs is None else vs, "vend": df if ve is None else ve,
            "vstep": st if vst is None else vst}


# Parámetros COMUNES (afectan a casi todos los tests): temp/cama/fan/velocidad.
def _p_hotend(df=200, vary=False, vs=215, ve=195, vst=5):
    return _P("hotend", "Hotend", "°C", 150, 300, 5, df, "temp", vary, vs, ve, vst)

def _p_bed(df=60):
    return _P("bed", "Cama", "°C", 0, 120, 5, df, "size")

def _p_fan(df=255, vary=False, vs=0, ve=255, vst=64):
    return _P("fan", "Ventilador", "", 0, 255, 5, df, "fast", vary, vs, ve, vst)

def _p_speed(df=40, vary=False, vs=20, ve=80, vst=10):
    return _P("speed", "Velocidad", "mm/s", 5, 200, 5, df, "fast", vary, vs, ve, vst)


_TESTS_META = [
    {"id": "linear_advance", "name": "Linear / Pressure Advance", "icon": "stream",
     "help": "Patrón de LÍNEAS (Marlin K-factor / Ellis): una línea por valor K con tramos "
             "lento–RÁPIDO–lento. Con la K correcta el ancho es uniforme y las transiciones no "
             "tienen bulto (K baja) ni hueco (K alta). Aplícala con M900 K<valor> (Klipper: "
             "pressure_advance). La K depende de temperatura y enfriamiento (fija/varía hotend y fan).",
     "params": [
        _P("k", "K", "", 0, 2, 0.05, 0, "fast", vary=True, vs=0, ve=1, vst=0.1),
        _P("slow_speed", "Vel. lenta", "mm/s", 5, 50, 5, 20, "size"),
        _P("fast_speed", "Vel. rápida", "mm/s", 40, 200, 10, 100, "size"),
        _p_hotend(210), _p_fan(255), _p_bed(60)],
     "template": "{{ builtin() }}\n"},
    {"id": "ringing_tower", "name": "Input shaping (resonance)", "icon": "wave-square",
     "help": "Esquinas afiladas a velocidad creciente con accel+jerk ALTOS para provocar "
             "ringing/ghosting (ecos tras la esquina). Mide D entre ecos: freq(Hz) = vel/D → "
             "M593 X/Y F<freq>. Si ya conoces la freq, ponla en 'Freq'. El fan afecta al "
             "amortiguamiento (fíjalo/varíalo).",
     "params": [
        _P("speed", "Velocidad", "mm/s", 20, 350, 10, 80, "fast", vary=True, vs=80, ve=200, vst=60),
        _P("accel", "Aceleración", "mm/s²", 1000, 12000, 500, 5000, "fast"),
        _P("jerk", "Jerk", "mm/s", 5, 40, 1, 20, "fast"),
        _P("shape_freq", "Freq (0=medir)", "Hz", 0, 120, 1, 0, "size"),
        _P("size", "Tamaño", "mm", 15, 60, 1, 35, "size"),
        _P("band_height", "Altura banda", "mm", 3, 15, 1, 5, "size"),
        _p_fan(255), _p_hotend(205), _p_bed(60)],
     "template": "{{ builtin() }}\n"},
    {"id": "backlash_probe", "name": "Backlash (G38 probe)", "icon": "exchange-alt", "type": "probe",
     "help": "Barrido interactivo del valor de M425 <axis> del eje seleccionado. Método: "
             "durante el barrido silencia el auto-report (M154 S0) y desactiva los soft endstops "
             "(M211 S0); en cada sondeo lanza `G38.3 <axis><target> F<feed>`. G38.3 usa el pin "
             "del endstop del eje para cortar el movimiento en el instante exacto del disparo; "
             "M400+M114 R D lee la posición REAL de disparo desde los contadores del stepper "
             "(línea FromStp:). Mide la deriva |A−B| tras un reversal completo; el M425 con "
             "mínimo error es el óptimo. Coloca el brazo alargado sobre el endstop del eje "
             "seleccionado varios mm dentro del recorrido para que el disparo no ocurra en el "
             "origen mecánico. Al terminar restaura M154/M211 y el M425 original.",
     "params": []},
]


def builtin_tests():
    """Definiciones de tests por defecto (copia profunda)."""
    import copy
    return copy.deepcopy(_TESTS_META)


# ============================================================================
# Controlador: tests dirigidos por firmware (PID, e-steps) + persistencia
# ============================================================================
class CalibrationController(object):
    def __init__(self, printer, logger, data_folder, send_message):
        self._printer = printer
        self.log = logger
        self._dir = data_folder
        self._send = send_message
        self.running = False
        self._pid = None
        self._pid_event = threading.Event()
        self._m92 = None
        self._m92_event = threading.Event()
        self.esteps = {"current": None}
        # Backlash G38 sweep state
        self._probe_result = None       # {"x":..,"y":..,"z":..,"hit":bool}
        self._probe_event = threading.Event()
        self._backlash_busy = False
        self._backlash_cancel = False
        self._m425_orig = None
        self._m425_event = threading.Event()

    # --- llamado desde el hook gcode.received ---
    def on_received(self, line):
        m = _PID_RE.search(line)
        if m and ("PID" in line or "Kp" in line or "p:" in line.lower() or line.strip().startswith("M30")):
            try:
                self._pid = {"p": float(m.group("p")), "i": float(m.group("i")),
                             "d": float(m.group("d"))}
                self._pid_event.set()
            except (TypeError, ValueError):
                pass
        if "M92" in line:
            mm = _M92_RE.search(line)
            if mm and mm.group("e"):
                try:
                    self._m92 = {k: (float(mm.group(k)) if mm.group(k) else None)
                                 for k in ("x", "y", "z", "e")}
                    self.esteps["current"] = self._m92.get("e")
                    self._m92_event.set()
                except (TypeError, ValueError):
                    pass
        # Backlash: capturar respuestas del ciclo `G38.3 + M400 + M114 R D`.
        # Sólo `FromStp:` (autoritativa desde contadores de stepper) o un fallo
        # explícito (`Probe failed` / `Failed to reach target`) cierran el
        # wait de `_do_probe`. La línea `Probe: X:.. Y:.. Z:..` de Marlin y la
        # primera línea "lógica" del M114 reportan el target planeado, no el
        # punto real de disparo — ignorarlas evita una race donde `_do_probe`
        # devolvería coords antiguas antes de que llegue FromStp:.
        if self._backlash_busy:
            l = line.strip()
            if ("Probe failed" in l
                    or "probe failed" in l.lower()
                    or "Failed to reach target" in l
                    or "failed to reach target" in l.lower()):
                self._probe_result = {"x": None, "y": None, "z": None, "hit": False}
                self._probe_event.set()
            elif l.startswith("FromStp:"):
                mx = re.search(r"X:\s*([-\d.]+)", l)
                my = re.search(r"Y:\s*([-\d.]+)", l)
                mz = re.search(r"Z:\s*([-\d.]+)", l)
                if mx and my and mz:
                    self._probe_result = {
                        "x": float(mx.group(1)),
                        "y": float(my.group(1)),
                        "z": float(mz.group(1)),
                        "hit": True,
                    }
                    self._probe_event.set()
            # M425 actual: `M425 X0.10 Y0.05 Z0.00` (Marlin M503)
            if l.startswith("M425 ") or l.startswith("echo:  M425"):
                d = {}
                for ax in ("X", "Y", "Z"):
                    ma = re.search(r"%s([-\d.]+)" % ax, l)
                    if ma:
                        d[ax.lower()] = float(ma.group(1))
                if d:
                    self._m425_orig = d
                    self._m425_event.set()

    def _push(self, payload):
        try:
            self._send(dict(payload, type="calib"))
        except Exception:
            pass

    # --- PID autotune (firmware lo calcula; nosotros parseamos + ofrecemos M500) ---
    def run_pid(self, heater="hotend", cycles=8, temp=200, fan=255, idx=0, save=False):
        if self.running:
            return False
        self.running = True
        self._pid = None
        self._pid_event.clear()

        def worker():
            try:
                e = idx if heater == "hotend" else -1
                cmds = []
                if heater == "hotend" and fan:
                    cmds.append("M106 S{:d}".format(int(_clamp(fan, 0, 255))))
                cmds.append("M303 E{:d} C{:d} S{:.0f} U1".format(
                    int(e), int(_clamp(cycles, 3, 20)), float(temp)))
                self._printer.commands(cmds)
                self._push({"event": "pid_started", "heater": heater})
                ok = self._pid_event.wait(timeout=600)
                if ok and self._pid:
                    if save:
                        self._printer.commands(["M500"])
                    self._push({"event": "pid_done", "heater": heater,
                                "pid": self._pid, "saved": save})
                else:
                    self._push({"event": "pid_timeout", "heater": heater})
            except Exception as ex:
                self.log.warning("PID worker: %s", ex)
                self._push({"event": "pid_error", "msg": str(ex)})
            finally:
                if heater == "hotend":
                    try:
                        self._printer.commands(["M107"])
                    except Exception:
                        pass
                self.running = False

        threading.Thread(target=worker, name="bk-calib-pid", daemon=True).start()
        return True

    # --- e-steps: leer actual, calentar+extruir, aplicar ---
    def read_m92(self):
        self._m92_event.clear()
        try:
            self._printer.commands(["M92"])
        except Exception:
            return None
        self._m92_event.wait(timeout=5)
        return self._m92

    def esteps_extrude(self, temp, length, speed):
        if self.running:
            return False
        self.running = True

        def worker():
            try:
                self._printer.commands([
                    "M104 S{:.0f}".format(float(temp)),
                    "M109 S{:.0f}".format(float(temp)),
                    "M83",
                    "G1 E{:.2f} F{:.0f}".format(float(length), float(speed) * 60),
                ])
                self._push({"event": "esteps_extruded", "length": length})
            except Exception as ex:
                self._push({"event": "esteps_error", "msg": str(ex)})
            finally:
                self.running = False

        threading.Thread(target=worker, name="bk-calib-esteps", daemon=True).start()
        return True

    @staticmethod
    def esteps_calc(current, mark, remained):
        """Nuevo M92 E = actual · mark / (mark − remained)."""
        extruded = float(mark) - float(remained)
        if extruded <= 0:
            return None
        return round(float(current) * float(mark) / extruded, 2)

    # --- Backlash sweep (G38.3 + M425) ---
    def cancel_backlash_sweep(self):
        self._backlash_cancel = True
        # Parada INMEDIATA: M410 aborta el G38.3/movimiento en curso y limpia el
        # planner; despertamos las esperas (sondeo/M503) para que el worker salga YA
        # en vez de esperar el timeout de _do_probe (hasta 30 s por muestra).
        try:
            self._printer.commands(["M410"], force=True)
        except Exception:
            pass
        try:
            self._probe_event.set()
            self._m425_event.set()
        except Exception:
            pass

    def _do_probe(self, axis, target, feed, timeout=30.0):
        """Sondeo por G38.3 (`G38.3 <axis><target> F<feed>` + M400 + M114 R D).

        G38.3 corta el movimiento en el instante exacto en que el pin del
        endstop del eje dispara. Este fork de Marlin implementa G38.3 sobre
        los endstops X/Y/Z. `M114 R D` reporta la posición REAL desde los
        contadores del stepper — la línea `FromStp:` es la fuente autoritativa
        del punto de disparo (la primera línea "lógica" reporta el target
        planeado, no dónde paró).

        Requisitos (los prepara `run_backlash_sweep`):
          - `M154 S0` durante todo el barrido → sin auto-report que ensucie
            el canal serial y confunda la respuesta al M114.
          - `M211 S0` durante todo el barrido → sin soft endstops que aborten
            un G38.3 con target fuera del área imprimible.

        Devuelve (hit, pos_on_axis). `hit=False` si el cabezal alcanzó el
        target sin disparo (|pos−target| < 0.3 mm), si `Probe failed` o si
        hubo timeout.
        """
        self._probe_result = None
        self._probe_event.clear()
        ax_upper = axis.upper()
        ax_lower = axis.lower()
        target_f = float(target)
        try:
            self._printer.commands([
                "G38.3 {}{:.3f} F{:d}".format(ax_upper, target_f, int(feed)),
                "M400",
                "M114 R D",
            ])
        except Exception:
            return False, None
        if not self._probe_event.wait(timeout=timeout):
            return False, None
        if self._backlash_cancel:   # cancelado mientras esperábamos -> abortar ya
            return False, None
        pr = self._probe_result or {}
        if pr.get("hit") is False:
            return False, None
        pos = pr.get(ax_lower)
        if pos is None:
            return False, None
        # "No hit": el cabezal alcanzó el target (o casi) sin que el endstop
        # dispare. Umbral generoso (0.3 mm) para tolerar redondeos de step.
        if abs(pos - target_f) < 0.3:
            return False, None
        return True, pos

    def _wait_pos(self, ax, feed, target, safe, samples_per, feed_travel):
        """Ejecuta un ciclo: safe → probe (A) → far → safe → probe (B). Devuelve
        error = |A − B| (mediana sobre samples_per) o None si fallo.

        Notas de rendimiento: NO usamos G4 (redundante — el planner de Marlin ya
        encola comandos en orden, el G38.3 no arranca hasta que el G0 previo
        termine). Los travel del reversal van en UNA sola llamada a `commands()`
        para amortiguar la latencia serial."""
        errs = []
        far = float(safe) + 5.0
        AX = ax.upper()
        setup_a = [
            "G90",
            "G0 {}{:.3f} F{:d}".format(AX, float(safe), int(feed_travel)),
        ]
        reversal = [
            # dirección + (alejar), luego − (volver): fuerza compensación de backlash
            "G0 {}{:.3f} F{:d}".format(AX, far, int(feed_travel)),
            "G0 {}{:.3f} F{:d}".format(AX, float(safe), int(feed_travel)),
        ]
        for _ in range(int(samples_per)):
            if self._backlash_cancel:
                return None
            try:
                self._printer.commands(setup_a)
            except Exception:
                return None
            hit, a = self._do_probe(ax, target, feed)
            if self._backlash_cancel or not hit or a is None:
                return None
            try:
                self._printer.commands(reversal)
            except Exception:
                return None
            hit, b = self._do_probe(ax, target, feed)
            if self._backlash_cancel or not hit or b is None:
                return None
            errs.append(abs(a - b))
        if not errs:
            return None
        errs.sort()
        return errs[len(errs) // 2]  # mediana

    def run_backlash_sweep(self, axis="x", m425_start=0.0, m425_end=0.2, m425_step=0.02,
                           ref_pos=50.0, back_off=5.0, target=-1.0, feed=200,
                           feed_travel=1500, samples_per=3, save=False):
        """Barre M425 <axis> Z<v> midiendo la deriva post-reversal con G38.3.

        Args:
            axis: "x", "y" o "z".
            m425_start/end/step: barrido de valores M425 (mm).
            ref_pos: posición segura desde la que arranca cada sondeo.
            back_off: cuánto retirarse tras el disparo (informativo; el reversal
                usa `ref_pos + 5.0` internamente).
            target: coordenada objetivo del G38.3 (ej. -1.0 para X/Y contra el
                brazo del endstop; para Z sería la altura por debajo de la cama).
            feed: velocidad del sondeo (mm/min).
            samples_per: muestras por valor de M425 (mediana).
            save: si True, tras aplicar el mejor valor envía M500.
        """
        if self._backlash_busy or self.running:
            return False
        ax = axis.lower()
        if ax not in ("x", "y", "z"):
            return False
        self._backlash_busy = True
        self._backlash_cancel = False

        def worker():
            try:
                # Setup: leer M425 actual para restaurar al final
                self._m425_orig = None
                self._m425_event.clear()
                try:
                    self._printer.commands(["M503"])
                except Exception:
                    pass
                self._m425_event.wait(timeout=5)
                orig = (self._m425_orig or {}).get(ax)
                if orig is None:
                    orig = 0.0

                # Aislar el canal serial: M154 S0 silencia el auto-report para
                # que las respuestas al M114 D del sondeo no se confundan con
                # updates periódicos; M211 S0 desactiva los soft endstops para
                # que el brazo físico sea el único que pueda cortar el G1.
                # M425 F1.0: ACTIVA el factor de corrección (por defecto 0.0). Sin
                # esto la compensación no se aplica y el barrido mediría el mismo
                # error para todos los valores de M425 -> resultado inútil.
                try:
                    self._printer.commands(["M154 S0", "M211 S0", "M425 F1.0"])
                except Exception:
                    pass

                self._push({"event": "backlash_started", "axis": ax,
                            "m425_orig": orig, "samples_per": int(samples_per)})

                # Barrido
                values = []
                v = float(m425_start)
                step = float(m425_step) if m425_step > 0 else 0.02
                end = float(m425_end)
                while v <= end + 1e-9:
                    values.append(round(v, 4))
                    v += step

                best = None
                best_err = float("inf")
                for vv in values:
                    if self._backlash_cancel:
                        break
                    try:
                        # Sintaxis Marlin: M425 X<v> pone la comp del eje X. NO
                        # es `M425 X Z<v>` (eso pone Z sin tocar X, error clásico).
                        self._printer.commands([
                            "M425 {}{:.4f}".format(ax.upper(), vv),
                        ])
                    except Exception:
                        continue
                    err = self._wait_pos(ax, feed, target, ref_pos,
                                         samples_per, feed_travel)
                    if self._backlash_cancel:   # cancelado durante el sondeo -> salir sin ruido
                        break
                    if err is None:
                        self._push({"event": "backlash_error",
                                    "msg": "probe_failed_or_timeout",
                                    "axis": ax, "m425": vv})
                        continue
                    self._push({"event": "backlash_sample", "axis": ax,
                                "m425": vv, "error": err})
                    if err < best_err:
                        best_err = err
                        best = vv

                if self._backlash_cancel:
                    self._push({"event": "backlash_cancelled", "axis": ax})
                else:
                    self._push({"event": "backlash_done", "axis": ax,
                                "best_m425": best, "best_error": best_err})

                # Restauración / persistencia
                try:
                    if save and best is not None and not self._backlash_cancel:
                        self._printer.commands([
                            "M425 {}{:.4f}".format(ax.upper(), best),
                            "M500",
                        ])
                    else:
                        self._printer.commands([
                            "M425 {}{:.4f}".format(ax.upper(), float(orig)),
                        ])
                except Exception:
                    pass
                # Parar cualquier movimiento en curso si cancelado
                if self._backlash_cancel:
                    try:
                        self._printer.commands(["M410"])
                    except Exception:
                        pass
            except Exception as ex:
                self.log.warning("backlash worker: %s", ex)
                self._push({"event": "backlash_error", "msg": str(ex)})
            finally:
                # Restaurar el canal serial y los soft endstops SIEMPRE, aunque
                # el worker haya explotado. Prioridad alta: dejar la impresora
                # en un estado seguro (soft endstops ON) antes de soltar el flag.
                try:
                    self._printer.commands(["M211 S1", "M154 S1"])
                except Exception:
                    pass
                self._backlash_busy = False
                self._backlash_cancel = False

        threading.Thread(target=worker, name="bk-calib-backlash", daemon=True).start()
        return True

    def apply_gcode(self, lines, save=False):
        cmds = list(lines)
        if save:
            cmds.append("M500")
        try:
            self._printer.commands(cmds)
            return True
        except Exception as ex:
            self.log.warning("apply_gcode: %s", ex)
            return False

    # --- Persistencia: presets + histórico (tracking) ---
    def _path(self, name):
        return os.path.join(self._dir, name)

    def _load_json(self, name, default):
        try:
            with open(self._path(name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, IOError, ValueError):
            return default

    def _save_json(self, name, data):
        try:
            with open(self._path(name), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            return True
        except (OSError, IOError):
            return False

    # --- Definiciones de tests (built-in + overrides del usuario) ---
    def tests(self):
        """Lista de tests = built-ins fusionados con overrides del usuario
        (calib_tests.json: {id: defn}). El usuario puede editar/añadir/borrar."""
        base = {t["id"]: t for t in builtin_tests()}
        user = self._load_json("calib_tests.json", {}) or {}
        for tid, defn in user.items():
            if defn is None:
                base.pop(tid, None)        # marcado como borrado
            else:
                base[tid] = defn
        return list(base.values())

    def test_get(self, tid):
        for t in self.tests():
            if t.get("id") == tid:
                return t
        return None

    def test_save(self, defn):
        if not isinstance(defn, dict) or not defn.get("id"):
            return False
        user = self._load_json("calib_tests.json", {}) or {}
        user[defn["id"]] = defn
        return self._save_json("calib_tests.json", user)

    def test_delete(self, tid):
        user = self._load_json("calib_tests.json", {}) or {}
        # Si es built-in, lo marcamos como None (borrado); si era del usuario, lo quita.
        if tid in {t["id"] for t in builtin_tests()}:
            user[tid] = None
        else:
            user.pop(tid, None)
        return self._save_json("calib_tests.json", user)

    def presets(self):
        return self._load_json("calib_presets.json", {})

    def preset_save(self, test, name, params):
        d = self.presets()
        lst = d.setdefault(test, [])
        lst[:] = [x for x in lst if x.get("name") != name]
        lst.append({"name": name, "params": params})
        self._save_json("calib_presets.json", d)
        return d

    def preset_delete(self, test, name):
        d = self.presets()
        if test in d:
            d[test] = [x for x in d[test] if x.get("name") != name]
        self._save_json("calib_presets.json", d)
        return d

    def history(self):
        return self._load_json("calib_history.json", [])

    def history_add(self, entry):
        h = self.history()
        entry = dict(entry)
        entry.setdefault("ts", int(time.time()))
        entry.setdefault("result", "pending")
        entry["id"] = (max([e.get("id", 0) for e in h]) + 1) if h else 1
        h.insert(0, entry)
        self._save_json("calib_history.json", h[:200])
        return entry

    def history_mark(self, entry_id, result, notes=None, measured=None):
        h = self.history()
        for e in h:
            if e.get("id") == entry_id:
                e["result"] = result
                if notes is not None:
                    e["notes"] = notes
                if measured is not None:
                    e["measured"] = measured
                break
        self._save_json("calib_history.json", h)
        return h
