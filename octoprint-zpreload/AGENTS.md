# AGENTS.md — octoprint-zpreload/

Plugin OctoPrint **OctoPrint-ZPreload** (paquete `octoprint_zpreload`, identifier
`zpreload`). Precarga física de backlash con **eje configurable** y **plantilla G-code
editable** (`preload_gcode`): inyecta la plantilla antes de un `G0/G1` que invierte el
sentido del eje rastreado (por defecto Z). **Separado** de `octoprint-k9suite` (BoostKit).

## Layout
```
setup.py
pyproject.toml            # build-system (setuptools.build_meta) — necesario para instalar SIN internet
MANIFEST.in
README.md
octoprint_zpreload/
├── __init__.py                # ZPreloadPlugin (única clase)
└── templates/zpreload_settings.jinja2
```

## Hechos load-bearing (verificados contra el firmware y OctoPrint 1.11)
- **Plantilla editable**: la secuencia inyectada sale de `preload_gcode` (settings), NO
  hardcodeada. Variables: `{axis}` `{low}` `{restore}` `{dwell}` `{preload}` `{dir}`
  `{signed_preload}` `{prev}` `{target}` `{preload_target}`. Se formatean con
  `str.format()`; un error se descarta con warning y NO inyecta (no rompe la impresión).
- **Plantilla por defecto (v0.3)**: `G4 P{dwell}` + `M907 {axis}{low}` +
  `M290 P0 {axis}{signed_preload}` + `G4 P{dwell}` + `M907 {axis}{restore}` + `G4 P{dwell}`.
  **CERO M400** (M400 = `planner.synchronize()` = drena la cola y PAUSA la impresión) y
  **CERO G92** (M290 no toca la posición lógica).
- **Dwell ADAPTATIVO**: `dwell = ceil(preload × steps_per_mm / BABYSTEP_RATE × 1000) + margin`.
  `BABYSTEP_RATE = 976` pasos/s (INTEGRATED_BABYSTEPPING desactivado → el babystep lo
  aplica el ISR de temperatura, NO el planner; `Planner::busy()` NO comprueba
  `babystep.has_steps()`). Settings `steps_per_mm` (606) y `dwell_margin_ms` (50).
- **M400 no espera babysteps**: solo `G4 P<ms>` (dwell) bloquea el main loop el tiempo
  justo. Por eso la plantilla usa `G4 P{dwell}` y NO `M400`.
- **M290 requiere `P0`**: `BABYSTEP_ZPROBE_OFFSET` activo → sin `P0` se corrompe
  `probe.offset.z`. Siempre `M290 P0 {axis}{signed_preload}`.
- **Hook único**: `octoprint.comm.protocol.gcode.queuing`. Los comandos que devuelve el
  hook se encolan DIRECTAMENTE (no re-entran en `queuing`) → los inyectados NO se
  re-procesan (sin bucles). Heredan los tags del original más `source:rewrite`/
  `phase:queuing`/`plugin:zpreload`.
- **Solo M907** es fiable en la placa (MKS_ROBIN_LITE + A4988 + PWM_MOTOR_CURRENT).
  M906/M908/M909/M910 no aplican aquí.
- El tracker es LÓGICO (parseo de G-code): se resetea con `M110`, `G28`, `M84`/`M18` y
  los eventos `PrintStarted`/`PrintDone`/`PrintFailed`/`PrintCancelled`/`Disconnected`.
- En modo relativo (`G91`) se envuelve la plantilla con `G90`/`G91` (por si el usuario
  escribe una plantilla `G1`/`G92`); con `M290` es un no-op inofensivo.

## Instalación — venv FUERA del repo, SIN internet
```bash
/home/antonio/Programas/3d_print/.venv/bin/pip install --no-build-isolation --no-deps -e /home/antonio/Desarrollo/solverius/3dprinter/octoprint-zpreload
/home/antonio/Programas/3d_print/.venv/bin/octoprint serve
```
- **`--no-build-isolation --no-deps` OBLIGATORIO** (sin internet). Necesita `pyproject.toml`
  con `[build-system] requires setuptools+wheel` y `build-backend setuptools.build_meta`.
- Reiniciar OctoPrint aborta la impresión en curso: hacerlo con la impresora libre.

## Anti-patrones
- **No** fusionar esto en `octoprint-k9suite`: son plugins independientes.
- **No** inyectar con `self._printer.commands(force=True)`: rompería el orden del stream.
- **No** usar `M400` en la plantilla por defecto (pausa la impresión y NO espera babysteps).
- **No** hardcodear la secuencia: mantenerla en `preload_gcode`.
