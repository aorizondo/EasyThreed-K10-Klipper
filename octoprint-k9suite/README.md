# OctoPrint-BoostKit

Suite unificada para la **EasyThreed K9** (firmware Marlin propio).
Pensada para crecer en módulos sobre cimientos compartidos. Empieza por:

## Módulo 1 — Hot-tune (corrección EN CALIENTE)

Aprovecha que el firmware tiene `EMERGENCY_PARSER`:

| Acción | Comando | Cómo funciona |
|---|---|---|
| **Hot-tune** | `M410` | `quick_stop()` → limpia el planner → captura pose → envía corrección → sintetiza segmento → seek → reanuda |
| **Jog en caliente** | `M290 P0` | Babystep aplicado en vivo (no toca Z-probe offset). El hot-tune lo envía durante M410 |
| **Velocidad** | `M220 S` | Override con efecto inmediato vía hot-tune |
| **Flujo** | `M221 S` | Override del flujo vía hot-tune |

### Cómo funciona

1. El usuario envía un comando de corrección (M220, M290, etc.) mientras imprime
2. El plugin lo intercepta en `gcode_queuing_hook` (solo comandos que NO son del job)
3. `M410` detiene la impresora inmediatamente (limpia el planner del firmware)
4. `M114 R E` captura la posición real del encoder
5. `hottune.match_move()` empareja el movimiento interrumpido
6. Los comandos del usuario se envían por el canal `plugin:boostkit:instant`
7. `hottune.synth_completion()` reconstruye el segmento incompleto
8. Seek al offset del fichero donde se interrumpió + `set_job_on_hold(False)`

### Limitación honesta

`M410` limpia el planner → se pierden los movimientos en buffer. El tracker y la síntesis
de segmentos mitigan esto, pero pueden quedar costuras visibles en la pieza. Para pausas
**largas** (cambio de filamento), usa la pausa nativa de OctoPrint.

## Instalación (cuando NO estés imprimiendo)

Reiniciar OctoPrint **aborta** cualquier impresión en curso. Hazlo con la impresora libre.

```bash
# 1. Detén OctoPrint (Ctrl-C en la terminal tmux donde corre, o el servicio)
# 2. Instala en modo editable dentro del venv:
/home/antonio/Programas/3d_print/.venv/bin/pip install -e /home/antonio/Desarrollo/solverius/3dprinter/octoprint-k9suite
# 3. Arranca OctoPrint de nuevo:
/home/antonio/Programas/3d_print/.venv/bin/octoprint serve
```

Tras reiniciar verás la pestaña **Boost Kit** y el panel de control en la navbar.
Modo editable: los cambios en el código se recogen reiniciando OctoPrint (el JS/CSS,
forzando recarga del navegador).

## Probar sin imprimir

1. Conecta la impresora.
2. Lanza un movimiento largo lento, p.ej. `G1 X5 F300` (desde la terminal de OctoPrint).
3. Ajusta velocidad (M220 S50) o flujo (M221 S80) desde el panel — deberían aplicarse sin delay.
4. Fuera de impresión, el jog normal (G1 relativo) y babystep (M290) funcionan sin hot-tune.
