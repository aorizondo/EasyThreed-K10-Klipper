# coding=utf-8
"""
Nivelación automática AGNÓSTICA (Boost Kit).

Funciona con cualquier impresora: auto-detecta el offset de la sonda (M851) y los
límites de la cama (M211) para calcular qué puntos de malla son alcanzables. Para
los inalcanzables (sondas con offset grande que no llegan a todo el lecho) mide el
punto alcanzable MÁS CERCANO con G30 y lo inyecta con M421 (mejor que extrapolar).
Si la sonda alcanza toda la cama -> 0 bordes, nivelación normal.

Soporta UBL (G29 P1/P3/S<slot>) y bilineal (G29 plano). La parte que el gcode no
puede (leer el resultado de G30 en vivo) se hace aquí capturando las líneas
"Bed X: .. Y: .. Z: .." vía el hook gcode.received.
"""
from __future__ import absolute_import

import math
import re
import threading
import time

_BED_RE = re.compile(r"Bed X:\s*(-?[\d.]+)\s*Y:\s*(-?[\d.]+)\s*Z:\s*(-?[\d.]+)")
_M851_RE = re.compile(r"M851\s+X\s*(-?[\d.]+)\s+Y\s*(-?[\d.]+)\s+Z\s*(-?[\d.]+)")
# M211: "Soft endstops: On  Min: X0.00 Y0.00 Z0.00  Max: X100.00 Y100.00 Z200.00"
_M211_RE = re.compile(
    r"Min:\s*X\s*(-?[\d.]+)\s*Y\s*(-?[\d.]+).*?Max:\s*X\s*(-?[\d.]+)\s*Y\s*(-?[\d.]+)",
    re.IGNORECASE,
)
# M206 home offset (reportado por M503): "echo: M206 X0.00 Y0.00 Z0.00"
_M206_RE = re.compile(r"M206\s+X\s*(-?[\d.]+)\s+Y\s*(-?[\d.]+)\s+Z\s*(-?[\d.]+)", re.IGNORECASE)
# Backlash medido (M425 report): "Average measured backlash (mm): X.. Y.. Z0.63"
_M425_MEAS_RE = re.compile(r"measured backlash.*?Z\s*(-?[\d.]+)", re.IGNORECASE)
# Posición actual (M114 / auto-report): "X:243.00 Y:300.00 Z:8.00 E:.. Count.."
_POS_RE = re.compile(r"X:\s*(-?[\d.]+)\s+Y:\s*(-?[\d.]+)\s+Z:\s*(-?[\d.]+)")
# Detección de capacidades de nivelación (al conectar):
_SLOTS_RE = re.compile(r"(\d+)\s+mesh slots available", re.IGNORECASE)
_FWNAME_RE = re.compile(r"FIRMWARE_NAME:\s*([^\s]+(?:\s+[\d.]+)?)", re.IGNORECASE)
_ACTIVESLOT_RE = re.compile(r"Active Mesh Slot[:\s]+(-?\d+)", re.IGNORECASE)


def grid_coords(n, lo, hi):
    """n puntos equiespaciados entre lo y hi (ambos inclusive)."""
    if n <= 1:
        return [(lo + hi) / 2.0]
    step = (hi - lo) / (n - 1)
    return [lo + k * step for k in range(n)]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _median(values):
    s = sorted(values)
    m = len(s)
    if m == 0:
        return None
    return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2.0


def _mode_z(samples, resolution=0.01):
    """Moda de una lista de Z agrupando a `resolution` mm (binning).

    Devuelve el promedio del bin más frecuente. Empate -> el bin cuyo centro está
    más cerca de la mediana global. Pensado para 4 lecturas de sonda por punto.
    """
    if not samples:
        return None
    bins = {}
    for z in samples:
        bins.setdefault(int(round(z / resolution)), []).append(z)
    top = max(len(v) for v in bins.values())
    cand = [k for k, v in bins.items() if len(v) == top]
    if len(cand) > 1:
        med = _median(samples)
        cand = [min(cand, key=lambda k: abs(k * resolution - med))]
    vals = bins[cand[0]]
    return sum(vals) / len(vals)


def _fit_plane(pts):
    """Ajuste de plano z = a*x + b*y + c por mínimos cuadrados sobre (x,y,z).

    Devuelve (a, b, c). Si los puntos son (casi) colineales o degenerados, cae a
    un plano horizontal en la media de z. Para cristal plano 3+ puntos bastan.
    """
    n = len(pts)
    if n == 0:
        return (0.0, 0.0, 0.0)
    Sx = sum(p[0] for p in pts); Sy = sum(p[1] for p in pts); Sz = sum(p[2] for p in pts)
    Sxx = sum(p[0] * p[0] for p in pts); Syy = sum(p[1] * p[1] for p in pts)
    Sxy = sum(p[0] * p[1] for p in pts)
    Sxz = sum(p[0] * p[2] for p in pts); Syz = sum(p[1] * p[2] for p in pts)
    # Sistema normal 3x3:  [Sxx Sxy Sx][a]=[Sxz] ; [Sxy Syy Sy][b]=[Syz] ; [Sx Sy n][c]=[Sz]
    M = [[Sxx, Sxy, Sx], [Sxy, Syy, Sy], [Sx, Sy, float(n)]]
    R = [Sxz, Syz, Sz]

    def _det(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))

    det = _det(M)
    if abs(det) < 1e-9:
        return (0.0, 0.0, Sz / n)  # colineales/degenerados -> plano horizontal
    out = []
    for col in range(3):
        Mc = [row[:] for row in M]
        for r in range(3):
            Mc[r][col] = R[r]
        out.append(_det(Mc) / det)
    return (out[0], out[1], out[2])


def _mad(values, center=None):
    """Desviación absoluta mediana (MAD). Escala robusta al ruido/outliers."""
    if not values:
        return 0.0
    c = center if center is not None else _median(values)
    return _median([abs(v - c) for v in values]) or 0.0


