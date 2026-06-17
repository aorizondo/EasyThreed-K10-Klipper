# 11 · Estado del proyecto y handoff

> Documento vivo del **progreso real** (los docs 00–10 son el plan/diseño). Última actualización: 2026-06-16.
> Pensado para retomar el proyecto en una sesión limpia.

## Resumen en una línea

Frankenprinter: fusionar el **cabezal de una EasyThreed K10** + la **mecánica de un CNC casero**
(Arduino Uno + CNC Shield V3) bajo **Klipper multi-MCU** desde una Raspberry Pi. Repo público:
<https://github.com/aorizondo/EasyThreed-K10-Klipper>.

## Hardware confirmado (con correcciones al plan original)

| Componente | Dato confirmado |
|------------|-----------------|
| MCU K10 | **GD32F303CBT6** — LQFP48, **128 KB** flash, 48 KB RAM, Cortex-M4 (no el RCT6/64 que citaba palmarci) |
| Comunicación K10 | **USB nativo** PA11(D-)/PA12(D+) + pull-up 1.5 kΩ a 3.3 V soldada. Cristal 8 MHz |
| MCU CNC | **Arduino Uno R3 oficial** (2341:0043, ATmega16U2), ATmega328P @16 MHz |
| Drivers CNC | **4× A4988** (PCB roja "HW-134"), microstepping **1/16** (3 jumpers bajo cada driver) |
| Motores X/Y | Reciclados de impresoras **Epson LX** (pasos/correa a calibrar) |
| Motor 2º en Y | El eje Y tiene **2 motores** (pórtico). Socket A del shield, clonado de Y |
| Eje Z | Tornillo (≈ **M8**, paso por confirmar — probable varilla roscada 1.25 mm) |
| Área de trabajo | **380 × 360 mm** (X/Y) · **recorrido Z 40 mm** |
| Endstops | **Ninguno** en ningún eje → homing manual (cero a mano antes de cada trabajo) |

## Hitos conseguidos ✅

1. **K10 con Klipper por USB — FUNCIONANDO y validado.**
   - Compilado en GitHub Actions (`.github/workflows/build-k10-klipper.yml`), flasheado por microSD
     (`p10_printer.bin` → bootloader lo acepta y renombra a `.CUR`, offset 0x4000).
   - Enumera `1d50:614e`, comunica (console.py, get_uptime ok). CLOCK_FREQ=72 MHz.
   - `by-id`: `usb-Klipper_stm32f103xe_30303B4D1C20300836303638-if00`.
   - Config: [`firmware/k10/klipper.config`](../firmware/k10/klipper.config).
2. **Arduino (CNC) con Klipper — flasheado y validado** (con `HARD_PWM`). 14 KB/32 KB.
   - `by-id`: `usb-Arduino__www.arduino.cc__0043_7573530303135180C070-if00`. Serial 250000.
   - Config: [`firmware/cnc/klipper.config`](../firmware/cnc/klipper.config).
3. **Firmware OEM de fábrica de la K10 — respaldado** (billete de vuelta a fábrica).
   - Fuente: palmarci. `firmware/k10/stock/` (gitignorado, propietario) + README con hashes.
4. **Reversing del firmware OEM** ([`firmware/k10/reversing.md`](../firmware/k10/reversing.md)):
   - Steps/mm K10: X606 Y606 Z600 E1040; PID hotend Kp22.2/Ki1.08/Kd114; botones S3–S9.
   - Mapa de pines (38 agentes): PA2=termistor, PA3=endstop, PA5/6/7=SPI, teclado en PB*,
     PWM PB4/5/6/7/8. **Sin resolver: pin del HEATER** (soft-PWM vía tabla RAM) y eje de cada stepper.

## Decisiones de arquitectura

- **Reparto multi-MCU**: Arduino = movimiento X/Y/Z + endstops; K10 = cabezal (heater, fan, termistor,
  extrusor E). Pi = Klippy.
- **Doble motor Y en Klipper**: Opción B (software) → `[stepper_y]` + `[stepper_y1]` (socket A = D12/D13),
  sin auto-cuadrado (no hay endstops). Requiere quitar los jumpers de clonado del shield.
- **Sin endstops** → homing manual: `[force_move] enable_force_move: True` + macro `G28` con
  `SET_KINEMATIC_POSITION X=0 Y=0 Z=0`. (A4988 no soportan sensorless.)
- **PWM spindle/láser**: como D12/D13 se usan para el 2º motor Y, el PWM iría a D9/D10/D11 (libres al no
  haber endstops; son PWM HW del 328p).

## Próximos pasos para RETOMAR la vía Klipper

1. Confirmar con **multímetro** en la placa K10 real: pin del **heater**, pin del **termistor** (¿PA2?),
   y **qué eje es cada stepper** (el reversing no lo fija; además el binario es de otra variante de placa).
2. Calibrar **steps/mm** reales del CNC (motores Epson LX + tornillo Z) — ver más abajo.
3. Montar `printer.cfg` multi-MCU (lado CNC ya definido; lado K10 tras el multímetro).
4. Ajustar **Vref** de cada A4988 al energizar los NEMA/Epson.
5. Instalar host Klipper + Moonraker en la Pi (KIAUH).

## Estado actual: GIRO TEMPORAL A GRBL

El usuario vuelve **temporalmente a GRBL** en el Arduino+Shield para tener el CNC operativo como antes
(la vía Klipper queda documentada aquí para retomarla). Para GRBL:
- Los **jumpers de clonado del eje A se han vuelto a poner en Y** (modo espejo HW para el 2º motor Y).
- GRBL estándar (gnea/grbl 1.1) ve 3 ejes; el 2º motor Y lo mueve el hardware clonado. Pendiente:
  clonar/compilar/flashear GRBL (bloqueado por red en esta sesión) + configurar `$$` (ver plan GRBL).

## Punteros rápidos

- Toolchains locales: `avr-gcc`+`avrdude` (AVR) sí; `arm-none-eabi-gcc` NO (la K10 se compila por CI).
- venv para `console.py`: `repos/klipper/.venv-klippy` (Python 3.12 + pyserial/greenlet/cffi).
- Validar un MCU: `repos/klipper/.venv-klippy/bin/python repos/klipper/klippy/console.py [-b 250000] <dev>`.
