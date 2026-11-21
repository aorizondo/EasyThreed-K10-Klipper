# coding=utf-8
"""
OctoPrint Boost Kit — muchas mejoras para OctoPrint en un solo plugin.

Plugin agnóstico (cualquier impresora). Cuando el firmware tiene EMERGENCY_PARSER +
REALTIME_REPORTING_COMMANDS (Marlin), ofrece además hot-tune y sincronización del visor:

  - Hot-tune: corrección EN CALIENTE (M410 + M290 P0 + reanudación sintética).
  - Override de velocidad (M220) y flujo (M221) con efecto inmediato.

Los comandos internos se envían con force=True para saltar la cola de job de OctoPrint
(con force se omite el _command_queue para latencia mínima).
"""
from __future__ import absolute_import

import collections
import os
import re
import threading
import time
import types

import flask
import octoprint.plugin

from .common.tracker import PositionTracker, _axis
from .calibration.leveling import LevelingController
from .common import gcode_index
from .calibration import calibration
from .calibration import tramming
from .control import hottune


class BoostKitPlugin(
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.EventHandlerPlugin,
):

    def __init__(self):
        self._tracker = None
        self._tracker_installed_on = None  # instancia comm donde ya se instaló
        self._leveling = None              # controlador de nivelación UBL
        self._calib = None                 # controlador de calibración
        # Trail de movimientos para el visor 3D (módulo gcode3d): puntos nuevos
        # pendientes de empujar + marca de tiempo del último push (throttling).
        self._trail_pending = []
        self._trail_last_push = 0.0
        self._trail_interval = 0.12        # cache de gcode3d.push_interval (s)
        self._trail_timer = None           # vacía la cola tras el último movimiento
        self._feed_factor = 100            # % de velocidad (M220 S) en vivo
        self._flow_factor = 100            # % de flujo (M221 S) en vivo
        # Persistencia del visor (sobrevive a recargas y a otro navegador):
        self._trail_history = []           # historial del trazado (cap)
        self._viewstate = {}               # modelo cargado + selección + prefs
        self._limits = None                # M201/M203/M204/M205 (de M503 o en vivo)
        # Estado COMPLETO del firmware que afecta al movimiento (M503 + en vivo):
        # linear advance, corriente motor, volumétrico, backlash, probe offset,
        # input shaping, steps/mm, autoretract, fade, ventilador, feed/flow %...
        # Keyed por código M (p.ej. "M900": {"K": 0.04}). El panel de control lo
        # escanea al abrir y lo rastrea en vivo si una impresión lo modifica.
        self._fw = {}
        self._pos_last_push = 0.0          # throttle del push de posición al visor
        # Control de trabajo portable (cancel-object / skip-to):
        self._index = None                 # GcodeIndex del fichero seleccionado
        self._index_path = None            # ruta en disco del fichero indexado
        self._cancelled = set()            # índices de objeto cancelados (M486/fallback)
        self._skip_E = 0.0                 # E acumulada para restaurar tras supresión
        self._fw_caps = {}                 # capacidades del firmware (M115): CANCEL_OBJECT...
        self._last_obj_sent = -2           # último objeto marcado con M486 S (inyección)
        self._m486_announced = False       # ¿se envió ya M486 T<count> este job?
        # Hot-tune: corrección EN CALIENTE con M410 (config / jog vía M290 P0 / extrude)
        self._move_log = collections.deque(maxlen=80)  # últimos movimientos del job
        self._last_filepos = 0             # offset de byte de la línea anterior
        self._hot_busy = False             # ciclo de hot-tune en curso (anti-reentrada)
        self._hot_pending = []             # comandos acumulados (debounce)
        self._hot_lock = threading.Lock()
        self._hot_pose = None              # pose real (M114) capturada en el ciclo
        self._hot_pose_event = threading.Event()
        self._hot_await_pose = False       # esperando reporte de posición para el hot-tune
        # Tramming manual de la cama (wizard): activo mientras el wizard corre
        self._tram_active = False

    # ---- StartupPlugin ----
    def on_after_startup(self):
        self._tracker = PositionTracker(self._logger)
        self._leveling = LevelingController(
            self._printer, self._logger,
            lambda msg: self._plugin_manager.send_plugin_message(self._identifier, msg),
        )
        self._calib = calibration.CalibrationController(
            self._printer, self._logger, self.get_plugin_data_folder(),
            lambda msg: self._plugin_manager.send_plugin_message(self._identifier, msg),
        )
        self._refresh_trail_interval()
        # Timer de respaldo: si los movimientos se detienen, empuja los últimos
        # puntos que quedaron en la cola (el flush por-movimiento no los cubre).
        try:
            from octoprint.util import RepeatedTimer
            self._trail_timer = RepeatedTimer(0.3, self._trail_tail_flush, run_first=False)
            self._trail_timer.start()
        except Exception as e:
            self._logger.warning("No se pudo iniciar el timer de trail: %s", e)
        self._logger.info(
            "Boost Kit activo. Tracker + hot-tune + nivelación UBL + gcode3d."
        )
        # Si ya hay conexión activa al arrancar, instala el override.
        self._install_tracker(getattr(self._printer, "_comm", None))
        # Si la impresora ya está conectada (auto-connect), detecta las capacidades
        # de nivelación ahora (Connected se disparó antes de on_after_startup).
        try:
            if self._leveling is not None and self._printer.is_operational():
                self._leveling.detect(delay=1.0)
        except Exception:
            pass

    def _refresh_trail_interval(self):
        try:
            self._trail_interval = float(
                self._settings.get(["gcode3d", "push_interval"], merged=True) or 0.12)
        except (TypeError, ValueError):
            self._trail_interval = 0.12

    def on_settings_save(self, data):
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self._refresh_trail_interval()

    # ---- EventHandlerPlugin ----
    def on_event(self, event, payload):
        if event in ("Connected", "PrinterStateChanged"):
            self._install_tracker(getattr(self._printer, "_comm", None))
            if event == "Connected":
                # lee la config del firmware (M201/M203/M204/M205) para alimentar
                # la aceleración/velocidad de la animación del visor 3D.
                try:
                    self._send("M503")
                    # M115 -> capacidades del firmware (CANCEL_OBJECT para M486).
                    self._send("M115")
                except Exception:
                    pass
                # activa el auto-report de posición (M154) para sincronizar el
                # cabezal del visor en vivo (incluido el homing). Marlin-nativo.
                try:
                    sec = int(self._settings.get(["gcode3d", "auto_report_pos"], merged=True) or 0)
                    if sec > 0:
                        self._send("M154 S{:d}".format(sec))
                except Exception:
                    pass
                # Detecta las capacidades de nivelación de ESTA impresora (sistema,
                # densidad de malla, nº de slots). Printer-dependiente: no asume nada.
                try:
                    if self._leveling is not None:
                        self._leveling.detect(delay=2.0)
                except Exception:
                    pass
        elif event == "PrintStarted":
            if self._tracker is not None:
                self._tracker.reset()
                # Gating de la inferencia de capa por geometría según capacidades.
                try:
                    self._tracker.set_planar(self.job_caps()["planar"])
                except Exception:
                    pass
                # Reinicia el estado de cancelación de objetos para el nuevo job.
                self._cancelled = set()
                self._skip_E = 0.0
                self._last_obj_sent = -2
                self._m486_announced = False
                # Reinicia el registro de movimientos del hot-tune.
                self._move_log.clear()
                self._last_filepos = 0
                self._logger.info("Tracker reiniciado (PrintStarted)")
            # Limpia el trail SOLO al empezar una impresión nueva (NO en Home, para
            # que la animación de homing G28 no se borre antes de reproducirse).
            self._trail_pending = []
            self._trail_history = []
            try:
                self._plugin_manager.send_plugin_message(
                    self._identifier, {"type": "tracktrail", "clear": True})
            except Exception:
                pass
        elif event in ("FileSelected", "FileAdded"):
            # Pre-analiza el gcode seleccionado para cancel-object / skip-to.
            try:
                origin = payload.get("origin") if payload else None
                path = payload.get("path") if payload else None
                if event == "FileSelected" and path:
                    self._analyze_file(origin, path)
            except Exception as e:
                self._logger.warning("No se pudo analizar el fichero: %s", e)
        elif event == "FileDeselected":
            self._index = None
            self._index_path = None
            self._push_objects()
        elif event == "ClientOpened":
            # Se abrió/recargó una pestaña: manda la posición ACTUAL + config del
            # firmware para que el visor 3D arranque sincronizado (si no, empezaría
            # en 0,0,0 hasta el siguiente movimiento).
            self._push_possync()
            self._push_objects()
        elif event == "Disconnected":
            self._tracker_installed_on = None
            # Safety: release any stuck hold from failed hot-tune
            comm = getattr(self._printer, "_comm", None)
            if comm is not None:
                try:
                    comm.set_job_on_hold(False)
                except Exception:
                    pass

    # ---- Capacidades de trabajo (planaridad / multi-eje / M486) ----
    def job_caps(self):
        """Resuelve las capacidades efectivas combinando los ajustes (que pueden
        forzar) con lo DETECTADO en el fichero por el índice. Devuelve un dict:
          planar    -> bool: la cinemática es planar-cartesiana (geometría fiable)
          multiaxis -> bool: hay ejes adicionales (A/B/C/U/V/W)
          axes      -> list: ejes extra detectados/declarados
          m486      -> bool: usar M486 nativo (firmware lo soporta y no está forzado off)
        Honra el principio rector: ante la duda, NO se asume planar.
        """
        job = self._settings.get(["job"], merged=True) or {}
        det = getattr(self._index, "caps", None) or {}

        # Ejes extra: unión de los declarados y los detectados.
        axes = list(dict.fromkeys(
            list(job.get("extra_axes") or []) + list(det.get("extra_axes") or [])))
        multiaxis = bool(axes) or bool(det.get("multiaxis"))

        # Planaridad: override explícito > detección > (multi-eje => no planar) > True.
        pl = str(job.get("planar", "auto")).lower()
        if pl in ("true", "1", "yes"):
            planar = True
        elif pl in ("false", "0", "no"):
            planar = False
        elif det.get("non_planar"):
            planar = False
        elif multiaxis:
            planar = False
        else:
            planar = True

        # Una cinemática declarada no cartesiana también descarta la geometría plana.
        if str(job.get("kinematics", "auto")).lower() in ("delta", "polar", "robot"):
            planar = False

        # M486: forzable; en "auto" depende de lo que reporte el firmware (M115).
        m486_pref = str(job.get("m486", "auto")).lower()
        if m486_pref == "on":
            m486 = True
        elif m486_pref == "off":
            m486 = False
        else:
            m486 = bool(self._fw_caps.get("CANCEL_OBJECT"))

        return {"planar": planar, "multiaxis": multiaxis, "axes": axes, "m486": m486}

    # ---- Capacidades para la suite de calibración ----
    def _calib_caps(self):
        try:
            profile = self._printer_profile_manager.get_current_or_default()
        except Exception:
            profile = None
        temp_max = {
            "hotend": self._settings.get(["temp", "hotend_max"], merged=True) or 280,
            "bed": self._settings.get(["temp", "bed_max"], merged=True) or 120,
        }
        lev = self._leveling.caps_state() if self._leveling is not None else None
        return calibration.resolve_caps(profile, self._limits, self._fw, temp_max, lev)

    # ---- Índice de gcode (cancel-object / skip-to) ----
    def _disk_path(self, origin, path):
        try:
            return self._file_manager.path_on_disk(origin or "local", path)
        except Exception:
            return None

    def _analyze_file(self, origin, path):
        """Construye (en segundo plano) el índice del gcode seleccionado, cacheado
        por ruta+mtime. No bloquea el hilo de eventos."""
        disk = self._disk_path(origin, path)
        if not disk or not os.path.isfile(disk):
            return
        if not re.search(r"\.(gco|gcode|g)$", disk, re.I):
            return
        try:
            mtime = os.path.getmtime(disk)
        except OSError:
            mtime = 0
        # Cache: mismo fichero y mtime -> nada que hacer.
        if (self._index is not None and self._index_path == disk
                and getattr(self._index, "_mtime", None) == mtime):
            self._push_objects()
            return

        def worker():
            try:
                idx = gcode_index.build_index(disk, logger=self._logger,
                                              layer_threshold=0.04)
                idx._mtime = mtime
                self._index = idx
                self._index_path = disk
                self._cancelled = set()
                self._logger.info("Índice listo para %s", os.path.basename(disk))
                self._push_objects()
            except Exception as e:
                self._logger.warning("Fallo al indexar %s: %s", disk, e)

        threading.Thread(target=worker, name="boostkit-index", daemon=True).start()

    def _push_objects(self):
        """Empuja al frontend objetos + checkpoints + capacidades del fichero."""
        idx = self._index
        caps = self.job_caps()
        if idx is None:
            payload = {"type": "objects", "objects": [], "checkpoints": [],
                       "caps": caps, "has_markers": False, "cancelled": []}
        else:
            s = idx.summary()
            payload = {
                "type": "objects",
                "objects": [{"index": o["index"], "name": o["name"],
                             "cancelled": o["index"] in self._cancelled}
                            for o in s["objects"]],
                "checkpoints": s["checkpoints"],
                "caps": caps,
                "has_markers": s["has_markers"],
                "has_m486": s["has_m486"],
                "cancelled": sorted(self._cancelled),
            }
        try:
            self._plugin_manager.send_plugin_message(self._identifier, payload)
        except Exception:
            pass

    # ---- SettingsPlugin ----
    def get_settings_defaults(self):
        return {
            "baby_increment": 0.025,   # mm por pulsación de babystep

            # Gráfico de temperatura (módulo bktemp): historial en segundos que
            # se conserva en el buffer del cliente (zoom/pan + análisis de pendiente).
            "temp": {
                "history_seconds": 900,    # cuánto historial guardar (s)
                "default_window": 120,     # ventana visible por defecto (s)
                # Topes de los sliders de temperatura del panel de control. Marlin NO
                # reporta los *_MAXTEMP por gcode, así que se configuran aquí (por
                # elemento). Solo se muestran sliders para los elementos CAPACITADOS
                # en el perfil activo (extrusores / cama / cámara).
                "hotend_max": 280,
                "bed_max": 120,
                "chamber_max": 70,
                # Piso ACTIVO del hotend: el slider trata 0 como APAGADO; al salir de 0
                # salta a este valor (no tiene sentido operar por debajo). 0..min_active
                # queda como zona muerta que colapsa a min_active (excepto el 0 exacto).
                "hotend_min_active": 150,
            },

            # Visor 3D del tab GCode Viewer (módulo gcode3d): inyecta una vista
            # 3D (Three.js) sobre el visor 2D nativo, con sync del cabezal en vivo
            # y un "trail" de los últimos N movimientos.
            "gcode3d": {
                "trail_len": 400,          # nº de movimientos recientes a dibujar
                "push_interval": 0.12,     # s mínimos entre empujes de trail (throttle)
                "follow_cam": False,       # la cámara sigue al cabezal
                "sim_accel": 1500,         # mm/s² para la animación del cabezal
                # Coloreado del trazado estilo slicer (Cura/Orca):
                "color_mode": "type",      # type | temp | speed | flow
                "show_travel": True,       # dibujar travels (punteado)
                "width_by_flow": True,     # ancho del trazo ∝ caudal
                "temp_lo": 150,            # °C -> azul (frío)
                "temp_hi": 300,            # °C -> rojo (caliente)
                # Auto-report de posición (M154 S<seg>) al conectar: el firmware
                # empuja la posición cada N segundos (formato Marlin que OctoPrint
                # entiende), y el plugin la usa para sincronizar el cabezal del
                # visor (incluido el homing). 0 = no activar. Requiere
                # AUTO_REPORT_POSITION en el firmware. Para una animación MÁS fluida
                # del homing se puede usar FULL_REPORT_TO_HOST_FEATURE (Grbl): el
                # plugin lo consume y lo suprime para no bloquear a OctoPrint.
                "auto_report_pos": 1,

                # Cama: la superficie física es mayor que el área alcanzable por los
                # ejes. En la K9 la cama es 120×120 pero la boquilla solo recorre el
                # 100×100 interior (X0/Y0 está a `bed_margin` mm del borde físico en
                # X-/Y-). Esto es SOLO representacional (el firmware sigue en 0–100).
                "nozzle_color": "bronze",  # "bronze" | "steel" (color del nozzle 3D)
                "nozzle_label": True,      # rotular el diámetro del nozzle (del perfil)
                "line_width_factor": 0.92, # ancho de cordón = factor × Ø nozzle (modelo sólido)
                "show_bed": True,          # dibujar la cama en el visor 3D
                "bed_margin": 10,          # mm de cama no alcanzable alrededor (X-/Y-)
                "show_bed_mesh": False,    # superponer la malla de nivelación activa
            },

            # Panel de control consolidado (reemplaza Command & Control). Todo
            # configurable para que el plugin sea autónomo (sin depender de otros).
            "control": {
                "jog_xy": [0.1, 1, 5, 10, 50, 100],  # pasos disponibles XY (mm)
                "jog_z": [0.05, 0.1, 1, 5, 10, 50],  # pasos disponibles Z (mm)
                "jog_speed_xy": 3000,             # mm/min (semilla del slider de feed)
                "jog_speed_z": 600,               # mm/min (semilla del slider de feed Z)
                "jog_feed_min": 100,              # mm/min mínimo del slider de feed del jog
                "jog_feed_max": 50000,            # mm/min máximo del slider de feed del jog
                "extrude_amounts": [5, 10, 50, 100],
                "extrude_speed": 300,             # mm/min
                # NOTA: los presets de temperatura usan los PERFILES NATIVOS de
                # OctoPrint (Ajustes › Temperaturas), no una lista propia.
                "load": {"len": 90, "speed": 600, "prime": 10, "prime_speed": 100},
                "unload": {"len": 95, "speed": 600},
                # Sliders en vivo estilo Marlin Slider: rango y paso configurables.
                "sliders": {
                    "feed": {"min": 10, "max": 300, "step": 5},   # M220 % (tope BASE; crece dinámico)
                    "flow": {"min": 50, "max": 200, "step": 5},   # M221 % (tope BASE; crece dinámico)
                    "fan":  {"min": 0,  "max": 100, "step": 5},   # M106 %
                    "baby": {"min": -0.5, "max": 0.5, "step": 0.01},  # M290 Z mm
                },
                # Ventiladores controlables. Marlin no reporta cuántos hay por gcode,
                # así que se declaran aquí: una entrada por ventilador. 'index' = P de
                # M106 (0 = ventilador de capa principal -> se envía M106 sin P).
                # Para una impresora con varios ventiladores, añade entradas aquí.
                "fans": [
                    {"name": "Ventilador", "index": 0},
                ],
            },

            # Tramming manual de la cama (esquema del plugin manual_bed_tramming):
            # esquinas + centro, límites de Z, feed y grosor de papel de referencia.
            "tram": {
                "front_left_x": 30, "front_left_y": 30,
                "front_right_x": 270, "front_right_y": 30,
                "back_left_x": 30, "back_left_y": 270,
                "back_right_x": 270, "back_right_y": 270,
                "center_x": 150, "center_y": 150,
                "lower_z": 0, "upper_z": 10,
                "feed_rate": 3600,
                "z_clear": 10,
                "paper_thickness": 0.10,
            },


            # Control de trabajo PORTABLE (cancel-object, skip-to, pausa segura).
            # Pensado para funcionar también en impresión NO PLANAR y >4 ejes: la
            # geometría es solo una pista, válida bajo cinemática planar-cartesiana.
            "job": {
                # Cinemática declarada. "auto" = inferir del perfil + del fichero
                # (presencia de ejes A/B/C/U/V/W, Z no monótona).
                "kinematics": "auto",      # auto|cartesian|corexy|delta|polar|robot
                "planar": "auto",          # auto|true|false (override del análisis)
                "extra_axes": [],          # ejes adicionales declarados (A/B/C/U/V/W)
                # Aparcado al pausar/reanudar según cinemática:
                #   cartesian = retract + lift Z + park XY (solo planar-cartesiana)
                #   firmware  = delega en park_gcode / G27 (conoce la cinemática)
                #   none      = sin lift; solo retract por el eje de herramienta
                "park_mode": "cartesian",
                "park_gcode": "",          # macro de park del usuario (modo firmware)
                # Leer la pose REAL con M114 tras el hold antes de reanudar (corrige
                # la costura de reanudar desde la posición lógica del tracker).
                "resume_readback": False,
                # Cancel-object: usar M486 nativo si el firmware lo soporta; si no,
                # fallback a supresión de comandos por rango de objeto.
                "m486": "auto",            # auto|on|off
                # Skip-to: re-homear antes de saltar (gated; peligroso en no planar).
                "skip_home": False,
                # Skip-to: NO esperar a que caliente (M104/M140 sin M109/M190) para
                # reanudar en caliente cuando la impresora ya está a temperatura.
                "skip_temp_wait": False,
            },

            # Numpad como jog (manejado en el frontend; el on/off se recuerda en el
            # navegador). Aquí solo el estado por defecto al cargar por primera vez.
            "numpad": {
                "default_on": False,       # arrancar con el numpad en modo JOG
            },

            # Modo realtime del numpad: movimiento continuo mientras se mantiene
            # pulsado (G1 enorme + M410 al soltar). Solo con impresora operativa
            # y SIN trabajo activo (ni print ni pausa).
            "realtime_numpad": {
                "extrude_max_len": 50,    # mm máximos por G1 de extrusión realtime
                "extrude_speed": 300,     # mm/min para extrusión realtime
            },

            # Hot-tune: corrección instantánea EN CALIENTE mediante M410 + reanudación
            # sintética. Convierte jog a M290 P0 (babystep, no toca Z-probe offset).
            # Checkbox on/off en el panel de control.
            "hottune": {
                "enabled": True,
                "debounce_ms": 80,
                "allow": ["M106", "M107", "M220", "M221", "M201", "M203", "M204",
                          "M205", "M900", "M906", "M907", "M425", "M593", "M200",
                          "M104", "M140", "M92", "G0", "G1", "G2", "G3"],
            },

            # Terminal mejorado (chat eficiente, autocompletado). Los FILTROS son los
            # NATIVOS de OctoPrint (Ajustes › Terminal); el chat los respeta para
            # OCULTAR las líneas y persiste la selección entre recargas (localStorage).
            "terminal": {
                "enabled": True,           # mejorar el terminal nativo
                "chat": True,              # agrupar comando→respuestas en burbujas
                "timestamps": False,       # mostrar marca de tiempo
                "max_lines": 1200,         # tope de líneas renderizadas (virtualización)
            },

            # Nivelación automática AGNÓSTICA (cualquier impresora). Con auto_detect
            # lee M211 (cama) y M851 (offset sonda) y calcula el alcance solo.
            # Los puntos inalcanzables se MIDEN (G30+M421) en vez de extrapolar.
            "leveling": {
                "system": "ubl",          # "ubl" | "bilinear"
                "method": "plugin",       # "plugin" (G30×N+moda) | "g29" (firmware)
                "auto_detect": True,      # leer M211 + M851 del firmware
                "grid_n": 7,              # debe COINCIDIR con GRID_MAX_POINTS del firmware
                "mesh_inset": 1,
                "probe_margin": 2,
                "probe_repeats": 4,       # medidas por punto (moda) en la vía plugin
                "slots_fallback": 4,      # nº de slots si la impresora no lo reporta
                "fade": 10,
                # Slots UBL nativos (máx. 4). El plugin gestiona nombre + ocupación y
                # guarda una copia de los datos Z para visualizar sin activar.
                "slots": [
                    {"name": "", "filled": False, "n": 0, "ts": 0, "z": None},
                    {"name": "", "filled": False, "n": 0, "ts": 0, "z": None},
                    {"name": "", "filled": False, "n": 0, "ts": 0, "z": None},
                    {"name": "", "filled": False, "n": 0, "ts": 0, "z": None},
                ],
                # usados solo si auto_detect = false:
                "bed_min_x": 0, "bed_max_x": 100,
                "bed_min_y": 0, "bed_max_y": 100,
                "probe_offset_x": 0, "probe_offset_y": 0,
                # Nivelación de PLATO (rectángulo más pequeño que la cama declarada):
                # sondea solo el cristal y ajusta un plano. Semillas del panel.
                "model": "plane",             # "plane" | "warp"
                "grid_n_probe": 3,            # rejilla de sondeo del plato (N×N)
                "probe_clearance": 8,         # mm de despegue entre puntos (sonda manual cuelga)
                "apply_backlash": True,       # setear backlash Z medido tras nivelar (si el fw lo soporta)
                "autoconfirm": True,          # auto-M108 en pausas de sonda manual (uno por pausa)
                "probe_area": {"x0": 50, "y0": 25, "w": 190, "h": 240, "inset": 5},
                # Robustez del plato: mediana por punto + rechazo de bultos de pegamento.
                "probe_central": "median",    # agregación por punto: "median" | "mode"
                "robust_reject": True,        # rechazar outliers (RLS-MAD) al ajustar el plano
                "reject_k": 2.5,              # umbral de rechazo = k·1.4826·MAD
                "reject_floor_mm": 0.05,      # umbral mínimo (no sobre-rechazar en cristal liso)
                "reject_ceiling_mm": 0.30,    # techo duro (un bulto > esto SIEMPRE se rechaza)
                "warp_smooth_sigma": 0.8,     # suavizado gaussiano del residuo (modo alabeo)
                "warp_smooth_strength": 1.0,
                # Inclinación severa: shift adaptativo de M851 (universal, sin M500).
                "probe_order": "center_out",  # orden de sondeo: "center_out" | "grid"
                "predict_seed": 3,            # puntos centrales antes de predecir la altura
                "probe_retries": 1,           # reintentos bajando el piso ante Probing Failed
                "probe_extend": 3,            # mm que se baja el piso por reintento
            },
        }

    # ---- AssetPlugin ----
    def get_assets(self):
        return {
            # Rutas organizadas por grupo funcional (core/control/calibration/
            # visualization). El ORDEN es load-bearing: bkcompat 1º (parcha window._
            # antes de instanciar VMs); la cadena vendor debe ir en orden (attach a
            # THREE.* en load-time). vendor/ NO se reorganiza.
            "js": [
                "js/core/bkcompat.js",
                "js/core/bkcore.js",       # helpers compartidos (window.BK) — tras bkcompat, antes de los VMs
                "js/control/boostkit.js",
                "js/control/bktemp.js",
                "js/control/bksidebar.js",
                "js/calibration/bkcalib.js",
                "js/control/bkterminal.js",
                # Three.js (r108) vendorizado de PrettyGCode para el visor 3D.
                # Orden: core -> extensiones de líneas gruesas -> loaders/controles.
                "js/vendor/three.min.js",
                "js/vendor/LineSegmentsGeometry.js",
                "js/vendor/LineGeometry.js",
                "js/vendor/LineSegments2.js",
                "js/vendor/LineMaterial.js",
                "js/vendor/Line2.js",
                "js/vendor/OBJLoader.js",
                "js/vendor/camera-controls.js",
                "js/vendor/Lut.js",
                "js/visualization/bkgcode3d.js",
                "js/calibration/bkmesh.js",
            ],
            "css": ["css/control/boostkit.css", "css/control/bktemp.css",
                    "css/visualization/bkgcode3d.css"],
        }

    # ---- TemplatePlugin ----
    def is_template_autoescaped(self):
        # Nuestras plantillas no emiten HTML crudo desde variables -> seguro.
        return True

    def get_template_configs(self):
        return [
            # Panel de control: tab PROPIO del plugin ("Enhanced Controls"). Antes
            # era un template "generic" que el JS movía dentro del tab nativo de
            # Control (#control); ahora vive en su propia pestaña sin tocar la nativa.
            {"type": "tab", "name": "Enhanced Controls", "template": "control/boostkit_tab.jinja2", "suffix": "_controls", "custom_bindings": True},
            # Visor 3D: tab PROPIO ("Enhanced Viewer"). Deja intacto el GCode Viewer
            # nativo de OctoPrint; el visor 3D se monta dentro de #bk3d_container.
            {"type": "tab", "name": "Enhanced Viewer", "template": "visualization/boostkit_gcode3d_tab.jinja2", "suffix": "_viewer", "custom_bindings": True},
            {"type": "tab", "name": "Temperatura", "template": "control/boostkit_temp_tab.jinja2", "suffix": "_temp", "custom_bindings": True},
            {"type": "tab", "name": "Calibración", "template": "calibration/boostkit_calib_tab.jinja2", "suffix": "_calib", "custom_bindings": True},
            # --- Tarjetas de sidebar (fijables/reordenables con UI Customizer) ---
            {"type": "sidebar", "name": "BK · Visor 3D", "icon": "cube",
             "template": "visualization/boostkit_card_viewer.jinja2", "suffix": "_card_viewer", "custom_bindings": False},
            {"type": "sidebar", "name": "BK · Temperatura", "icon": "thermometer-half",
             "template": "control/boostkit_card_temp.jinja2", "suffix": "_card_temp", "custom_bindings": True},
            {"type": "sidebar", "name": "BK · Minijog", "icon": "arrows-alt",
             "template": "control/boostkit_card_jog.jinja2", "suffix": "_card_jog", "custom_bindings": True},
            # Estado: NO es una card propia; se inyecta dentro del panel de estado
            # NATIVO de OctoPrint (patrón SkipTo). El JS lo mueve a #state .accordion-inner.
            {"type": "generic", "template": "control/boostkit_card_status.jinja2", "custom_bindings": True},
            {"type": "navbar", "template": "control/boostkit_navbar.jinja2", "custom_bindings": True},
            {"type": "settings", "name": "Boost Kit", "template": "control/boostkit_settings.jinja2", "custom_bindings": False},
        ]

    # ---- SimpleApiPlugin ----
    def is_api_protected(self):
        # Control de impresora: exigir autenticación.
        return True

    def get_api_commands(self):
        return {
            "job_cancel": [],
            "babystep": ["z"],
            "feedrate": ["value"],
            "flow": ["value"],
            "gcode": ["cmd"],
            "level": [],        # {home?}  -> arranca nivelación UBL
            "level_cancel": [],
            "leveling_caps": [],   # consulta/re-detecta capacidades de nivelación
            # --- Slots de malla (UBL nativos; cantidad printer-dependiente) ---
            "mesh_slots": [],      # consulta estado + datos Z de los slots
            "mesh_get": [],        # malla activa (para el visor topográfico)
            "mesh_save": ["slot"], # {slot, name?} guarda la malla activa en el slot
            "mesh_activate": ["slot"],  # {slot} carga y activa la malla del slot
            "mesh_clear": ["slot"],     # {slot} marca el slot como vacío
            # --- Editor manual de malla (sin sonda) ---
            "mesh_goto": [],       # {x, y, z_clear?} mueve la boquilla a un nodo (absoluto)
            "mesh_set_point": [],  # {i, j} fija el nodo (i,j) con la Z actual (M421 I J Z)
            # --- Panel de control consolidado ---
            "jog": [],          # {x?, y?, z?, speed?}  movimiento relativo
            "home": [],         # {axes?: ['x','y','z']}
            "extrude": ["amount"],  # {amount, speed?}
            "settemp": ["heater", "value"],  # heater 'tool0'|'bed'
            "motors_off": [],
            "load": [],         # carga filamento (usa settings)
            "unload": [],       # descarga filamento (usa settings)
            "get_limits": [],   # M503 -> escanea TODA la config de movimiento
            "fan": [],          # {value:0-100}  M106/M107 (ventilador de capa)
            "cold_extrude": [], # {allow:bool, min_temp?}  M302 (extrusión en frío)
            "set_mode": [],     # {target:'xyz'|'e', relative:bool}  G90/G91/M82/M83
            "viewstate": [],    # {state} -> persiste estado del visor 3D (cross-browser)
            # --- Control de trabajo portable (cancel-object / skip-to) ---
            "list_objects": [],      # objetos + checkpoints + capacidades del fichero
            "cancel_object": ["index"],    # {index} cancela un objeto en curso
            "uncancel_object": ["index"],  # {index} deshace la cancelación
            "skip_to": [],           # {offset?|checkpoint?|layer?, start_print?}
            # --- Numpad modo realtime (movimiento continuo con M410) ---
            "realtime_move": ["axis", "dir"],   # axis: x|y|z|e  dir: 1|-1
            "realtime_stop": [],                # M410 + restaurar modo
            # --- Suite de calibración universal ---
            "calib_caps": [],            # capacidades resueltas (cama/nozzle/volumétrico…)
            "calib_run": ["test"],       # {test, params?, print?} genera patrón → fichero
            "calib_pid": [],             # {heater, cycles?, temp?, fan?, save?}
            "calib_esteps_read": [],     # lee M92 actual
            "calib_esteps_extrude": [],  # {temp, length, speed}
            "calib_apply": [],           # {gcode:[...], save?}
            "calib_preset_save": ["test", "name"],  # {test, name, params}
            "calib_preset_delete": ["test", "name"],
            "calib_presets": [],
            "calib_history": [],
            "calib_history_add": [],     # {test, params, result?}
            "calib_history_mark": ["id", "result"],  # {id, result, notes?, measured?}
            # --- Tests data-driven (Jinja) + preview ---
            "calib_tests": [],           # lista de definiciones de test
            "calib_test_save": [],       # {def}
            "calib_test_delete": ["id"],
            "calib_test_render": ["test"],  # {test, params?, template?} -> gcode + trazos
            # --- Backlash sweep interactivo (G38.2 + M425) ---
            "calib_backlash_start": ["axis"],   # {axis:x|y|z, m425_start, m425_end, m425_step,
                                                #  ref_pos, target, feed, samples_per, save}
            "calib_backlash_cancel": [],
            # --- Tramming manual de la cama (wizard) ---
            "tram_start": [], "tram_goto": [], "tram_z": [], "tram_register": [],
            "tram_mesh_save": [], "tram_cancel": [],
        }

    def on_api_command(self, command, data):
        if command == "job_cancel":
            self._send("M410")
            try:
                self._printer.cancel_print()
            except Exception as e:
                self._logger.warning("job_cancel: cancel_print falló: %s", e)
            self._logger.info("Cancelar job")

        elif command == "babystep":
            # Babystep en cualquier eje (M290 X/Y/Z). Marlin solo aplica X/Y si el
            # firmware lo soporta (BABYSTEP_XY); Z siempre.
            parts = []
            for ax in ("x", "y", "z"):
                if ax in data:
                    try:
                        parts.append("{}{:.3f}".format(ax.upper(), float(data[ax])))
                    except (TypeError, ValueError):
                        pass
            if parts:
                # P0 = no modificar el offset de la sonda (Probe Z Offset). Útil para
                # ajustar la primera capa en caliente sin tocar la calibración de la sonda.
                if data.get("p0"):
                    parts.insert(0, "P0")
                self._send("M290 " + " ".join(parts))

        elif command == "feedrate":
            v = self._clamp_int(data.get("value", 100), 10, 300)
            self._send("M220 S{:d}".format(v))

        elif command == "flow":
            v = self._clamp_int(data.get("value", 100), 50, 200)
            self._send("M221 S{:d}".format(v))

        elif command == "gcode":
            # Escotilla genérica: manda un gcode arbitrario con force=True.
            cmd = str(data.get("cmd", "")).strip()
            if cmd:
                self._send(cmd)


        elif command == "level":
            if self._leveling is None:
                return
            opts = dict(self._settings.get(["leveling"], merged=True) or {})
            opts["home"] = bool(data.get("home", True))
            # Overrides en vivo desde el panel (nivelación de plato): rectángulo,
            # modelo plano/alabeo, método G30/G29, rejilla de sondeo, auto-M108,
            # robustez (rechazo de outliers) y shift adaptativo de M851.
            for k in ("method", "model", "autoconfirm", "probe_area", "probe_clearance",
                      "grid_n", "grid_n_probe", "probe_repeats", "apply_backlash",
                      "probe_central", "robust_reject", "reject_k", "reject_floor_mm",
                      "reject_ceiling_mm", "warp_smooth_sigma", "warp_smooth_strength",
                      "probe_order", "predict_seed", "probe_retries", "probe_extend"):
                if k in data:
                    opts[k] = data[k]
            if not self._leveling.start(opts):
                self._logger.info("Nivelación ya en curso, ignorado")

        elif command == "level_cancel":
            if self._leveling is not None:
                self._leveling.cancel()

        elif command == "leveling_caps":
            # devuelve lo cacheado y, si se pide, vuelve a detectar
            if self._leveling is not None:
                self._plugin_manager.send_plugin_message(self._identifier, {
                    "type": "leveling_caps", "caps": self._leveling.caps_state()})
                if bool(data.get("redetect", False)):
                    self._leveling.detect(delay=0.0)

        elif command == "mesh_slots":
            self._push_slots()

        elif command == "mesh_get":
            mesh = self._leveling.mesh if self._leveling is not None else None
            self._plugin_manager.send_plugin_message(self._identifier, {
                "type": "mesh",
                "mesh": {"n": len(mesh) if mesh else 0, "z": mesh,
                         "xs": getattr(self._leveling, "mesh_xs", None),
                         "ys": getattr(self._leveling, "mesh_ys", None)},
            })

        elif command == "mesh_save":
            self._mesh_save(data)

        elif command == "mesh_activate":
            cnt = self._slot_count()
            slot = self._clamp_int(data.get("slot", 0), 0, max(0, cnt - 1))
            if self._leveling is not None and cnt > 0:
                fade = int((self._settings.get(["leveling"], merged=True) or {}).get("fade", 10))
                self._leveling.activate_slot(slot, fade)
                self._logger.info("Malla: activado slot %d", slot)

        elif command == "mesh_clear":
            cnt = self._slot_count()
            slot = self._clamp_int(data.get("slot", 0), 0, max(0, cnt - 1))
            slots = self._get_slots()
            slots[slot] = {"name": "", "filled": False, "n": 0, "ts": 0, "z": None}
            self._settings.set(["leveling", "slots"], slots)
            self._settings.save()
            self._push_slots()

        elif command == "mesh_goto":
            # Editor manual: mover la boquilla a un nodo (coords ABSOLUTAS), subiendo
            # antes a una altura segura para no arrastrar. Preserva el modo abs/rel.
            try:
                x = float(data.get("x")); y = float(data.get("y"))
            except (TypeError, ValueError):
                return
            self._goto_preserving(x, y, float(data.get("z_clear", 8)))

        elif command == "mesh_set_point":
            # Editor manual: fija el nodo (i,j) con la Z ACTUAL de la máquina
            # (M421 I J Z + M420 S1). Universal (UBL/bilineal/MBL).
            if self._leveling is None:
                return
            i = self._clamp_int(data.get("i", 0), 0, 30)
            j = self._clamp_int(data.get("j", 0), 0, 30)
            z = self._leveling.read_z()
            if z is None:
                self._plugin_manager.send_plugin_message(self._identifier, {
                    "type": "mesh_edit", "event": "error", "msg": "no pude leer la Z (M114)"})
                return
            self._send(["M421 I%d J%d Z%.3f" % (i, j, z), "M420 S1"])
            self._plugin_manager.send_plugin_message(self._identifier, {
                "type": "mesh_edit", "event": "set", "i": i, "j": j, "z": z})

        elif command == "tram_start":
            self._tram_active = True
            if data.get("home_first"):
                self._printer.home(["x", "y", "z"])
            self._send("M420 S0")

        elif command == "tram_cancel":
            self._tram_active = False
            self._send("M420 S1")

        elif command == "tram_goto":
            t = self._settings.get(["tram"], merged=True) or {}
            corner = str(data.get("corner", ""))
            key_x = {"fl": "front_left_x", "fr": "front_right_x",
                     "bl": "back_left_x", "br": "back_right_x",
                     "center": "center_x"}.get(corner)
            key_y = {"fl": "front_left_y", "fr": "front_right_y",
                     "bl": "back_left_y", "br": "back_right_y",
                     "center": "center_y"}.get(corner)
            if not key_x or not key_y:
                self._logger.warning("tram_goto: esquina desconocida %r", corner)
                return
            try:
                x = float(t.get(key_x) or 0); y = float(t.get(key_y) or 0)
            except (TypeError, ValueError):
                return
            z_clear = float(t.get("z_clear") or 10)
            self._goto_preserving(x, y, z_clear)

        elif command == "tram_z":
            try:
                z = float(data.get("z") or 0)
            except (TypeError, ValueError):
                return
            if not z:
                return
            speed = int(self._settings.get(["tram", "feed_rate"], merged=True) or 3600)
            self._jog_preserving({"z": z}, speed)

        elif command == "tram_register":
            if self._leveling is None:
                return {"z": None, "error": "nivelación no disponible"}
            z = self._leveling.read_z()
            if z is None:
                return {"z": None, "error": "no pude leer la Z (M114)"}
            return {"z": z}

        elif command == "tram_mesh_save":
            t = self._settings.get(["tram"], merged=True) or {}
            def _f(v, d):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return float(d)
            corners = {
                "fl": (_f(t.get("front_left_x"), 30), _f(t.get("front_left_y"), 30), _f(data.get("z_fl"), 0)),
                "fr": (_f(t.get("front_right_x"), 270), _f(t.get("front_right_y"), 30), _f(data.get("z_fr"), 0)),
                "bl": (_f(t.get("back_left_x"), 30), _f(t.get("back_left_y"), 270), _f(data.get("z_bl"), 0)),
                "br": (_f(t.get("back_right_x"), 270), _f(t.get("back_right_y"), 270), _f(data.get("z_br"), 0)),
            }
            lv = self._settings.get(["leveling"], merged=True) or {}
            try:
                grid_n = max(1, int(lv.get("grid_n") or 7))
            except (TypeError, ValueError):
                grid_n = 7
            bounds = (_f(lv.get("bed_min_x"), 0), _f(lv.get("bed_max_x"), 100),
                      _f(lv.get("bed_min_y"), 0), _f(lv.get("bed_max_y"), 100))
            paper = _f(data.get("paper_thickness"), t.get("paper_thickness") or 0.10)
            cmds = tramming.build_mesh_commands(corners, grid_n, bounds, paper)
            if cmds:
                self._send(cmds)
                self._send("M420 S1")
            return {"count": len(cmds)}

        # ---- Panel de control consolidado ----
        elif command == "jog":
            axes = {}
            for ax in ("x", "y", "z"):
                if ax in data:
                    try:
                        axes[ax] = float(data[ax])
                    except (TypeError, ValueError):
                        pass
            if not axes:
                return
            # Feedrate del jog (mm/min) desde el slider; clamp a los límites configurables
            # (por defecto 100–50000) para no mandar un F absurdo al firmware.
            spd = self._clamp_int(int(float(data.get("speed") or 0) or 3000),
                                  int(self._settings.get(["control", "jog_feed_min"]) or 100),
                                  int(self._settings.get(["control", "jog_feed_max"]) or 50000))
            printing = self._printer.is_printing()
            if printing:
                parts = ["{}{:.3f}".format(k.upper(), v) for k, v in axes.items()]
                self._send("M290 P0 " + " ".join(parts))
            else:
                self._jog_preserving(axes, spd)


        elif command == "home":
            axes = data.get("axes") or ["x", "y", "z"]
            axes = [a for a in axes if a in ("x", "y", "z")]
            if axes:
                self._printer.home(axes)

        elif command == "extrude":
            try:
                amt = float(data.get("amount", 0))
            except (TypeError, ValueError):
                return
            spd = int(float(data.get("speed") or 0) or 300)
            # Durante impresión, extruir por API directa colisionaría con el job.
            # Notificar y no ejecutar (el usuario usará el panel de babystep / hot-tune).
            if self._printer.is_printing():
                try:
                    self._plugin_manager.send_plugin_message(
                        self._identifier,
                        {"type": "action_gated", "cmd": "extrude", "reason": "printing"})
                except Exception:
                    pass
                return
            self._extrude_preserving(amt, spd)

        elif command == "fan":
            # Ventilador (M106 S0-255 / M107). Aceptamos 0-100 (%). 'index' = P de
            # M106 (0 = ventilador de capa principal -> se envía sin P por compat.).
            v = self._clamp_int(data.get("value", 0), 0, 100)
            try:
                idx = int(data.get("index", 0) or 0)
            except (TypeError, ValueError):
                idx = 0
            p = "" if idx <= 0 else " P{:d}".format(idx)
            if v <= 0:
                self._send("M107" + p)
            else:
                self._send("M106{} S{:d}".format(p, int(round(v * 255 / 100.0))))

        elif command == "set_mode":
            # Cambia el modo de posicionamiento de forma EXPLÍCITA (botón del panel).
            # OJO: G90/G91 también afectan a E, así que tras conmutar XYZ re-aplicamos
            # el modo de E para mantenerlo independiente (como en el jog).
            target = data.get("target")
            rel = bool(data.get("relative"))
            t = self._tracker
            if target == "xyz":
                # captura el modo de E ANTES (G90/G91 lo arrastra) para reponerlo
                abs_e = t.abs_e if t is not None else True
                self._send("G91" if rel else "G90")
                self._send("M82" if abs_e else "M83")
            elif target == "e":
                self._send("M83" if rel else "M82")

        elif command == "settemp":
            heater = data.get("heater", "tool0")
            if heater not in ("bed", "chamber") and not re.match(r"^tool\d+$", heater):
                return
            try:
                self._printer.set_temperature(heater, float(data.get("value", 0)))
            except (TypeError, ValueError):
                pass

        elif command == "motors_off":
            self._send("M18")

        elif command == "load":
            if self._printer.is_printing():
                try:
                    self._plugin_manager.send_plugin_message(
                        self._identifier,
                        {"type": "action_gated", "cmd": "load", "reason": "printing"})
                except Exception:
                    pass
                return
            c = self._settings.get(["control", "load"], merged=True) or {}
            ln = self._num(data.get("len"), c.get("len", 90))
            sp = int(self._num(data.get("speed"), c.get("speed", 600)))
            pr = self._num(data.get("prime"), c.get("prime", 10))
            psp = int(self._num(data.get("prime_speed"), c.get("prime_speed", 100)))
            self._extrude_seq_preserving([
                "G1 E{:.1f} F{:d}".format(ln, sp),
                "G1 E{:.1f} F{:d}".format(pr, psp),
            ])

        elif command == "unload":
            if self._printer.is_printing():
                try:
                    self._plugin_manager.send_plugin_message(
                        self._identifier,
                        {"type": "action_gated", "cmd": "unload", "reason": "printing"})
                except Exception:
                    pass
                return
            c = self._settings.get(["control", "unload"], merged=True) or {}
            ln = self._num(data.get("len"), c.get("len", 95))
            sp = int(self._num(data.get("speed"), c.get("speed", 600)))
            self._extrude_seq_preserving([
                "G1 E-{:.1f} F{:d}".format(ln, sp),
            ])

        elif command == "cold_extrude":
            # M302: permite/prohíbe extrudir en frío. P0 = permitir, P1 = prohibir.
            # Opcional min_temp -> S<temp> fija la temperatura mínima de extrusión.
            allow = bool(data.get("allow"))
            cmd = "M302 P0" if allow else "M302 P1"
            mt = data.get("min_temp")
            if mt is not None:
                cmd += " S{:d}".format(int(self._num(mt, 170)))
            self._send(cmd)

        elif command == "get_limits":
            # M503 -> el hook gcode.received parsea M201/M203/M204/M205 y los empuja
            self._send("M503")

        elif command == "viewstate":
            # Persiste el estado del visor 3D (modelo cargado, selección, prefs) para
            # que sobreviva a recargas y se comparta con otros navegadores.
            st = data.get("state")
            if isinstance(st, dict):
                self._viewstate = st

        elif command == "list_objects":
            self._push_objects()

        elif command == "cancel_object":
            self._cancel_object(data.get("index"), cancel=True)

        elif command == "uncancel_object":
            self._cancel_object(data.get("index"), cancel=False)

        elif command == "skip_to":
            self._skip_to(data)

        elif command == "realtime_move":
            axis = data.get("axis", "").lower()
            direction = 1 if str(data.get("dir", "1")) == "1" else -1
            if axis not in ("x", "y", "z", "e"):
                return
            tags_rt = {"plugin:boostkit:instant"}
            if axis == "e":
                mx = self._settings.get(["realtime_numpad", "extrude_max_len"], merged=True) or 50
                spd = self._settings.get(["realtime_numpad", "extrude_speed"], merged=True) or self._settings.get(["control", "extrude_speed"], merged=True) or 300
                self._send("G1 E{:.1f} F{:d}".format(float(mx) * direction, int(spd)), tags=tags_rt)
            else:
                spd = int(self._settings.get(["control", "jog_speed_z"], merged=True) or 600) if axis == "z" else int(self._settings.get(["control", "jog_speed_xy"], merged=True) or 3000)
                self._send("G1 {}{:.1f} F{:d}".format(axis.upper(), 9999.0 * direction, spd), tags=tags_rt)

        elif command == "realtime_stop":
            self._send("M410")

        elif command == "calib_caps":
            self._plugin_manager.send_plugin_message(
                self._identifier, {"type": "calib_caps", "caps": self._calib_caps()})

        elif command == "calib_run":
            self._calib_run(data)

        elif command == "calib_pid":
            self._calib.run_pid(
                heater=data.get("heater", "hotend"),
                cycles=self._clamp_int(data.get("cycles", 8), 3, 20),
                temp=self._num(data.get("temp"), 200),
                fan=self._clamp_int(data.get("fan", 255), 0, 255),
                idx=self._clamp_int(data.get("idx", 0), 0, 8),
                save=bool(data.get("save", False)))

        elif command == "calib_esteps_read":
            m92 = self._calib.read_m92()
            self._plugin_manager.send_plugin_message(
                self._identifier, {"type": "calib", "event": "esteps_m92", "m92": m92})

        elif command == "calib_esteps_extrude":
            self._calib.esteps_extrude(
                self._num(data.get("temp"), 210),
                self._num(data.get("length"), 100),
                self._num(data.get("speed"), 3))

        elif command == "calib_apply":
            lines = data.get("gcode") or []
            if isinstance(lines, str):
                lines = [lines]
            self._calib.apply_gcode(lines, save=bool(data.get("save", False)))

        elif command == "calib_preset_save":
            d = self._calib.preset_save(data.get("test"), data.get("name"),
                                        data.get("params") or {})
            self._push_presets(d)

        elif command == "calib_preset_delete":
            d = self._calib.preset_delete(data.get("test"), data.get("name"))
            self._push_presets(d)

        elif command == "calib_presets":
            self._push_presets(self._calib.presets())

        elif command == "calib_history":
            self._push_history(self._calib.history())

        elif command == "calib_history_add":
            self._calib.history_add(data)
            self._push_history(self._calib.history())

        elif command == "calib_history_mark":
            self._calib.history_mark(self._clamp_int(data.get("id"), 0, 10**9),
                                     data.get("result"), data.get("notes"),
                                     data.get("measured"))
            self._push_history(self._calib.history())

        elif command == "calib_tests":
            self._plugin_manager.send_plugin_message(
                self._identifier, {"type": "calib_tests", "tests": self._calib.tests()})

        elif command == "calib_test_save":
            if self._calib.test_save(data.get("def") or {}):
                self._plugin_manager.send_plugin_message(
                    self._identifier, {"type": "calib_tests", "tests": self._calib.tests()})

        elif command == "calib_test_delete":
            self._calib.test_delete(data.get("id"))
            self._plugin_manager.send_plugin_message(
                self._identifier, {"type": "calib_tests", "tests": self._calib.tests()})

        elif command == "calib_test_render":
            test = data.get("test")
            try:
                gcode, info = self._calib_render(test, data)
                traces = calibration.extract_traces(gcode)
                # Persiste el gcode para el botón "Ver en 3D" (Enhanced Viewer).
                preview_file = None
                if data.get("want_file"):
                    preview_file = self._calib_write(test, gcode, preview=True)
                self._plugin_manager.send_plugin_message(
                    self._identifier, {"type": "calib_preview", "test": test,
                                       "traces": traces, "lines": gcode.count("\n"),
                                       "file": preview_file, "count": info.get("count"),
                                       "bands": info.get("bands"), "overflow": info.get("overflow")})
            except Exception as e:
                self._plugin_manager.send_plugin_message(
                    self._identifier, {"type": "calib_preview", "test": test,
                                       "error": str(e)})

        elif command == "calib_backlash_start":
            self._calib.run_backlash_sweep(
                axis=str(data.get("axis", "x")).lower(),
                m425_start=self._num(data.get("m425_start"), 0.0),
                m425_end=self._num(data.get("m425_end"), 0.2),
                m425_step=self._num(data.get("m425_step"), 0.02),
                ref_pos=self._num(data.get("ref_pos"), 50.0),
                back_off=self._num(data.get("back_off"), 5.0),
                target=self._num(data.get("target"), -1.0),
                feed=self._clamp_int(data.get("feed"), 60, 600),
                samples_per=self._clamp_int(data.get("samples_per"), 1, 10),
                save=bool(data.get("save", False)))

        elif command == "calib_backlash_cancel":
            self._calib.cancel_backlash_sweep()

    # ---- Calibración: generar patrón -> fichero -> opcional imprimir ----
    def _push_presets(self, d):
        self._plugin_manager.send_plugin_message(
            self._identifier, {"type": "calib_presets", "presets": d})

    def _push_history(self, h):
        self._plugin_manager.send_plugin_message(
            self._identifier, {"type": "calib_history", "history": h})

    def _calib_render(self, test, data):
        """Genera el test. `data` puede traer:
          - `fixed` (dict) + `axes` ([{k,start,end,step}]) -> MATRIZ multi-variable.
          - `template` (Jinja del editor) -> render simple (1 espécimen).
          - `params` legacy / nada -> defaults del esquema.
        Devuelve (gcode, info) con info={count,bands,overflow}."""
        caps = self._calib_caps()
        tdef = self._calib.test_get(test) or None
        template = data.get("template")
        if template:   # editor "Probar": render simple del template Jinja
            g = calibration.render_template(template, caps, data.get("fixed") or data.get("params") or {},
                                            builtin_id=test)
            return g, {"count": 1, "bands": 1, "overflow": False}
        fixed = data.get("fixed")
        axes = data.get("axes")
        if fixed is None and axes is None:
            params = data.get("params") or {}
            if tdef:
                fixed, axes = calibration.default_args(tdef)
                fixed.update(params)
            else:
                return calibration.generate(test, caps, params), {"count": 1, "bands": 1, "overflow": False}
        return calibration.render_matrix(test, fixed or {}, axes or [], caps, test_def=tdef)

    def _calib_write(self, test, gcode, preview=False):
        """Escribe el gcode del test en uploads. Devuelve el nombre (relativo a local) o
        None. `preview=True` usa un nombre fijo reutilizable (para el visor 3D)."""
        try:
            uploads = self._file_manager.path_on_disk("local", "")
        except Exception:
            uploads = self.get_plugin_data_folder()
        safe = re.sub(r"[^a-z0-9_]", "", str(test).lower())
        name = "bk_calib_preview.gcode" if preview else "bk_calib_{}.gcode".format(safe)
        disk = os.path.join(uploads, name)
        try:
            with open(disk, "w", encoding="utf-8") as f:
                f.write(gcode)
        except (OSError, IOError) as e:
            self._logger.warning("calib_write: no se pudo escribir %s: %s", disk, e)
            return None
        return name

    def _calib_run(self, data):
        test = data.get("test")
        params = data.get("params") or {}
        try:
            gcode, _info = self._calib_render(test, data)
        except Exception as e:
            self._logger.warning("calib_run %s falló: %s", test, e)
            self._plugin_manager.send_plugin_message(
                self._identifier, {"type": "calib", "event": "gen_error",
                                   "test": test, "msg": str(e)})
            return
        # Escribe el patrón en uploads y opcionalmente lo imprime.
        name = self._calib_write(test, gcode)
        if name is None:
            return
        try:
            uploads = self._file_manager.path_on_disk("local", "")
        except Exception:
            uploads = self.get_plugin_data_folder()
        disk = os.path.join(uploads, name)
        started = False
        if data.get("print"):
            try:
                self._printer.select_file(disk, False, printAfterSelect=True)
                started = True
            except Exception as e:
                self._logger.warning("calib_run: select_file falló: %s", e)
        # registra en histórico
        self._calib.history_add({"test": test, "params": params})
        self._plugin_manager.send_plugin_message(
            self._identifier, {"type": "calib", "event": "generated", "test": test,
                               "file": name, "started": started,
                               "lines": gcode.count("\n")})
        self._push_history(self._calib.history())

    # ---- Cancel-object ----
    def _cancel_object(self, index, cancel):
        if self._index is None:
            return
        try:
            i = int(index)
        except (TypeError, ValueError):
            return
        if i < 0 or i >= len(self._index.objects):
            return
        if cancel:
            self._cancelled.add(i)
            caps = self.job_caps()
            if caps.get("m486"):
                # M486 P<idx>: el firmware deja de imprimir ese objeto al instante.
                self._send("M486 P{:d}".format(i))
            self._logger.info("Cancelado objeto %d (%s)", i,
                              self._index.objects[i]["name"])
        else:
            self._cancelled.discard(i)
            caps = self.job_caps()
            if caps.get("m486"):
                self._send("M486 U{:d}".format(i))
            self._logger.info("Reactivado objeto %d", i)
        self._push_objects()

    # ---- Skip-to: saltar a un checkpoint reconstruyendo el estado ----
    def _resolve_checkpoint(self, data):
        """Elige el checkpoint destino desde {checkpoint|layer|offset}."""
        idx = self._index
        if idx is None or not idx.checkpoints:
            return None
        if data.get("checkpoint") is not None:
            try:
                return idx.checkpoints[int(data["checkpoint"])]
            except (ValueError, IndexError):
                return None
        if data.get("layer") is not None:
            try:
                return idx.checkpoint_for_layer(int(data["layer"]))
            except (TypeError, ValueError):
                return None
        if data.get("offset") is not None:
            try:
                off = int(data["offset"])
            except (TypeError, ValueError):
                return None
            best = None
            for cp in idx.checkpoints:
                if cp["pos"] <= off:
                    best = cp
                else:
                    break
            return best or idx.checkpoints[0]
        return None

    def _build_skip_prelude(self, cp, caps):
        """Reconstruye el estado de máquina del checkpoint como una lista de gcode.
        Cinemática-segura: el lift/home solo se hace en planar-cartesiano; en no
        planar / multi-eje se respeta park_gcode o se omite (con aviso)."""
        st = cp["state"]
        planar = caps.get("planar")
        g = ["; --- Boost Kit skip-to: preludio de estado ---"]
        g.append("G21" if st.get("units_mm", True) else "G20")
        # Temperaturas: siempre se SETEAN; la espera (M109/M190) se omite si el
        # usuario activó skip_temp_wait (reanudar en caliente sin esperar).
        wait = not bool(self._settings.get(["job", "skip_temp_wait"], merged=True))
        if st.get("bed") is not None:
            g.append("M140 S{:.0f}".format(st["bed"]))
        if st.get("hotend") is not None:
            g.append("M104 S{:.0f}".format(st["hotend"]))
        if wait and st.get("bed") is not None:
            g.append("M190 S{:.0f}".format(st["bed"]))
        if wait and st.get("hotend") is not None:
            g.append("M109 S{:.0f}".format(st["hotend"]))
        fan = st.get("fan", 0)
        g.append("M107" if not fan else "M106 S{:.0f}".format(fan))
        # Homing + recolocación SOLO en planar-cartesiano.
        skip_home = bool(self._settings.get(["job", "skip_home"], merged=True))
        if planar:
            if skip_home:
                g.append("G28")
            g.append("G90")
            zt = st.get("z", 0.0)
            g.append("G1 Z{:.3f} F{:d}".format(zt + 5.0, 1200))   # subir a Z seguro
            g.append("G1 X{:.3f} Y{:.3f} F{:d}".format(
                st.get("x", 0.0), st.get("y", 0.0), 6000))
            g.append("G1 Z{:.3f} F{:d}".format(zt, 1200))          # bajar a la capa
        else:
            # No planar / multi-eje: no inventamos trayectoria. Usa la macro del
            # usuario si existe; si no, solo declara la pose (asume máquina colocada).
            park = self._settings.get(["job", "park_gcode"], merged=True)
            if park:
                g += self._extra_lines(park)
            self._logger.info(
                "skip-to no planar/multi-eje: sin lift automático (park_gcode=%s)",
                "sí" if park else "no")
        # Restaurar modal de ejes y extrusor.
        g.append("G90" if st.get("abs_xyz", True) else "G91")
        g.append("M82" if st.get("abs_e", True) else "M83")
        # Redefinir la posición lógica a la pose capturada (TODOS los ejes presentes).
        g92 = "G92 E{:.5f}".format(st.get("e", 0.0))
        if not planar:
            # En multi-eje declaramos también XYZ + ejes extra (la máquina ya está ahí).
            g92 = "G92 X{:.3f} Y{:.3f} Z{:.3f} E{:.5f}".format(
                st.get("x", 0.0), st.get("y", 0.0), st.get("z", 0.0), st.get("e", 0.0))
            for ax, val in (st.get("axes") or {}).items():
                g92 += " {}{:.3f}".format(ax, val)
        g.append(g92)
        if st.get("f"):
            g.append("G1 F{:d}".format(int(st["f"])))
        g.append("; --- fin del preludio; sigue el gcode original ---")
        return g

    def _skip_to(self, data):
        idx = self._index
        if idx is None or self._index_path is None:
            self._logger.warning("skip-to: no hay fichero indexado")
            return
        cp = self._resolve_checkpoint(data)
        if cp is None:
            self._logger.warning("skip-to: no se pudo resolver el checkpoint")
            self._plugin_manager.send_plugin_message(
                self._identifier, {"type": "skip_result", "ok": False,
                                   "msg": "Sin checkpoint válido (¿fichero no planar sin marcadores?)"})
            return
        caps = self.job_caps()
        prelude = self._build_skip_prelude(cp, caps)
        try:
            with open(self._index_path, "rb") as f:
                f.seek(cp["pos"])
                tail = f.read()
        except (OSError, IOError) as e:
            self._logger.warning("skip-to: no se pudo leer la cola: %s", e)
            return
        body = ("\n".join(prelude) + "\n").encode("utf-8") + tail
        # Escribe <nombre>.skip.gcode junto al original (carpeta de uploads local).
        base = os.path.basename(self._index_path)
        stem = re.sub(r"\.(gco|gcode|g)$", "", base, flags=re.I)
        skip_name = stem + ".skip.gcode"
        skip_disk = os.path.join(os.path.dirname(self._index_path), skip_name)
        try:
            with open(skip_disk, "wb") as f:
                f.write(body)
        except (OSError, IOError) as e:
            self._logger.warning("skip-to: no se pudo escribir %s: %s", skip_disk, e)
            return
        start = bool(data.get("start_print", False))
        try:
            self._printer.select_file(skip_disk, False, printAfterSelect=start)
        except Exception as e:
            self._logger.warning("skip-to: select_file falló: %s", e)
            return
        self._logger.info("skip-to: %s (capa %s, pos %d, start=%s)",
                          skip_name, cp.get("layer"), cp["pos"], start)
        self._plugin_manager.send_plugin_message(
            self._identifier, {"type": "skip_result", "ok": True,
                               "file": skip_name, "layer": cp.get("layer"),
                               "started": start})

    # ---- Tracker de posición: override de los handlers de comm ----
    # ---- Hot-tune: registro de movimientos del job (para recolocación) ----
    def _pose(self):
        t = self._tracker
        if t is None:
            return {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0, "axes": {}}
        return {"x": t.x, "y": t.y, "z": t.z, "e": t.e, "axes": dict(t.axes)}

    def _record_move(self, kind, tags, start, end, e_total, feedrate, arc=None):
        """Guarda un movimiento del JOB en el ring buffer (solo source:file)."""
        if not tags or "source:file" not in tags:
            return
        fp = None
        for tg in tags:
            if tg.startswith("filepos:"):
                try:
                    fp = int(tg[8:])
                except ValueError:
                    fp = None
                break
        if fp is None:
            return
        start_off = self._last_filepos
        self._last_filepos = fp
        self._move_log.append({
            "kind": kind, "start_offset": start_off, "end_offset": fp,
            "start": start, "end": end, "e_total": e_total,
            "feedrate": int(feedrate or 1500), "arc": arc,
        })
        # Cascada: la entrada ANTERIOR ahora termina donde empieza esta (end_offset
        # apunta a la posición de la línea siguiente, no a la propia).
        if len(self._move_log) >= 2:
            self._move_log[-2]["end_offset"] = fp

    # ---- Hot-tune: corrección EN CALIENTE ----------------------------------
    @staticmethod
    def _gcode_token(cmd):
        m = re.match(r"\s*([GM]\d+)", str(cmd), re.I)
        return m.group(1).upper() if m else ""

    def _is_extrude_cmd(self, cmd):
        """G0/G1 SOLO con E (extruir/retraer manual)."""
        g = self._gcode_token(cmd)
        return (g in ("G0", "G1") and _axis(cmd, "E") is not None
                and not any(_axis(cmd, a) is not None for a in ("X", "Y", "Z")))

    def _is_head_move(self, cmd):
        """G0/G1/G2/G3 que mueve el CABEZAL (X/Y/Z)."""
        g = self._gcode_token(cmd)
        return (g in ("G0", "G1", "G2", "G3")
                and any(_axis(cmd, a) is not None for a in ("X", "Y", "Z")))

    def _hot_wrappable(self, gcode, cmd, ht):
        """¿El comando (NO del job) debe pasar por el ciclo hot-tune?"""
        g = (gcode or "").upper()
        if g in (ht.get("allow") or []):
            return True
        if self._is_extrude_cmd(cmd):
            return True
        if self._is_head_move(cmd):
            return True
        return False

    def _hot_enqueue(self, cmd, ht):
        with self._hot_lock:
            self._hot_pending.append(cmd)
            if self._hot_busy:
                return
            self._hot_busy = True
        ms = max(0, int(ht.get("debounce_ms", 80)))
        threading.Timer(ms / 1000.0, self._hot_run).start()

    def _hot_run(self):
        with self._hot_lock:
            cmds = self._hot_pending
            self._hot_pending = []
        if cmds:
            try:
                self._hot_cycle(cmds)
            except Exception as e:
                self._logger.warning("hot-tune: ciclo falló: %s", e)
                # Safety: release hold if hot-tune crashed mid-cycle
                comm = getattr(self._printer, "_comm", None)
                if comm is not None:
                    try:
                        comm.set_job_on_hold(False)
                    except Exception:
                        pass
        with self._hot_lock:
            if self._hot_pending:
                threading.Timer(0.0, self._hot_run).start()
            else:
                self._hot_busy = False

    def _hot_cycle(self, cmds):
        """Corrige impresión EN CALIENTE con efecto inmediato (M410).

        M410 limpia el planner → movimientos en buffer se pierden. La estrategia
        es recolocar el cabezal (G1 + G92) y dejar que OctoPrint re-envíe la línea
        interrumpida de forma natural (sin síntesis, que duplica el segmento).

        Tipos de corrección:
          - Config (M220, M204, M900…): M410 + enviar + recolocar + reanudar
          - Jog   (G0/G1 con X/Y/Z):    M290 P0 (babystep), mismo ciclo
          - Extrude (G1 solo E):        M410 + purga + G92 reset E + recolocar
        """
        ht = self._settings.get(["hottune"], merged=True) or {}
        self._logger.info("hot-tune: %d cmd(s): %s", len(cmds), cmds)

        # --- Fase 1: M410 + hold del stream ---
        self._send("M410")
        comm = getattr(self._printer, "_comm", None)
        held = False
        if comm is not None:
            try:
                comm.set_job_on_hold(True)
                held = True
            except Exception:
                pass

        try:
            # --- Fase 2: Capturar pose (encoder M114 R E + fallback tracker) ---
            pose, phys = self._hot_capture_pose()
            if not phys:
                try:
                    self._plugin_manager.send_plugin_message(
                        self._identifier,
                        {"type": "hottune", "event": "no_realtime"})
                except Exception:
                    pass

            # --- Fase 3: Emparejar movimiento interrumpido ---
            move, f = hottune.match_move(list(self._move_log), pose)
            if move is None:
                try:
                    self._plugin_manager.send_plugin_message(
                        self._identifier,
                        {"type": "hottune", "event": "no_match"})
                except Exception:
                    pass
            if move is not None and pose.get("e") is None:
                pose["e"] = (float(move["start"].get("e", 0.0))
                             + float(move.get("e_total", 0.0)) * f)
        
            # --- Fase 4: Convertir jog a M290 P0 ---
            is_jog = any(self._is_head_move(c) for c in cmds)
            is_extrude = any(self._is_extrude_cmd(c) for c in cmds)

            send_cmds = []
            for c in cmds:
                c_str = str(c).strip()
                if self._is_head_move(c_str):
                    parts = []
                    for ax in ("X", "Y", "Z"):
                        val = _axis(c_str, ax)
                        if val is not None:
                            parts.append("{}{:.4f}".format(ax, val))
                    if parts:
                        send_cmds.append("M290 P0 " + " ".join(parts))
                else:
                    send_cmds.append(c_str)

            # --- Fase 5: Enviar comandos + G92 reset de E si extrusión ---
            self._send(send_cmds, tags={"plugin:boostkit:instant"})
            if is_extrude:
                self._send(["G92 E{:.5f}".format(pose.get("e", 0.0))],
                           tags={"plugin:boostkit:instant"})

            # --- Fase 6: Recolocar + sintetizar segmento + reanudar ---
            t = self._tracker
            abs_e = t.abs_e if t is not None else True
            abs_xyz = t.abs_xyz if t is not None else True
            spd = int(self._settings.get(["control", "jog_speed_xy"], merged=True) or 3000)

            if phys:
                repos = "G1 X{:.4f} Y{:.4f} Z{:.4f} F{:d}".format(
                    pose["x"], pose["y"], pose["z"], spd)
            else:
                repos = "G1 X{:.4f} Y{:.4f} F{:d}".format(pose["x"], pose["y"], spd)
                self._logger.info("hot-tune: Z no fiable (sin M114) -> recoloco solo X/Y")

            lines = ["G90", repos]
            lines.append("G92 E{:.5f}".format(pose.get("e", 0.0) or 0.0))
            for k, v in (pose.get("axes") or {}).items():
                lines.append("G92 {}{:.4f}".format(k.upper(), v))

            if move is not None:
                move["_p_real"] = pose
                lines += ["M83"] + hottune.synth_completion(move, f) + ["M82" if abs_e else "M83"]

            lines += ["G90" if abs_xyz else "G91"]
            self._send(lines, tags={"plugin:boostkit:instant"})

            # --- Fase 7: Seek (defensivo: comm._currentFile es API privada) ---
            if move is not None and comm is not None:
                cur = getattr(comm, "_currentFile", None)
                seek = getattr(cur, "seek", None) if cur is not None else None
                if callable(seek):
                    try:
                        seek(move["end_offset"])
                        # Si end_offset no fue cascaded (última entrada del log),
                        # salta la línea interrumpida para reanudar en la siguiente.
                        readline = getattr(cur, "readline", None)
                        if callable(readline):
                            try:
                                readline()
                            except Exception:
                                pass
                        self._logger.info("hot-tune: recolocado + seek=%d (f=%.2f, %s)",
                                          move["end_offset"], f, move["kind"])
                    except Exception as e:
                        self._logger.warning("hot-tune: seek a %s falló: %s",
                                             move.get("end_offset"), e)
                else:
                    self._logger.info("hot-tune: recolocado (sin seek disponible)")
            else:
                self._logger.info("hot-tune: recolocado (sin match de segmento)")

            self._hot_notify(cmds)
        finally:
            # SIEMPRE liberar el hold del stream: cualquier excepción intermedia sin
            # esto dejaría el job congelado permanentemente.
            if held and comm is not None:
                try:
                    comm.set_job_on_hold(False)
                except Exception:
                    pass



    def _hot_capture_pose(self):
        """Captura la pose actual. PRIMARIO = M114 R E (encoder, fiable). Fallback
        = tracker (lógico) para ejes que M114 no reporte."""
        t = self._tracker
        track = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0, "axes": {}}
        if t is not None:
            track = {"x": t.x, "y": t.y, "z": t.z, "e": t.e, "axes": dict(t.axes or {})}
        m114 = self._hot_read_pose()
        phys = m114 is not None
        pose = dict(m114) if phys else dict(track)
        pose.setdefault("axes", {})
        for k in ("x", "y", "z", "e"):
            if pose.get(k) is None:
                pose[k] = track.get(k, 0.0)
        for k, v in track["axes"].items():
            pose["axes"].setdefault(k, v)
        return pose, phys

    def _hot_read_pose(self, timeout=3.0):
        self._hot_pose = None
        self._hot_pose_event.clear()
        self._hot_await_pose = True
        try:
            self._send("M114 R E")
        except Exception:
            pass
        self._hot_pose_event.wait(timeout)
        self._hot_await_pose = False
        return self._hot_pose

    @staticmethod
    def _parse_pose_line(line):
        m = re.search(r"[MW]Pos:([-\d.]+),([-\d.]+),([-\d.]+)", line)
        if m:
            return {"x": float(m.group(1)), "y": float(m.group(2)),
                    "z": float(m.group(3)), "e": None, "axes": {}}
        if "Count" in line and "X:" in line:
            mx = re.search(r"(?:^|\s)X:([-\d.]+)", line)
            my = re.search(r"(?:^|\s)Y:([-\d.]+)", line)
            mz = re.search(r"(?:^|\s)Z:([-\d.]+)", line)
            me = re.search(r"(?:^|\s)E:([-\d.]+)", line)
            if mx and my and mz:
                pose = {"x": float(mx.group(1)), "y": float(my.group(1)),
                        "z": float(mz.group(1)),
                        "e": float(me.group(1)) if me else None, "axes": {}}
                for ax in ("A", "B", "C", "U", "V", "W"):
                    ma = re.search(r"(?:^|\s)%s:([-\d.]+)" % ax, line)
                    if ma:
                        pose["axes"][ax] = float(ma.group(1))
                return pose
        return None

    def _hot_notify(self, cmds):
        try:
            self._plugin_manager.send_plugin_message(
                self._identifier, {"type": "hottune",
                                   "cmds": [str(c) for c in cmds]})
        except Exception:
            pass

    def _install_tracker(self, comm):
        """Reemplaza en la instancia `comm` los handlers de tracking de Z por unos
        mode-aware y añade los que OctoPrint no tiene (G90/G91/G92/M82/M83).

        OctoPrint despacha por getattr(self, "_gcode_<GCODE>_sent"), así que basta
        con poner estos atributos (como MethodType, para que reciban self=comm).
        """
        if comm is None or self._tracker is None:
            return
        if self._tracker_installed_on is comm:
            return  # ya instalado en esta conexión

        tracker = self._tracker
        plugin = self

        def move_handler(comm_self, command, cmd_type=None, subcode=None, tags=None, **kw):
            start = plugin._pose()
            z_changed = tracker.feed_move(command)
            end = plugin._pose()
            plugin._record_move("line", tags, start, end,
                                end["e"] - start["e"], tracker.f or 1500)
            # F sigue rastreándose como hacía OctoPrint
            if comm_self._currentF is not None or _axis(command, "F") is not None:
                f = _axis(command, "F")
                if f is not None:
                    comm_self._currentF = f
            # Trail para el visor 3D: alimenta cada movimiento (throttled).
            plugin._feed_trail()
            # Z CORRECTA hacia el currentZ nativo + evento ZChange (override)
            if z_changed and comm_self._currentZ != tracker.z:
                comm_self._currentZ = tracker.z
                plugin._flush_trail()   # empuja ya el punto del cambio de capa
                try:
                    comm_self._callback.on_comm_z_change(tracker.z)
                except Exception:
                    pass
                plugin._push_state()
            return None

        def mode_handler(fn):
            def handler(comm_self, command, cmd_type=None, subcode=None, tags=None, **kw):
                fn(command)
                # notifica al frontend el cambio de modo/posición (G90/G91/M82/M83/
                # G92) para que los toggles abs/rel del panel se actualicen en vivo.
                plugin._push_state()
                return None
            return handler

        def home_handler(comm_self, command, cmd_type=None, subcode=None, tags=None, **kw):
            # G28: NO asumimos 0,0,0 (con Z_SAFE_HOMING la posición final no es el
            # origen). La posición REAL la dan los reportes de auto-posición
            # (M154/Grbl durante el homing) y el M114 que OctoPrint manda tras G28,
            # que el hook gcode_received parsea y sincroniza en el visor.
            tracker.f = 3000
            return None

        def feedfactor_handler(comm_self, command, cmd_type=None, subcode=None, tags=None, **kw):
            s = _axis(command, "S")
            if s is not None:
                plugin._feed_factor = max(10, min(500, s))
            try:
                if plugin._parse_fw_line(command):
                    plugin._push_fw()
            except Exception:
                pass
            return None

        def flowfactor_handler(comm_self, command, cmd_type=None, subcode=None, tags=None, **kw):
            s = _axis(command, "S")
            if s is not None:
                plugin._flow_factor = max(10, min(500, s))
            try:
                if plugin._parse_fw_line(command):
                    plugin._push_fw()
            except Exception:
                pass
            return None

        def limits_set_handler(comm_self, command, cmd_type=None, subcode=None, tags=None, **kw):
            # M201/M203/M204/M205 enviados DURANTE el print (algunos slicers los
            # cambian por feature) -> actualiza límites en vivo y avisa al visor.
            try:
                plugin._parse_limits(command)
            except Exception:
                pass
            try:
                if plugin._parse_fw_line(command):
                    plugin._push_fw()
            except Exception:
                pass
            return None

        def fw_set_handler(comm_self, command, cmd_type=None, subcode=None, tags=None, **kw):
            # Cualquier M-code de config de movimiento (M900/M906/M200/M425/M851/
            # M593/M209/M420/M92/M106...) enviado en vivo -> refleja en el panel.
            try:
                if plugin._parse_fw_line(command):
                    plugin._push_fw()
            except Exception:
                pass
            return None

        def opaque_handler(comm_self, command, cmd_type=None, subcode=None, tags=None, **kw):
            return None

        def seg_handler(genfn):
            # arco/bézier: interpola en segmentos y los empuja al trail
            def handler(comm_self, command, cmd_type=None, subcode=None, tags=None, **kw):
                start = plugin._pose()
                segs = genfn(command)
                if segs is None:
                    return move_handler(comm_self, command, tags=tags)  # sin centro -> lineal
                end = plugin._pose()
                arc = dict(tracker.last_arc) if tracker.last_arc else None
                plugin._record_move("arc", tags, start, end,
                                    end["e"] - start["e"], tracker.f or 1500, arc)
                for (qx, qy, qz, ef, d, ed) in segs:
                    plugin._push_trail_point(qx, qy, qz, ef == 1, tracker.f, ed, d)
                plugin._flush_trail()
                if comm_self._currentZ != tracker.z:
                    comm_self._currentZ = tracker.z
                    try:
                        comm_self._callback.on_comm_z_change(tracker.z)
                    except Exception:
                        pass
                    plugin._push_state()
                return None
            return handler

        comm._gcode_G0_sent = types.MethodType(move_handler, comm)
        comm._gcode_G1_sent = types.MethodType(move_handler, comm)
        comm._gcode_G2_sent = types.MethodType(seg_handler(lambda c: tracker.feed_arc(c, True)), comm)
        comm._gcode_G3_sent = types.MethodType(seg_handler(lambda c: tracker.feed_arc(c, False)), comm)
        comm._gcode_G5_sent = types.MethodType(seg_handler(lambda c: tracker.feed_bezier(c)), comm)
        comm._gcode_G28_sent = types.MethodType(home_handler, comm)
        comm._gcode_M220_sent = types.MethodType(feedfactor_handler, comm)
        comm._gcode_M221_sent = types.MethodType(flowfactor_handler, comm)
        for code in ("M201", "M203", "M204", "M205"):
            setattr(comm, "_gcode_%s_sent" % code,
                    types.MethodType(limits_set_handler, comm))
        # Resto de config de movimiento -> tracking en vivo en el panel de control.
        for code in ("M92", "M900", "M906", "M907", "M200", "M425", "M851", "M206",
                     "M209", "M207", "M208", "M302", "M420", "M593", "M106", "M107"):
            setattr(comm, "_gcode_%s_sent" % code,
                    types.MethodType(fw_set_handler, comm))
        # Comandos OPACOS al stream (el firmware genera el movimiento él mismo):
        # homing, probing, leveling, parking, cambio de filamento.
        for code in ("G12", "G27", "G29", "G30", "G33", "G34", "G38",
                     "G42", "G76", "M125", "M600", "M701", "M702"):
            setattr(comm, "_gcode_%s_sent" % code,
                    types.MethodType(opaque_handler, comm))
        comm._gcode_G90_sent = types.MethodType(mode_handler(lambda c: tracker.set_abs_xyz(True)), comm)
        comm._gcode_G91_sent = types.MethodType(mode_handler(lambda c: tracker.set_abs_xyz(False)), comm)
        comm._gcode_M82_sent = types.MethodType(mode_handler(lambda c: tracker.set_e_abs(True)), comm)
        comm._gcode_M83_sent = types.MethodType(mode_handler(lambda c: tracker.set_e_abs(False)), comm)
        comm._gcode_G92_sent = types.MethodType(mode_handler(tracker.set_position), comm)

        self._tracker_installed_on = comm
        self._logger.info(
            "Tracker instalado: override de _gcode_G0/G1/G90/G91/G92/M82/M83_sent en comm."
        )

    def _push_possync(self):
        """Empuja la posición actual + config (límites, feed/flow factor) para que
        un cliente recién cargado sincronice sin esperar al siguiente movimiento."""
        if self._tracker is None:
            return
        try:
            self._plugin_manager.send_plugin_message(self._identifier, {
                "type": "possync",
                "state": self._tracker.state(),
                "limits": self._limits,
                "fw": self._fw,
                "feed_factor": self._feed_factor,
                "flow_factor": self._flow_factor,
                "history": self._trail_history[-1500:],   # trazado persistente
                "viewstate": self._viewstate,             # modelo + selección + prefs
                "slots": self._get_slots(),               # slots de malla (nombre+Z)
                "slot_count": self._slot_count(),
                "leveling": (self._leveling.state() if self._leveling is not None else None),
                "leveling_caps": (self._leveling.caps_state() if self._leveling is not None else None),
            })
        except Exception:
            pass
        # si aún no conocemos la config del firmware, pídela
        if self._limits is None:
            try:
                self._send("M503")
            except Exception:
                pass

    def _push_state(self):
        # Empuja el estado del tracker al frontend (para la pestaña).
        if self._tracker is not None:
            try:
                self._plugin_manager.send_plugin_message(self._identifier, {
                    "type": "trackstate", "state": self._tracker.state(),
                })
            except Exception:
                pass

    # ---- Trail de movimientos para el visor 3D ----
    def _feed_trail(self):
        """Acumula el punto actual del cabezal y lo empuja batcheado (throttle por
        tiempo). e_flag=1 si el último movimiento extruía (trazo) o 0 (travel)."""
        t = self._tracker
        if t is None:
            return
        self._push_trail_point(t.x, t.y, t.z, t.printing, t.f,
                               t.last_e_delta, t.last_dist)

    def _push_trail_point(self, x, y, z, extruding, f, e_delta, dist):
        # Punto del trail para el visor 3D, con datos para colorear/anchear estilo
        # slicer: [x, y, z, extrudeFlag, feedrate_efectivo, temp_hotend, flujo].
        #  - feedrate efectivo = comandado * M220% (velocidad real).
        #  - temp = temperatura actual del hotend (colorear por temperatura).
        #  - flujo = extrusión por mm de recorrido (ancho del trazo ∝ caudal).
        eff_f = (f or 0) * (self._feed_factor / 100.0)
        flow = 0.0
        if dist > 1e-3 and e_delta > 0:
            flow = round((e_delta / dist) * (self._flow_factor / 100.0), 4)
        self._trail_pending.append(
            [round(x, 2), round(y, 2), round(z, 2),
             1 if extruding else 0, int(eff_f),
             round(self._hotend_temp(), 1), flow])
        now = time.monotonic()
        if now - self._trail_last_push >= self._trail_interval or len(self._trail_pending) >= 1500:
            self._flush_trail(now)

    def _hotend_temp(self):
        try:
            temps = self._printer.get_current_temperatures() or {}
            tool = temps.get("tool0") or {}
            v = tool.get("actual")
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    def _trail_tail_flush(self):
        """Llamado periódicamente: si hay puntos en cola y ya pasó el intervalo
        desde el último push (los movimientos se pararon), los empuja."""
        if not self._trail_pending:
            return
        if time.monotonic() - self._trail_last_push >= self._trail_interval:
            self._flush_trail()

    def _flush_trail(self, now=None):
        if not self._trail_pending:
            return
        pts = self._trail_pending
        self._trail_pending = []
        self._trail_last_push = now if now is not None else time.monotonic()
        # historial persistente para clientes que recargan o abren en otro navegador
        self._trail_history.extend(pts)
        if len(self._trail_history) > 2000:
            self._trail_history = self._trail_history[-2000:]
        try:
            self._plugin_manager.send_plugin_message(self._identifier, {
                "type": "tracktrail", "points": pts,
            })
        except Exception:
            pass


    # ---- Auto-report de posición -> sincroniza el visor 3D ----
    # Consume reportes de posición y mueve el cabezal del visor en vivo (homing,
    # probing, jog manual). Formatos soportados:
    #   Grbl  : "<Idle|MPos:0,0,0|...>"  (de FULL_REPORT_TO_HOST_FEATURE)
    #   Marlin: "X:0.0 Y:0.0 Z:0.0 E:0.0 Count ..."  (M114 / auto-report M154)
    @staticmethod
    def _is_grbl_report(line):
        s = line.lstrip()
        return s.startswith("<") and ("MPos:" in s or "WPos:" in s)

    def _apply_position_report(self, line):
        """Parsea un reporte de posición y sincroniza el visor. Devuelve True si
        la posición cambió. NO suprime la línea (eso lo decide el hook)."""
        if self._tracker is None:
            return False
        x = y = z = None
        m = re.search(r"[MW]Pos:([-\d.]+),([-\d.]+),([-\d.]+)", line)
        if m:
            x, y, z = float(m.group(1)), float(m.group(2)), float(m.group(3))
        else:
            mx = re.search(r"(?:^|\s)X:([-\d.]+)", line)
            my = re.search(r"(?:^|\s)Y:([-\d.]+)", line)
            mz = re.search(r"(?:^|\s)Z:([-\d.]+)", line)
            if mx and my and mz and "Count" in line:  # reporte M114/M154
                x, y, z = float(mx.group(1)), float(my.group(1)), float(mz.group(1))
        if x is None:
            return False
        t = self._tracker
        moved = abs(t.x - x) > 0.02 or abs(t.y - y) > 0.02 or abs(t.z - z) > 0.02
        t.x, t.y, t.z = x, y, z
        now = time.monotonic()
        if not moved:
            return False
        # Throttle: con Grbl los reportes llegan muy rápido; ~16 Hz al visor basta
        # para una animación fluida sin inundar el websocket.
        if now - self._pos_last_push < 0.06:
            return False
        self._pos_last_push = now
        if not t.f:
            t.f = 3000
        # punto de travel (no extruye durante homing/probing) -> el visor mueve el cabezal
        self._trail_pending.append(
            [round(x, 2), round(y, 2), round(z, 2), 0, int(t.f or 3000),
             round(self._hotend_temp(), 1), 0.0])
        self._flush_trail(now)
        self._push_state()
        return True

    # ---- SimpleApiPlugin: GET estado del tracker ----
    def on_api_get(self, request):
        comm = getattr(self._printer, "_comm", None)
        octo_z = getattr(comm, "_currentZ", None) if comm is not None else None
        state = self._tracker.state() if self._tracker is not None else {}
        return flask.jsonify(
            tracker=state,
            octoprint_currentZ=octo_z,
            installed=(self._tracker_installed_on is comm and comm is not None),
            leveling=(self._leveling.state() if self._leveling is not None else None),
        )

    # ---- Hook de líneas recibidas: alimenta al controlador de nivelación ----
    def gcode_received_hook(self, comm, line, *args, **kwargs):
        if self._leveling is not None:
            try:
                self._leveling.on_received(line)
            except Exception:
                pass
        if self._calib is not None:
            try:
                self._calib.on_received(line)
            except Exception:
                pass
        # Capacidades del firmware (respuesta a M115): "Cap:CANCEL_OBJECT:1" etc.
        # Determina si podemos usar M486 nativo para cancel-object.
        if "Cap:" in line:
            mcap = re.search(r"Cap:([A-Z0-9_]+):([01])", line)
            if mcap:
                self._fw_caps[mcap.group(1)] = mcap.group(2) == "1"
        # Pose real para el ciclo de hot-tune (M114 o reporte grbl).
        if self._hot_await_pose:
            p = self._parse_pose_line(line)
            if p is not None:
                self._hot_pose = p
                self._hot_pose_event.set()
        try:
            self._parse_limits(line)
        except Exception:
            pass
        try:
            if self._parse_fw_line(line):
                self._push_fw()
        except Exception:
            pass
        # Auto-report de posición -> sincroniza el cabezal del visor en vivo.
        # Durante un print activo NO lo usamos (la posición la lleva el stream de
        # G0/G1; el reporte va por delante por el buffer del firmware y daría
        # saltos). En homing/probing/jog manual SÍ -> anima el movimiento.
        is_grbl = self._is_grbl_report(line)
        try:
            printing = self._printer.is_printing()
        except Exception:
            printing = False
        if not printing:
            try:
                self._apply_position_report(line)
            except Exception:
                pass
        # Grbl <...> no lo entiende OctoPrint: lo CONSUMIMOS nosotros y lo
        # suprimimos para que no estorbe el protocolo (evita el bloqueo que
        # provocaba FULL_REPORT_TO_HOST_FEATURE). M114/M154 se dejan pasar.
        if is_grbl:
            return ""
        return line

    # ---- Hook de comando EN COLA: cancel-object (M486 nativo o supresión) ----
    @staticmethod
    def _filepos_of(tags):
        if not tags:
            return None
        for t in tags:
            if t.startswith("filepos:"):
                try:
                    return int(t[8:])
                except ValueError:
                    return None
        return None

    def gcode_queuing_hook(self, comm, phase, cmd, cmd_type, gcode,
                           subcode=None, tags=None, *args, **kwargs):
        """Cancel-object portable. Mapea el offset de byte del fichero (tag
        `filepos:`) al objeto activo del índice y, según las capacidades:
          - M486 nativo: inyecta `M486 S<idx>` en las fronteras de objeto (si el
            slicer no las trae) para que el firmware sepa el objeto en curso. La
            cancelación real la hace `M486 P<idx>` (enviado al pulsar Cancelar).
          - Sin M486: SUPRIME la extrusión de los objetos cancelados (convierte el
            movimiento en travel en extrusión relativa; si es absoluta, omite el
            movimiento). Se avisa de la limitación en la UI.
        Devuelve None (passthrough), [] (suprimir) o lista de comandos a enviar.
        """
        # --- Hot-tune: envolver correcciones EN CALIENTE (solo durante impresión) ---
        # Captura comandos de configuración de máquina enviados por API/terminal/calib
        # (NO del job, NO de nuestro propio ciclo) y los aplica al instante vía el
        # ciclo pausa→inyectar→reanudar. Fuera de impresión: passthrough.
        ht = self._settings.get(["hottune"], merged=True) or {}
        if (ht.get("enabled", True) and gcode and tags is not None
                and "source:file" not in tags
                and "plugin:boostkit:instant" not in tags):
            try:
                printing = self._printer.is_printing()
            except Exception:
                printing = False
            if printing and self._hot_wrappable(gcode, cmd, ht):
                self._hot_enqueue(cmd, ht)
                return []   # suprimir; el ciclo lo reinyecta con efecto instantáneo

        idx = self._index
        if idx is None or not idx.objects:
            return None
        caps = self.job_caps()
        native = caps.get("m486")

        # Camino nativo con marcadores ya en el fichero: nada que reescribir.
        if native and idx.has_m486:
            return None
        # Sin nativo y sin nada cancelado: passthrough barato.
        if not native and not self._cancelled:
            return None

        pos = self._filepos_of(tags)
        if pos is None:
            return None  # no viene del fichero (terminal/script/API)
        active = idx.object_at(pos)

        if native:
            # Inyectar M486 S<idx> en cada cambio de objeto (+ T<count> al inicio).
            out = []
            if not self._m486_announced:
                out.append("M486 T{:d}".format(len(idx.objects)))
                self._m486_announced = True
            if active != self._last_obj_sent:
                out.append("M486 S{:d}".format(active if active is not None else -1))
                self._last_obj_sent = active
            if out:
                out.append(cmd)
                return out
            return None

        # --- Fallback sin M486: suprimir la extrusión de objetos cancelados ---
        if active is None or active not in self._cancelled:
            return None
        g = (gcode or "").upper()
        if g not in ("G0", "G1"):
            return None
        if _axis(cmd, "E") is None:
            return None  # ya es un travel: dejarlo pasar (mueve sin depositar)
        t = self._tracker
        if t is not None and not t.abs_e:
            # Extrusión RELATIVA: quitar la E -> queda un travel limpio, sin desync.
            stripped = re.sub(r"\s[Ee][-+]?[0-9.]+", "", cmd)
            return [stripped]
        # Extrusión ABSOLUTA: no se puede quitar la E sin desincronizar; se omite el
        # movimiento entero (limitación conocida sin M486 nativo).
        return []

    # ---- Hook de comando ENVIADO: refleja en los controles TODO lo que se manda,
    #      venga del job, de un plugin o TECLEADO en la terminal por el usuario ----
    def gcode_sent_hook(self, comm, phase, cmd, cmd_type, gcode, *args, **kwargs):
        # Los handlers _gcode_<CODE>_sent que parcheamos en comm se disparan con el
        # stream del job pero NO con comandos del terminal/API; este hook SÍ se
        # dispara con todos. Aquí reflejamos los cambios de configuración.
        if not cmd:
            return
        g = (gcode or "").upper()
        # Filtro: solo gcodes de config/modo (evita regex en cada G0/G1 del print).
        if g not in self._SENT_INTEREST:
            return
        try:
            c = str(cmd).strip()
            t = self._tracker
            # feed/flow override
            if g == "M220":
                s = _axis(c, "S")
                if s is not None:
                    self._feed_factor = max(10, min(500, s))
            elif g == "M221":
                s = _axis(c, "S")
                if s is not None:
                    self._flow_factor = max(10, min(500, s))
            # modos de posicionamiento (toggles abs/rel)
            if t is not None:
                if g == "G90":
                    t.set_abs_xyz(True); self._push_state()
                elif g == "G91":
                    t.set_abs_xyz(False); self._push_state()
                elif g == "M82":
                    t.set_e_abs(True); self._push_state()
                elif g == "M83":
                    t.set_e_abs(False); self._push_state()
                elif g == "G92":
                    t.set_position(c); self._push_state()
            # resto de config de movimiento (M201/203/204/205/M900/M851/M425/...)
            if self._parse_fw_line(c):
                self._push_fw()
            self._parse_limits(c)
        except Exception:
            pass

    # ---- Parseo de límites de máquina (M503 -> M201/M203/M204/M205) ----
    _LIMIT_CODES = ("M201", "M203", "M204", "M205")

    def _parse_limits(self, line):
        for code in self._LIMIT_CODES:
            idx = line.find(code + " ")
            if idx < 0:
                continue
            rest = line[idx + len(code):]
            vals = {}
            for tok in rest.split():
                if len(tok) >= 2 and tok[0].isalpha():
                    try:
                        vals[tok[0].upper()] = float(tok[1:])
                    except ValueError:
                        pass
            if vals:
                if not hasattr(self, "_limits") or self._limits is None:
                    self._limits = {}
                self._limits[code] = vals
                try:
                    self._plugin_manager.send_plugin_message(
                        self._identifier, {"type": "limits", "limits": self._limits})
                except Exception:
                    pass
            return

    # ---- Estado COMPLETO del firmware (movimiento): M503 + tracking en vivo ----
    # Códigos cuyos valores afectan al movimiento y queremos mostrar/rastrear.
    # (M201/M203/M204/M205 siguen yendo por _parse_limits para el grid existente;
    #  aquí cubrimos el resto + los duplicamos en _fw para el panel unificado.)
    _FW_CODES = (
        "M203", "M201", "M204", "M205",  # vel/accel/jerk-jd (también en _limits)
        "M92",                            # pasos/mm
        "M900",                           # linear advance (K)
        "M906", "M907",                   # corriente motor (TMC / digipot)
        "M200",                           # extrusión volumétrica (D=diám, S=on)
        "M425",                           # backlash (X/Y/Z + F factor)
        "M851",                           # offset de la sonda (X/Y/Z)
        "M206",                           # home offset / desplazamiento de origen (X/Y/Z)
        "M209",                           # autoretract firmware (S)
        "M207", "M208",                   # firmware retract / recover
        "M302",                           # extrusión en frío (P0 permite / P1 prohíbe)
        "M420",                           # fade de la malla (S=on, Z=fade)
        "M220", "M221",                   # feed % / flow %
    )

    # Gcodes que el hook gcode.sent debe procesar (config + modos). Evita correr
    # regex en cada G0/G1 del stream de impresión.
    _SENT_INTEREST = frozenset(_FW_CODES) | {
        "G90", "G91", "G92", "M82", "M83", "M593", "M106", "M107",
    }

    def _parse_fw_line(self, line):
        """Parsea una línea (eco de M503 o comando enviado) y fusiona en self._fw
        los parámetros de movimiento. Devuelve True si algo cambió."""
        s = line.strip()
        if s.startswith("echo:"):
            s = s[5:].strip()
        if not s:
            return False
        changed = False

        # ---- Respuestas del firmware con formato PROPIO (no estilo M503) ----
        # Estas salen tanto si el comando lo manda el plugin como si el usuario lo
        # teclea en la terminal -> mantienen los controles sincronizados siempre.

        # Offset de la sonda (M851). Marlin lo reporta como "Probe Offset X.. Y.. Z.."
        # o solo "Probe Offset Z.." (al hacer babystep con BABYSTEP_ZPROBE_OFFSET,
        # donde el babystep Z == offset Z de la sonda).
        if "Probe Offset" in s:
            rest = s[s.index("Probe Offset") + len("Probe Offset"):]
            node = self._fw.setdefault("M851", {})
            for ax in ("X", "Y", "Z"):
                m = re.search(r"%s\s*(-?[\d.]+)" % ax, rest)
                if m:
                    v = float(m.group(1))
                    if node.get(ax) != v:
                        node[ax] = v
                        changed = True
            return changed

        # Feedrate override (M220). Respuesta a 'M220' suelto: "FR:100%".
        mfr = re.match(r"FR:\s*(\d+)\s*%", s)
        if mfr:
            v = float(mfr.group(1))
            if self._fw.get("M220", {}).get("S") != v:
                self._fw["M220"] = {"S": v}
                self._feed_factor = max(10, min(500, v))
                changed = True
            return changed

        # Flow override (M221). Respuesta a 'M221': "E0 Flow: 100%" / "Flow: 100%".
        mfl = re.search(r"Flow:\s*(\d+)\s*%", s)
        if mfl:
            v = float(mfl.group(1))
            if self._fw.get("M221", {}).get("S") != v:
                self._fw["M221"] = {"S": v}
                self._flow_factor = max(10, min(500, v))
                changed = True
            return changed

        # M593 input shaping: 'M593 X F40.00 D0.15' (una línea por eje) o
        # 'M593 F40 D0.15' (ambos). Lo guardamos por eje bajo M593.
        if s.startswith("M593"):
            rest = s[4:]
            f = re.search(r"F(-?[\d.]+)", rest)
            d = re.search(r"D(-?[\d.]+)", rest)
            axes = [a for a in ("X", "Y") if re.search(r"(?:^|\s)%s(?:\s|$|F|D)" % a, rest)]
            if not axes:
                axes = ["X", "Y"]
            node = self._fw.setdefault("M593", {})
            for ax in axes:
                ent = node.setdefault(ax, {})
                if f:
                    ent["F"] = float(f.group(1)); changed = True
                if d:
                    ent["D"] = float(d.group(1)); changed = True
            return changed

        # M107 = ventilador OFF (equivale a M106 S0).
        if s == "M107" or s.startswith("M107 "):
            if self._fw.get("M106", {}).get("S") != 0:
                self._fw["M106"] = {"S": 0}
                changed = True
            return changed
        if s.startswith("M106"):
            mS = re.search(r"S(-?[\d.]+)", s)
            if mS:
                self._fw["M106"] = {"S": float(mS.group(1))}
                changed = True
            return changed

        for code in self._FW_CODES:
            if s == code or s.startswith(code + " "):
                rest = s[len(code):]
                vals = {}
                for letter, num in re.findall(r"([A-Za-z])(-?[\d.]+)", rest):
                    try:
                        vals[letter.upper()] = float(num)
                    except ValueError:
                        pass
                if vals:
                    node = self._fw.setdefault(code, {})
                    for k, v in vals.items():
                        if node.get(k) != v:
                            node[k] = v
                            changed = True
                return changed
        return False

    def _push_fw(self):
        try:
            self._plugin_manager.send_plugin_message(
                self._identifier, {"type": "fwstate", "fw": self._fw})
        except Exception:
            pass


    @staticmethod
    def _extra_lines(text):
        return [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]

    # ---- helpers ----
    def _send(self, cmd, tags=None):
        """Envía comandos con force=True (salta la cola de job de OctoPrint).
        Acepta string o lista. `tags` es para marcar comandos como internos
        (p.ej. {'plugin:boostkit:instant'} para el ciclo hot-tune)."""
        if isinstance(cmd, (list, tuple)):
            lines = [l for l in cmd if l]
        else:
            lines = [cmd] if cmd else []
        if not lines:
            return
        try:
            if tags is not None:
                self._printer.commands(lines, tags=tags, force=True)
            else:
                self._printer.commands(lines, force=True)
        except TypeError:
            self._printer.commands(lines, force=True)


    # ---- Movimientos con PRESERVACIÓN DE MODO ----------------------------------
    # OctoPrint usa siempre G91...G90 (y M83...M82) asumiendo que "absoluto" es el
    # estado por defecto. Eso PISA el modo real del print: si pausas en G91/M83 y
    # haces jog, al reanudar la impresión queda en absoluto y se arruina. Aquí
    # leemos el modo REAL del tracker, movemos en relativo, y restauramos EXACTO
    # el modo de ejes y de extrusor por separado. Clave: G90/G91 también arrastran
    # el modo de E, por eso el M82/M83 de restauración va SIEMPRE al final.
    def _jog_preserving(self, axes, speed):
        t = self._tracker
        abs_xyz = t.abs_xyz if t is not None else True
        abs_e = t.abs_e if t is not None else True
        parts = []
        for ax in ("x", "y", "z"):
            if ax in axes:
                parts.append("{}{:.3f}".format(ax.upper(), float(axes[ax])))
        if not parts:
            return
        seq = ["G91", "G1 {} F{:d}".format(" ".join(parts), int(speed)),
               "G90" if abs_xyz else "G91",
               "M82" if abs_e else "M83"]
        self._send(seq)

    def _goto_preserving(self, x, y, z_clear, speed=3000):
        # Editor manual de malla: sube Z a una altura segura y se mueve al XY del nodo
        # en ABSOLUTO, restaurando el modo real de ejes/extrusor al terminar.
        t = self._tracker
        abs_xyz = t.abs_xyz if t is not None else True
        abs_e = t.abs_e if t is not None else True
        seq = ["G90",
               "G0 Z{:.2f} F600".format(float(z_clear)),
               "G0 X{:.3f} Y{:.3f} F{:d}".format(float(x), float(y), int(speed)),
               "G90" if abs_xyz else "G91",
               "M82" if abs_e else "M83"]
        self._send(seq)

    def _extrude_preserving(self, amount, speed):
        self._extrude_seq_preserving(
            ["G1 E{:.4f} F{:d}".format(float(amount), int(speed))])

    def _extrude_seq_preserving(self, move_lines):
        # M82/M83 solo afectan a E, así que el modo XYZ queda intacto.
        t = self._tracker
        abs_e = t.abs_e if t is not None else True
        seq = ["M83"] + list(move_lines) + ["M82" if abs_e else "M83"]
        self._send(seq)


    @staticmethod
    def _num(value, default):
        try:
            if value is None or value == "":
                return float(default)
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _clamp_int(value, lo, hi):
        try:
            v = int(round(float(value)))
        except (TypeError, ValueError):
            v = 100
        return max(lo, min(hi, v))

    @staticmethod
    def _clamp_float(value, lo, hi):
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = lo
        return max(lo, min(hi, v))

    # ---- Slots de malla de nivelación (cantidad PRINTER-DEPENDIENTE) ----
    def _slot_count(self):
        """Nº de slots según lo detectado en la impresora; fallback al ajuste."""
        caps = self._leveling.caps_state() if self._leveling is not None else {}
        n = caps.get("slots")
        if isinstance(n, int) and n > 0:
            return min(n, 64)   # tope defensivo
        # Fallback: nº configurado (o 4) cuando la detección no dio dato (UBL sin
        # EEPROM_CHITCHAT, etc.). Si el sistema no tiene slots (bilineal) -> 0.
        if caps.get("system") in ("bilinear", "mbl"):
            return 0
        return int(self._settings.get(["leveling", "slots_fallback"], merged=True) or 4)

    def _get_slots(self):
        """Lista de slots normalizada al recuento dinámico (rellena/recorta)."""
        count = self._slot_count()
        slots = list((self._settings.get(["leveling", "slots"], merged=True) or []))
        while len(slots) < count:
            slots.append({"name": "", "filled": False, "n": 0, "ts": 0, "z": None})
        return slots[:count] if count > 0 else []

    def _push_slots(self):
        # No incluir los datos Z completos en cada push de estado: pesa mucho. El
        # frontend pide los Z bajo demanda al abrir el visor (mesh_get/slot).
        slots = self._get_slots()
        light = [{"name": s.get("name", ""), "filled": bool(s.get("filled")),
                  "n": s.get("n", 0), "ts": s.get("ts", 0),
                  "z": s.get("z")} for s in slots]
        self._plugin_manager.send_plugin_message(
            self._identifier, {"type": "slots", "slots": light,
                               "count": self._slot_count()})

    def _mesh_save(self, data):
        """Guarda la malla activa en un slot UBL y registra nombre + copia de Z."""
        if self._leveling is None:
            return
        count = self._slot_count()
        if count <= 0:
            self._logger.info("Esta impresora no tiene slots de malla")
            return
        slot = self._clamp_int(data.get("slot", 0), 0, count - 1)
        mesh = self._leveling.save_slot(slot)
        if mesh is None:
            self._logger.info("Malla: nada que guardar (no hay malla nivelada)")
            return
        name = str(data.get("name", "")).strip() or ("Slot %d" % slot)
        slots = self._get_slots()
        slots[slot] = {"name": name, "filled": True, "n": len(mesh),
                       "ts": int(time.time()), "z": mesh}
        self._settings.set(["leveling", "slots"], slots)
        self._settings.save()
        self._logger.info("Malla: guardada en slot %d ('%s')", slot, name)
        self._push_slots()

    # ---- Software update: deliberadamente NO registramos check_config para
    #      evitar 404 de GitHub en el log (plugin privado, sin repo de release).


__plugin_name__ = "OctoPrint Boost Kit"
__plugin_version__ = "0.4.0"
__plugin_description__ = "Muchas mejoras para OctoPrint: panel de control consolidado, gráfico de temperatura con análisis de pendiente, hot-tune (M410 + M290 P0), tracker mode-aware y nivelación automática."
__plugin_pythoncompat__ = ">=3.7,<4"
__plugin_implementation__ = BoostKitPlugin()

__plugin_hooks__ = {
    "octoprint.comm.protocol.gcode.received": __plugin_implementation__.gcode_received_hook,
    "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.gcode_sent_hook,
    "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.gcode_queuing_hook,
}