def _plane_stats(pts, inliers, a, b, c):
    """Empaqueta el resultado de un ajuste de plano con métricas de calidad.
    residuo_k = z_k - (a·x+b·y+c). rms/max_res se calculan SOLO sobre inliers
    (los outliers no representan la superficie plana)."""
    inset = set((round(p[0], 4), round(p[1], 4)) for p in inliers)
    outlier_pts = []
    in_res = []
    for (x, y, z) in pts:
        r = z - (a * x + b * y + c)
        if (round(x, 4), round(y, 4)) in inset:
            in_res.append(r)
        else:
            outlier_pts.append((x, y, r))
    rms = (math.sqrt(sum(r * r for r in in_res) / len(in_res))) if in_res else 0.0
    max_res = max((abs(r) for r in in_res), default=0.0)
    return {
        "a": a, "b": b, "c": c,
        "inliers": len(inliers), "rejected": len(outlier_pts),
        "rms": rms, "max_res": max_res,
        "inlier_pts": list(inliers), "outlier_pts": outlier_pts,
    }


def fit_plane_robust(pts, k=2.5, floor_mm=0.05, ceiling_mm=0.30, max_iter=10, min_pts=3):
    """Ajuste de plano robusto a outliers (RLS-MAD): mínimos cuadrados iterativo
    con rechazo duro de puntos cuyo residuo excede un umbral basado en la MAD.

    Umbral por iteración: T = clamp(k·1.4826·MAD, floor_mm, ceiling_mm). El
    `floor_mm` evita sobre-rechazar en cristal impecable (ruido ~0.02 mm); el
    `ceiling_mm` (techo duro) garantiza que un bulto de pegamento SIEMPRE se
    rechaza aunque la MAD esté inflada. Nunca baja de `min_pts` inliers.

    Devuelve un dict con a,b,c, contadores, rms/max_res (sobre inliers) y las
    listas de puntos inlier/outlier (para métricas y marcado en la UI).
    """
    pts = list(pts)
    a, b, c = _fit_plane(pts)
    if len(pts) < min_pts:
        return _plane_stats(pts, pts, a, b, c)
    inliers = list(pts)
    prev_key = None
    for _ in range(max_iter):
        res = [(x, y, z, z - (a * x + b * y + c)) for (x, y, z) in pts]
        mad = _mad([t[3] for t in res], center=0.0)
        thresh = max(k * 1.4826 * mad, floor_mm)
        if ceiling_mm:
            thresh = min(thresh, ceiling_mm)
        new_in = [(x, y, z) for (x, y, z, r) in res if abs(r) <= thresh]
        if len(new_in) < min_pts:
            new_in = [(t[0], t[1], t[2])
                      for t in sorted(res, key=lambda t: abs(t[3]))[:min_pts]]
        key = tuple(sorted((round(p[0], 3), round(p[1], 3)) for p in new_in))
        inliers = new_in
        a, b, c = _fit_plane(inliers)
        if key == prev_key:
            break
        prev_key = key
    return _plane_stats(pts, inliers, a, b, c)


def plane_metrics(fit, pts):
    """Métricas legibles del ajuste para la UI: RMS (µm), inclinación (mm por eje
    sobre la extensión sondeada), nº de outliers y una etiqueta de calidad."""
    a, b = fit["a"], fit["b"]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    dx = (max(xs) - min(xs)) if xs else 0.0
    dy = (max(ys) - min(ys)) if ys else 0.0
    tilt_x = a * dx; tilt_y = b * dy
    tilt_deg = math.degrees(math.atan(math.sqrt(a * a + b * b)))
    rms = fit.get("rms", 0.0)
    quality = ("excelente" if rms <= 0.03 else "buena" if rms <= 0.06
               else "regular" if rms <= 0.12 else "pobre")
    return {
        "rms_um": round(rms * 1000.0, 1),
        "max_res_mm": round(fit.get("max_res", 0.0), 3),
        "rejected": fit.get("rejected", 0),
        "n_total": len(pts),
        "tilt_deg": round(tilt_deg, 3),
        "tilt_x_mm": round(tilt_x, 3),
        "tilt_y_mm": round(tilt_y, 3),
        "dz_total_mm": round(tilt_x + tilt_y, 3),
        "outliers": [{"x": round(x, 1), "y": round(y, 1), "r": round(r, 3)}
                     for (x, y, r) in fit.get("outlier_pts", [])],
        "quality": quality,
    }


def _idw_residual(X, Y, res_pts):
    """Residuo interpolado (IDW) en (X,Y). Fuera del casco de puntos -> 0 (plano
    puro), para no extrapolar alabeo donde no se midió."""
    if not res_pts:
        return 0.0
    xs = [p[0] for p in res_pts]; ys = [p[1] for p in res_pts]
    if not (min(xs) <= X <= max(xs) and min(ys) <= Y <= max(ys)):
        return 0.0
    num = 0.0; den = 0.0
    for (px, py, pr) in res_pts:
        d2 = (X - px) ** 2 + (Y - py) ** 2
        if d2 < 1e-9:
            return pr
        w = 1.0 / d2
        num += w * pr; den += w
    return num / den if den else 0.0


def _gauss_kernel(sigma):
    if sigma <= 0:
        return [1.0]
    radius = max(1, int(round(3 * sigma)))
    w = [math.exp(-(x * x) / (2 * sigma * sigma)) for x in range(-radius, radius + 1)]
    s = sum(w)
    return [v / s for v in w]


def smooth_grid(grid, sigma_cells=0.8, strength=1.0):
    """Suaviza una rejilla con un Gauss separable y renormalización de bordes
    (los bordes no se sesgan hacia 0). `strength` mezcla hacia el suavizado
    (1.0=totalmente suavizado, 0=sin cambio). Para el modo alabeo."""
    if sigma_cells <= 0 or strength <= 0 or not grid:
        return grid
    ker = _gauss_kernel(sigma_cells)
    rad = len(ker) // 2
    n = len(grid); m = len(grid[0]) if n else 0

    def _pass(src, along_rows):
        out = [[0.0] * m for _ in range(n)]
        for i in range(n):
            for j in range(m):
                acc = 0.0; wsum = 0.0
                for t, kk in enumerate(ker):
                    ii, jj = (i, j + (t - rad)) if along_rows else (i + (t - rad), j)
                    if 0 <= ii < n and 0 <= jj < m:
                        acc += kk * src[ii][jj]; wsum += kk
                out[i][j] = acc / wsum if wsum else src[i][j]
        return out

    sm = _pass(_pass(grid, True), False)
    return [[grid[i][j] + strength * (sm[i][j] - grid[i][j]) for j in range(m)]
            for i in range(n)]


