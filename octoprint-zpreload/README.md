# OctoPrint-ZPreload

Plugin de OctoPrint que compensa el **backlash con precarga física**, con **eje
configurable** y **secuencia G-code editable**. Rastrea un eje (por defecto Z) durante
el stream de impresión y, justo antes de un `G0`/`G1` que **invierte el sentido**, inyecta
la secuencia que tú definas en los ajustes.

## Por qué existe

El eje Z de la impresora (MK3, placa K9 reciclada) tiene **dos motores**, cada uno con
una holgura diferente. La compensación por software de Marlin (`BACKLASH_COMPENSATION`)
suma pasos con **una sola distancia por eje**, así que no puede corregir dos holguras
distintas. La solución física: al invertir el sentido, bajar la corriente para que cada
motor cierre su propia holgura (gira en el juego libre) sin mover el carro, y luego
restaurar la corriente.

## Qué hace el plugin

Detecta inversión de sentido del eje rastreado e inyecta la plantilla `preload_gcode`
antes de ese movimiento. La plantilla por defecto usa **babystep (`M290`)** + **dwell
adaptativo (`G4 P{dwell}`)**, **cero `M400`** (fuerza el drenado del planner y pausa la
impresión) y **cero `G92`** (el babystep no toca la posición lógica):

```gcode
G4 P{dwell}                    ; asienta la cola (dwell adaptativo)
M907 Z{low}                    ; baja la corriente (cierra la holgura sin mover el carro)
M290 P0 Z{signed_preload}      ; babystep en el nuevo sentido (sin tocar posición ni offset de sonda)
G4 P{dwell}                    ; espera a que el babystep termine a baja corriente
M907 Z{restore}                ; restaura la corriente
G4 P{dwell}                    ; asienta la cola
; ...y después sigue el G0/G1 original
```

El `{dwell}` es **adaptativo**: se calcula a partir de `preload_distance` y `steps_per_mm`
para que el babystep (~976 pasos/s, ISR de temperatura) termine dentro del dwell.

La plantilla es **totalmente editable**: puedes cambiar a `G1`, otros ejes, etc.

## Variables disponibles en la plantilla

| Variable | Ejemplo | Descripción |
|----------|---------|-------------|
| `{axis}` | `Z` | Letra del eje rastreado |
| `{low}` | `400` | Corriente baja (mA) |
| `{restore}` | `800` | Corriente de restauración (rastreada de `M907` o `restore_current`) |
| `{dwell}` | `392` | Dwell adaptativo (ms) = ceil(preload × pasos/mm ÷ 976 × 1000) + margen |
| `{preload}` | `0.550` | Distancia de precarga, sin signo |
| `{dir}` | `+1` / `-1` | Sentido del movimiento |
| `{signed_preload}` | `+0.550` / `-0.550` | Distancia de precarga con signo |
| `{prev}` | `0.400` | Posición anterior del eje |
| `{target}` | `0.600` | Posición objetivo del movimiento |
| `{preload_target}` | `0.950` | `prev + dir*preload` (para plantillas `G1`) |

## Instalación

El venv de OctoPrint vive FUERA de este repo (`/home/antonio/Programas/3d_print/.venv`).
**Sin internet** hay que usar `--no-build-isolation --no-deps`:

```bash
/home/antonio/Programas/3d_print/.venv/bin/pip install --no-build-isolation --no-deps -e /home/antonio/Desarrollo/solverius/3dprinter/octoprint-zpreload
/home/antonio/Programas/3d_print/.venv/bin/octoprint serve
```

> Reiniciar OctoPrint aborta la impresión en curso. Hazlo siempre con la impresora libre.

## Ajustes (Ajustes → Plugins → Z Preload)

| Ajuste | Default | Descripción |
|--------|---------|-------------|
| `enabled` | ✔ | Activar/desactivar la inyección |
| `axis` | `Z` | Eje a rastrear (`X` / `Y` / `Z`) |
| `low_current` | 400 mA | Corriente baja (`{low}`) |
| `restore_current` | 800 mA | Corriente de restauración (`{restore}`; fallback si no se rastrea `M907`) |
| `preload_distance` | 0.55 mm | Distancia de precarga (`{preload}`) — cubre la **mayor** de las dos holguras |
| `steps_per_mm` | 606 | pasos/mm del eje (para el dwell adaptativo) |
| `dwell_margin_ms` | 50 | Margen extra (ms) sobre el dwell calculado |
| `only_during_print` | ✔ | Inyectar solo en stream de fichero (`source:file`) |
| `preload_on_first_move` | ✘ | Precargar también el 1er movimiento (tras homing/apagar motores) |
| `preload_gcode` | (ver arriba) | **G-code editable** a inyectar |

`restore_current` es un fallback: si el plugin ve un `M907 <eje><x>` (o `M907 S<x>`) en el
propio fichero, `{restore}` toma **ese** valor en lugar del ajuste.

### Cálculo del dwell adaptativo

```
dwell_ms = ceil( preload_mm × steps_per_mm / 976 × 1000 ) + dwell_margin_ms
```

Ejemplos (Z = 606 pasos/mm, margen 50 ms):
- 0.05 mm → 31 ms → dwell = 82 ms
- 0.55 mm → 342 ms → dwell = 392 ms

## Por qué no `M400`

`M400` = `planner.synchronize()`: bloquea hasta que TODOS los movimientos del planner
terminen. En un stream de impresión eso **pausa** visiblemente la impresión (stutter).
Además `M400` **no** espera babysteps (los aplica el ISR de temperatura, no el planner).
El `G4 P<ms>` (dwell) sí bloquea el tiempo justo y no fuerza el drenado de la cola.

## Estados que se rastrean (y resets)

- `G90`/`G91`: modo absoluto/relativo.
- `G92`: redefine la posición lógica.
- `G28`: homing → la posición vuelve a ser desconocida.
- `M84`/`M18`: motores apagados → la dirección previa se pierde.
- `M110`: OctoPrint lo envía al iniciar cada job → reset completo del tracker.
- También se resetea con los eventos `PrintStarted`/`PrintDone`/`PrintFailed`/
  `PrintCancelled`/`Disconnected`.

## Interacciones conocidas

- **BoostKit** (`octoprint-k9suite`): su tracker ve los comandos inyectados (heredan
  `source:file`), pero `M290`/`G4`/`M907` no mueven la posición lógica → sin deriva.

## Limitaciones honestas

- La secuencia asume que el carro **realmente no se mueve** con la corriente baja. Si
  `low_current` queda demasiado alta y el motor mueve el carro, la precarga desplazaría
  el eje. Calibra `low_current`.
- `M290` clampa a **±2 mm** en el firmware: una `preload_distance` mayor se truncaría.
- El tracker es lógico (parseo de G-code), no lee la posición real del firmware.
- Una plantilla con una variable mal escrita (`{foo}`) se descarta con un warning en el
  log y no inyecta nada (no rompe la impresión).
