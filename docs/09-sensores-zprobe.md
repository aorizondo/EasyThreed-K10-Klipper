# 09 — Sensores y sonda de eje Z

El nivelado automático (sonda Z + bed mesh) es de lo que más mejora una frankenprinter con cama no
perfectamente plana o no perpendicular a Z. Conecta la sonda preferiblemente en el **MCU del CNC**
(mismo MCU que los steppers XYZ → homing/probing fiable, sin latencia multi-MCU).

Concepto de pin en Klipper: `^` = pull-up, `!` = invertir (trigger en bajo). Si el endstop/sonda
aparece invertido (reporta "TRIGGERED" en reposo), añade/quita el `!`.

---

## Opciones de sonda

### A) BLTouch / CRTouch (servo, 5 hilos) — la más cómoda
```ini
[bltouch]
sensor_pin: ^PB3          # señal del probe (pin libre del CNC; ajusta)
control_pin: PB5          # PWM del servo (ajusta a un pin libre)
stow_on_each_sample: True
x_offset: -40.0           # MIDE físicamente respecto a la boquilla
y_offset: -10.0
#z_offset: 1.5            # lo escribe PROBE_CALIBRATE -> SAVE_CONFIG
samples: 2
samples_tolerance: 0.05

[safe_z_home]
home_xy_position: 140, 140   # centro de TU cama
speed: 50
z_hop: 10
z_hop_speed: 5
```
> Problema en el CNC: el ATmega328P tiene **pocos pines libres**. El BLTouch necesita 2 (señal + servo
> PWM). Si no quedan, valora ponerlo en la K10... pero entonces es **probing multi-MCU** (latencia) — no
> ideal. Mejor liberar pines (p.ej. no usar spindle) o ir a sonda de 1 hilo (inductiva/microswitch).
Pruebas: `BLTOUCH_DEBUG COMMAND=pin_down` / `pin_up` / `reset` / `self_test`.

### B) Sonda inductiva NPN (1 hilo de señal) — buena para frankenprinter con cama metálica
Detecta metal; **requiere cama/superficie metálica**. Muchas son de 12/24 V → **necesitas adaptar el
nivel** a 3.3/5 V (divisor resistivo o transistor) para no quemar el pin del MCU.
```ini
[probe]
pin: ^PB3                 # NPN open-collector: pull-up; añade ! si aparece invertido (^!PB3)
x_offset: 20.0
y_offset: 0.0
#z_offset: 0.8            # por PROBE_CALIBRATE
speed: 5.0
samples: 2
samples_result: median
sample_retract_dist: 2.0
```

### C) Microswitch en soporte (mecánico, 1 hilo, barato)
Un final de carrera montado junto a la boquilla, baja y toca la cama.
```ini
[probe]
pin: ^PB3                 # NC o NO -> ajusta con ! según el caso
x_offset: 0.0
y_offset: 25.0
#z_offset: ...
speed: 5.0
samples: 3
```

### D) Sonda piezo (detecta el toque de la propia boquilla) — offsets 0
```ini
[probe]
pin: ^PB3                 # salida del módulo piezo (comparador)
x_offset: 0.0
y_offset: 0.0
#z_offset: 0.0
speed: 3.0
samples: 3
samples_tolerance: 0.03
```

---

## Homing de Z con sonda

Para que la sonda haga de endstop de Z, en `[stepper_z]`:
```ini
endstop_pin: probe:z_virtual_endstop     # en vez de un endstop físico
# y ELIMINA position_endstop
```
Y añade `[safe_z_home]` (sube Z, homea XY, va al centro, homea Z). Si usas **endstop Z físico**
(microswitch fijo) en lugar de sonda, mantén `endstop_pin: ^PB4` y `position_endstop`.

---

## Calibrar z_offset (común a todas las sondas)

1. `G28` y lleva el cabezal cerca del centro.
2. `PROBE_CALIBRATE` → sondea y te deja bajar a mano con `TESTZ Z=-0.1`, `TESTZ Z=+0.05`… hasta que un
   papel roce la boquilla. `ACCEPT` y `SAVE_CONFIG` escribe el `z_offset`.
   - Con endstop Z mecánico (sin sonda): el equivalente es `Z_ENDSTOP_CALIBRATE`.

---

## Bed mesh (compensación de cama)

Primero el `z_offset` debe estar calibrado.
```ini
[bed_mesh]
speed: 120
horizontal_move_z: 5
mesh_min: 20, 20            # ajusta al alcance real de la sonda en TU volumen
mesh_max: 260, 260
probe_count: 5, 5
algorithm: bicubic
```
```
BED_MESH_CALIBRATE
SAVE_CONFIG
```
Carga la malla en cada impresión añadiendo `BED_MESH_PROFILE LOAD=default` a tu macro `PRINT_START`.

---

## Otros sensores que puedes añadir

- **Final de carrera de filamento** (`[filament_switch_sensor]`) para pausar al acabarse el filamento.
- **Termistor de cama** si añades cama caliente (otro MOSFET + ADC).
- **ADXL345** para input shaping ([08](08-calibracion.md)).
- **Endstops de Z dobles / Z-tilt** solo si pones 2 motores Z (deben ir en el **mismo MCU**).

Fuentes: <https://www.klipper3d.org/BLTouch.html> · <https://www.klipper3d.org/Probe_Calibrate.html> ·
<https://www.klipper3d.org/Bed_Mesh.html> · <https://www.klipper3d.org/Config_Reference.html>
