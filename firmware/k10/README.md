# Firmware Klipper para la EasyThreed K10

Compilación **en la nube** (GitHub Actions) de Klipper para la placa K10, evitando
necesitar el toolchain ARM en local.

## MCU y parámetros (verificados)

| Dato | Valor |
|------|-------|
| MCU | **GD32F303CBT6** — LQFP48, 128 KB flash, 48 KB RAM, Cortex-M4 |
| Target Klipper | `STM32F103` (+ *Disable SWD* para clones GigaDevice) |
| Cristal | 8 MHz externo |
| Comunicación | USB nativo en **PA11 (D−) / PA12 (D+)** + pull-up 1.5 kΩ externa en D+ |
| Bootloader SD | escribe en **0x08004000** (offset `0x4000` = 16 KiB) |
| Fichero que busca el bootloader | **`p10_printer.bin`** (NO `mksLite.bin`, eso es la K9) |
| Señal de éxito | el bootloader renombra el fichero a `p10_printer.CUR` |

La configuración fuente está en [`klipper.config`](klipper.config) (fragmento mínimo;
el CI lo expande con `make olddefconfig`).

## Cómo obtener el firmware

1. En GitHub: pestaña **Actions** → **Build K10 Klipper firmware** → **Run workflow**.
2. Al terminar, descarga el artefacto **`p10_printer`** (contiene `p10_printer.bin`).

## Cómo flashear (microSD)

> ⚠️ Sin SWD no hay rescate. No sabemos si el cargador valida checksum/cabecera propia.
> El renombrado a `.CUR` es la señal de que lo aceptó.

1. Copia `p10_printer.bin` a la **raíz** de una microSD en **FAT32**.
2. Con la placa apagada, inserta la SD; enciende y espera ~10–30 s.
3. Reinserta la SD en el PC:
   - Si ahora se llama **`p10_printer.CUR`** → aceptado y escrito. ✅
   - Si **sigue** como `.bin` → rechazado (posible checksum/cabecera) → haría falta SWD.
4. Conecta el USB (PA11/PA12) al PC. Solo ahora debe aparecer
   `/dev/serial/by-id/usb-Klipper_stm32f103xe_*`.

Detalle del cableado USB y la pull-up: [`../../diagrams/k10-soldadura-usb.md`](../../diagrams/k10-soldadura-usb.md).
