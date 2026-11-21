# AGENTS.md — octoprint-k9suite/

Plugin OctoPrint **OctoPrint-BoostKit** (paquete `octoprint_boostkit`, identifier `boostkit`). **El nombre del directorio engaña**: el plugin es **agnóstico**, no específico de la K9. Hot-tune, override M220/M221, visor 3D, nivelación UBL, cancel-object.

## Layout

```
setup.py                             # paquete + entry_point octoprint.plugin
MANIFEST.in
octoprint_boostkit/
├── __init__.py                      # BoostKitPlugin (clase principal, ~+1000 líneas)
├── __init__.py.bak, __init__.py.bak2  # versiones antiguas — IGNORAR
├── hottune.py                       # M410 sync + reanudación sintética
├── tracker.py                       # PositionTracker (encoder via M114 R E)
├── leveling.py                      # LevelingController (UBL/bilineal/MBL + nivelación de PLATO)
├── leveling.py.bak                  # vieja — IGNORAR
├── calibration.py                   # CalibrationController
├── gcode_index.py                   # GcodeIndex (M486 cancel-object + skip-to)
├── static/                          # JS/CSS frontend
├── templates/                       # Jinja2 (nav-bar, settings, tab Boost Kit)
└── __pycache__/                     # gitignored implícito
```

## Hechos load-bearing

- **Plugin agnóstico**: requisito real al firmware = **EMERGENCY_PARSER + REALTIME_REPORTING_COMMANDS** de Marlin (no específico K9). Sin ellos, hot-tune se degrada silenciosamente (no falla).
- **Versión 0.4.0**, AGPLv3, `python_requires=">=3.7,<4"`. Entry point: `boostkit = octoprint_boostkit`.
- **Hooks OctoPrint**: `StartupPlugin, SettingsPlugin, AssetPlugin, TemplatePlugin, SimpleApiPlugin, EventHandlerPlugin` — todos en `BoostKitPlugin`.
- **Archivos `.bak`/`.bak2`**: rastros de iteraciones previas. **No los importes** desde `__init__.py` (no aparecen en los `from .X import Y`). Si los limpias, verifica que `git status` no los tenía tracked.
- **Canal `plugin:boostkit:instant`**: los comandos del hot-tune se envían con `force=True` para saltar el `_command_queue` de OctoPrint (latencia mínima). Si reescribes esto, el hot-tune deja de funcionar en mitad de un job.
- **Estado en memoria**: `_fw{}` rastrea M201/M203/M204/M205/M900 (linear advance)/etc. — escaneado al abrir el panel y actualizado en vivo. Persiste `_trail_history` y `_viewstate` para sobrevivir recargas del navegador.
- **`gcode_index` indexa el fichero ANTES** de imprimir (cancel-object). Si cambias el offset de bytes en planeación, rompe `skip_to` (busca por byte-offset, no por número de línea).

## Nivelación de plato (`leveling.py`) — contrato load-bearing

Dos vías de sondeo: `_run_plugin` (sondea toda la cama, moda) y **`_run_plate`** (sondea solo el rectángulo del cristal, ajusta un **plano/alabeo** y rellena los `n_fw×n_fw` nodos de la malla del firmware con `M421`). Método `g30_plate`/`plate` → `_run_plate`. Todo es **universal** (M421/M420/M851/M425 estándar; caps auto-detectadas con M115/M503) y **sin reflasheo**.

### M851-shift adaptativo (inclinación severa) — NO invertir el signo

Verificado contra el firmware K9 (`repos/EasyThreeD-K9_ET4000PLUS/Marlin/…`):

- **`probe.cpp:737`**: `z_probe_low_point = axis_is_trusted(Z_AXIS) ? -offset.z + Z_PROBE_LOW_POINT : -10.0;`. Como `_run_plate` **homea Z antes de sondear** (G28 → eje "trusted"), aplica la rama `-offset.z + Z_PROBE_LOW_POINT`. `Z_PROBE_LOW_POINT=-2` (`Configuration.h:1596`), rango M851 `[-20,20]` (`:1599-1600`). El descenso de G30 **para en una coord nativa fija**; `G1 Z<bajo>` (lo pisa `Z_CLEARANCE_DEPLOY_PROBE`) y `G92` (solo `position_shift`) **NO** extienden el alcance. **La única palanca gcode del piso es `M851 Z`** (mueve `offset.z` → mueve el piso `-M851.z + LP`).
- **`probe.cpp:907`**: `measured_z = run_z_probe() + offset.z` = `B + M851.z`, donde `B` (Z lógica de la boquilla al disparar) es **independiente** de M851. Corolario, el **contrato de corrección de lectura**: una lectura `v` tomada con `M851=k` vuelve al frame original con **`z_real = v − (k − z0)`** (implementado en `_probe_shifted`). Si cambias esto, toda la malla queda sesgada por el offset.
- **Signo contraintuitivo**: para sondear MÁS PROFUNDO (plato caído por debajo del piso) hay que **SUBIR k** (baja el piso `-k+LP`). Centrado de ventana: `k = clamp(z0 + 1.5 − z_pred, ±20)`. Reintento ante `Probing Failed`: **`k += probe_extend`** (NO `-=`). Predicción `z_pred` con el plano robusto provisional en orden **centro→afuera** (`_center_out_order`); las primeras `predict_seed` semillas usan `k=z0` (sin cambio).
- **Restaurar SIEMPRE `M851 Z<z0>`**, en el `finally` de `_run_plate` **y** en `cancel()` (`self._m851_restore` guarda z0). **NUNCA `M500`** — persistiría un offset falso. Si z0 no se pudo leer (M851 no parseado) → shift deshabilitado (fallback universal-safe). El clamp ±20 cubre `Z_PROBE_OFFSET_RANGE`.

