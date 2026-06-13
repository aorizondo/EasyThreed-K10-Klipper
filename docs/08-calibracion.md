# 08 — Calibración

Orden recomendado: primero que **se mueva bien** (dirección + distancia), luego **térmica** (PID),
luego **extrusor**, y por último **input shaping**.

---

## 0. Antes de nada: comprobar cada motor

```
STEPPER_BUZZ STEPPER=stepper_x
```
Mueve 1 mm adelante/atrás 10 veces. Diagnóstico:
- **No se mueve** → revisa `enable_pin` / `step_pin` / Vref del driver.
- **No vuelve al origen / se va** → `dir_pin` invertido (añade o quita `!`).
- **Distancia no cuadra** → `rotation_distance` mal (siguiente sección).

Repite con `stepper_y`, `stepper_z`, `extruder`.

---

## 1. rotation_distance de X / Y / Z (mover y medir)

Klipper usa `rotation_distance`, no steps/mm. Para mecánica Epson reciclada (paso de correa/dientes
desconocidos) se calibra **experimentalmente**:

1. Pon un valor de partida en `[stepper_x]` (ej. `rotation_distance: 40`) y `FIRMWARE_RESTART`.
2. `G28` (homing). Marca con cinta la posición del carro; mide con regla/calibre desde un punto fijo.
3. Mueve una distancia grande conocida (más distancia = menos error relativo):
   ```
   G90
   G1 X100 F3000
   ```
4. Mide la distancia **real** recorrida (`medida`).
5. Corrige:
   ```
   rotation_distance_nuevo = rotation_distance_anterior × medida / 100
   ```
   Ej.: pediste 100, midió 96, partías de 40 → `40 × 96/100 = 38.4`.
6. Pon el nuevo valor, `FIRMWARE_RESTART`, y repite hasta que 100 pedidos = 100 ± 0.1 medidos.
7. Repite **independiente** para Y y para Z (en Z, mover en vertical y medir con calibre; husillo
   recuperado → mismo método).

> Mide siempre moviendo en la **misma dirección** para no contaminar con el backlash de los carros Epson.
> Cambiar `microsteps` NO cambia `rotation_distance` (esa es la gracia del modelo de Klipper).

---

## 2. PID del hotend (y cama si la tienes)

Requiere la K10 ya comunicando y el termistor leyendo bien (sección 4 más abajo para validarlo).
```
PID_CALIBRATE HEATER=extruder TARGET=210
SAVE_CONFIG
```
Cama (si lleva PWM):
```
PID_CALIBRATE HEATER=heater_bed TARGET=60
SAVE_CONFIG
```
`SAVE_CONFIG` reescribe los `pid_Kp/Ki/Kd` en un bloque autogenerado al final de `printer.cfg` y reinicia.
En `[extruder]` debe estar `control: pid`. Alternativa simple: `control: watermark` (bang-bang) sin calibrar.

---

## 3. rotation_distance del extrusor (measure & trim)

Con hotend **caliente** (>min_extrude_temp) y filamento cargado:
1. Marca el filamento a **70 mm** de la entrada del extrusor (mide con calibre).
2. ```
   G91
   G1 E50 F60        # extruye 50 mm lento
   ```
3. Mide de nuevo: `extruido_real = 70 − (lo que quedó)`.
4. Corrige:
   ```
   rotation_distance_nuevo = rotation_distance_anterior × extruido_real / 50
   ```
   Redondea a 3 decimales. Si la diferencia con 50 fue > 2 mm, repite tras corregir.

`max_extrude_only_distance: 50` por defecto; si purgas/cargas más de 50 mm de golpe, súbelo (100–150) o
dará "Extrude only move too long".

---

## 4. Validar el termistor de la K10

Como el `sensor_type` lo determinas por reversing, verifica que la lectura sea coherente:
- Con el hotend **frío**, debe marcar ≈ temperatura ambiente (20–25 °C). Si marca 0/−something o un valor
  absurdo, el `sensor_type` o el `sensor_pin` están mal.
- Tipos comunes a probar: `Generic 3950` (NTC 100k β=3950, el más habitual en clones) o
  `EPCOS 100K B57560G104F`. Cambia `sensor_type`, `RESTART`, y compara con un termómetro.
- `min_temp`/`max_temp` disparan **shutdown** si la lectura se sale: pon `min_temp: 0` y un `max_temp`
  seguro para tu hotend (250 con PTFE, 300+ all-metal).

---

## 5. Input shaping (anti-ringing)

### Con acelerómetro ADXL345 (recomendado)
Config (ADXL en la Pi por SPI):
```ini
[mcu rpi]
serial: /tmp/klipper_host_mcu

[adxl345]
cs_pin: rpi:None

[resonance_tester]
accel_chip: adxl345
probe_points: 140, 140, 20      # centro de TU cama (ajusta a tu volumen)
```
Comandos:
```
ACCELEROMETER_QUERY      # ¿responde? muestra X/Y/Z
MEASURE_AXES_NOISE       # ruido de fondo (debe ser bajo)
SHAPER_CALIBRATE         # mide ambos ejes y sugiere shaper+frecuencia
SAVE_CONFIG
```
Resultado típico:
```ini
[input_shaper]
shaper_freq_x: 49.4
shaper_type_x: mzv
shaper_freq_y: 42.0
shaper_type_y: mzv
```

### Sin acelerómetro (torre de ringing manual)
Imprime `~/klipper/docs/prints/ringing_tower.stl` (0 % relleno, 1–2 perímetros, 80–100 mm/s). Antes:
```
SET_VELOCITY_LIMIT MINIMUM_CRUISE_RATIO=0
SET_PRESSURE_ADVANCE ADVANCE=0
SET_INPUT_SHAPER SHAPER_FREQ_X=0 SHAPER_FREQ_Y=0
TUNING_TOWER COMMAND=SET_VELOCITY_LIMIT PARAMETER=ACCEL START=1500 STEP_DELTA=500 STEP_HEIGHT=5
```
Mide la distancia D entre picos de ringing y cuenta N oscilaciones: **f = V·N / D (Hz)** (V = velocidad
del perímetro). Prueba frecuencias con `SET_INPUT_SHAPER SHAPER_TYPE=mzv SHAPER_FREQ_X=...`.
Tipos: ZV, **MZV** (recomendado), ZVD, EI, 2HUMP_EI/3HUMP_EI.

> Recuerda el límite del ATmega328P: input shaping añade carga de pasos. Si aparece `Timer too close`,
> baja microsteps/aceleración.

---

## Resumen de comandos
| Tarea | Comando |
|---|---|
| Probar motor | `STEPPER_BUZZ STEPPER=stepper_x` |
| PID hotend | `PID_CALIBRATE HEATER=extruder TARGET=210` → `SAVE_CONFIG` |
| Extrusor | `G91` + `G1 E50 F60` (measure & trim) |
| z_offset sonda | `PROBE_CALIBRATE` → `TESTZ Z=±..` → `ACCEPT` → `SAVE_CONFIG` |
| Malla cama | `BED_MESH_CALIBRATE` → `SAVE_CONFIG` |
| Resonancias | `SHAPER_CALIBRATE` → `SAVE_CONFIG` |

Fuentes: <https://www.klipper3d.org/Rotation_Distance.html> · <https://www.klipper3d.org/Config_checks.html> ·
<https://www.klipper3d.org/Resonance_Compensation.html> · <https://www.klipper3d.org/Measuring_Resonances.html>
