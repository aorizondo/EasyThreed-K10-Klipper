# Realtime reporting en Marlin (K9 ET4000PLUS) — qué activar y qué no

Documenta la relación entre los dos flags de "reporte en tiempo real" de Marlin y la
decisión tomada para el plugin **Boost Kit** (octoprint-k9suite). El firmware **no se
edita aquí**; esta nota es para aplicar el cambio en el build y recompilar.

Archivo de configuración:
`repos/EasyThreeD-K9_ET4000PLUS/Marlin/Configuration_adv.h` (≈ líneas 2459–2472).

```c
/**
 *  S000 : Report State and Position while moving.
 *  P000 : Instant Pause / Hold while moving.
 *  R000 : Resume from Pause / Hold.
 */
//#define REALTIME_REPORTING_COMMANDS        // <-- flag PADRE
#if ENABLED(REALTIME_REPORTING_COMMANDS)
  //#define FULL_REPORT_TO_HOST_FEATURE      // Auto-report the machine status like Grbl CNC
#endif
```

## Qué hace cada flag

### `REALTIME_REPORTING_COMMANDS` (padre) — MANTENER ACTIVADO
Añade al *emergency parser* tres comandos en tiempo real (se procesan al instante,
saltándose la cola de gcode):

- **`P000`** — *hold* / pausa instantánea durante el movimiento.
- **`R000`** — *resume* desde el hold (en estado hold solo responden `M112`, `M410` y `R000`).
- **`S000`** — reporta estado + posición **bajo demanda** (una sola vez por petición).

> El plugin **depende de `P000`/`R000`** para su pausa/reanudación instantánea
> (botones de la navbar → API `hold`/`resume`). Si se desactiva este flag, esa función
> deja de existir. **Debe quedar activado.**

### `FULL_REPORT_TO_HOST_FEATURE` (hijo) — DEJAR DESACTIVADO
Sólo compila si el padre está activo. Añade el **auto-reporte continuo** estilo Grbl:
mientras la máquina se mueve / hace homing / sondea, emite por serie sin que nadie lo
pida:

- estado de máquina `S_XYZ:<n>` (0=init … 5=run, 6=hold, 9=homing, etc.),
- y la posición tipo Grbl `<Idle|MPos:x,y,z|...>`.

Pensado para CNC/láser. En nuestro caso es **ruido**: el plugin tiene que consumirlo y
suprimirlo para que OctoPrint no se atasque. **Debe quedar desactivado (comentado).**

## Decisión

| Flag | Estado deseado |
|---|---|
| `REALTIME_REPORTING_COMMANDS` | **ACTIVADO** (descomentar) — necesario para P000/R000/S000 |
| `FULL_REPORT_TO_HOST_FEATURE` | **DESACTIVADO** (comentado) — sin auto-spam Grbl |

Tras el cambio, recompilar y flashear el firmware del ET4000PLUS.

## Efecto en el plugin

- La pausa/reanudación instantánea (P000/R000) sigue funcionando.
- Se elimina el auto-reporte continuo Grbl. El código que lo parseaba/suprimía en
  `octoprint_boostkit/__init__.py::gcode_received_hook` queda **inocuo** (no estorba) y
  sigue siendo válido para el sondeo manual con `S000` (`_rt_tick`, desactivado por
  defecto vía `gcode3d.realtime_poll`).
- El trail del visor 3D no se ve afectado en impresión normal: se alimenta del parseo de
  `G0/G1` en los hooks `*_sent`, no del auto-reporte.

Fuentes Marlin: [PR #19330 Realtime Reporting](https://github.com/MarlinFirmware/Marlin/pull/19330),
[M114](https://marlinfw.org/docs/gcode/M114.html),
[M154 Position Auto-Report](https://marlinfw.org/docs/gcode/M154.html).
