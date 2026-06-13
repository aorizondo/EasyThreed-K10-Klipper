# 01 — Arquitectura del sistema (multi-MCU Klipper)

## Concepto

Klipper separa el "pensar" del "ejecutar":

- El **host** (Raspberry Pi) hace **toda** la planificación: cinemática, look-ahead,
  aceleración, input shaping, pressure advance. Convierte el G-code en una lista temporizada
  de pasos.
- Cada **microcontrolador (MCU)** solo ejecuta los pasos que el host le manda, con timing
  preciso. Es "tonto" a propósito.

Esto permite **multi-MCU**: varios microcontroladores conectados a la misma Pi, cada uno
responsable de unos actuadores. El host sincroniza sus relojes automáticamente y reparte el
trabajo. Es exactamente el patrón "toolhead board" que usan impresoras comerciales modernas.

## Topología de esta frankenprinter (Opción A)

```
                       ┌─────────────────────────────┐
                       │      Raspberry Pi (host)     │
                       │  Klipper host + Moonraker +  │
                       │       Mainsail/Fluidd        │
                       └──────────────┬───────┬───────┘
                          USB serie   │       │  USB serie
                  (/dev/serial/by-id) │       │ (/dev/serial/by-id)
                       ┌──────────────┘       └──────────────┐
                       ▼                                      ▼
          ┌────────────────────────┐            ┌───────────────────────────┐
          │  MCU "cnc"             │            │  MCU "k10"                │
          │  Arduino Uno (328P)    │            │  Placa K10 (GD32F303)     │
          │  + CNC Shield V3       │            │  (UART soldado al MCU)    │
          ├────────────────────────┤            ├───────────────────────────┤
          │  X stepper + endstop   │            │  Extrusor (stepper)       │
          │  Y stepper + endstop   │            │  Calefactor hotend (MOSFET)│
          │  Z stepper + endstop   │            │  Termistor (ADC)          │
          │  Sonda Z (opcional)    │            │  Fan de capa / hotend     │
          └────────────────────────┘            └───────────────────────────┘
                  motores NEMA17                     cabezal de la K10
                  (mecánica Epson)
```

> En la **Opción B/C** el bloque "MCU k10" se sustituye por una placa de impresora barata
> (mismo rol de toolhead), o por una única placa que asume también X/Y/Z. La lógica de
> Klipper es idéntica; solo cambian los `serial:` y los nombres de pin.

## Cómo se declara en `printer.cfg`

Un MCU es el **principal** (`[mcu]`, sin nombre) y el resto **secundarios** (`[mcu nombre]`).
Cada pin de un MCU secundario se referencia con el **prefijo** `nombre:`.

```ini
# --- MCU principal: el CNC (ejes) ---
[mcu]
serial: /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0   # tu Arduino (ver ls by-id)

# --- MCU secundario: el cabezal K10 ---
[mcu k10]
serial: /dev/serial/by-id/usb-Klipper_stm32f103xe_XXXX-if00

[stepper_x]
step_pin: PD2            # sin prefijo -> MCU principal (Arduino)
dir_pin: PD5
enable_pin: !PB0
...

[extruder]
step_pin: k10:PB?        # con prefijo -> MCU k10 (pin a determinar por reversing)
heater_pin: k10:PA?
sensor_pin: k10:PA?
...
```

Reglas:
- Los **modificadores de pin** van antes del prefijo: `^k10:PA1` (pull-up), `!k10:PB0` (invertir),
  `^!cnc:PB4` (ambos).
- Un eje movido por **varios** steppers (p.ej. doble Z) debe tener **todos** sus steppers en el
  **mismo** MCU.

## Sincronización de relojes y homing multi-MCU

- Klipper **sincroniza los relojes** de todos los MCU con el host de forma continua y automática.
  No hay que configurar nada.
- Cuidado con el **homing/probing cuando el endstop está en un MCU distinto** al de los steppers
  de ese eje. Hay latencia de red; puede haber sobrepaso de hasta ~0.25 mm a 10 mm/s. Por eso en
  esta arquitectura conviene que **los endstops y la sonda Z estén en el MISMO MCU que los ejes
  X/Y/Z** (el del CNC). El cabezal (K10) no lleva endstops, solo extrusor + térmica.
- Documentación oficial: <https://www.klipper3d.org/Multi_MCU_Homing.html>

## Identificación estable de puertos serie

Nunca uses `/dev/ttyUSB0` / `/dev/ttyACM0` en la config: cambian al reiniciar o según el orden de
enchufe. Usa el ID persistente:

```bash
ls /dev/serial/by-id/*
```

Si una placa usa un **CH340** clónico (sin número de serie único), `by-id` puede no distinguir dos
placas iguales. En ese caso identifica por **puerto físico**:

```bash
ls /dev/serial/by-path/*
```

y mantén siempre cada MCU enchufado en el **mismo puerto USB** de la Pi (o usa un hub con posiciones fijas).

## Flujo de una impresión

1. Subes el G-code a Mainsail (servido por Moonraker).
2. El host Klipper lee el G-code, aplica cinemática cartesiana + aceleración + input shaping +
   pressure advance, y genera comandos temporizados.
3. Reparte: pasos de X/Y/Z → MCU `cnc`; pasos de extrusor + PWM de calefactor/fan → MCU `k10`.
4. Cada MCU ejecuta en su reloj; el host mantiene todo sincronizado.
5. La térmica (termistor) la lee el MCU `k10` y reporta al host, que cierra el lazo PID.

## Referencias
- Config multi-MCU: <https://www.klipper3d.org/Config_Reference.html> (secciones `[mcu]` / `[mcu nombre]`)
- Multi-MCU homing: <https://www.klipper3d.org/Multi_MCU_Homing.html>
- Overview Klipper: <https://www.klipper3d.org/Overview.html>
