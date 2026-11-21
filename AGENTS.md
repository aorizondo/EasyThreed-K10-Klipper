# AGENTS.md — Frankenprinter R&D workspace

Repo identity: I+D personal de Antonio para fusionar una **EasyThreed K10** + un **CNC casero (Arduino Uno + CNC Shield V3)** bajo **Klipper multi-MCU**. Estado: documentación + configs de partida, no producto. Repo público: `aorizondo/EasyThreed-K10-Klipper`.

## Lee primero (en este orden)

1. `README.md` — visión y diagrama del repo.
2. `docs/11-estado-y-handoff.md` — **estado real** y vivo (no `docs/00–10`, que es el plan). Aquí está qué hitos están conseguidos, qué hardware confirmado, settings GRBL actuales y por qué hay un "giro temporal a GRBL".
3. `docs/00-RESUMEN-EJECUTIVO.md` — matriz de decisión K10-soldar-UART vs alternativa pragmática.
4. `firmware/cnc/grbl-setup.md` — settings GRBL **calibrados** vigentes (no editar a ciegas: ver "Hardware sticky facts" abajo).

## Los tres workspaces (no están enlazados, pero son el mismo proyecto)

Antonio trabaja desde tres rutas separadas. Cuando una tarea hable de "los modelos", "el slicer" o "el plugin", deduce de cuál se trata:

| Ruta | Qué es | Uso típico |
|------|--------|-----------|
| `/home/antonio/Desarrollo/solverius/3dprinter/` (**este repo, git**) | I+D: docs, configs Klipper/GRBL, CAD paramétrico de enclosures, plugin OctoPrint (`octoprint-k9suite`), perfil Cura K9 (`cura-k9-profile`), firmware CI | Cambios versionados. Aquí van docs, código y configs. |
| `/home/antonio/Documentos/Hobby/Maker/` (**no-git, assets**) | `3DPrinting/` (modelos STL/3MF, `marlin comands.txt`, perfiles Cura/Prusa heredados, fotos del cabezal), `CAD/` (SCAD/SolveSpace WIP de utilitarios: cooler, disipadores, antena), `CAM/toolbits/` (FreeCAD Path), `CNC/proyecto cnc/`, `Electronics/` (Labrador) | Diseño 3D y CAM. **No commitear** a este repo; queda como sandbox personal. |
| `/home/antonio/Programas/3d_print/` (**no-git, tooling**) | Cura 5.13 AppImage + `cura-5.13/` config dir, Poetry `.venv` con OctoPrint 1.11, `backlash_compensator.py`, `CuraEngine_plugin_infill_generate/`, `cura_profiles/`, firmware OEM `easythreed k9/`, `lite_cfg.txt` | **Aquí vive el venv y OctoPrint que corre la K9.** No tocar como código fuente. |

Antonio quiere mejor organización entre los tres → si propones mover cosas, valida con él antes (existen referencias cruzadas frágiles: el plugin se instala desde el venv de `Programas/3d_print`).

## Comandos no-obvios

### CAD (OpenSCAD)

```bash
cd cad && ./build.sh           # genera stl/ + renders/ de todas las cajas
```
Requiere `openscad` + `xvfb-run`. Los `cad/stl/` están **gitignored** (regenerables); los `cad/renders/*.png` **sí se versionan** como referencia visual. El script avisa si una pieza no es 2-manifold (no falla, sólo imprime); revisa la salida.

Una sola pieza: `openscad -D 'part="body"' -o out.stl k10_enclosure.scad` (parts: `body`, `lid`, `assembly`).

### Firmware K10 (Klipper)

**No hay toolchain ARM local.** Se compila en GitHub Actions: `.github/workflows/build-k10-klipper.yml`. Disparo: pestaña Actions → "Build K10 Klipper firmware" → Run workflow, o push a `firmware/k10/klipper.config`. El workflow fija `KLIPPER_COMMIT=4c137ed3...` (no lo bumpees sin probar) y verifica que el ELF se enlaza en `0x08004000` y cabe en 112 KB. Artefacto descargable: `p10_printer.bin`.

### Firmware CNC (GRBL o Klipper, en paralelo)

GRBL (modo actual) — toolchain AVR sí está local:
```bash
cd repos/grbl && make
avrdude -c arduino -p atmega328p -P /dev/ttyACM0 -b 115200 -D -U flash:w:grbl.hex:i
```
Klipper-en-Arduino (vía paralela documentada): `firmware/cnc/klipper.config` + `repos/klipper-drawbot` como referencia.

### Repos de terceros

`repos/` está **gitignored**. Reconstruir con `./scripts/clone-references.sh` (clones shallow de klipper, moonraker, kiauh, klipper-drawbot, ECF-Marlin, EasyThreeD-K9-STM32). Si falta `repos/grbl/`, hay que clonarlo aparte (`gnea/grbl`, no está en el script).

### Validar MCU desde el host

```bash
repos/klipper/.venv-klippy/bin/python repos/klipper/klippy/console.py [-b 250000] <dev>
```
El venv `repos/klipper/.venv-klippy` (Python 3.12 + pyserial/greenlet/cffi) es propio del repo Klipper, no del proyecto.

