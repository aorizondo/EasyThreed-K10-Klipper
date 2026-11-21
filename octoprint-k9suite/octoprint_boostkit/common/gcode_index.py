# coding=utf-8
"""
Pre-análisis (índice) del fichero gcode para control de trabajo PORTABLE.

En UNA sola pasada por el fichero construye:
  - checkpoints: puntos reanudables (capas/secciones del slicer; o geométricos si la
    cinemática es planar y no hay marcadores) con un VECTOR DE ESTADO acumulado
    (modal, posición de TODOS los ejes, temperaturas, ventilador, herramienta, feed).
  - objects/segments: objetos imprimibles y los rangos de offset de byte que ocupan
    en el fichero, para cancel-object (M486 nativo o supresión por rango).
  - caps: capacidades detectadas (multi-eje, no planar, ejes extra presentes).

Principios:
  * Los MARCADORES del slicer son la fuente de verdad. La geometría (contar capas por
    progresión de Z) es solo un fallback válido bajo cinemática PLANAR-cartesiana.
  * El offset que se guarda es el `pos` estilo OctoPrint: bytes UTF-8 acumulados al
    FINAL de cada línea (igual que PrintingGcodeFileInformation.getNext -> filepos),
    de modo que el hook gcode.queuing pueda casar `filepos:<N>` con un rango.
"""
from __future__ import absolute_import

import re

from .tracker import PositionTracker, _axis, _EXTRA_AXES

# --- Marcadores de capa/sección por slicer. (numbered=True -> el grupo 1 es el nº;
#     numbered=False -> se cuenta cada aparición). Portados de DisplayLayerProgress. ---
_LAYER_MARKERS = [
    (re.compile(r";\s*LAYER:\s*(-?[0-9]+)", re.I), True),    # Cura / ideaMaker
    (re.compile(r";\s*layer\s+([0-9]+),", re.I), True),      # Simplify3D
    (re.compile(r";\s*LAYER_CHANGE", re.I), False),          # PrusaSlicer / Orca / Super
    (re.compile(r";\s*BEFORE_LAYER_CHANGE", re.I), False),   # Slic3r
    (re.compile(r";\s*BEGIN_LAYER_OBJECT", re.I), False),    # KISSlicer
]

# --- Marcadores de objeto imprimible (cancel-object). Portados de CancelObject. ---
_OBJ_START = [
    re.compile(r";\s*MESH:\s*(.+?)\s*$", re.I),              # Cura  (NONMESH = sin objeto)
    re.compile(r";\s*printing object\s+(.+?)\s*$", re.I),    # PrusaSlicer / SuperSlicer
    re.compile(r";\s*PRINTING:\s*(.+?)\s*$", re.I),          # ideaMaker
]
_OBJ_STOP = re.compile(r";\s*stop printing object", re.I)
# Host-command @Object <name> que inyecta el plugin CancelObject al subir (reescribe
# los ;MESH:/; printing object). Lo reconocemos para funcionar con o sin ese plugin.
_OBJ_AT = re.compile(r"@Object\s+(.+?)\s*$", re.I)
# M486: S<idx> selecciona objeto actual, T<n> total, P/U/C cancelar/deshacer/cancelar-actual.
_M486 = re.compile(r"M486\s+([STPUC])\s*(-?[0-9]+)?", re.I)

# Temperaturas / ventilador / herramienta (estado a restaurar en skip/resume).
_RE_TOOLTEMP = re.compile(r"M10[49]\b", re.I)   # M104/M109 (hotend)
_RE_BEDTEMP = re.compile(r"M1[49]0\b", re.I)    # M140/M190 (cama)
_RE_FANON = re.compile(r"M106\b", re.I)
_RE_FANOFF = re.compile(r"M107\b", re.I)
_RE_TOOL = re.compile(r"^T(\d+)", re.I)


def _gword(cmd):
    """Devuelve el token de gcode normalizado (G1, M104, T0...) o ''."""
    m = re.match(r"\s*([GMT]\d+)", cmd, re.I)
    return m.group(1).upper() if m else ""


class GcodeIndex(object):
    def __init__(self, path):
        self.path = path
        self.checkpoints = []   # [{pos,line,layer,z,label,kind,state}]
        self.objects = []       # [{index,name}]
        self.segments = []      # [(start_pos, end_pos, obj_index)] ordenados por start
        self.caps = {"multiaxis": False, "non_planar": False, "extra_axes": []}
        self.has_markers = False    # ¿hubo marcadores de capa del slicer?
        self.has_m486 = False       # ¿el fichero ya trae comandos M486?
        self.total_layers = 0
        self.planar = True          # planaridad usada para construir el índice

    # ---- consulta para el hook gcode.queuing ----
    def object_at(self, pos):
        """Índice de objeto activo en el offset `pos` (o None si está entre objetos)."""
        # segments está ordenado por start; búsqueda lineal acotada (los segmentos
        # son pocos por capa). Para ficheros enormes podría binarizarse.
        for start, end, oi in self.segments:
            if start <= pos < end:
                return oi
        return None

    def checkpoint_for_layer(self, layer):
        best = None
        for cp in self.checkpoints:
            if cp.get("layer") is not None and cp["layer"] <= layer:
                best = cp
            if cp.get("layer") == layer:
                return cp
        return best

    def summary(self):
        return {
            "objects": [dict(o) for o in self.objects],
            "checkpoints": [
                {"pos": c["pos"], "line": c["line"], "layer": c.get("layer"),
                 "z": c.get("z"), "label": c.get("label"), "kind": c.get("kind")}
                for c in self.checkpoints
            ],
            "caps": dict(self.caps),
            "has_markers": self.has_markers,
            "has_m486": self.has_m486,
            "total_layers": self.total_layers,
            "planar": self.planar,
        }


