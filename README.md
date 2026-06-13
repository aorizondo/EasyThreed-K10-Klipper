# Frankenprinter — K10 + CNC → impresora/CNC multifunción con Klipper

Proyecto de fusión de una impresora **EasyThreed K10** con un **CNC cartesiano casero**
(Arduino Uno + CNC Shield, motores NEMA17, mecánica reciclada de impresoras Epson de cinta)
para construir una máquina multifunción gobernada por **Klipper** desde una **Raspberry Pi**.

> Estado: **fase de documentación y experimentación**. Este repo es la base de conocimiento
> y las configuraciones de partida, no un producto terminado.

## Idea de partida (la tuya)

- Reutilizar el **cabezal de la K10** (hotend, termistor, cartucho calefactor, motor de
  extrusión, fan) porque mecánicamente la K10 es limitada.
- Usar la **mecánica del CNC** (guías más precisas, mejores motores NEMA17, mayor volumen
  ~280×280×70 mm) para los ejes **X, Y, Z**.
- **Klipper en ambos MCU**, sincronizados por la Pi en modo **multi-MCU**:
  - **MCU CNC** (Arduino Uno + CNC Shield) → ejes X/Y/Z + endstops + sonda Z.
  - **MCU K10** (placa GD32F303) → extrusor + calefactor + termistor + fan.
- Añadir sensores extra (sonda de eje Z, etc.).

## ⚠️ Lee esto primero

Hay un hallazgo de la investigación que **cambia el plan**: el USB-C de la K10 es **solo
alimentación** (no datos), y la placa **no tiene chip USB-serie**. Habilitar Klipper en la
K10 implica **soldar UART directamente al MCU** y **reversear el pinout** (nadie lo ha
publicado). Es factible pero es ingeniería inversa, no "soldar un USB".

👉 Lee **[docs/00-RESUMEN-EJECUTIVO.md](docs/00-RESUMEN-EJECUTIVO.md)** antes de comprar o
soldar nada. Incluye una **matriz de decisión** entre el camino ambicioso (fiel a tu idea) y
una alternativa pragmática de menor riesgo.

## Índice de documentación

| Doc | Contenido |
|-----|-----------|
| [00 Resumen ejecutivo](docs/00-RESUMEN-EJECUTIVO.md) | Hallazgos críticos y matriz de decisión de arquitectura |
| [01 Arquitectura](docs/01-arquitectura.md) | Modelo multi-MCU de Klipper, roles, flujo de datos |
| [02 Hardware K10](docs/02-hardware-k10.md) | MCU GD32F303, USB-PD, drivers HR4988, MOSFET, SWD |
| [03 Hardware CNC](docs/03-hardware-cnc.md) | Arduino Uno + CNC Shield V3, A4988/DRV8825, NEMA17, mecánica Epson |
| [04 Soldadura UART K10](docs/04-soldadura-uart-k10.md) | La realidad del "USB", dónde soldar, técnica SMD |
| [05 Flasheo Klipper](docs/05-flasheo-klipper.md) | Compilar/flashear en ATmega328P y en GD32F303 |
| [06 Cableado y pinout](docs/06-cableado-pines.md) | Tablas de pines, conexiones físicas |
| [07 Instalación host](docs/07-instalacion-host-pi.md) | Raspberry Pi, KIAUH, Moonraker, Mainsail |
| [08 Calibración](docs/08-calibracion.md) | rotation_distance, PID, extrusor, input shaping |
| [09 Sensores y sonda Z](docs/09-sensores-zprobe.md) | BLTouch, inductiva, microswitch, piezo; bed mesh |
| [10 Reversing pinout K10](docs/10-reversing-pinout-k10.md) | Metodología para mapear los pines del GD32 |
| [99 BOM, riesgos, troubleshooting](docs/99-bom-riesgos-troubleshooting.md) | Compras, errores comunes y soluciones |

## Estructura del repo

```
3dprinter/
├── README.md                  ← este archivo
├── docs/                      ← documentación técnica (orden de lectura por número)
├── klipper-config/            ← configs Klipper de partida
│   ├── printer.cfg            ← config principal (incluye los módulos)
│   ├── include/               ← módulos: MCUs, steppers, extrusor, sonda, macros
│   └── moonraker.conf         ← config de Moonraker
├── diagrams/                  ← diagramas de arquitectura y pinout (Mermaid/ASCII)
├── repos/                     ← repos clonados (referencia y código)
│   ├── klipper/               ← firmware + host
│   ├── moonraker/             ← API server
│   ├── kiauh/                 ← instalador
│   ├── klipper-drawbot/       ← REFERENCIA: Klipper en Arduino Uno + CNC Shield V3
│   ├── ECF-Marlin/            ← REFERENCIA: Marlin comunitario para EasyThreed
│   └── EasyThreeD-K9-STM32/   ← REFERENCIA: firmware K9
└── reference/                 ← notas y fuentes externas guardadas
```

## Fuentes clave

- Klipper: <https://www.klipper3d.org/> · repo <https://github.com/Klipper3d/klipper>
- Reversing de la placa K10 (fuente técnica más completa):
  <https://palmarci.me/blog/2025-05-02-easythreed-k10-3d-printer-main-board-reversing/index.html>
- Klipper en Arduino Uno + CNC Shield (referencia directa):
  <https://github.com/gwisp2/klipper-drawbot>
- Hackaday K9 (identificación de MCU):
  <https://hackaday.com/2024/02/12/easythreed-k9-the-value-in-a-e72-aliexpress-fdm-3d-printer/>
