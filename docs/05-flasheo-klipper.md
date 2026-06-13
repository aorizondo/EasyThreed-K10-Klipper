# 05 — Flasheo de Klipper en los dos MCU

Cada MCU necesita el firmware de Klipper compilado **para su chip**. Se compila en la Pi
(`~/klipper`) con `make menuconfig` + `make`, y se flashea por su vía. El proceso host (que hace la
cinemática) es común; lo que cambia es el binario de cada MCU.

---

## A. Arduino Uno (ATmega328P) — el CNC

### menuconfig
```bash
cd ~/klipper
make clean
make menuconfig
```
Opciones:
```
[*] Enable extra low-level configuration options
    Micro-controller Architecture: Atmega AVR
    Processor model: atmega328p
    Processor speed: 16 MHz
    Communication interface: Serial (UART)
    Baud rate for serial port: 250000
```
> Flash de 32 KB justa: **desactiva** lo que no uses para que el binario quepa. Si `make` se queja de
> tamaño, quita features opcionales.

### Compilar y flashear
```bash
make
# El Uno ya trae bootloader Arduino/Optiboot -> NO necesitas ISP externo:
avrdude -c arduino -p atmega328p -P /dev/ttyACM0 -b 115200 -D \
        -U flash:w:out/klipper.elf.hex:i
```
- Puerto: `/dev/ttyACM0` (Uno genuino) o `/dev/ttyUSB0` (clon CH340). Mejor usa el `by-id`.
- Baud bootloader: **115200** (Optiboot moderno). Clones viejos: prueba **57600** si da "out of sync".
- `-D` = no borrar todo (necesario con bootloader).
- Alternativa: `make flash FLASH_DEVICE=/dev/serial/by-id/usb-...`.
- Vía KIAUH: `[Advanced] → [Build + Flash]`.

> Solo necesitas un ISP externo si quisieras **regrabar el bootloader**, no para Klipper.

---

## B. Placa K10 (GD32F303 ≈ STM32F103) — el cabezal (Opción A)

El GD32F303 se trata como **STM32F103**. Hay **dos** vías de flasheo:

### B.1 — Vía SWD (recomendada para el primer flasheo): ST-Link / Picoprobe + OpenOCD
La K10 expone SWD: **SWDIO=PA13, SWCLK=PA14, NRST=PB4** (ver [02](02-hardware-k10.md)).

menuconfig:
```bash
cd ~/klipper && make menuconfig
```
```
Micro-controller Architecture: STMicroelectronics STM32
Processor model: STM32F103
Bootloader offset: No bootloader        <-- al flashear por SWD vas a 0x08000000
Clock Reference: 8 MHz crystal          <-- verifica el cristal de la placa (8 MHz típico)
Communication interface: <segun la via que elijas, ver docs/04>
   - Opción A (USB nativo, recomendada): USB (on PA11/PA12)   <-- requiere R 1.5kΩ en D+
   - Opción B/C (UART):                  Serial (on USART1 PA9/PA10)
[*] Disable SWD at startup (GD32)        <-- IMPORTANTE para GD32F303
Baud rate: 250000                        <-- solo aplica a Serial (no a USB)
```
Compilar y flashear con OpenOCD/ST-Link:
```bash
make
# Con ST-Link V2:
openocd -f interface/stlink.cfg -f target/stm32f1x.cfg \
        -c "program out/klipper.bin verify reset exit 0x08000000"
```
> El "Disable SWD at startup" libera PA13/PA14 (que en GD32 a veces dan problemas) y es lo que hace que
> el GD32F303 arranque Klipper de forma fiable.

### B.2 — Vía bootloader SD (sin tocar SWD)
La K10 trae el bootloader MKS que carga un `.bin` desde microSD a flash en **offset 0x4000** (= 16 KiB).
En ese caso:
```
Bootloader offset: 16KiB bootloader
```
Genera `out/klipper.bin`, renómbralo a algo único acabado en `.bin` (que **no** sea igual al último
flasheado), cópialo a la microSD, insértala y enciende. El bootloader lo graba.
> Ojo: el offset del bootloader (16KiB vs 28KiB) depende del bootloader real de la placa. El de Creality
> 4.2.2 GD32 usa **28KiB**; el MKS Lite de EasyThreed apunta a **0x4000 = 16KiB**. **Prueba 16KiB primero**
> (es lo que indica el reversing de la K10); si no arranca, prueba 28KiB. Conserva siempre un **volcado del
> firmware original** por SWD antes de sobreescribir (ver abajo).

### B.3 — Salvaguarda: vuelca el firmware original primero
Antes de sobreescribir nada en la K10, con SWD:
```bash
openocd -f interface/stlink.cfg -f target/stm32f1x.cfg \
        -c "init; reset halt; dump_image k10_original.bin 0x08000000 0x40000; exit"
```
Guarda `k10_original.bin` en `reference/`. Si algo sale mal, puedes restaurar Marlin.

---

## Verificación tras flashear

```bash
ls /dev/serial/by-id/*          # cada MCU debe aparecer
```
En `printer.cfg` pon esos `by-id` en `[mcu]` (CNC) y `[mcu k10]`. Luego en Mainsail: `FIRMWARE_RESTART`.
Si conecta, en la consola verás la versión de Klipper de cada MCU. Comprueba que **las versiones host y
MCU coinciden** (si no, recompila/reflashea).

---

## Errores típicos
| Síntoma | Causa probable | Solución |
|---|---|---|
| `avrdude: stk500_recv(): programmer is not responding` | baud bootloader o puerto | prueba 57600; revisa /dev correcto; sin el monitor serie abierto |
| `Unable to connect` (k10) | TX/RX cruzados, baud, 3.3V | intercambia TX/RX; mismo baud host/MCU; GND común |
| GD32 no arranca tras SWD | falta "Disable SWD at startup" u offset | activa esa opción; revisa offset/cristal |
| Versiones host/MCU distintas | recompilaste host pero no MCU (o al revés) | reflashea con el mismo commit |

Fuentes: <https://www.klipper3d.org/Installation.html> · <https://www.klipper3d.org/Bootloaders.html> ·
GD32F303: <https://klipper.discourse.group/t/installing-klipper-on-ender-3-4-2-2-board-and-gd32f303-chip/8165>
