# Reversing del firmware OEM de la K10 — parámetros extraídos

Datos obtenidos del firmware de fábrica (`stock/p10_printer.bin` + `stock/k10_cfg.txt`),
no derivados a mano. Fuente del binario: reversing de palmarci (ver [stock/README.md](stock/README.md)).

## 1. Parámetros mecánicos/térmicos (de `k10_cfg.txt`, texto plano) — COMPLETO

| Parámetro | X | Y | Z | E |
|-----------|---|---|---|---|
| **steps/mm** | 606 | 606 | 600 | 1040 |
| corriente (mA) | 700 | 700 | 800 | 800 |
| feedrate máx (mm/s) | 20 | 20 | 20 | 20 |
| acel. máx | 50 | 50 | 50 | 100 |
| jerk | 2 | 2 | 0.4 | 0.5 |
| dir invertida | 1 | 1 | 1 | 1 |

- Volumen: **100 × 100 × 100 mm** (X/Y/Z min=0, max=100)
- Homing: **X→MIN (-1), Y→MAX (+1), Z→MIN (-1)**; feedrate homing 1800 mm/m
- Acel. global/retract/travel = 1000; XY jerk = 2
- Térmico: `EXTRUDE_MINTEMP=170`, `HEATER_0_MAXTEMP=275`
- **PID hotend: Kp=22.2, Ki=1.08, Kd=114**
- Thermal runaway: period 40 s, hyst 4 °C; watch 20 s / 2 °C
- Filament change: X5 Y5, Z lift +5

## 2. Acciones de botones (blog palmarci + G-code embebido en el binario) — COMPLETO

| Botón | Función | Secuencia G-code |
|-------|---------|------------------|
| S3 | Retract / descargar | `M104 S200` → `G1 E-120 F300` → `M104 S0` |
| S4 | Feed / cargar | `M104 S200` → `G1 E120 F120` → `G1 E20 F120` |
| S5 | Print / Lift | `M23 <archivo>` → `M24` · lift `G1 Z+10 F1000` |
| S6–S9 | Posición 1–4 | `G28` + movimientos (`G1 X20 Y20 … X80 Y80`) |

## 3. Pines GPIO — PARCIAL (acotado, sin mapeo fino aún)

Escaneo de referencias a periféricos en el binario (`p10_printer.bin`):

- **Puertos GPIO en uso: solo PA, PB, PC** (nada en PD/PE). Coherente con LQFP48.
- **ADC1** → termistor (entrada analógica).
- **TIM1, TIM3, TIM4** → generación de pasos y/o PWM de heater/fan.
- **4 drivers HR4988** (Heroic) para X/Y/Z/E (identificados por palmarci).

Pendiente: mapeo fino función↔pin (qué PAxx es X_STEP, etc.). Ni palmarci lo hizo
("requeriría reversing completo del PCB"). Vías: (a) desensamblado profundo del init
GPIO e ISRs de los timers; (b) híbrido — el firmware acota a PA/PB/PC y multímetro
confirma continuidad desde los pads de los HR4988 a los pines del MCU.

## Equivalencia rápida a Klipper (lo ya listo)

```ini
[stepper_x]
rotation_distance: <full_steps*microsteps / 606>   # steps/mm = 606
# ... (microstepping de los HR4988 por confirmar; típico 1/16)
[extruder]
# rotation_distance a partir de 1040 steps/mm
control: pid
pid_Kp: 22.2
pid_Ki: 1.08
pid_Kd: 114
min_extrude_temp: 170
max_temp: 275
```
