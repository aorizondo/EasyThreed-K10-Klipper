# 02 — Hardware de la EasyThreed K10

Datos de ingeniería inversa publicada y de identificación de componentes. La fuente técnica más
completa es el reversing de palmarci.me; donde un dato no está verificado se indica.

Fuente principal: <https://palmarci.me/blog/2025-05-02-easythreed-k10-3d-printer-main-board-reversing/index.html>

---

## Microcontrolador

- **GigaDevice GD32F303RCT6**, núcleo ARM **Cortex-M4**.
- Encapsulado leído como **LQFP48** (nota: la referencia "RC" suele ser LQFP64/256 KB; posible
  discrepancia de lectura — verifícalo contando pines en tu placa).
- **Clon electrónico de STM32F103.** Para Klipper se compila como **STM32F103** y se trata como tal.
- Frecuencia/SRAM exactas de **esta** placa no confirmadas por la fuente (el GD32F303 de catálogo
  llega a 120 MHz). No son críticas para Klipper.

### Por qué importa
- **Klipper SÍ corre en GD32F303**, compilando con target STM32F103 + **bootloader de 28 KiB** y la
  opción *"Disable SWD at startup"*. Probado en miles de placas Creality 4.2.2.
  Ref: <https://klipper.discourse.group/t/installing-klipper-on-ender-3-4-2-2-board-and-gd32f303-chip/8165>

---

## Alimentación y "USB"

- Entrada por **DC barrel jack** + **USB-C**, con interruptor de 3 posiciones (OFF / USB-PD / DC).
- El **USB-C es SOLO alimentación**: controlador **PW6606 (USB-PD Sink)**. **No hay datos USB al MCU.**
- **No hay chip USB-serie** (CH340/CP2102). La operación de fábrica es **por microSD**.
- Hay una función USART0 en el firmware que **no se ejecuta**; los pines UART **no están ruteados** a
  ningún conector. → Para Klipper hay que **soldar UART al MCU** (ver [04](04-soldadura-uart-k10.md)).
- Tensión del sistema: el contexto apunta a **12 V** (no confirmado al 100% por la fuente). **Mídelo**
  antes de conectar nada: pon el multímetro en la entrada del barrel jack y en el riel principal.

> ⚠️ Antes de alimentar la K10 por DC mientras tienes el UART soldado y conectado a la Pi, asegúrate
> de **GND común** entre la K10 y la Pi/adaptador USB-TTL, y de **no** alimentar lógica de 5 V hacia
> pines de 3.3 V. El GD32 es de **3.3 V**: el adaptador USB-TTL debe ir en modo **3.3 V**, no 5 V.

---

## Drivers de motores

- **4× Heroic HR4988** (clon de A4988 con traductor integrado): X, Y, Z y extrusor.
- **Van soldados en la placa** (no son módulos extraíbles). No son TMC: **sin** StealthChop, **sin**
  UART, **sin** sensorless homing.
- En esta arquitectura solo usarás **el driver del extrusor** de la K10 (los de X/Y/Z los pone el
  CNC). Tendrás que localizar por reversing los pines step/dir/enable de **ese** driver.

---

## Calefactor y térmica

- MOSFET de potencia **Hooyi HY1403 (N-channel)** conmuta el cartucho calefactor del hotend.
  → El pin de **gate** de ese MOSFET es el `heater_pin` que buscas.
- **Termistor** NTC en divisor resistivo hacia un **pin ADC** del GD32 → ese es el `sensor_pin`.
  Tipo probable: NTC 100k. Verifica el `sensor_type` por calibración/medición (ver [08](08-calibracion.md)).
- Regulador **AMS1117** (genera el 3.3 V de la lógica).

---

## Depuración / flasheo (SWD)

Pines de debug accesibles (clave para reversing y para flashear Klipper sin bootloader SD):

| Señal | Pin GD32 |
|-------|----------|
| SWDIO | **PA13** |
| SWCLK | **PA14** |
| NRST  | **PB4**  |
| (JTAG extra) | PA15 / PB3 |

- En la unidad analizada la **RDP (read protection) estaba desactivada** → se puede volcar y reflashear.
- Puedes usar un **ST-Link V2** (~3€) o un **Raspberry Pi Pico como Picoprobe** + **OpenOCD**.
- Carga alternativa por **SD**: el bootloader MKS lee un `.bin` (la K10 carga `p10_printer.bin` a
  flash en offset `0x4000`, config en `k10_cfg.txt` offset `0x1F800`). Regla: el nombre del `.bin`
  debe acabar en `.bin` y **no** coincidir con el último flasheado, o el bootloader lo ignora.

---

## Firmware de fábrica

- **Marlin** (base 1.1.1 modificada por MKS, personalizada por EasyThreed). Las UIs EasyThreed están
  en Marlin mainline desde 2.0.9.3 (`MKS_ROBIN_LITE`).
- Para K10 se puede compilar Marlin con `BOARD_CREALITY_V422_GD32_MFL`.
- Proyecto comunitario Marlin para EasyThreed: **ECF-Marlin** (clonado en `repos/ECF-Marlin/`).
  Útil como **referencia de pinout** aunque sea para K7/K9.

---

## Lo que NO está publicado (lo tendrás que reversear tú)

- Pinout completo: qué pin del GD32 va a cada driver (step/dir/enable del extrusor), al gate del
  MOSFET del calefactor, al ADC del termistor, al fan y a los endstops.
- Metodología paso a paso en **[10 — Reversing del pinout K10](10-reversing-pinout-k10.md)**.

---

## Resumen para Klipper (Opción A)

| Recurso K10 | Uso en la frankenprinter | Pin Klipper |
|-------------|--------------------------|-------------|
| Driver extrusor (HR4988) | `[extruder]` step/dir/enable | `k10:P??` (reversing) |
| Gate MOSFET HY1403 | `[extruder] heater_pin` | `k10:P??` (reversing) |
| Divisor termistor → ADC | `[extruder] sensor_pin` | `k10:P??` (reversing) |
| Fan | `[fan]` / `[heater_fan]` | `k10:P??` (reversing) |
| UART soldado al MCU | enlace serie con la Pi | (define `serial:` en `[mcu k10]`) |
