# AGENTS.md — firmware/k10/

Firmware Klipper para la **EasyThreed K10** (MCU **GD32F303CBT6** = clon de STM32F103, 128 KB flash, 48 KB RAM). El K10 es la placa donante del proyecto Frankenprinter — gobierna **sólo extrusor + hotend** en el reparto multi-MCU.

## Layout

```
klipper.config       # FRAGMENTO mínimo (CI lo expande con `make olddefconfig`)
reversing.md         # parámetros extraídos del firmware OEM + mapa pines (incompleto)
README.md            # flujo de flasheo SD + parámetros MCU
stock/               # GITIGNORED · respaldo OEM (propietario, no redistribuir)
build/               # gitignored, salida local si compilas a mano
```

## Compilación: SÓLO en CI

**No hay toolchain ARM en esta máquina.** Compilar localmente requiere `gcc-arm-none-eabi`. El flujo correcto es:

1. GitHub → Actions → "Build K10 Klipper firmware" → Run workflow (o push a `klipper.config`).
2. El workflow (`.github/workflows/build-k10-klipper.yml`) fija `KLIPPER_COMMIT=4c137ed3...` para builds reproducibles. **No bumpees este SHA** sin replicar localmente.
3. Verifica automáticamente: ELF enlazado en **`0x08004000`** y tamaño ≤ **112 KB** (128 KB flash – 16 KB bootloader). Si alguna falla → workflow rojo.
4. Artefacto: `p10_printer.bin`.

## klipper.config (fragmento)

Símbolos mínimos críticos — `olddefconfig` rellena el resto:

| Símbolo | Razón |
|---------|-------|
| `CONFIG_MACH_STM32F103=y` | target Klipper para STM32F1 (vale para clones GigaDevice) |
| `CONFIG_STM32F103GD_DISABLE_SWD=y` | libera PA13/PA14 al boot (clones GD lo requieren) |
| `CONFIG_STM32_FLASH_START_4000=y` | desplaza el código a 0x4000 = offset del bootloader SD |
| `CONFIG_STM32_CLOCK_REF_8M=y` | cristal externo 8 MHz |
| USB nativo PA11/PA12 | comunicación con Klippy (no usar para steppers — ver alerta abajo) |

## Flasheo (microSD)

- Bootloader busca **`p10_printer.bin`** (NO `mksLite.bin`, eso es la K9).
- SD FAT32, raíz, archivo en raíz. Encender ~10–30 s.
- **Señal de éxito**: bootloader renombra a `p10_printer.CUR`. Si sigue como `.bin` → rechazado (probable checksum/cabecera) → necesitarías SWD para depurar (no soportado).
- Tras flash, USB aparece como `usb-Klipper_stm32f103xe_*` (ej.: `30303B4D1C20300836303638-if00`).

## reversing.md — qué está resuelto y qué no

Datos del firmware OEM (`stock/*.bin`, `stock/k10_cfg.txt`):

- ✅ **Resuelto**: PA2=termistor (único ADC → confirma "no hay cama caliente"); PA3=endstop; PA5/PA6/PA7=SPI1 (SD/display); PA13/PA14=SWD (no reasignar); PB0/PB1/PB10/PB12–15=teclado 7 botones (S3–S9).
- ✅ **Térmico OEM**: `Kp=22.2 Ki=1.08 Kd=114`, `EXTRUDE_MINTEMP=170`, `HEATER_MAXTEMP=275` (límite PTFE). Úsalos como punto de partida; `PID_CALIBRATE` los sobrescribe.
- ✅ **Steps/mm OEM** (referencia): X=606 Y=606 Z=600 E=1040.
- 🔴 **SIN RESOLVER (crítico)**: pin del **HEATER del hotend** (es software-PWM con tabla en RAM, no resoluble por reversing estático). Candidatos: PA4 / PA15 / PB9. **Confirmar con multímetro en la placa real antes de habilitar PWM.**
- 🔴 **SIN RESOLVER**: qué eje es cada stepper (ISR analizada movía los 4 a la vez).

### ALERTA PA11/PA12

El OEM usa PA11/PA12 como STEP/DIR de stepper. **En esta placa CBT6/LQFP48 son nuestro USB Klipper** (el reversing es de la variante RCT6/LQFP64 de palmarci, layout distinto). **No declares stepper en PA11/PA12** o pierdes la conexión USB.

## stock/ (gitignored)

Respaldo OEM (billete de vuelta a fábrica). Archivos: `p10_printer.bin` (OEM, NO el que compila el CI), `bl.dump` (bootloader), `disasm.txt`, `flash.bin`, `k10_cfg.txt` (config en texto plano que dumpea el firmware OEM al SD), `README.md` (hashes). **No regenerar ni "limpiar"**; no redistribuir.

## Anti-patrones

- **No** ejecutes `make -C klipper` localmente sin `arm-none-eabi-gcc` instalado — el Makefile fallará silenciosamente.
- **No** muevas la dirección 0x4000 (ej.: `CONFIG_STM32_FLASH_START_0=y`) — el bootloader la busca exactamente ahí y bricaría sin SWD.
- **No** asumas que `stock/p10_printer.bin` es el firmware Klipper compilado; es el OEM. El CI produce uno con el mismo nombre por razones del bootloader.
