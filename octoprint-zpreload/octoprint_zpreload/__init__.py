# coding=utf-8
"""
OctoPrint Z Preload — precarga física de backlash, con G-code EDITABLE y eje configurable.

Rastrea un eje (por defecto Z) durante el stream de impresión y, justo antes de un
movimiento G0/G1 que INVIERTE el sentido de ese eje, inyecta una secuencia de G-code
**editable** por el usuario en los ajustes (campo `preload_gcode`).

La idea física (probada por el usuario): al invertir el sentido, bajar la corriente con
M907 para que el motor cierre la holgura (gira en el juego libre) sin mover el carro,
hacer un movimiento de precarga (babystep M290) y esperar con un dwell adaptativo (G4 P{dwell})
a que termine, restaurando la corriente después. La plantilla por defecto usa
`M290 + G4 P{dwell}` (cero M400: M400 fuerza el drenado del planner y pausa la impresión).

Variables disponibles en la plantilla (se sustituyen por su valor):
  {axis}            -> letra del eje rastreado (Z, X, Y)
  {low}             -> corriente baja (mA)        [low_current]
  {restore}         -> corriente de restauración  [restore_current o la rastreada de M907]
  {feed}            -> velocidad de precarga      [preload_feedrate]
  {dwell}           -> dwell adaptativo (ms) = ceil(preload × pasos_mm ÷ 976 × 1000) + margen
  {preload}         -> distancia de precarga, sin signo ("0.550")
  {dir}             -> sentido del movimiento: "+1" o "-1"
  {signed_preload}  -> distancia de precarga con signo ("+0.550" / "-0.550")
  {prev}            -> posición anterior del eje ("0.400")
  {target}          -> posición objetivo del movimiento ("0.600")
  {preload_target}  -> posición de precarga = prev + dir*preload ("0.950" / "-0.150")

Los comandos que devuelve el hook se encolan en orden (no se re-procesan), y heredan los
tags del comando original. Solo M907 es fiable en esta placa (MKS_ROBIN_LITE + A4988 +
PWM_MOTOR_CURRENT); M906/M908/M909 no aplican.
"""
from __future__ import absolute_import

import re
import math

import octoprint.plugin

_FLOAT = r"[-+]?[0-9]*\.?[0-9]+"

# El babystep (M290) lo aplica el ISR de temperatura a ~976 pasos/s (INTEGRATED_BABYSTEPPING
# desactivado en este firmware). Sirve para calcular el dwell adaptativo.
BABYSTEP_RATE = 976  # pasos/s

# Precarga por defecto con babystep (M290): NO toca la posición lógica (sin G92) y el dwell
# adaptativo (G4 P{dwell}) espera a que el babystep termine a baja corriente. CERO M400
# (M400 fuerza el drenado del planner y pausa la impresión).
DEFAULT_PRELOAD_GCODE = (
    "G4 P{dwell}\n"
    "M907 {axis}{low}\n"
    "M290 P0 {axis}{signed_preload}\n"
    "G4 P{dwell}\n"
    "M907 {axis}{restore}\n"
    "G4 P{dwell}"
)


def _axis(cmd, axis):
    """Devuelve el valor numérico del parámetro `axis` (X/Y/Z/E/F/S/...) o None."""
    m = re.search(r"(?:^|[^A-Za-z])%s(%s)" % (axis, _FLOAT), cmd)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _homes_axis(cmd, axis):
    """True si un G28 hominga `axis` (G28 sin parámetros = homing total)."""
    named = re.search(r"(?:^|[^A-Za-z])[XYZ](?![0-9.])", cmd) is not None
    if not named:
        return True  # G28 solo = homing total
    return re.search(r"(?:^|[^A-Za-z])%s(?![0-9.])" % axis, cmd) is not None