### Otros invariantes de `_run_plate`

- **Zero-referencia en el punto de HOME de Z** (centro = `Z_SAFE_HOMING`): `z_nodo = campo(nodo) − campo(home)`. Con `RESTORE_LEVELING_AFTER_G28`, si la malla no vale 0 en el home acabas con **Z=−0.1 tras G28** y la boquilla roza. No lo quites.
- **Neutraliza M206** durante el run (sondea/rellena en coords de máquina) y lo restaura en el `finally`.
- **Ajuste robusto** (`fit_plane_robust`, RLS-MAD): rechaza bultos de pegamento (outliers) → plano inclinado limpio sin re-limpiar el cristal. Umbral `clamp(k·1.4826·MAD, floor 0.05, techo 0.30)`: el `floor` evita sobre-rechazar en cristal impecable (repetibilidad ~0.02 mm); el techo garantiza que un bulto real SIEMPRE cae. Métricas al frontend por el mensaje `leveling_fit` (RMS µm, inclinación mm/eje, outliers marcados en rojo).
- **Agregación por punto** `_probe_point(...method)` → `(z, spread)`: **mediana** en el plato, **moda** en la vía cama completa (regresión nula). Cada G30 ya promedia `MULTIPLE_PROBING` descensos internos → 3 repeticiones bastan.
- **Modo alabeo** (`model="warp"`): `plano_robusto + smooth_grid(residuo_inliers)` (Gauss separable con renormalización de bordes). Por defecto `plane`.

### M108 de disparo ÚNICO (`on_received`)

Auto-confirma las pausas de la sonda manual (`PAUSE_BEFORE_DEPLOY_STOW`) con **un solo M108 por pausa**, disparado SOLO tras `echo:busy: paused for user` (la señal REAL de bloqueo; los `prompt_*` del protocolo de host se adelantan y provocaban 3-4 envíos). Flag `_pause_acked`, re-armado con `busy: processing`/`prompt_begin`. `force=True` = comando de emergencia (salta el flow-control). `Probing Failed` sin línea `Bed X:` → despierta `_bed_event` con `None` para no colgar el timeout.

### Backlash Z (`_apply_backlash`)

El firmware SOLO **mide** el backlash durante los G30. Para APLICARLO el plugin envía `M425 Z` (copia la media medida a la distancia de compensación) + `M425 F1.0` (activa el factor, por defecto 0.0). Persiste solo con `M500` (al guardar la malla en un slot). Universal: si el fw no reporta `M425` en M503 o la media es ~0, se omite en silencio.

## Instalación — venv vive FUERA del repo

```bash
/home/antonio/Programas/3d_print/.venv/bin/pip install -e /home/antonio/Desarrollo/solverius/3dprinter/octoprint-k9suite
/home/antonio/Programas/3d_print/.venv/bin/octoprint serve
```

- Modo editable (`-e`): cambios Python recogidos al **reiniciar OctoPrint**; JS/CSS al recargar navegador (Ctrl+Shift+R).
- **Reiniciar OctoPrint aborta la impresión en curso.** Hazlo SIEMPRE con la impresora libre. Esto es la mayor causa de pérdida de pieza en este plugin.
- El venv es Poetry, Python 3.13. Antonio lo creó allí; no migrarlo al repo sin avisar.

## Hot-tune (módulo `hottune`) — limitación honesta

`M410` limpia el planner del firmware → se pierden los segmentos en buffer. El plugin sintetiza un segmento de cierre con `synth_completion()`, pero **pueden quedar costuras visibles**. Para pausas largas (cambio filamento), usa la **pausa nativa de OctoPrint**, no el hot-tune.

## Anti-patrones

- **No** uses `self._printer.commands(...)` para los comandos internos del hot-tune (van por la cola de job); usa el canal `instant` con `force=True`.
- **No** edites los `.bak` pensando que es código vivo. Si vas a borrarlos, comprueba primero que su contenido no se referencia desde tests o scripts externos (no debería).
- **No** asumas que el plugin instalado en producción es la copia editable del repo si el venv no apunta aquí: verifica con `pip show -f OctoPrint-BoostKit` antes de debuggear.
- **No** dispares hot-tune fuera de un job (`set_job_on_hold(False)` esperará un job para liberar) — el plugin lo evita, pero parches futuros podrían romperlo.