class LevelingController:
    def __init__(self, printer, logger, send_message):
        self._printer = printer
        self.log = logger
        # send_message(dict) -> envía un plugin message arbitrario al frontend.
        self._send = send_message
        self._push = lambda st: send_message({"type": "leveling", "state": st})
        self.running = False
        self._thread = None
        self.status = "idle"
        self.detail = ""
        self.cancelled = False
        # auto-M108 en las pausas de la sonda manual (PAUSE_BEFORE_DEPLOY_STOW)
        # durante un run: desatendido tras un despliegue manual.
        self._autoconfirm = False
        # Un ÚNICO M108 por pausa: se arma en cada pausa nueva y se desarma tras
        # contestar (el "paused for user" se repite cada ~2 s -> sin flag = 3-4 envíos).
        self._pause_acked = False
        # M851 Z a restaurar tras el sondeo de plato con shift adaptativo (o None).
        self._m851_restore = None
        self._home_offset = None
        self._home_event = threading.Event()
        # Backlash de Z: media medida (M425) y soporte del firmware.
        self._backlash_meas = None
        self._backlash_event = threading.Event()
        self._cap_backlash = None
        # Editor manual: lectura puntual de posición (M114) para "Fijar".
        self._pos = None
        self._pos_event = threading.Event()
        self._want_pos = False

        self._bed_result = None
        self._bed_event = threading.Event()
        self._offset = None
        self._offset_event = threading.Event()
        self._bounds = None
        self._bounds_event = threading.Event()
        self._marker_event = threading.Event()

        # Malla resultante de la última nivelación (matriz n×n de Z, o None).
        self.mesh = None
        self.mesh_xs = None
        self.mesh_ys = None
        # Captura de la malla devuelta por el firmware (vía G29 T1, formato CSV).
        self._capturing_mesh = False
        self._mesh_lines = []

        # Capacidades de nivelación detectadas al conectar la impresora. Hasta que
        # se detecten, available=None (desconocido). El frontend se adapta a esto:
        # nº de slots, densidad de malla y sistema son PRINTER-DEPENDIENTES.
        self.caps = {
            "available": None, "system": "unknown", "grid_x": None,
            "grid_y": None, "slots": None, "active_slot": None, "fw": None,
        }
        self._detecting = False
        # flags de parseo durante la detección
        self._cap_system = None
        self._cap_leveling_data = None
        self._cap_slots = None
        self._cap_fw = None
        self._cap_active = None

    # --- llamado desde el hook gcode.received ---
    def on_received(self, line):
        # Sonda de despliegue MANUAL: durante un run con auto-confirmar, contestar
        # las pausas de deploy/stow (PAUSE_BEFORE_DEPLOY_STOW) con UN ÚNICO M108,
        # disparado SOLO tras "echo:busy: paused for user" -> es la señal REAL de
        # Marlin de que está bloqueado esperando (los prompt_* del protocolo de host
        # llegan antes/después y provocaban 3-4 envíos). El flag garantiza uno solo
        # por pausa; se re-arma al reanudar (busy: processing) o en una pausa nueva
        # (prompt_begin). force=True = comando de emergencia -> salta el flow-control.
        if self.running and self._autoconfirm:
            if "busy: processing" in line or "prompt_begin" in line:
                self._pause_acked = False
            elif "paused for user" in line and not self._pause_acked:
                try:
                    self._printer.commands("M108", force=True)
                    self._pause_acked = True
                except Exception:
                    pass
        # Sondeo FALLIDO (punto fuera del plato o la sonda no dispara): Marlin manda
        # "Error:Probing Failed" SIN una línea "Bed X:..". Desbloquear _probe con None
        # para NO colgarnos el timeout y poder saltar el punto y seguir.
        if self.running and "Probing Failed" in line:
            self._bed_result = None
            self._bed_event.set()
            return
        # Lectura puntual de posición (editor manual): solo mientras se pide, para no
        # parsear cada auto-report. No confundir con "Bed X:" (sondeo, otro formato).
        if self._want_pos:
            mp = _POS_RE.search(line)
            if mp:
                self._pos = (float(mp.group(1)), float(mp.group(2)), float(mp.group(3)))
                self._pos_event.set()
        if self._capturing_mesh:
            # Mientras capturamos la malla del firmware (G29 T1) acumulamos las
            # líneas con varios números (filas de la rejilla). El marcador BKLVL_
            # cierra la captura más abajo.
            if "BKLVL_" not in line:
                self._mesh_lines.append(line)
        m = _BED_RE.search(line)
        if m:
            self._bed_result = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
            self._bed_event.set()
            return
        m = _M851_RE.search(line)
        if m:
            self._offset = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
            self._offset_event.set()
            return
        m = _M211_RE.search(line)
        if m:
            self._bounds = (float(m.group(1)), float(m.group(2)),
                            float(m.group(3)), float(m.group(4)))
            self._bounds_event.set()
            return
        m = _M206_RE.search(line)
        if m:
            self._home_offset = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
            self._home_event.set()
            return
        m = _M425_MEAS_RE.search(line)
        if m:
            self._backlash_meas = float(m.group(1))
            self._backlash_event.set()
            return
        # --- Parseo de capacidades de nivelación (durante detect()) ---
        if self._detecting:
            if "LEVELING_DATA:1" in line or "AUTOLEVEL:1" in line:
                self._cap_leveling_data = True
            # M503 reporta una línea "M425 F.. X.. Y.. Z.." si hay compensación de
            # backlash compilada -> el firmware soporta setear/aplicar el backlash.
            if "M425" in line:
                self._cap_backlash = True
            if self._cap_system is None:
                if "Unified Bed Leveling" in line:
                    self._cap_system = "ubl"
                elif "Mesh Bed Leveling" in line:
                    self._cap_system = "mbl"
                elif "Auto Bed Leveling" in line:
                    self._cap_system = "bilinear"
            ms = _SLOTS_RE.search(line)
            if ms:
                self._cap_slots = int(ms.group(1))
            ma = _ACTIVESLOT_RE.search(line)
            if ma:
                self._cap_active = int(ma.group(1))
            mf = _FWNAME_RE.search(line)
            if mf and self._cap_fw is None:
                self._cap_fw = mf.group(1)

        if "BKLVL_" in line:
            self._marker_event.set()

    def state(self):
        return {"running": self.running, "status": self.status, "detail": self.detail}

    def start(self, opts):
        if self.running:
            return False
        self.cancelled = False
        self.running = True
        self._thread = threading.Thread(target=self._run, args=(opts,), daemon=True)
        self._thread.start()
        return True

    def cancel(self):
        self.cancelled = True
        # Parada INMEDIATA: M410 aborta el movimiento en curso y limpia el planner.
        # Además despertamos cualquier espera (sondeo G30 / marcador M118 / M503…)
        # para que el hilo de nivelación salga YA en vez de esperar el timeout.
        try:
            self._printer.commands("M410", force=True)
        except Exception:
            pass
        # Si un sondeo de plato dejó M851 Z desplazado (shift adaptativo), restaurarlo
        # YA (force=True). El finally de _run_plate lo restaura también (idempotente).
        r = self._m851_restore
        if r is not None:
            try:
                self._printer.commands("M851 Z%.3f" % r, force=True)
            except Exception:
                pass
        for ev in (self._bed_event, self._marker_event, self._offset_event,
                   self._bounds_event, self._home_event):
            try:
                ev.set()
            except Exception:
                pass

    def caps_state(self):
        return dict(self.caps)

    # --- Detección de capacidades de nivelación (al conectar la impresora) ---
    def detect(self, delay=2.0):
        """Pregunta a la impresora qué nivelación tiene (sistema, densidad de malla,
        nº de slots). Printer-dependiente: NO asume UBL ni 7×7 ni 4 slots."""
        if self._detecting or self.running:
            return
        threading.Thread(target=self._detect, args=(delay,), daemon=True).start()

    def _detect(self, delay):
        self._detecting = True
        try:
            time.sleep(max(0, delay))  # dar tiempo a que el firmware esté listo
            self._cap_system = None
            self._cap_leveling_data = None
            self._cap_backlash = None
            self._cap_slots = None
            self._cap_fw = None
            self._cap_active = None

            # 1) M115 (capacidades) + M503 (encabezado del sistema de nivelación).
            self._printer.commands(["M115", "M503"])
            time.sleep(3.0)

            system = self._cap_system or ("ubl" if self._cap_leveling_data else "none")
            available = bool(self._cap_system) or bool(self._cap_leveling_data)
            grid = None
            slots = None
            active = self._cap_active

            if available and system == "ubl":
                # nº de slots: store a un slot inválido -> "N mesh slots available"
                self._cap_slots = None
                self._printer.commands(["G29 S250"])
                time.sleep(1.5)
                slots = self._cap_slots
                grid = self._detect_grid("ubl")
            elif available and system in ("bilinear", "mbl"):
                grid = self._detect_grid(system)
                slots = 0  # bilineal/MBL: una sola malla activa (M500), sin slots
            # else: sin nivelación por malla -> available=False

            # Guardar la malla leída por _detect_grid para el visor topográfico
            if grid:
                gx, gy = grid
                rows = []
                for ln in self._mesh_lines:
                    nums = re.findall(r"[+-]?\d+\.\d+", ln)
                    if len(nums) >= gx:
                        rows.append([float(v) for v in nums[:gx]])
                self.log.info("detect: grid=%s rows=%d gy=%d", grid, len(rows), gy)
                if len(rows) >= gy:
                    self.mesh = rows[-gy:]
                    self.mesh_xs = [float(k) for k in range(gx)]
                    self.mesh_ys = [float(k) for k in range(gy)]
                    self.log.info("detect: mesh saved %dx%d, pushing to frontend", gx, gy)
                    try:
                        self._send({"type": "mesh", "mesh": {
                            "n": len(self.mesh), "z": self.mesh,
                            "xs": self.mesh_xs, "ys": self.mesh_ys,
                        }})
                    except Exception as e:
                        self.log.info("detect: mesh push error: %s", e)
                else:
                    self.log.info("detect: not enough rows for mesh (need %d, got %d)", gy, len(rows))

            self.caps = {
                "available": available,
                "system": system,
                "grid_x": grid[0] if grid else None,
                "grid_y": grid[1] if grid else None,
                "slots": slots,
                "active_slot": active,
                "fw": self._cap_fw,
                "backlash": bool(self._cap_backlash),
            }
            self.log.info("Leveling caps: %s", self.caps)
            try:
                self._send({"type": "leveling_caps", "caps": self.caps})
            except Exception:
                pass
        except Exception as e:  # pragma: no cover - defensivo
            self.log.info("detect() error: %s", e)
        finally:
            self._detecting = False

    def _detect_grid(self, system, timeout=12):
        """Cuenta la densidad de la malla (gx, gy) leyendo la rejilla del firmware.
        UBL: 'G29 T1' (CSV). Bilineal/MBL: 'M420 V1'. None si no se puede parsear."""
        cmd = "G29 T1" if system == "ubl" else "M420 V1"
        self._mesh_lines = []
        self._capturing_mesh = True
        try:
            self._wait_marker([cmd], "BKLVL_GRID", timeout)
        finally:
            self._capturing_mesh = False
        counts = []
        for ln in self._mesh_lines:
            nums = re.findall(r"-?\d+\.\d+", ln)
            if len(nums) >= 2:
                counts.append(len(nums))
        if not counts:
            return None
        gx = max(counts)
        gy = sum(1 for c in counts if c == gx)
        return (gx, gy) if gx >= 2 and gy >= 2 else None

    # --- helpers ---
    def _set(self, status, detail=""):
        self.status = status
        self.detail = detail
        self.log.info("Leveling: %s %s", status, detail)
        try:
            self._push(self.state())
        except Exception:
            pass

    def _wait_marker(self, cmds, marker, timeout):
        self._marker_event.clear()
        self._printer.commands(list(cmds) + ["M118 " + marker])
        return self._marker_event.wait(timeout)

    def _query(self, cmd, event, getter, timeout=8):
        event.clear()
        self._printer.commands(cmd)
        if event.wait(timeout):
            return getter()
        return None

    def _probe(self, px, py, timeout=120):
        # Timeout alto: con sonda de despliegue MANUAL (PAUSE_BEFORE_DEPLOY_STOW) cada
        # G30 lleva pausas deploy/stow (auto-M108) y baja lento -> puede tardar decenas
        # de segundos. Un fallo real se resuelve rápido vía "Probing Failed" (on_received).
        self._bed_result = None
        self._bed_event.clear()
        self._printer.commands("G30 X%.2f Y%.2f" % (px, py))
        if self._bed_event.wait(timeout):
            return self._bed_result   # None si el sondeo falló (Probing Failed)
        return None

    def _probe_point(self, px, py, repeats=3, method="median", resolution=0.01):
        """Sondea `repeats` veces el mismo punto y agrega las Z. Devuelve
        `(z, spread)` donde spread=max-min (señal de calidad de la repetición) o
        `(None, None)` si TODAS las repeticiones fallaron (Probing Failed).

        `method`: "median" (robusta a una lectura suelta, por defecto en el plato)
        o "mode" (binning a `resolution`, usada por la vía de cama completa). Cada
        G30 ya promedia 4 descensos internos, así que 3 repeticiones bastan.
        """
        samples = []
        for _ in range(max(1, repeats)):
            if self.cancelled:
                return (None, None)
            res = self._probe(px, py)
            if res is not None:
                samples.append(res[2])
        if not samples:
            return (None, None)
        z = _mode_z(samples, resolution) if method == "mode" else _median(samples)
        return (z, max(samples) - min(samples))

    @staticmethod
    def _center_out_order(pts):
        """Ordena los puntos del centro hacia afuera (por distancia al centroide).
        Los primeros (centrales) suelen ser los más alcanzables -> siembran el plano
        provisional que predice la altura de los puntos exteriores (más inclinados)."""
        if not pts:
            return pts
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        return sorted(pts, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)

    def _probe_shifted(self, X, Y, z0, z_pred, repeats, method, retries, extend):
        """Sondea (X,Y) desplazando `M851 Z` para centrar la ventana de sondeo del
        firmware (piso nativo = -M851.z + Z_PROBE_LOW_POINT, único resorte gcode)
        sobre la altura predicha `z_pred`. Extiende el alcance en inclinación severa.

        Devuelve `(z_en_frame_z0, k_usado, spread)`. La lectura de G30 viene sesgada
        por el offset movido: measured = B + k (firmware) -> Z real (frame z0) =
        measured - (k - z0). Si `z0` es None -> sondeo directo, sin tocar M851.

        Ante `Probing Failed` (samples vacío) SUBE k (baja el piso, más alcance) en
        `extend` mm y reintenta hasta `retries` veces.
        """
        if z0 is None:
            z, spread = self._probe_point(X, Y, repeats, method=method)
            return (z, None, spread)
        # Centrar la ventana ~1.5 mm sobre z_pred; sin predicción aún -> k=z0 (sin cambio).
        if z_pred is not None:
            k = clamp(round(z0 + 1.5 - z_pred, 2), -20.0, 20.0)
        else:
            k = z0
        for _ in range(max(0, retries) + 1):
            if self.cancelled:
                return (None, k, None)
            self._printer.commands("M851 Z%.3f" % k)   # SIN M500 (solo RAM)
            z, spread = self._probe_point(X, Y, repeats, method=method)
            if z is not None:
                return (z - (k - z0), k, spread)
            # Falló: subir k baja el piso nativo (-k + LP) -> más profundidad de sondeo.
            k = clamp(round(k + abs(extend), 2), -20.0, 20.0)
        return (None, k, None)

    @staticmethod
    def _interpolate_missing(mesh):
        """Rellena las celdas None de `mesh` por distancia inversa (IDW) desde las
        celdas ya medidas. Devuelve cuántas se rellenaron. Mutación in-place."""
        n = len(mesh)
        known = [(i, j, mesh[i][j])
                 for i in range(n) for j in range(len(mesh[i]))
                 if mesh[i][j] is not None]
        if not known:
            return 0
        filled = 0
        for i in range(n):
            for j in range(len(mesh[i])):
                if mesh[i][j] is not None:
                    continue
                num = 0.0
                den = 0.0
                for (ki, kj, kz) in known:
                    d2 = (i - ki) ** 2 + (j - kj) ** 2
                    w = 1.0 / d2 if d2 else 1e9
                    num += w * kz
                    den += w
                mesh[i][j] = num / den if den else 0.0
                filled += 1
        return filled

    def _read_fw_mesh(self, n, timeout=20):
        """Pide la malla activa al firmware (G29 T1, formato CSV) y la parsea a una
        matriz n×n. Best-effort: si no se puede parsear, devuelve None."""
        self._mesh_lines = []
        self._capturing_mesh = True
        try:
            if not self._wait_marker(["G29 T1"], "BKLVL_MESH", timeout):
                return None
        finally:
            self._capturing_mesh = False
        rows = []
        for ln in self._mesh_lines:
            nums = re.findall(r"-?\d+\.\d+", ln)
            if len(nums) >= n:
                rows.append([float(v) for v in nums[:n]])
        if len(rows) >= n:
            return rows[-n:]
        return None

    def _run(self, o):
        try:
            system = str(o.get("system", "ubl")).lower()
            method = str(o.get("method", "plugin")).lower()
            self._autoconfirm = bool(o.get("autoconfirm", True))
            self._pause_acked = False   # re-armar el M108-único para este run
            n = int(o.get("grid_n", 7))
            inset = float(o.get("mesh_inset", 1))
            repeats = int(o.get("probe_repeats", 4))
            fade = int(o.get("fade", 10))

            self.mesh = None

            # --- Auto-detección de cama y offset, o valores manuales ---
            if o.get("auto_detect", True):
                self._set("detectando", "leyendo M211 (cama) y M851 (sonda)")
                bounds = self._query("M211", self._bounds_event, lambda: self._bounds)
                offset = self._query("M851", self._offset_event, lambda: self._offset)
                if bounds is None or offset is None:
                    return self._set("error", "no pude leer M211/M851 (auto-detect)")
                bmnx, bmny, bmxx, bmxy = bounds
                offx, offy = offset[0], offset[1]
            else:
                bmnx = float(o.get("bed_min_x", 0)); bmxx = float(o.get("bed_max_x", 100))
                bmny = float(o.get("bed_min_y", 0)); bmxy = float(o.get("bed_max_y", 100))
                offx = float(o.get("probe_offset_x", 0)); offy = float(o.get("probe_offset_y", 0))

            # Zona alcanzable por la SONDA (px tal que el nozzle px-off cae en [min,max])
            reach_min_x = max(bmnx, bmnx + offx)
            reach_max_x = min(bmxx, bmxx + offx)
            reach_min_y = max(bmny, bmny + offy)
            reach_max_y = min(bmxy, bmxy + offy)

            # Puntos de malla (región con inset). Deben coincidir con los puntos UBL
            # del firmware (mismo MESH_INSET y GRID_MAX_POINTS) para que M421 I/J cuadre.
            xs = grid_coords(n, bmnx + inset, bmxx - inset)
            ys = grid_coords(n, bmny + inset, bmxy - inset)
            self.mesh_xs = xs
            self.mesh_ys = ys

            if self.cancelled:
                return self._set("cancelled")

            # Nodos de la malla del FIRMWARE (rejilla fija sobre TODA la cama). El
            # método de plato sondea solo el cristal pero rellena estos nodos.
            n_fw = int(self.caps.get("grid_x") or n)
            xs_fw = grid_coords(n_fw, bmnx + inset, bmxx - inset)
            ys_fw = grid_coords(n_fw, bmny + inset, bmxy - inset)

            if method in ("g30_plate", "plate"):
                # _run_plate gestiona home + M206 + sondeo del plato + ajuste + relleno.
                mesh = self._run_plate(o, n_fw, xs_fw, ys_fw, fade)
            else:
                if o.get("home", True):
                    self._set("homing", "G28 (¿sonda desplegada?)")
                    if not self._wait_marker(["G28"], "BKLVL_HOMED", 180):
                        return self._set("error", "timeout en G28")
                if self.cancelled:
                    return self._set("cancelled")
                if method == "g29":
                    mesh = self._run_g29(system, n, fade)
                else:
                    mesh = self._run_plugin(n, xs, ys, reach_min_x, reach_max_x,
                                            reach_min_y, reach_max_y, repeats, fade)
            if mesh is None:
                return  # _run_* ya fijó el estado (cancelled/error)

            self.mesh = mesh
            self._push_mesh()
            self._apply_backlash(o)   # setea el backlash Z medido si procede
            self._set("done", "%s %dx%d lista y activa (sin guardar — usa un slot)"
                      % (system.upper(), n, n))
        except Exception as e:  # pragma: no cover - defensivo
            self._set("error", str(e))
        finally:
            # IMPORTANTE: poner running=False y RE-EMITIR el estado. Los _set() previos
            # (done/error/cancelled) se emitieron con running=True todavía, así que el
            # frontend nunca recibiría running=False y el botón "Nivelar" quedaría
            # deshabilitado para siempre. Empujamos aquí el estado final ya con
            # running=False para rehabilitarlo.
            self._autoconfirm = False
            self.running = False
            try:
                self._push(self.state())
            except Exception:
                pass

    # --- Vía primaria: sondeo punto a punto desde el plugin (G30 ×4 + moda) ---
    def _run_plugin(self, n, xs, ys, rminx, rmaxx, rminy, rmaxy, repeats, fade):
        mesh = [[None] * n for _ in range(n)]
        total = n * n
        done = 0
        self._set("probing", "0/%d puntos (G30 ×%d, moda)" % (total, repeats))
        self._printer.commands("G1 Z5 F600" )
        for i, X in enumerate(xs):
            for j, Y in enumerate(ys):
                if self.cancelled:
                    self._set("cancelled")
                    return None
                done += 1
                reachable = (rminx <= X <= rmaxx and rminy <= Y <= rmaxy)
                if reachable:
                    z, _ = self._probe_point(X, Y, repeats, method="mode")
                    if z is not None:
                        mesh[i][j] = z
                        self._printer.commands("M421 I%d J%d Z%.3f" % (i, j, z))
                        self._printer.commands("G1  Z5 F600" )
                self._set("probing", "%d/%d puntos" % (done, total))

        # Puntos inalcanzables -> interpolar desde los medidos e inyectar.
        missing = sum(1 for i in range(n) for j in range(n) if mesh[i][j] is None)
        if missing:
            self._set("interpolando", "%d puntos inalcanzables" % missing)
            before = [[mesh[i][j] for j in range(n)] for i in range(n)]
            self._interpolate_missing(mesh)
            for i in range(n):
                for j in range(n):
                    if before[i][j] is None and mesh[i][j] is not None:
                        self._printer.commands("M421 I%d J%d Z%.3f" % (i, j, mesh[i][j]))

        # Activar la malla en RAM para previsualizar (SIN guardar en EEPROM).
        self._set("activando", "M420 S1 (en RAM, sin guardar)")
        if not self._wait_marker(["M420 S1 Z%d" % fade], "BKLVL_ACT", 30):
            self._set("error", "timeout al activar la malla")
            return None
        return mesh

    # --- Vía plato: sondea SOLO el rectángulo del cristal y ajusta un plano ---
    def _run_plate(self, o, n_fw, xs_fw, ys_fw, fade):
        """Sondea la rejilla del plato (más pequeño que la cama), ajusta un plano
        (o alabeo) y escribe los n_fw×n_fw nodos de la malla del firmware por M421.
        Neutraliza M206 durante el run (probe/relleno en coords de máquina)."""
        pa = o.get("probe_area") or {}
        x0 = float(pa.get("x0", 0)); y0 = float(pa.get("y0", 0))
        w = float(pa.get("w", 100)); h = float(pa.get("h", 100))
        pinset = float(pa.get("inset", 5))
        gn = max(2, int(o.get("grid_n_probe", o.get("grid_n", 3))))
        repeats = max(1, int(o.get("probe_repeats", 3)))
        model = str(o.get("model", "plane")).lower()
        central = str(o.get("probe_central", "median")).lower()
        # Altura de despegue entre puntos (mm). Configurable: la sonda de brazo manual
        # cuelga por debajo de la boquilla y arrastra si no se levanta lo suficiente.
        clearance = max(0.5, float(o.get("probe_clearance", 8)))
        # Ajuste robusto (rechazo de bultos de pegamento -> plano inclinado limpio).
        robust = bool(o.get("robust_reject", True))
        reject_k = float(o.get("reject_k", 2.5))
        reject_floor = float(o.get("reject_floor_mm", 0.05))
        reject_ceiling = float(o.get("reject_ceiling_mm", 0.30))
        warp_sigma = float(o.get("warp_smooth_sigma", 0.8))
        warp_strength = float(o.get("warp_smooth_strength", 1.0))
        # Inclinación severa: shift adaptativo de M851 (centro->afuera + predicción).
        order = str(o.get("probe_order", "center_out")).lower()
        predict_seed = max(2, int(o.get("predict_seed", 3)))
        probe_retries = max(0, int(o.get("probe_retries", 1)))
        probe_extend = abs(float(o.get("probe_extend", 3)))

        pxs = grid_coords(gn, x0 + pinset, x0 + w - pinset)
        pys = grid_coords(gn, y0 + pinset, y0 + h - pinset)
        grid_pts = [(X, Y) for X in pxs for Y in pys]
        if order == "center_out":
            grid_pts = self._center_out_order(grid_pts)

        # z0 = offset Z real de la sonda (M851): referencia del shift y valor a
        # restaurar. Preferimos el ya leído en _run (auto-detect); si no, lo pedimos.
        # z0 None (no legible) -> shift deshabilitado (fallback universal-safe).
        z0 = self._offset[2] if self._offset else None
        if z0 is None:
            off = self._query("M851", self._offset_event, lambda: self._offset)
            z0 = off[2] if off else None
        use_shift = z0 is not None

        # Neutralizar M206 solo si hay un offset no nulo Y pudimos leerlo (para
        # restaurarlo). Si no, no lo tocamos (mejor que dejarlo a 0 sin restaurar).
        saved = self._read_home_offset()
        restore = saved if (saved and any(abs(v) > 1e-6 for v in saved)) else None
        if restore is not None:
            self._printer.commands("M206 X0 Y0 Z0")
        try:
            if o.get("home", True):
                self._set("homing", "G28 (¿sonda desplegada?)")
                if not self._wait_marker(["G28"], "BKLVL_HOMED", 180):
                    self._set("error", "timeout en G28"); return None
            if self.cancelled:
                self._set("cancelled"); return None

            pts = []
            total = len(grid_pts)
            done = 0
            if use_shift:
                self._m851_restore = z0   # a restaurar en finally / cancel()
            self._set("probing", "0/%d puntos (plato, %s%s)" % (
                total, central, ", shift M851" if use_shift else ""))
            self._printer.commands("G1 Z%.2f F900" % clearance)
            for (X, Y) in grid_pts:
                if self.cancelled:
                    self._set("cancelled"); return None
                # Predecir la altura del siguiente punto con el plano robusto
                # provisional (centro->afuera) para centrar la ventana de sondeo.
                z_pred = None
                if use_shift and len(pts) >= predict_seed:
                    pf = fit_plane_robust(pts, k=reject_k, floor_mm=reject_floor,
                                          ceiling_mm=reject_ceiling)
                    if pf["inliers"] >= 3:
                        z_pred = pf["a"] * X + pf["b"] * Y + pf["c"]
                z, _k, _sp = self._probe_shifted(
                    X, Y, z0 if use_shift else None, z_pred,
                    repeats, central, probe_retries, probe_extend)
                done += 1
                if z is not None:
                    pts.append((X, Y, z))
                self._printer.commands("G1 Z%.2f F900" % clearance)
                self._set("probing", "%d/%d puntos (plato)" % (done, total))

            if len(pts) < 3:
                self._set("error", "pocos puntos válidos (el plano necesita ≥3)")
                return None

            # Ajuste ROBUSTO: rechaza bultos de pegamento (outliers) -> plano
            # inclinado limpio sin re-limpiar el cristal. Publica métricas a la UI.
            if robust:
                fit = fit_plane_robust(pts, k=reject_k, floor_mm=reject_floor,
                                       ceiling_mm=reject_ceiling)
            else:
                _a, _b, _c = _fit_plane(pts)
                fit = _plane_stats(pts, pts, _a, _b, _c)
            a, b, c = fit["a"], fit["b"], fit["c"]
            try:
                self._send({"type": "leveling_fit", **plane_metrics(fit, pts)})
            except Exception:
                pass

            # Modo alabeo: plano robusto + residuo de los inliers (IDW) suavizado.
            res_pts = ([(x, y, z - (a * x + b * y + c)) for (x, y, z) in fit["inlier_pts"]]
                       if model == "warp" else None)

            def _field(X, Y):
                base = a * X + b * Y + c
                return base + (_idw_residual(X, Y, res_pts) if res_pts is not None else 0.0)

            # Zero-referenciar la malla en el punto de HOMING de Z (centro de la cama =
            # Z_SAFE_HOMING_X/Y_POINT). Con RESTORE_LEVELING_AFTER_G28, tras G28 el Z
            # reportado = -malla(punto_home); si la malla no vale 0 ahí, acabas con
            # Z=-0.1 (y la boquilla rozaría a Z bajo). Restando el valor en ese punto,
            # G28 deja Z=0 y se conserva la corrección de INCLINACIÓN (relativa). Se
            # evalúa el MODELO ROBUSTO (si el home cae en un bulto, ya fue rechazado).
            home_x = (xs_fw[0] + xs_fw[-1]) / 2.0
            home_y = (ys_fw[0] + ys_fw[-1]) / 2.0
            z_home = _field(home_x, home_y)
            self._set("ajustando", "%s + M421 ×%d (ref. cero en el home)" % (
                "alabeo" if model == "warp" else "plano", n_fw * n_fw))

            # Rejilla de nodos. En alabeo, suaviza el residuo (Gauss) y recompone.
            node = [[_field(X, Y) for Y in ys_fw] for X in xs_fw]
            if model == "warp":
                resg = [[node[i][j] - (a * xs_fw[i] + b * ys_fw[j] + c)
                         for j in range(len(ys_fw))] for i in range(len(xs_fw))]
                resg = smooth_grid(resg, sigma_cells=warp_sigma, strength=warp_strength)
                node = [[(a * xs_fw[i] + b * ys_fw[j] + c) + resg[i][j]
                         for j in range(len(ys_fw))] for i in range(len(xs_fw))]

            mesh = [[0.0] * n_fw for _ in range(n_fw)]
            for i, X in enumerate(xs_fw):
                for j, Y in enumerate(ys_fw):
                    z = node[i][j] - z_home
                    mesh[i][j] = z
                    self._printer.commands("M421 I%d J%d Z%.3f" % (i, j, z))
            self.mesh_xs = xs_fw
            self.mesh_ys = ys_fw

            self._set("activando", "M420 S1 (en RAM, sin guardar)")
            if not self._wait_marker(["M420 S1 Z%d" % fade], "BKLVL_ACT", 30):
                self._set("error", "timeout al activar la malla")
                return None
            return mesh
        finally:
            if restore is not None:
                self._printer.commands("M206 X%.3f Y%.3f Z%.3f" % restore)
            # Restaurar M851 Z (shift adaptativo) SIEMPRE, nunca con M500.
            if self._m851_restore is not None:
                self._printer.commands("M851 Z%.3f" % self._m851_restore)
                self._m851_restore = None

    def _read_home_offset(self, timeout=10):
        """Lee el home offset actual (M206) vía M503. Devuelve (x,y,z) o None."""
        return self._query("M503", self._home_event, lambda: self._home_offset, timeout=timeout)

    # --- Vía secundaria: nivelación nativa del firmware (G29) ---
    def _run_g29(self, system, n, fade):
        if system == "ubl":
            self._set("probing", "G29 P1 (UBL %dx%d, firmware)" % (n, n))
            if not self._wait_marker(["G29 P1"], "BKLVL_P1", 600):
                self._set("error", "timeout en G29 P1")
                return None
        else:
            self._set("probing", "G29 (bilineal %dx%d, firmware)" % (n, n))
            if not self._wait_marker(["G29"], "BKLVL_P1", 600):
                self._set("error", "timeout en G29")
                return None
        if self.cancelled:
            self._set("cancelled")
            return None
        self._set("activando", "M420 S1")
        if not self._wait_marker(["M420 S1 Z%d" % fade], "BKLVL_ACT", 30):
            self._set("error", "timeout al activar la malla")
            return None
        # Leer la malla del firmware para poder visualizarla (best-effort).
        return self._read_fw_mesh(n) or [[0.0] * n for _ in range(n)]

    def _push_mesh(self):
        try:
            self._send({"type": "mesh", "mesh": {
                "n": len(self.mesh) if self.mesh else 0,
                "z": self.mesh,
                "xs": self.mesh_xs,
                "ys": self.mesh_ys,
            }})
        except Exception:
            pass

    def _apply_backlash(self, o):
        """Setea el backlash de Z medido durante los G30, si el firmware lo soporta
        y el usuario lo pide. El firmware SOLO mide (measure_with_probe); para que
        surta efecto y persista hacen falta: `M425 Z` (copia la media medida a la
        distancia de compensación), `M425 F1.0` (activa el factor, por defecto 0.0)
        y `M500` (al guardar la malla). Universal: si el fw no lo reporta o la media
        es ~0, se omite en silencio. Empuja el valor a la UI en cualquier caso."""
        if not o.get("apply_backlash", True) or not self.caps.get("backlash"):
            return None
        self._backlash_meas = None
        self._backlash_event.clear()
        try:
            self._printer.commands("M425")   # reporta la media ya medida en los G30
        except Exception:
            return None
        self._backlash_event.wait(6)
        z = self._backlash_meas
        applied = False
        if z is not None and abs(z) > 0.01:
            try:
                self._printer.commands(["M425 Z", "M425 F1.0"])
                applied = True
                self.log.info("Backlash Z aplicado: %.3f mm (M425 Z + M425 F1.0)", z)
            except Exception:
                pass
        try:
            self._send({"type": "leveling_backlash",
                        "z": z if z is not None else 0.0, "applied": applied})
        except Exception:
            pass
        return z if applied else None

    def read_z(self, timeout=6):
        """Lee la Z actual de la máquina vía M114 (para el editor manual: "Fijar").
        Devuelve float o None. Universal (M114 es estándar)."""
        self._pos = None
        self._want_pos = True
        self._pos_event.clear()
        try:
            self._printer.commands("M114")
        except Exception:
            self._want_pos = False
            return None
        ok = self._pos_event.wait(timeout)
        self._want_pos = False
        return self._pos[2] if (ok and self._pos) else None

    # --- Slots: guardar / activar / leer la malla actual (llamado desde __init__) ---
    def save_slot(self, slot):
        """Persiste la malla activa en el slot UBL `slot` (G29 S<slot> + M500).

        Devuelve la matriz Z de la malla recién nivelada (para que el plugin la
        guarde como copia y poder visualizarla luego sin activarla), o None si no
        hay malla disponible.
        """
        self._printer.commands(["G29 S%d" % int(slot), "M500"])
        return self.mesh

    def activate_slot(self, slot, fade=10):
        """Carga y activa la malla del slot UBL `slot` (G29 L<slot> + M420 S1)."""
        self._printer.commands(["G29 L%d" % int(slot), "M420 S1 Z%d" % int(fade)])
