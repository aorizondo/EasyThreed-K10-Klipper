# AGENTS.md — firmware/cnc/

Firmware del **CNC casero** (Arduino Uno R3 oficial, ATmega328P @16 MHz, CNC Shield V3, 4× A4988 microstep 1/16). **Dos rutas en paralelo**: GRBL (operativa hoy) y Klipper (planificada). Cambiar de una a otra implica reflasheo + ajustes de hardware (jumpers).

## Layout

```
klipper.config               # ruta Klipper (FRAGMENTO mínimo)
grbl-setup.md                # ruta GRBL — settings calibrados vigentes
grbl-settings-backup.txt     # snapshot $$ (backup ante pérdida de EEPROM)
build/                       # gitignored
```

## Estado actual: GRBL operativo

Toolchain AVR (`avr-gcc`, `avrdude`) **sí está local**. Recompilar GRBL desde `repos/grbl` (gnea/grbl 1.1h):

```bash
cd repos/grbl && make                     # → grbl.hex (~30.7 KB, cabe justo en 32 KB)
avrdude -c arduino -p atmega328p -P /dev/ttyACM0 -b 115200 -D -U flash:w:grbl.hex:i
```

`repos/grbl` **no** lo clona `scripts/clone-references.sh` — clónalo aparte (no está en el script).

### Settings GRBL calibrados (no romper)

| Eje | steps/mm | vel. (mm/min) | acel. (mm/s²) | Estado |
|-----|----------|---------------|---------------|--------|
| X | `$100=75.83` | `$110=10000` | `$120=1200` | hereda Y; **falta verificar recableado bipolar del motor X** |
| Y | `$101=75.83` | `$111=10000` | `$121=1200` | ✅ calibrado <0.5% tras recablear a bipolar |
| Z | `$102=2226` | `$112=200` | `$122=8` | ✅ calibrado ±2%; dir invertida `$3=4` |

Recorrido: `$130/131/132 = 380/360/40 mm`. Soft/hard limits + homing **todos = 0** (no hay endstops). Restaurar con `grbl-settings-backup.txt` si la EEPROM se borra.

### Hechos físicos no obvios

- **Z avanza ~1.44 mm/vuelta**, NO 2 mm (el paso a ojo engañaba). Por eso `$102=2226`, no ~1600.
- **Backlash Z ≈ 0.55 mm** sin compensar (GRBL 1.1 no lo compensa) → tuerca anti-holgura o asumirlo.
- **Vref Z = 0.71 V** (más bajo perdía pasos al subir).
- **Doble motor Y por HARDWARE**: jumpers de clonado del socket A puestos en Y. GRBL ve 3 ejes; el 2º motor lo mueve el shield clonando la señal de Y. **No declares `$N=4 ejes`** — sigue siendo 3.
- **Motores X/Y/Z/E**: STP-42D221-01 (= EM-284), NEMA17 1.8°/paso (200 pasos/v), **unipolar 5 cables** conectados como bipolar a los A4988 (~1 A/fase). Si X aún no rinde como Y → revisar recableado a bipolar antes de tocar steps/mm.

## Ruta Klipper (no activa)

`klipper.config` = símbolos mínimos. Mismo flujo de `make olddefconfig` que el K10 pero **toolchain local** (no requiere CI).

### Por qué el config es minimalista

ATmega328P = **32 KB flash** (incluido optiboot ~0.5 KB). Klipper sin recortes no cabe. El config **desactiva explícitamente** todo lo opcional:

```
# CONFIG_WANT_ADC, _SPI, _I2C, _BUTTONS, _TMCUART, _NEOPIXEL,
# _ST7920, _HD44780, _ADXL345/_LIS2DW/_BMI160/_MPU9250/_ICM20948,
# _THERMOCOUPLE, _HX71X, _ADS131M0X/_ADS1220, _LDC1612,
# _SENSOR_ANGLE, _TRIGGER_ANALOG, _PULSE_COUNTER  →  all "is not set"
CONFIG_WANT_HARD_PWM=y      # único feature opcional: PWM HW para spindle/láser
```

**No habilites** ninguno de los `WANT_*` sin verificar que cabe. El config validado pesa ~14 KB / 32 KB.

### Cambio GRBL → Klipper requiere también HW

- **Quitar jumpers de clonado del socket A**. Klipper declara el 2º motor Y como `[stepper_y1]` software-side. **Sin quitar los jumpers, los dos motores se moverán a la vez Y TAMBIÉN como Z/A → daño mecánico.**
- Velocidad serie esperada por Klipper: **250000 baud** (no 115200 como GRBL).
- `by-id` del Uno: `usb-Arduino__www.arduino.cc__0043_7573530303135180C070-if00`.

## Anti-patrones

- **No reescribir** `grbl-settings-backup.txt` salvo tras una recalibración completa con medida real.
- **No habilites endstops en GRBL** (`$20/21/22 ≠ 0`) sin hardware: GRBL bloqueará todo movimiento esperando homing.
- **No subas microstepping a 1/32** en Klipper-ATmega: "Timer too close" error. 1/16 es el techo práctico.
