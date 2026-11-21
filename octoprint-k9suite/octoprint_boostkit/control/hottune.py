# coding=utf-8
"""
Geometría del wrapper de pausa instantánea (hot-tune).

Funciones PURAS (testables) para:
  - emparejar la pose real (M114) con el movimiento del job que estaba en curso,
  - calcular su progreso `f`,
  - sintetizar el GCODE de FINALIZACIÓN del movimiento interrumpido (lineal o arco
    PARCIAL), con la E restante proporcional y soporte de >4 ejes (A/B/C/U/V/W).

El motor del ciclo (M410/M114 R E/seek/...) vive en __init__.py porque toca el
`comm` de OctoPrint; aquí solo va la matemática.

Formato de un movimiento del ring buffer (dict):
  {
    "kind": "line" | "arc",
    "start_offset": int, "end_offset": int,
    "start": pose, "end": pose,           # pose = {"x","y","z","e","axes":{...}}
    "e_total": float, "feedrate": float,
    "arc": {"cx","cy","cw","rad","a0","sweep"}   # solo si kind == "arc"
  }
"""
from __future__ import absolute_import

import math

# Ejes de MOVIMIENTO (no E) para proyección/distancia.
_MOVE_AXES = ("x", "y", "z")


def _motion(pose):
    """Coordenadas de movimiento de una pose: X/Y/Z + ejes extra (sin E)."""
    d = {a: float(pose.get(a, 0.0) or 0.0) for a in _MOVE_AXES}
    for k, v in (pose.get("axes") or {}).items():
        d[k] = float(v or 0.0)
    return d


def _project(start, end, p):
    """Proyección de p sobre el segmento start→end. Devuelve (t, perp) o None."""
    keys = set(start) | set(end) | set(p)
    num = den = 0.0
    for k in keys:
        d = end.get(k, 0.0) - start.get(k, 0.0)
        num += (p.get(k, 0.0) - start.get(k, 0.0)) * d
        den += d * d
    if den < 1e-9:
        return None
    t = num / den
    perp = 0.0
    for k in keys:
        proj = start.get(k, 0.0) + t * (end.get(k, 0.0) - start.get(k, 0.0))
        perp += (p.get(k, 0.0) - proj) ** 2
    return t, math.sqrt(perp)


def _arc_frac(arc, p):
    """Fracción de barrido recorrida hasta p en un arco + desviación radial."""
    cx, cy, rad, a0, sweep = arc["cx"], arc["cy"], arc["rad"], arc["a0"], arc["sweep"]
    dx, dy = p["x"] - cx, p["y"] - cy
    r = math.hypot(dx, dy)
    perp = abs(r - rad)
    if abs(sweep) < 1e-9:
        return 0.0, perp
    ang = math.atan2(dy, dx)
    d = ang - a0
    if sweep > 0:
        while d < 0:
            d += 2 * math.pi
        while d > 2 * math.pi:
            d -= 2 * math.pi
    else:
        while d > 0:
            d -= 2 * math.pi
        while d < -2 * math.pi:
            d += 2 * math.pi
    return d / sweep, perp


def match_move(move_log, p_real, tol=0.8):
    """Localiza el movimiento del job que contiene la pose real `p_real`.

    Acumula TODOS los candidatos del log (no hace early-return) y elige:
      1) el match más INTERIOR (f lejos de 0 y 1) — corresponde al movimiento
         que se está ejecutando, no a sus vecinos cuya frontera coincide;
      2) en empate por interioridad y perp, el ÍNDICE MÁS BAJO — el más
         antiguo del log, que es el que el firmware está TERMINANDO (no el
         siguiente, que el planner aún no había empezado a ejecutar).

    Devuelve (move, f) o (None, None).
    """
    pk = _motion(p_real)
    cand = []  # (idx, mv, f, perp)
    for idx, mv in enumerate(move_log):
        if mv.get("kind") == "arc" and mv.get("arc"):
            fr, perp = _arc_frac(mv["arc"], pk)
            if -0.06 <= fr <= 1.06 and perp < tol:
                cand.append((idx, mv, max(0.0, min(1.0, fr)), perp))
            continue
        s, e = _motion(mv["start"]), _motion(mv["end"])
        r = _project(s, e, pk)
        if r is None:
            continue
        t, perp = r
        if -0.06 <= t <= 1.06 and perp < tol:
            cand.append((idx, mv, max(0.0, min(1.0, t)), perp))
    if not cand:
        return None, None
    # Clave: -boundary_dist primero (queremos MAYOR boundary_dist = MÁS interior
    # → menor clave), luego perp ascendente, luego idx ascendente.
    def _key(c):
        _, _, f, perp = c
        boundary_dist = min(f, 1.0 - f)
        return (-boundary_dist, perp, c[0])
    cand.sort(key=_key)
    _, mv, f, _ = cand[0]
    return mv, f


def _fmt_axes(pose):
    """Componentes de ejes EXTRA (A/B/C/U/V/W) de la pose destino, como ' A.. B..'."""
    out = ""
    for k, v in (pose.get("axes") or {}).items():
        out += " {}{:.4f}".format(k.upper(), float(v))
    return out


def synth_completion(move, f):
    """Genera las líneas de GCODE que COMPLETAN el movimiento interrumpido desde la
    pose actual (se asume que el motor ya hizo G92 a la pose real). E en RELATIVO
    (M83) para ser correcto en modo absoluto o relativo; el motor restaura el modo.

    Devuelve lista de líneas (sin el M83/restore, que los pone el motor)."""
    end = move["end"]
    e_rem = float(move.get("e_total", 0.0)) * (1.0 - max(0.0, min(1.0, f)))
    feed = int(move.get("feedrate") or 1500)
    tx, ty, tz = float(end.get("x", 0.0)), float(end.get("y", 0.0)), float(end.get("z", 0.0))
    extra = _fmt_axes(end)

    if move.get("kind") == "arc" and move.get("arc"):
        arc = move["arc"]
        # I/J recomputados RELATIVOS a la pose actual (real); el motor hizo G92, así
        # que "actual" = move no aplica: usamos el centro absoluto - destino actual.
        # Como tras G92 la posición lógica = pose real, I/J = centro - pose_real.
        # El motor nos pasa la pose real dentro de move["_p_real"].
        pr = move.get("_p_real") or move["start"]
        i = arc["cx"] - float(pr.get("x", 0.0))
        j = arc["cy"] - float(pr.get("y", 0.0))
        g = "G2" if arc["cw"] else "G3"
        return ["{} X{:.4f} Y{:.4f} Z{:.4f}{} I{:.4f} J{:.4f} E{:.5f} F{:d}".format(
            g, tx, ty, tz, extra, i, j, e_rem, feed)]
    # lineal
    return ["G1 X{:.4f} Y{:.4f} Z{:.4f}{} E{:.5f} F{:d}".format(
        tx, ty, tz, extra, e_rem, feed)]
