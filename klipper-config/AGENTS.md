# AGENTS.md — klipper-config/

Config Klipper **multi-MCU** (Arduino + K10) **de partida**. Diseñada para copiarse tal cual a `~/printer_data/config/`. No es la config "real" en producción.

> **`mk4/`** es OTRA máquina (MK4, Robin Lite de la K9, MCU única + Pi como 2ª MCU para 2× ADXL345 y DHT). Autocontenida; ver `docs/12-mk4-klipper.md`. Lo de abajo aplica solo al frankenprinter.

## Layout

```
printer.cfg              # entry point, sólo [include]s + [printer] + arcs/sdcard
include/
├── mcu-cnc.cfg          # MCU principal (Arduino Uno), XYZ
├── steppers-xyz.cfg     # ejes (rotation_distance, currents)
├── extruder-hotend.cfg  # MCU k10, extrusor + hotend
├── probe-zsensor.cfg    # sonda Z — DESACTIVADO en el include de printer.cfg
└── macros.cfg           # PRINT_START/END, G32, LOAD/UNLOAD_FILAMENT
moonraker.conf           # API server
```

## Hechos load-bearing

- **Dos MCU**: `[mcu]` = Arduino (XYZ + futuras endstops/PWM); `[mcu k10]` = K10 (extrusor + hotend + termistor). Klippy en la Pi orquesta.
- **`PARCHEAR`** aparece como marcador en los `serial:` (`mcu-cnc.cfg`, `extruder-hotend.cfg`). Antes de aplicar potencia, sustituir por la ruta real (`ls /dev/serial/by-id/*`). Para Uno genuino: `usb-Arduino__...`; clon CH340: `usb-1a86_USB_Serial-...`.
- **`probe-zsensor.cfg` está comentado** en `printer.cfg`. Descomentar sólo cuando la sonda esté físicamente montada.
- **Valores conservadores intencionales**: `max_velocity=150`, `max_accel=1500`, `max_z_velocity=10`, `max_z_accel=100`. Subir SOLO tras input shaping (ver `docs/08`). El ATmega328P tiene límite de step-rate documentado.
- **`[gcode_arcs] resolution: 1.0`** activado a propósito para reutilizar G-code de CAM (G2/G3). No quitar.
- **`SAVE_CONFIG`**: tras `PID_CALIBRATE`, `PROBE_CALIBRATE`, `SHAPER_CALIBRATE`, Klipper **reescribe** un bloque `#*# <---- SAVE_CONFIG ---->` al final de `printer.cfg`. **Nunca editarlo a mano** — se sobrescribe en el siguiente calibrado.
- **`[virtual_sdcard]` apunta a `~/printer_data/gcodes`** (estándar Mainsail/Fluidd). Cambiarlo rompe el sirve-archivos de Moonraker.
- **Macros sin cama caliente**: `M140`/`M190` y `#BED_MESH_PROFILE LOAD` están comentados. Si Antonio añade cama caliente al K10 (improbable, hay un único ADC PA2 = termistor hotend), descomentarlas.
- **`LOAD_FILAMENT` empuja 50+20 mm** y `UNLOAD_FILAMENT` retrae 60 mm. Para el extrusor K10 (steps/mm OEM=1040) los valores quedan en mm absolutos — no hay multiplicador.

## Si tocas algo

1. Cualquier cambio en pines K10 implica leer **`../firmware/k10/AGENTS.md`** primero (hay pines OEM sin resolver).
2. Cambios de step-rate o microstepping: revisar `../firmware/cnc/AGENTS.md` (límite hardware ATmega328P).
3. **Acelerómetro host** opcional para input shaping está pre-cableado y comentado en `mcu-cnc.cfg` (sección `[mcu rpi]` + `[adxl345]` + `[resonance_tester]`). Descomentar requiere instalar `klipper_mcu` en la Pi.