### Plugin OctoPrint (`octoprint-k9suite`, paquete `octoprint_boostkit`)

**El venv de OctoPrint vive FUERA de este repo**, en `/home/antonio/Programas/3d_print/.venv` (Poetry, Python 3.13):
```bash
/home/antonio/Programas/3d_print/.venv/bin/pip install -e /home/antonio/Desarrollo/solverius/3dprinter/octoprint-k9suite
/home/antonio/Programas/3d_print/.venv/bin/octoprint serve
```
Reiniciar OctoPrint **aborta** la impresión en curso → hazlo siempre con la impresora libre. Modo editable: cambios JS/CSS recargan en navegador, cambios Python requieren reiniciar.

### Perfil Cura K9

`cura-k9-profile/` se instala copiando a `~/.local/share/cura/5.13/{definitions,extruders,quality,materials,variants}` (ver su README). El Start G-code **no hace homing** (asume homing manual previo desde OctoPrint).

## K9 ≠ K10 (distinción crítica)

Hay dos impresoras EasyThreed en juego y un agente las confunde fácilmente:

- **K9** (ET4000PLUS): impresora **existente y operativa** con Marlin 2.1 personalizado (EMERGENCY_PARSER, UBL, LIN_ADV). Cama 100×100×100, sin cama caliente. El **plugin OctoPrint** (`octoprint-k9suite`) y el **perfil Cura** (`cura-k9-profile`) son para ESTA. Bootloader busca `mksLite.bin`.
- **K10**: placa donante del **proyecto frankenprinter** (en construcción). MCU GD32F303CBT6 (no STM32 oficial), USB-C **sólo alimentación**, sin chip USB-serie. Bootloader busca `p10_printer.bin` en offset `0x4000`. Compilación SOLO por CI.

Si el usuario dice "la impresora" sin más, suele referirse a la K9 (la que funciona). Para el K10 nombra ambos.

## Hardware sticky facts (no deducibles del código)

- **GRBL calibrado**: `$100=$101=75.83`, `$102=2226`, `$3=4` (Z invertido), `$110=$111=10000 mm/min`, `$120=$121=1200 mm/s²`, `$112=200`, `$122=8`. Eje Z: **2226 steps/mm** porque el tornillo avanza ~1.44 mm/vuelta (no 2 mm). Backlash ≈ 0.55 mm sin compensar. Vref Z = 0.71 V (menos pierde pasos al subir).
- **Doble motor Y por hardware**: jumpers de clonado del socket A están puestos en Y. GRBL ve 3 ejes. Si pasamos a Klipper, hay que **quitar esos jumpers** y declarar `[stepper_y1]` en software.
- **Sin endstops en ningún eje**: homing manual antes de cada trabajo; macro G28 usa `SET_KINEMATIC_POSITION`. A4988 no soporta sensorless.
- **Motores X/Y reciclados de Epson LX**, NEMA17 unipolar STP-42D221-01 (200 pasos/vuelta). El motor X **necesita recableado a bipolar** antes de fiarse de su calibración (Y ya está hecho).
- **Firmware OEM K10** (`firmware/k10/stock/*.bin`): propietario, gitignored, **no redistribuir**. Es el "billete de vuelta a fábrica".
- **PWM spindle/láser libre** en D9/D10/D11 (D12/D13 ocupados por 2º Y si se va a Klipper).

## Convenciones del repo

- Docs en **español**, numeradas (`docs/NN-tema.md`). El doc `11-estado-y-handoff.md` es el único que se actualiza con fecha.
- Commits en español, formato `tipo: descripción` (ver `git log`: `docs:`, `feat:`, etc.).
- CAD: tornillería M3 estándar, `FIT=0.30 mm` por defecto, ventiladores con tamaños estándar (lib lo expone). PETG sin cama caliente → diseñar para brim/orejas.
- Firmware K10 OEM en `.bin`/`.dump`/`disasm.txt` gitignored; los hashes van en su README.

## Recursos externos / memoria

- Investigación temática estructurada del usuario: `/home/antonio/Documentos/Business/Solverius/Research/3D-Printing-CAD/` (COMPAS, BRL-CAD, Obico, AliceVision, FullControl, Kiri:Moto). Consultar antes de proponer herramientas nuevas.
- Memoria del agente: `/home/antonio/.acp-model-provider/claude-config/projects/-home-antonio-Desarrollo-solverius-3dprinter/memory/` — usar `hindsight_recall` para sesiones previas (hay 20+ memorias sobre este proyecto).
- Referencias técnicas externas guardadas en `reference/` (URLs y notas; `images/` incluye fotos del cabezal/PCB).

## Qué evitar

- **No bumpear `KLIPPER_COMMIT`** del workflow sin replicar el build localmente con el toolchain ARM (no instalado en esta máquina).
- **No regenerar `cad/stl/`** y commitearlo (gitignored a propósito).
- **No commitear** assets de `/home/antonio/Documentos/Hobby/Maker/` ni nada de `/home/antonio/Programas/3d_print/` a este repo.
- **No "limpiar"** `firmware/k10/stock/`: aunque parezca redundante, es el respaldo OEM.
- **No reordenar** `docs/00..11`: el orden numérico es parte del índice del README.
