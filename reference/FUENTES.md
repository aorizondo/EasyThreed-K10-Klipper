# Fuentes y referencias (investigación 2026-06-11)

## Hardware EasyThreed K9 / K10
- **Reversing placa K10 (fuente técnica clave)** — GD32F303, USB-C solo PD, HR4988, MOSFET HY1403, SWD:
  https://palmarci.me/blog/2025-05-02-easythreed-k10-3d-printer-main-board-reversing/index.html
- Hackaday K9 (identifica MCU AT32F403ARCT7, 240 MHz):
  https://hackaday.com/2024/02/12/easythreed-k9-the-value-in-a-e72-aliexpress-fdm-3d-printer/
- Marlin comunitario EasyThreed (ECF-Marlin) — referencia de pinout:
  https://github.com/schmttc/ECF-Marlin
- Firmware K9 STM32:
  https://github.com/armandioan/EasyThreeD-K9-STM32 · https://github.com/jonylentzmc/EasyThreeD-K9-STM32-WIP-

## Klipper en GD32F303 (chip de la K10)
- Discourse: instalar Klipper en placas GD32F303 (compilar como STM32F103 + 28KiB + Disable SWD):
  https://klipper.discourse.group/t/installing-klipper-on-ender-3-4-2-2-board-and-gd32f303-chip/8165
- Issue Klipper GD32F303:
  https://github.com/Klipper3d/klipper/issues/5537

## Klipper en Arduino Uno + CNC Shield V3
- klipper-drawbot (montaje idéntico, config de referencia) — clonado en repos/:
  https://github.com/gwisp2/klipper-drawbot
- Benchmarks Klipper (límites step-rate AVR):
  https://www.klipper3d.org/Benchmarks.html
- GRBL cpu_map atmega328p (pinout autoritativo CNC Shield):
  https://github.com/grbl/grbl/blob/master/grbl/cpu_map/cpu_map_atmega328p.h
- Bootloaders (flasheo avrdude):
  https://www.klipper3d.org/Bootloaders.html

## Klipper general (docs oficiales)
- Config reference (multi-MCU, extruder, probe, bed_mesh):
  https://www.klipper3d.org/Config_Reference.html
- Multi-MCU homing:
  https://www.klipper3d.org/Multi_MCU_Homing.html
- Rotation distance:
  https://www.klipper3d.org/Rotation_Distance.html
- Config checks (PID, STEPPER_BUZZ):
  https://www.klipper3d.org/Config_checks.html
- Resonance / input shaping:
  https://www.klipper3d.org/Resonance_Compensation.html · https://www.klipper3d.org/Measuring_Resonances.html
- BLTouch / Probe / Bed mesh:
  https://www.klipper3d.org/BLTouch.html · https://www.klipper3d.org/Probe_Calibrate.html · https://www.klipper3d.org/Bed_Mesh.html
- Instalación / RPi como MCU:
  https://www.klipper3d.org/Installation.html · https://www.klipper3d.org/RPi_microcontroller.html

## Host / interfaces
- KIAUH (instalador) — clonado en repos/: https://github.com/dw-0/kiauh
- Moonraker — clonado en repos/: https://github.com/Arksine/moonraker
- Mainsail: https://github.com/mainsail-crew/mainsail · https://docs.mainsail.xyz/setup/kiauh/
- Fluidd: https://github.com/fluidd-core/fluidd

## Incertidumbres registradas (verificar físicamente)
- Encapsulado exacto del GD32 (RCT6/LQFP48 vs LQFP64): contar pines en TU placa.
- Voltaje del sistema K10/K9 (12V vs 24V): medir.
- Tipo exacto de termistor de la K10 (`sensor_type`): validar por lectura.
- Pinout completo K10 (drivers, heater, ADC, fan): NO publicado → reversing (docs/10).
- USART libre en la K10 (PA9/PA10 vs PA2/PA3 vs PB10/PB11): confirmar en reversing.
- Pin Z-endstop de TU CNC Shield (PB4 vs PB3): verificar con multímetro.
