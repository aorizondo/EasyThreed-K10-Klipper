# coding=utf-8
"""
Rastreador de posición mode-aware (Módulo 2 de Boost Kit).

Corrige el bug de OctoPrint: su tracker de Z (_gcode_G0_sent en comm.py) lee el
parámetro Z de cada G0/G1 y lo guarda DIRECTO como Z absoluto, SIEMPRE, sin tener
ningún concepto de modo relativo (no tiene handlers de G90/G91/G92/M82/M83). En
modo relativo (G91, típico de tests en espiral/vase) eso da una Z totalmente
errónea y rompe la detección de capas.

Esta clase mantiene el modelo LÓGICO de posición igual que lo haría el firmware:
- G90/G91: absoluto/relativo para X/Y/Z (y E).
- M82/M83: absoluto/relativo para E independientemente.
- G92: redefine la posición lógica actual.
Y deriva una Z absoluta correcta + un contador de capas básico.
"""
from __future__ import absolute_import

import re

_FLOAT = r"[-+]?[0-9]*\.?[0-9]+"

# Ejes ADICIONALES (>3) que rastreamos genéricamente además de X/Y/Z/E.
# Se eligen A/B/C/U/V/W (los nombres por defecto de Marlin para 4º-9º eje) y NO
# I/J/K porque esas letras son parámetros de centro de arco en G2/G3 (y P/Q en
# G5); meterlas aquí confundiría arcos con ejes. Cubre brazos, polares y rotativos.
_EXTRA_AXES = ("A", "B", "C", "U", "V", "W")


def _axis(cmd, axis):
    m = re.search(r"(?:^|[^A-Za-z])%s(%s)" % (axis, _FLOAT), cmd)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