class ZPreloadPlugin(
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.EventHandlerPlugin,
    octoprint.plugin.TemplatePlugin,
):
    def __init__(self):
        self._pos = None        # última posición lógica conocida del eje rastreado (mm)
        self._dir = 0           # última dirección: +1, -1, 0 desconocida
        self._abs_xyz = True    # modo de posicionamiento (G90/G91)
        self._current = None    # corriente del eje rastreada en el stream (mA); None = usar ajuste

    # ---- SettingsPlugin ----
    def get_settings_defaults(self):
        return {
            "enabled": True,
            "axis": "Z",              # eje a rastrear (X / Y / Z)
            "preload_distance": 0.55, # mm — debe cubrir la MAYOR de las dos holguras
            "low_current": 400,       # mA — cierra la holgura sin mover el carro
            "restore_current": 800,   # mA — corriente estándar (fallback si no se rastrea M907)
            "preload_feedrate": 600,  # mm/min del movimiento de precarga
            "steps_per_mm": 606,      # pasos/mm del eje (para calcular el dwell adaptativo)
            "dwell_margin_ms": 50,    # margen extra (ms) sobre el dwell calculado
            "only_during_print": True,     # solo inyectar durante impresión (stream de fichero)
            "preload_on_first_move": False,  # precargar también el 1er movimiento (tras homing/M84)
            "preload_gcode": DEFAULT_PRELOAD_GCODE,  # G-code editable a inyectar
        }

    # ---- EventHandlerPlugin ----
    def on_event(self, event, payload):
        if event in (
            "PrintStarted", "PrintDone", "PrintFailed",
            "PrintCancelled", "PrintCancelling", "Disconnected",
        ):
            self._reset()

    def _reset(self):
        self._pos = None
        self._dir = 0
        self._abs_xyz = True
        self._current = None

    # ---- TemplatePlugin ----
    def get_template_configs(self):
        return [
            {
                "type": "settings",
                "name": "Z Preload",
                "template": "zpreload_settings.jinja2",
                "custom_bindings": False,
            }
        ]

    # ---- helpers ----
    def _cfg(self, key, default=None):
        try:
            return self._settings.get([key], merged=True)
        except Exception:
            return default

    def _bool(self, key, default):
        try:
            return bool(self._settings.get([key], merged=True))
        except Exception:
            return default

    def _num(self, key, default):
        try:
            return float(self._settings.get([key], merged=True))
        except (TypeError, ValueError):
            return float(default)

    def _int(self, key, default):
        try:
            return int(round(float(self._settings.get([key], merged=True))))
        except (TypeError, ValueError):
            return int(default)

    def _axis_letter(self):
        a = str(self._cfg("axis", "Z") or "Z").strip().upper()
        return a if a in ("X", "Y", "Z") else "Z"

    # ---- gcode.queuing hook ----
    def gcode_queuing(self, comm, phase, cmd, cmd_type, gcode,
                      subcode=None, tags=None, *args, **kwargs):
        try:
            if not self._bool("enabled", True):
                return None
        except Exception:
            return None

        if not cmd or not gcode:
            return None

        axis = self._axis_letter()
        g = gcode.upper()
        c = str(cmd).strip()

        # --- comandos de estado (se rastrean SIEMPRE, vengan del job o de la terminal) ---
        if g == "G90":
            self._abs_xyz = True
            return None
        if g == "G91":
            self._abs_xyz = False
            return None
        if g == "G92":
            v = _axis(c, axis)
            if v is not None:
                self._pos = v
            else:
                # G92 sin parámetros = pone todo a 0
                has_named = any(_axis(c, a) is not None for a in ("X", "Y", "Z", "E"))
                if not has_named:
                    self._pos = 0.0
            return None
        if g == "G28":
            # homing: solo si hominga el eje rastreado (o es homing total) se pierde su posición
            if _homes_axis(c, axis):
                self._pos = None
                self._dir = 0
            return None
        if g == "M110":
            # OctoPrint manda M110 al iniciar cada job (reset de números de línea)
            self._reset()
            return None
        if g == "M907":
            v = _axis(c, axis)
            if v is not None:
                self._current = v
            else:
                s = _axis(c, "S")
                if s is not None:
                    self._current = s
            return None
        if g in ("M84", "M18"):
            # motores apagados: la próxima re-engranada puede reabrir la holgura
            self._dir = 0
            return None

        if g not in ("G0", "G1"):
            return None

        val = _axis(c, axis)
        if val is None:
            return None  # movimiento sin el eje rastreado: nada que precargar

        # Solo durante impresión (stream de fichero), si está activado
        if self._bool("only_during_print", True):
            if not tags or "source:file" not in tags:
                return None

        # Resolver la posición objetivo según el modo de posicionamiento
        if self._abs_xyz:
            target = val
        else:
            target = (self._pos if self._pos is not None else 0.0) + val

        prev_pos = self._pos
        if prev_pos is None:
            # primer movimiento conocido: establecer baseline, sin precarga
            self._pos = target
            self._dir = 0
            return None

        dz = target - prev_pos
        if abs(dz) < 1e-6:
            self._pos = target
            return None

        direction = 1 if dz > 0 else -1

        reversal = (self._dir != 0 and direction != self._dir)
        first = (self._dir == 0)

        # actualizar el estado ANTES de decidir la salida (el comando original se ejecutará)
        self._pos = target
        self._dir = direction

        should_preload = reversal
        if first and self._bool("preload_on_first_move", False):
            should_preload = True

        if not should_preload:
            return None

        preload = self._num("preload_distance", 0.55)
        low = self._num("low_current", 400)
        restore = self._current if self._current is not None else self._num("restore_current", 800)
        feed = self._int("preload_feedrate", 600)
        steps_per_mm = self._num("steps_per_mm", 606)
        margin = self._num("dwell_margin_ms", 50)
        dwell = int(math.ceil(preload * steps_per_mm / BABYSTEP_RATE * 1000.0)) + int(margin)

        if preload <= 0 or low <= 0 or restore <= 0:
            return None

        # Variables sustituibles en la plantilla (todas como cadenas ya formateadas)
        vars_map = {
            "axis": axis,
            "low": "{:.0f}".format(low),
            "restore": "{:.0f}".format(restore),
            "feed": "{:d}".format(feed),
            "dwell": "{:d}".format(dwell),
            "preload": "{:.3f}".format(preload),
            "dir": "+1" if direction > 0 else "-1",
            "signed_preload": "{:+.3f}".format(direction * preload),
            "prev": "{:.3f}".format(prev_pos),
            "target": "{:.3f}".format(target),
            "preload_target": "{:.3f}".format(prev_pos + direction * preload),
        }

        template = self._cfg("preload_gcode", DEFAULT_PRELOAD_GCODE) or ""
        lines = [ln.strip() for ln in str(template).splitlines() if ln.strip()]
        if not lines:
            self._logger.warning("ZPreload: plantilla preload_gcode vacía, no se inyecta nada")
            return None

        formatted = []
        try:
            for ln in lines:
                formatted.append(ln.format(**vars_map))
        except (KeyError, ValueError, IndexError) as e:
            self._logger.warning(
                "ZPreload: error en la plantilla preload_gcode (%s), no se inyecta nada", e
            )
            return None

        # En modo relativo (G91) forzar absoluto para la plantilla (G1/G92 son absolutos)
        # y restaurar el modo justo antes del comando original.
        out = []
        if not self._abs_xyz:
            out.append("G90")
        out.extend(formatted)
        if not self._abs_xyz:
            out.append("G91")
        out.append(c)

        self._logger.info(
            "ZPreload: inversión de %s %.3f -> %.3f (dir %s): %d comandos inyectados",
            axis, prev_pos, target, direction, len(out) - 1,
        )
        return out


__plugin_name__ = "Z Preload"
__plugin_version__ = "0.3.0"
__plugin_description__ = "Precarga física de backlash (eje configurable) con secuencia G-code editable, inyectada antes de invertir el sentido del movimiento."
__plugin_pythoncompat__ = ">=3.7,<4"
__plugin_implementation__ = ZPreloadPlugin()

__plugin_hooks__ = {
    "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.gcode_queuing,
}