def build_index(path, logger=None, layer_threshold=0.04, max_bytes=None):
    """Recorre `path` y devuelve un GcodeIndex. `max_bytes` (opcional) limita la
    cantidad analizada para ficheros gigantes (los checkpoints más profundos
    quedarían fuera; se registra en el log)."""
    idx = GcodeIndex(path)
    # Tracker en modo planar para mantener posición; la capa geométrica se usará solo
    # como fallback al final si no hubo marcadores y la cinemática es planar.
    tr = PositionTracker(logger, layer_threshold=layer_threshold, planar=True)

    # Estado acumulado a restaurar:
    hotend = None
    bed = None
    fan = 0
    tool = 0
    units_mm = True

    marker_layer = 0          # nº de capa por marcadores
    geo_checkpoints = []      # checkpoints geométricos (fallback)
    saw_layer_marker = False
    z_off_plane = 0           # nº de moves que EXTRUYEN por debajo del plano de capa
    extra_seen = set()

    # Objetos (cancel-object): mapa nombre->índice + segmentos por offset.
    obj_by_name = {}
    cur_obj = None            # índice de objeto en curso
    seg_start = 0             # offset de inicio del segmento actual

    def capture_state(pos, line, layer, z, label, kind):
        return {
            "pos": pos, "line": line, "layer": layer, "z": z,
            "label": label, "kind": kind,
            "state": {
                "abs_xyz": tr.abs_xyz, "abs_e": tr.abs_e, "units_mm": units_mm,
                "x": round(tr.x, 4), "y": round(tr.y, 4), "z": round(tr.z, 4),
                "e": round(tr.e, 5), "f": tr.f,
                "axes": {k: round(v, 4) for k, v in tr.axes.items()},
                "hotend": hotend, "bed": bed, "fan": fan, "tool": tool,
            },
        }

    def close_segment(pos):
        if cur_obj is not None and pos > seg_start:
            idx.segments.append((seg_start, pos, cur_obj))

    # Replica EXACTA del cálculo de offset de OctoPrint (utf-8-sig, pos en bytes utf-8).
    pos = 0
    line_no = 0
    try:
        handle = open(path, encoding="utf-8-sig", errors="replace", newline="")
    except (OSError, IOError) as e:
        if logger:
            logger.warning("No se pudo abrir para indexar %s: %s", path, e)
        return idx

    with handle:
        for raw in handle:
            start_pos = pos
            pos += len(raw.encode("utf-8"))
            if max_bytes and start_pos > max_bytes:
                if logger:
                    logger.info("Índice truncado en %d bytes (fichero grande)", max_bytes)
                break

            # Separar comando y comentario.
            hashpos = raw.find(";")
            cmd = (raw if hashpos < 0 else raw[:hashpos]).strip()
            comment = "" if hashpos < 0 else raw[hashpos:]

            # ---- Marcadores de comentario (capa / objeto) ----
            if comment:
                # Objetos
                handled_obj = False
                if _OBJ_STOP.search(comment):
                    close_segment(pos)
                    cur_obj = None
                    seg_start = pos
                    handled_obj = True
                if not handled_obj:
                    for rx in _OBJ_START:
                        m = rx.search(comment)
                        if not m:
                            continue
                        name = m.group(1).strip()
                        if name.upper() == "NONMESH":   # Cura: hueco entre objetos
                            close_segment(pos)
                            cur_obj = None
                            seg_start = pos
                        else:
                            if name not in obj_by_name:
                                obj_by_name[name] = len(idx.objects)
                                idx.objects.append({"index": len(idx.objects),
                                                    "name": name})
                            new_obj = obj_by_name[name]
                            if new_obj != cur_obj:
                                close_segment(pos)
                                cur_obj = new_obj
                                seg_start = pos
                        break

                # Capas / secciones
                for rx, numbered in _LAYER_MARKERS:
                    m = rx.search(comment)
                    if not m:
                        continue
                    saw_layer_marker = True
                    if numbered:
                        try:
                            marker_layer = int(m.group(1))
                        except (TypeError, ValueError):
                            marker_layer += 1
                    else:
                        marker_layer += 1
                    idx.checkpoints.append(capture_state(
                        pos, line_no, marker_layer, round(tr.max_z, 3),
                        "Capa %d" % marker_layer, "layer"))
                    break

            if not cmd:
                continue
            line_no += 1

            # Host-command @Object <name> (CancelObject): frontera de objeto.
            mo = _OBJ_AT.match(cmd)
            if mo:
                name = mo.group(1).strip()
                if name.upper() == "NONMESH":
                    close_segment(pos)
                    cur_obj = None
                    seg_start = pos
                else:
                    if name not in obj_by_name:
                        obj_by_name[name] = len(idx.objects)
                        idx.objects.append({"index": len(idx.objects), "name": name})
                    if obj_by_name[name] != cur_obj:
                        close_segment(pos)
                        cur_obj = obj_by_name[name]
                        seg_start = pos
                continue

            g = _gword(cmd)

            # ---- Estado modal / temperaturas / ventilador / herramienta ----
            if g == "G90":
                tr.set_abs_xyz(True)
            elif g == "G91":
                tr.set_abs_xyz(False)
            elif g == "M82":
                tr.set_e_abs(True)
            elif g == "M83":
                tr.set_e_abs(False)
            elif g == "G20":
                units_mm = False
            elif g == "G21":
                units_mm = True
            elif g == "G92":
                tr.set_position(cmd)
            elif g in ("G0", "G1"):
                tr.feed_move(cmd)
                # No-planar REAL = se extruye por DEBAJO del plano de la capa actual.
                # Un Z-hop (lift sin extrusión) y su de-hop retornan al plano ANTES de
                # extruir, así que terminan AL nivel de la capa -> nunca dispara esto.
                # Incluso un de-hop combinado con extrusión en el mismo G1 acaba en el
                # plano de capa. Solo el corte cónico/no planar extruye fuera de plano.
                if (tr.printing and tr.last_layer_z is not None
                        and tr.z < tr.last_layer_z - 0.08):
                    z_off_plane += 1
                if tr.axes:
                    extra_seen.update(tr.axes.keys())
                # Fallback geométrico de capa: registra checkpoint en cada incremento.
                if not saw_layer_marker and tr.layer > len(geo_checkpoints):
                    geo_checkpoints.append(capture_state(
                        pos, line_no, tr.layer, round(tr.max_z, 3),
                        "Capa %d" % tr.layer, "geo"))
            elif g in ("G2", "G3"):
                segs = tr.feed_arc(cmd, g == "G2")
                if segs is None:
                    tr.feed_move(cmd)
                if tr.axes:
                    extra_seen.update(tr.axes.keys())
            elif g == "G5":
                if tr.feed_bezier(cmd) is None:
                    tr.feed_move(cmd)
            elif _RE_TOOLTEMP.match(cmd):
                s = _axis(cmd, "S")
                if s is not None:
                    hotend = s
            elif _RE_BEDTEMP.match(cmd):
                s = _axis(cmd, "S")
                if s is not None:
                    bed = s
            elif _RE_FANOFF.match(cmd):
                fan = 0
            elif _RE_FANON.match(cmd):
                s = _axis(cmd, "S")
                fan = s if s is not None else 255
            elif _M486.match(cmd):
                idx.has_m486 = True
                mm = _M486.match(cmd)
                tok, num = mm.group(1).upper(), mm.group(2)
                if tok == "S" and num is not None:
                    n = int(num)
                    if n >= 0:
                        close_segment(pos)
                        cur_obj = n
                        seg_start = pos
                        # asegura que el objeto exista en la lista
                        while len(idx.objects) <= n:
                            idx.objects.append({"index": len(idx.objects),
                                                "name": "Objeto %d" % len(idx.objects)})
                    else:                       # S-1 => fuera de objeto
                        close_segment(pos)
                        cur_obj = None
                        seg_start = pos
            else:
                tm = _RE_TOOL.match(cmd)
                if tm:
                    tool = int(tm.group(1))

    # cerrar último segmento
    close_segment(pos)

    # ---- Resolver capacidades + fuente de capa ----
    idx.caps["extra_axes"] = sorted(extra_seen)
    idx.caps["multiaxis"] = bool(extra_seen)
    idx.caps["non_planar"] = z_off_plane > 8
    idx.has_markers = saw_layer_marker
    idx.planar = not (idx.caps["multiaxis"] or idx.caps["non_planar"])

    if saw_layer_marker:
        idx.total_layers = marker_layer
    elif idx.planar:
        # Sin marcadores pero cinemática planar -> usar checkpoints geométricos.
        idx.checkpoints = geo_checkpoints
        idx.total_layers = tr.layer
    else:
        # No planar / multi-eje y sin marcadores: no hay capas fiables. Skip-to solo
        # podrá ofrecerse por objeto/offset (la UI lo informa).
        idx.checkpoints = []
        idx.total_layers = 0

    if logger:
        logger.info(
            "Índice: %d checkpoints, %d objetos, planar=%s, multiaxis=%s, "
            "non_planar=%s, m486=%s",
            len(idx.checkpoints), len(idx.objects), idx.planar,
            idx.caps["multiaxis"], idx.caps["non_planar"], idx.has_m486)
    return idx