class PositionTracker:
    def __init__(self, logger, layer_threshold=0.04, planar=True):
        self.log = logger
        self.layer_threshold = layer_threshold
        # planar=True: cinemática cartesiana planar -> el contador de capa por Z
        # es válido. planar=False (no planar / multi-eje): la capa NO se infiere de
        # la geometría (Z no es monótona), se delega a los marcadores del slicer.
        self.planar = planar
        self.reset()

    def reset(self):
        # Marlin arranca en absoluto para todos los ejes (G90).
        self.abs_xyz = True
        self.abs_e = True
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.e = 0.0
        self.f = None
        # Posición FÍSICA acumulada: suma de los deltas de G0/G1, IGNORANDO G92.
        # px/py/pz/pe NO se redefinen con G92 (que cambia el datum lógico sin mover),
        # así que (px - px_en_pausa) = desplazamiento físico real desde la pausa.
        # Sirve para "volver a la posición de pausa" de forma inmune a G92.
        self.px = 0.0
        self.py = 0.0
        self.pz = 0.0
        self.pe = 0.0
        # Ejes adicionales (A/B/C/U/V/W) presentes en el gcode -> {letra: valor}.
        # Solo se crean cuando aparecen; en cartesiana clásica queda vacío.
        self.axes = {}
        self.max_z = 0.0          # = altura física (height), siempre correcta
        self.layer = 0
        self.last_layer_z = None  # Z de la última capa CONTADA (por extrusión)
        self.printing = False     # último movimiento extruía
        self.vase = False         # heurística: subiendo Z mientras extruye+XY
        self.last_dist = 0.0      # distancia XYZ del último movimiento (mm)
        self.last_e_delta = 0.0   # extrusión del último movimiento (mm)
        self.last_arc = None      # geometría del último arco (para recolocación)

    # ---- modos ----
    def set_abs_xyz(self, value):
        # G90 pone TODO absoluto, G91 todo relativo (incluida E), igual que Marlin.
        self.abs_xyz = value
        self.abs_e = value

    def set_e_abs(self, value):
        # M82/M83 anulan solo el modo de E.
        self.abs_e = value

    def set_planar(self, value):
        # Cambia el modo de inferencia de capa (cartesiana planar vs no planar).
        self.planar = bool(value)

    def _apply_extra_axes(self, cmd, absolute):
        """Actualiza self.axes con los ejes A/B/C/U/V/W presentes en cmd,
        respetando el modo absoluto/relativo. Solo crea claves que aparezcan."""
        for ax in _EXTRA_AXES:
            v = _axis(cmd, ax)
            if v is None:
                continue
            if absolute:
                self.axes[ax] = v
            else:
                self.axes[ax] = self.axes.get(ax, 0.0) + v

    def set_position(self, cmd):
        # G92: redefine la posición lógica actual (sin mover).
        any_named = False
        for axis, attr in (("X", "x"), ("Y", "y"), ("Z", "z"), ("E", "e")):
            v = _axis(cmd, axis)
            if v is not None:
                setattr(self, attr, v)
                any_named = True
        for ax in _EXTRA_AXES:
            v = _axis(cmd, ax)
            if v is not None:
                self.axes[ax] = v
                any_named = True
        # G92 sin parámetros = pone todo a 0 (incluidos los ejes extra ya vistos)
        if not any_named:
            self.x = self.y = self.z = self.e = 0.0
            for ax in list(self.axes):
                self.axes[ax] = 0.0

    # ---- movimiento ----
    def feed_move(self, cmd):
        """Procesa un G0/G1. Devuelve True si la Z cambió."""
        prev_z, prev_x, prev_y, prev_e = self.z, self.x, self.y, self.e

        for axis, attr in (("X", "x"), ("Y", "y"), ("Z", "z")):
            v = _axis(cmd, axis)
            if v is not None:
                if self.abs_xyz:
                    setattr(self, attr, v)
                else:
                    setattr(self, attr, getattr(self, attr) + v)

        ev = _axis(cmd, "E")
        if ev is not None:
            self.e = ev if self.abs_e else self.e + ev

        # Ejes adicionales (A/B/C/U/V/W): mismo modo abs/rel que XYZ.
        self._apply_extra_axes(cmd, self.abs_xyz)

        fv = _axis(cmd, "F")
        if fv is not None:
            self.f = fv

        dz = self.z - prev_z
        dx = self.x - prev_x
        dy = self.y - prev_y
        # Acumulador FÍSICO: un G0/G1 mueve físicamente lo mismo que cambia la lógica.
        # (El G92 va por set_position y NO toca px/py/pz/pe -> inmune a G92.)
        self.px += dx
        self.py += dy
        self.pz += dz
        self.pe += self.e - prev_e
        xy_moved = abs(dx) > 1e-4 or abs(dy) > 1e-4
        extruding = (self.e - prev_e) > 1e-5
        self.printing = extruding
        self.last_dist = (dx * dx + dy * dy + dz * dz) ** 0.5
        self.last_e_delta = self.e - prev_e

        # vase/espiral: Z sube de forma continua MIENTRAS se extruye y hay XY.
        if dz > 1e-6 and extruding and xy_moved:
            self.vase = True
        elif dz > 1e-6 and not extruding:
            self.vase = False  # un lift puro = cambio de capa clásico

        if self.z > self.max_z:
            self.max_z = self.z

        self._update_layer(dz, extruding)
        return self.z != prev_z

    def feed_arc(self, cmd, clockwise):
        """Procesa un G2/G3 (arco). Interpola el arco en segmentos de ~1mm y
        actualiza la posición al punto final + cuenta capa. Devuelve la lista de
        segmentos [(x, y, z, extrudeFlag, dist, e_delta), ...] para el trail, o
        None si no hay info de centro (I/J/R) -> que el llamador lo trate como G1."""
        import math
        sx, sy, sz, se = self.x, self.y, self.z, self.e

        fv = _axis(cmd, "F")
        if fv is not None:
            self.f = fv

        def tgt(axis, cur):
            v = _axis(cmd, axis)
            if v is None:
                return cur
            return v if self.abs_xyz else cur + v

        nx, ny, nz = tgt("X", sx), tgt("Y", sy), tgt("Z", sz)
        ev = _axis(cmd, "E")
        ne = (ev if self.abs_e else se + ev) if ev is not None else se

        i, j, r = _axis(cmd, "I"), _axis(cmd, "J"), _axis(cmd, "R")
        if i is not None or j is not None:
            cx, cy = sx + (i or 0.0), sy + (j or 0.0)
        elif r is not None:
            # forma por radio: centro perpendicular a la cuerda
            mx, my = (sx + nx) / 2.0, (sy + ny) / 2.0
            dx, dy = nx - sx, ny - sy
            q = math.hypot(dx, dy)
            if q < 1e-6:
                return None
            h = (r * r) - (q / 2.0) ** 2
            h = math.sqrt(h) if h > 0 else 0.0
            sign = 1.0 if ((r > 0) ^ clockwise) else -1.0
            cx = mx + sign * h * (-dy / q)
            cy = my + sign * h * (dx / q)
        else:
            return None

        rad = math.hypot(sx - cx, sy - cy)
        a0 = math.atan2(sy - cy, sx - cx)
        a1 = math.atan2(ny - cy, nx - cx)
        if clockwise:
            while a1 >= a0:
                a1 -= 2 * math.pi
        else:
            while a1 <= a0:
                a1 += 2 * math.pi
        sweep = a1 - a0
        arc_len = abs(sweep) * rad
        n = max(2, min(160, int(arc_len) + 1))   # ~1mm por segmento, tope 160

        segs = []
        px, py, pz, pe = sx, sy, sz, se
        for k in range(1, n + 1):
            frac = k / float(n)
            a = a0 + sweep * frac
            qx = cx + rad * math.cos(a)
            qy = cy + rad * math.sin(a)
            qz = sz + (nz - sz) * frac
            qe = se + (ne - se) * frac
            d = math.sqrt((qx - px) ** 2 + (qy - py) ** 2 + (qz - pz) ** 2)
            ed = qe - pe
            segs.append((round(qx, 3), round(qy, 3), round(qz, 3),
                         1 if ed > 1e-6 else 0, d, ed))
            px, py, pz, pe = qx, qy, qz, qe

        # Geometría del arco para la recolocación del wrapper de pausa instantánea:
        # centro absoluto, sentido, barrido total y radio.
        self.last_arc = {
            "cx": cx, "cy": cy, "cw": bool(clockwise), "rad": rad,
            "a0": a0, "sweep": sweep,
        }
        # estado final + capa
        self.x, self.y, self.z, self.e = nx, ny, nz, ne
        self._apply_extra_axes(cmd, self.abs_xyz)
        self.last_dist = arc_len
        self.last_e_delta = ne - se
        if self.z > self.max_z:
            self.max_z = self.z
        self._update_layer(nz - sz, (ne - se) > 1e-5)
        self.printing = (ne - se) > 1e-5
        return segs

    def feed_bezier(self, cmd):
        """G5: spline cúbica de Bézier. I/J = control 1 (relativo al inicio),
        P/Q = control 2 (relativo al final), X/Y = final. Interpola en segmentos."""
        sx, sy, sz, se = self.x, self.y, self.z, self.e
        fv = _axis(cmd, "F")
        if fv is not None:
            self.f = fv

        def tgt(axis, cur):
            v = _axis(cmd, axis)
            return (v if self.abs_xyz else cur + v) if v is not None else cur

        nx, ny, nz = tgt("X", sx), tgt("Y", sy), tgt("Z", sz)
        ev = _axis(cmd, "E")
        ne = (ev if self.abs_e else se + ev) if ev is not None else se
        i, j, p, q = _axis(cmd, "I"), _axis(cmd, "J"), _axis(cmd, "P"), _axis(cmd, "Q")
        if None in (i, j, p, q):
            return None
        c1x, c1y = sx + i, sy + j
        c2x, c2y = nx + p, ny + q
        chord = ((nx - sx) ** 2 + (ny - sy) ** 2) ** 0.5
        n = max(8, min(160, int(chord) + 4))
        segs = []
        px, py, pz, pe = sx, sy, sz, se
        for k in range(1, n + 1):
            tt = k / float(n)
            u = 1 - tt
            bx = (u**3) * sx + 3 * u * u * tt * c1x + 3 * u * tt * tt * c2x + tt**3 * nx
            by = (u**3) * sy + 3 * u * u * tt * c1y + 3 * u * tt * tt * c2y + tt**3 * ny
            bz = sz + (nz - sz) * tt
            be = se + (ne - se) * tt
            d = ((bx - px) ** 2 + (by - py) ** 2 + (bz - pz) ** 2) ** 0.5
            ed = be - pe
            segs.append((round(bx, 3), round(by, 3), round(bz, 3),
                         1 if ed > 1e-6 else 0, d, ed))
            px, py, pz, pe = bx, by, bz, be
        self.x, self.y, self.z, self.e = nx, ny, nz, ne
        self._apply_extra_axes(cmd, self.abs_xyz)
        self.last_dist = sum(s[4] for s in segs)
        self.last_e_delta = ne - se
        if self.z > self.max_z:
            self.max_z = self.z
        self._update_layer(nz - sz, (ne - se) > 1e-5)
        self.printing = (ne - se) > 1e-5
        return segs

    def _update_layer(self, dz, extruding):
        """Cuenta capas por PROGRESIÓN DE EXTRUSIÓN en altura (robusto a travels
        y a modo relativo). Una capa nueva = se empieza a extruir a una Z mayor
        que la última capa contada por al menos `layer_threshold`. Para vase, eso
        equivale a ~1 capa por cada `layer_threshold` de altura ganada extruyendo.
        Los lifts/travels (sin extrusión) NUNCA cuentan capa.

        Solo válido en cinemática planar: si planar=False (no planar / multi-eje),
        Z no es monótona dentro de una capa y este conteo sería basura -> se omite
        y la capa se toma de los marcadores del slicer (índice).
        """
        if not self.planar:
            return
        if not extruding:
            return
        ref = self.last_layer_z
        if ref is None or self.z >= ref + self.layer_threshold:
            self.layer += 1
            self.last_layer_z = self.z

    def state(self):
        st = {
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "z": round(self.z, 3),
            "e": round(self.e, 3),
            "layer": self.layer,
            "height": round(self.max_z, 3),
            "abs_xyz": self.abs_xyz,
            "abs_e": self.abs_e,
            "vase": self.vase,
            "printing": self.printing,
            "planar": self.planar,
        }
        if self.axes:
            st["axes"] = {k: round(v, 3) for k, v in self.axes.items()}
        return st

    def snapshot(self):
        """Copia inmutable del modo y posición — para restaurar al reanudar tras
        una pausa (durante la cual el jogging puede haber cambiado el modo).
        Incluye los ejes extra para reanudación cinemática-segura en >4 ejes."""
        return {
            "x": self.x, "y": self.y, "z": self.z, "e": self.e,
            "abs_xyz": self.abs_xyz, "abs_e": self.abs_e, "f": self.f,
            "axes": dict(self.axes),
            "px": self.px, "py": self.py, "pz": self.pz, "pe": self.pe,
        }
