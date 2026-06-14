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

## 3. Pines GPIO — mapeo por reversing multi-agente (38 agentes, 5 lentes + verificación)

Desensamblado Thumb del firmware OEM + verificación adversarial pin a pin.
**⚠️ El binario es de la placa de palmarci (RCT6/LQFP64/256KB); esta placa es CBT6/LQFP48/128KB.
El enrutado de pines PUEDE diferir entre variantes. Confirmar lo crítico en la placa real.**

### ALTA confianza (consistente en varias lentes)

| Pin | Función | Notas |
|-----|---------|-------|
| **PA2** | **Termistor hotend** (ADC1_IN2) | único pin analógico → sin cama caliente |
| **PA3** | **Endstop** (input pull-up, polled) | hay **un solo** endstop discreto, no 3; eje sin confirmar |
| **PA5 / PA6 / PA7** | **SPI1** SCK / MISO / MOSI | display/lector SD. OJO: PA6 es MISO, **no** un endstop |
| **PB0,PB1,PB10,PB12,PB13,PB14,PB15** | **Teclado 7 botones** (S3–S9) | pull-up + EXTI; nº de botón concreto sin resolver |
| **PB4 / PB5** | PWM TIM3 (remap parcial) CH1/CH2 | fan/heater (etiqueta tentativa) |
| **PB6 / PB7 / PB8** | PWM TIM4 CH1/CH2/CH3 | fan/heater (etiqueta tentativa) |
| **PA13 / PA14** | SWD (SWDIO/SWCLK) | **no reasignar** |

### MEDIA confianza — STEPPERS (candidatos; eje X/Y/Z/E NO determinable)

La ISR analizada es una rutina de **test que mueve los 4 ejes a la vez**, así que se distingue
STEP vs DIR pero **no** qué eje es cada uno.

| Grupo | Pines candidatos |
|-------|------------------|
| **STEP** (rápido) | PC15, PC13, PA9, **PA11** |
| **DIR** (lento) | PC14, PA8, PA10, **PA12** |
| **ENABLE** (LOW al boot, activo-bajo HR4988) | PB3, PA1 |
| Aux estáticos (enable/select/sleep?) | PA4, PA15, PB9 |

*La verificación adversarial refutó PA9=STEP y PC14=DIR → el grupo stepper es el menos fiable.*

### SIN RESOLVER (crítico)

- **🔴 Pin del HEATER del hotend.** Es **software-PWM** sobre un GPIO seleccionado desde una
  tabla en RAM (`@0x20000754`/`@0x2000079c`, offset 0x2c) → no se resolvió a un pin literal.
  Candidatos: PA4 / PA15 / PB9 o uno de los PWM. **Es el dato más importante que falta** (seguridad).
- **Eje** de cada stepper (X/Y/Z/E).
- **Etiqueta fan vs heater** de los 5 canales PWM (CCR escrito por punteros en runtime).

### 🔴 ALERTA para el plan Klipper — PA11/PA12

El firmware OEM usa **PA11/PA12 (las patas del USB D-/D+) como STEP/DIR de stepper**. Pero
**tu USB de Klipper soldado ahí FUNCIONA** (enumera y comunica). La explicación más probable:
tu placa CBT6/LQFP48 tiene un layout distinto al RCT6/LQFP64 de palmarci, y/o el grupo stepper
del análisis (rutina de test) no aplica a tu placa. **Conclusión práctica:** en TU placa,
PA11/PA12 = tu USB Klipper. No los uses para steppers en el `printer.cfg`.

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
