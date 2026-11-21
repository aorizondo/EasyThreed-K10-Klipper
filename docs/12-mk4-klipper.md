# 12 — MK4: migración Marlin → Klipper (Pi 4 + 2× ADXL345 + DHT)

> Máquina distinta del frankenprinter K10+CNC (docs 00–11). Aquí: impresora **MK4** con la
> placa **MKS Robin Lite** (STM32F103RC) reciclada de la K9. Config en `klipper-config/mk4/`.

---

## 1. Inventario (estado 2026-10-15)

| Elemento | MK4 | Origen del dato |
|---|---|---|
| Placa | MKS Robin Lite, STM32F103RCT6, CH340 en USART1, 4 × A4988 | `pins_MKS_ROBIN_LITE.h` |
| Cinemática | Cartesiana, **cama levadiza** (Z mueve cama; X/Y mueven cabezal) | usuario |
| X | 1 motor, 75 pasos/mm, endstop físico `PC13` | M503 2026-08-17 |
| Y | **2 motores en paralelo en el driver Y**, 75 pasos/mm, endstop físico `PC0` | idem |
| Z | **2 brazos K9 en un solo driver**, 606 pasos/mm, recorrido 110 | idem |
| E | Extrusor K9, 1040 pasos/mm, PA 0.08 | idem |
| Corriente (M907) | XY 850 mA (canal compartido) · Z 1200 · E 800 | idem |
| Sonda | Brazo basculante **manual** en `Z_MAX` = `PB9`, contacto = HIGH; M851 X-12 Y-10 Z-0.70 | idem |
| Origen | M206 X-40 Y-60 (origen = esquina del plato 190×240) | idem |
| Hotend | EPCOS 100K en `PA0`, calentador `PC9`, PID 22.20/1.08/114.00 | idem |
| Cama caliente | **No** (PA1/PC8 libres) | perfil OctoPrint MK3 |

Recursos Robin Lite sin uso: `PC12` (Z_MIN), `PB8` (MT_DET), `PA1`, `PC8`, cabecera EXP3.

### Qué no puede hacer esta placa (límites duros)
- **Sin Z independiente**: los 2 motores Z comparten driver → no hay `Z_TILT_ADJUST`.
  Lo mismo en Y (no hay cuadratura del pórtico por software).
- **A4988 sin UART** → nada de sensorless ni corriente dinámica real; sí corriente fija por PWM.
- Para más drivers en el futuro: segunda MCU barata (Pico/RP2040 o el Arduino+CNC Shield) bajo
  Klipper multi-MCU.

---

## 2. Arquitectura

```
Raspberry Pi 4B (Klipper + Moonraker + Mainsail)
 ├── USB ── CH340 ── Robin Lite (MCU "mcu"): motores, hotend, ventilador, endstops, sonda
 └── proceso klipper_mcu (MCU "rpi")
      ├── SPI0 CE0 ── ADXL345 cabezal
      ├── SPI0 CE1 ── ADXL345 cama
      └── GPIO4 ───── DHT (driver del kernel → extras/dht_iio.py)
```

Ventaja: el firmware de la Robin Lite **se compila en la propia Pi** (Klipper instala el toolchain
ARM). No hace falta CI como en el K10.

---

## 3. Cableado en la Raspberry Pi (header de 40 pines)

### ADXL345 (módulo de 10 pines) — conectar **por rótulo**, no por posición

| Rótulo en el módulo | Pi pin | Señal |
|---|---|---|
| `3V3` (si solo hay `VCC`/`VS`, ese) | 1 o 17 | 3.3 V |
| `GND` | 6, 9, 20 o 25 | masa |
| `SCL` | 23 | GPIO11 SPI0_SCLK (compartido) |
| `SDA` | 19 | GPIO10 SPI0_MOSI (compartido) |
| `SDO` | 21 | GPIO9 SPI0_MISO (compartido) |
| `CS` cabezal | **24** | GPIO8 CE0 → `spidev0.0` |
| `CS` cama | **26** | GPIO7 CE1 → `spidev0.1` |
| `INT1`, `INT2`, 2º `GND`, `5V` | — | sin conectar |

> ⚠️ **Nuestros módulos de 10 pines son "solo I2C"** (verificado 2026-11-20 con los dos): el SDO
> del chip va a GND en la placa (pad `SDO` ≈ 6 kΩ a GND). Por I2C responden (`0x53`, DEVID
> `0xE5`), por SPI normal Klipper da `Invalid adxl345 id (got 0 vs e5)`. Probado por bit-bang que
> en **SPI de 3 hilos** (datos por SDA) sí responden. Cableado para 3 hilos, en lugar de la tabla:
>
> | Módulo | Pi |
> |---|---|
> | `SDA` | pin 21 (MISO) directo **y** pin 19 (MOSI) a través de **1 kΩ** (470 Ω–4.7 kΩ) |
> | `SDO` | suelto |
> | `SCL`, `CS`, `3V3`, `GND` | como arriba |
>
> Nunca unir 19 y 21 a SDA sin resistencia (contención de salidas). Software:
> `klipper-config/mk4/extras/adxl345_3w.py` (enlazado en `~/klipper/klippy/extras/`), sección
> `[adxl345_3w cabezal]`; mismos comandos (`ACCELEROMETER_QUERY CHIP=cabezal`).
> **Estado 2026-11-20 (final): los dos módulos son defectuosos o falsos.** Por I2C (hardware, pines
> 3/5, `extras/adxl345_i2c.py`) el chip responde y activa `DATA_READY`, pero X/Y/Z salen siempre
> `0xF000` (fondo de escala negativo) en cualquier rango, sin ruido y sin reaccionar al autotest,
> con `3V3` y `VS` ≈ 3.3 V medidos en el módulo. La parte analógica no mide: ni I2C ni 3 hilos
> lo arreglan. Sustituto recomendado: **MPU-6050/9250 o LIS2DW por I2C** (Klipper los admite de
> serie en la MCU `rpi`, `i2c_bus: i2c.1`, mismos pines 3/5) o un ADXL345 de proveedor fiable.
>
> **Actualización 2026-11-20 (noche): con otro módulo ADXL345 funciona por I2C.** Cableado de 4
> hilos: `5V`/Vin → pin 2 (pasa por el regulador del módulo; antes, comprobar que SDA en reposo
> está a ≈ 3.3 V), `GND` → pin 6, `SDA` → pin 3, `SCL` → pin 5; CS, SDO y VS sueltos. Con
> `[adxl345_i2c cabezal]` (rate 800): 789 Hz reales, muestras uniformes, `MEASURE_AXES_NOISE`
> 37/46/98 mm/s². El módulo anterior daba siempre `0xF000`: estaba defectuoso.
>
> **5º cable (2026-11-21)**: `INT1` → pin 11 (GPIO17), para el homing X/Y por choque (§7.2).

- Alimentar **siempre a 3.3 V**: si el módulo tiene pines `5V` y `3V3`, usar `3V3`.
- **Cable al cabezal**: lo más corto posible, trenzar `SCL` con `GND`; si Klipper da errores de
  lectura, bajar `spi_speed: 2000000` en `[adxl345 cabezal]`. Si sigue fallando, plan B:
  RP2040 (Pico) en el cabezal como MCU USB con el ADXL (recomendado en la doc de Klipper).
- **Montaje**: ejes del chip alineados con los de la impresora, atornillado rígido (nada de
  cinta ni bridas). El de la cama, en el portacama, no en el plato extraíble.

### DHT (3 pines: `+`, `out`, `-`)

| Pin módulo | Pi pin |
|---|---|
| `+` | 1 o 17 (**3.3 V, nunca 5 V**: el pull-up del módulo pondría 5 V en el GPIO) |
| `out` | 7 (GPIO4) |
| `-` | 9 (GND) |

Sin rótulo: azul de rejilla = **DHT11** (±2 °C, ±5 %HR); blanco = **DHT22/AM2302** (±0.5 °C,
±2–5 %HR). El driver del kernel lee los dos.

---

## 4. Preparar la Pi

> **Fase 1 (probar la impresora sin sensores)**: hacer solo 1 y 6; `[include sensors.cfg]`
> va comentado en `printer.cfg`. Los pasos 2–5 cuando se cableen ADXL y DHT.

1. SO + KIAUH + Klipper + Moonraker + Mainsail: ver `docs/07-instalacion-host-pi.md`.
   Al instalar Mainsail, aceptar sus macros (`[include mainsail.cfg]`).
2. `/boot/firmware/config.txt` → añadir y reiniciar:
   ```
   dtparam=spi=on
   dtoverlay=dht11,gpiopin=4
   ```
   Comprobar: `ls /dev/spidev0.*` (dos) y `cat /sys/bus/iio/devices/iio:device0/name` (`dht11…`).
3. MCU "rpi" (proceso Linux, `docs/RPi_microcontroller.md` de Klipper):
   ```bash
   cd ~/klipper && make menuconfig      # Micro-controller: Linux process
   sudo service klipper stop && make flash
   sudo cp ./scripts/klipper-mcu.service /etc/systemd/system/
   sudo systemctl enable --now klipper-mcu.service
   ```
4. numpy para input shaping:
   ```bash
   sudo apt install python3-numpy python3-matplotlib libatlas-base-dev libopenblas-dev
   ~/klippy-env/bin/pip install -v "numpy<1.26"
   ```
5. Extra DHT (enlace simbólico; sobrevive a `git pull` de Klipper):
   ```bash
   ln -sf ~/printer_data/config/extras/dht_iio.py ~/klipper/klippy/extras/dht_iio.py
   ```
6. Copiar `klipper-config/mk4/*` a `~/printer_data/config/` (incluida `extras/`).

**Hecho en la Pi (2026-11-20)**: `dtparam=spi=on` (sin overlay DHT aún), `klipper-mcu.service`
(compilado con `KCONFIG_CONFIG=/tmp/rpi.config OUT=/tmp/out_rpi/` para no pisar el `.config` de la
Robin Lite), numpy 2.5.5 en klippy-env (wheel bajada en el PC), enlace de `adxl345_3w.py`.
`sensors.cfg` incluido; cama y DHT comentados.

### 4.1 Red: configurable desde la microSD

La red la gestiona `mk4-red` (`klipper-config/mk4/host/`): en cada arranque aplica
**`/boot/firmware/mk4-red.txt`** (partición FAT `bootfs`, editable en cualquier PC) a
NetworkManager. Plantilla comentada en `host/mk4-red.txt`: `[mk4] pais = ES` + una sección
`[SSID]` con `clave`/`prioridad` por red. Las conexiones van como `mk4-wifi:<SSID>`; las que se
quiten del archivo se borran. cloud-init ya no toca la red (`99-mk4-network-disabled.cfg`).

- **País Wi-Fi `ES`, no `CU`**: el firmware del BCM43455 rechaza `CU` ("Firmware rejected
  country setting") y se queda en canales 1–11; el hotspot del móvil en el canal 13 era
  invisible. Si se cambia `pais`, la Pi reinicia sola una vez.
- **Cable**: `netplan-eth0` DHCP + `ipv4.link-local fallback` → funciona con router, con
  "conexión compartida" del PC (10.42.0.x) o sin DHCP (169.254.x.x).
- Acceso siempre por **`ssh antonio@mk4.local`** (la subred del hotspot cambia).

Instalación (ya hecha): `install -m755 mk4-red.py /usr/local/sbin/mk4-red`, el `.service` a
`/etc/systemd/system/` + `systemctl enable mk4-red`.

---

## 5. Flashear Klipper en la Robin Lite

**Antes**, billete de vuelta:
- Copia completa de la microSD de la placa (lleva `eeprom.dat` de Marlin).
- El `mksLite.bin` Marlin vigente está en `repos/EasyThreeD-K9_ET4000PLUS`, rama
  `custom-et4000plus`, `config/EasyThreeD/ET4000PLUS/mksLite.bin`. (Hay cambios sin commitear
  en `Configuration*.h` de esa rama: commitear o descartar antes.)
- **Medir Vref** de los drivers X, Z y E con Marlin funcionando y apuntarlo (referencia §6).

En la Pi:
```bash
cd ~/klipper && make clean && make menuconfig
```
| Opción | Valor |
|---|---|
| Micro-controller | STMicroelectronics STM32 |
| Processor model | STM32F103 |
| Bootloader offset | **20KiB bootloader** (Marlin enlaza en `0x08005000`) |
| Clock Reference | 8 MHz crystal |
| Communication interface | **Serial (on USART1 PA10/PA9)** (CH340) |
| Baud rate | 250000 |

> ⚠️ **El bootloader de esta placa rechaza Klipper moderno sin parche** (verificado 2026-10-16).
> Klipper ≥ v0.11 enlaza `ResetHandler`/IRQs por encima de `0x08008000` y el bootloader ignora
> el archivo (no lo renombra). Marlin y el Klipper v0.10 de MKS tienen todos los vectores por
> debajo y sí entran. Arreglo: [`firmware/mk4/robin-lite-vectors-first.patch`](../firmware/mk4/robin-lite-vectors-first.patch)
> (4 líneas en el linker: manejadores justo tras la tabla de vectores). Se aplica en un worktree
> para no ensuciar `~/klipper` (Moonraker lo marcaría *dirty*). Copia en la Pi: `~/mk4-fw/`.
> Ojo: el `.cfg` de MKS para esta placa dice "28KiB bootloader", pero su propio binario está en
> `0x08005000` (20KiB): la nota es errónea.

```bash
cd ~/klipper && git worktree add --detach /tmp/kmk4 HEAD && cd /tmp/kmk4
git apply ~/mk4-fw/robin-lite-vectors-first.patch
cp ~/mk4-fw/klipper.config .config && make olddefconfig && make   # o: cp ~/klipper/.config
./scripts/update_mks_robin.py out/klipper.bin out/mksLite.bin   # mismo cifrado XOR que Marlin
arm-none-eabi-nm -n out/klipper.elf | grep -E " (ResetHandler|SysTick_Handler)$"   # ambos < 0x08008000
```
Copiar `out/mksLite.bin` a la raíz de la microSD, insertar, encender. **Éxito = el archivo se
renombra o desaparece**; si sigue igual, el bootloader lo rechazó. Después: `cd ~/klipper &&
git worktree remove --force /tmp/kmk4`. La versión MCU sale `…-dirty-…-mk4` (normal por el parche).
Vuelta a Marlin = mismo proceso con el `mksLite.bin` de Marlin.

`ls /dev/serial/by-id/` → poner la ruta real en `[mcu] serial:` de `printer.cfg`.

---

## 6. Primer arranque: verificación en orden seguro

Mano en el interruptor de corriente. Hasta el paso 5, **nada de G28**.

1. Mainsail muestra `Ready` y las dos MCU (`mcu`, `rpi`) conectadas.
2. **Vref** con multímetro ≈ el medido con Marlin. Esperado: `V = 3.3 × A / 1.986` →
   XY 0.85 A ≈ 1.41 V · Z 1.2 A ≈ 1.99 V · E 0.8 A ≈ 1.33 V. Ajuste en caliente:
   `M907 Z1000` (mA, tope 1500; `M907` solo informa). Los pines `_vref_*` van ocultos en Mainsail a propósito (su slider en % llega a ~2 A).
3. **Endstops**: `QUERY_ENDSTOPS` suelto = `open`, pulsado = `TRIGGERED`. Si sale al revés,
   poner/quitar `!` en `endstop_pin`. **Sonda**: `QUERY_PROBE` igual (si al revés, `!` en `pin`).
4. **Direcciones** sin homing:
   ```
   SET_KINEMATIC_POSITION X=100 Y=100 Z=50
   G91
   G1 X10 F1200     ; debe ALEJARSE del endstop X
   G1 Y10 F1200     ; debe ALEJARSE del endstop Y
   G1 Z5 F300       ; la cama debe BAJAR (alejarse de la boquilla)
   G90
   ```
   Si alguno va al revés: `!` en su `dir_pin`.
5. `G28 X`, `G28 Y` (una vez cada uno, vigilando).
6. **Origen**: mover la boquilla a la esquina del plato y leer `GET_POSITION`. Ajustar
   `position_endstop`/`position_min` de X/Y (hoy -40/-60 heredados de M206) y `position_max`.
7. `G28` completo → aparece el diálogo de la sonda (ver §6.1). Después:
   `SONDA_LISTA` → `PROBE_ACCURACY` → `PROBE_CALIBRATE` → `SAVE_CONFIG`.
8. `PID_CALIBRATE HEATER=extruder TARGET=210` → `SAVE_CONFIG`.
9. Extrusor: marcar 120 mm, `M83`, `G1 E100 F60` a temperatura, corregir `rotation_distance`.
10. `BED_MESH_CALIBRATE` → `BED_MESH_PROFILE SAVE=default` → `SAVE_CONFIG`.
11. Acelerómetros e input shaping (§7). Después, pressure advance (`TUNING_TOWER`).

### 6.1 Flujo de la sonda manual (macros.cfg)
- `G28` homea X/Y y, si la sonda no está confirmada, **se detiene** con un diálogo
  "Baja el brazo… Listo". `Listo` = `SONDA_LISTA` → marca desplegada y continúa con `G28 Z`.
- Al acabar el sondeo pide subir el brazo → `Recogida` = `SONDA_GUARDADA`.
- `BED_MESH_CALIBRATE` igual. `PRINT_START` aborta si la sonda figura desplegada o falta homing.
- Los diálogos usan `action:prompt_*`: los muestran Mainsail, Fluidd y OctoPrint.

---

## 7. Acelerómetros: input shaping + diagnóstico de la cama

Como **X e Y mueven el cabezal**, el input shaper se calibra con el ADXL del cabezal en ambos
ejes. El de la cama no calibra nada por sí solo: **mide cuánto vibra el chasis** por la reacción
de las aceleraciones del cabezal.

1. Comprobar chips en reposo:
   `ACCELEROMETER_QUERY CHIP=cabezal` y `ACCELEROMETER_QUERY CHIP=cama` → ~(0, 0, 9800) mm/s²
   con el chip plano. Si la gravedad cae en otro eje, corregir `axes_map`.
   `MEASURE_AXES_NOISE` → ruido bajo (decenas de mm/s²).
2. **Input shaper** (cabezal): `SHAPER_CALIBRATE` → `SAVE_CONFIG`. Anotar el `max_accel`
   recomendado por eje.
3. **Diagnóstico cama vs cabezal** (los dos chips a la vez, mismo barrido):
   ```
   TEST_RESONANCES AXIS=X CHIPS="adxl345 cabezal,adxl345 cama" OUTPUT=raw_data NAME=x_cmp
   TEST_RESONANCES AXIS=Y CHIPS="adxl345 cabezal,adxl345 cama" OUTPUT=raw_data NAME=y_cmp
   ```
   Comparar espectros (una gráfica por eje):
   ```bash
   # archivos: /tmp/raw_data_x_adxl345_<chip>[_punto]_x_cmp.csv
   ~/klipper/scripts/graph_accelerometer.py /tmp/raw_data_x_adxl345_*x_cmp.csv -o /tmp/cmp_x.png
   ```
4. Lectura:
   - Pico en la cama a la **misma frecuencia** que el del cabezal → el shaper ya lo cubre.
   - Pico propio de la cama, de amplitud apreciable → flexión del chasis que el shaper no ve.
     Opciones: shaper más ancho (`ei`, `2hump_ei`) o frecuencia entre ambos picos; bajar
     `max_accel`; y sobre todo **rigidizar** (escuadras/diagonales, portacama, fijar el chasis
     a la mesa).
   - Repetir el paso 3 tras cada cambio mecánico: el chip de la cama da antes/después objetivo.
5. Subir `max_accel` en `[printer]` hasta el valor recomendado por `SHAPER_CALIBRATE`.

Idea pendiente: script que reste cama − cabezal (mismo reloj de la Pi) para calcular el shaper
sobre el **movimiento relativo** boquilla/cama, que es lo que marca la pieza.

> **Klipper v0.13.0-800 no tiene `AXES_MAP_CALIBRATION`.** Alternativa manual: `ACCELEROMETER_MEASURE`
> mientras se mueve `G1 X+30` (luego Y), y ver qué eje y signo dominan en el CSV de `/tmp`.

### 7.1 Resultado 2026-11-20 (cabezal, `[adxl345_i2c cabezal]`, solo cabezal)

- Reposo: `ACCELEROMETER_QUERY` → (−1147, 444, 8963) mm/s² (módulo ~7° inclinado en X, aceptable).
  Ruido `MEASURE_AXES_NOISE` 33/44/82 mm/s².
- `axes_map: -x, y, -z` verificado: +X → +x, +Y → +y. Z no se puede comprobar desde el cabezal
  (Z mueve la cama).
- `SHAPER_CALIBRATE` → `SAVE_CONFIG` (bloque al final de `printer.cfg`):

| Eje | Shaper | Frecuencia | Vibración residual | `max_accel` sugerido |
|---|---|---|---|---|
| X | `3hump_ei` | 116.4 Hz | 0.4 % | ≤ 8900 |
| Y | `2hump_ei` | 52.0 Hz | 1.3 % | ≤ 2900 |

  Gráficas: [X](img/mk4-shaper-x-20261120.png), [Y](img/mk4-shaper-y-20261120.png).
- Lectura: **Y es el eje débil**, con picos en ~25, 43 y 56 Hz (el de 25 Hz es bajo: revisar
  tensión de correas Y, holgura del carro y fijación de los 2 motores Y). X tiene picos en
  53/60/90 Hz. Tras cualquier ajuste mecánico, repetir `SHAPER_CALIBRATE`.
- `max_accel: 2500` en `[printer]` (antes 500), con margen bajo el límite de Y (≤ 2900).

### 7.2 Homing X/Y por choque con el ADXL (experimento, 2026-11-21)

Los finales de carrera físicos de X/Y están **retirados**: estaban ajustados para activarse justo
cuando el carro toca el marco, así que el choque se toma como `position_endstop` (−40 / −60).

- **Cómo funciona** (`extras/accel_endstop.py`): el ADXL compara cada muestra (3200 Hz, un eje,
  acoplado en AC) con un umbral y levanta **INT1 → pin 11 de la Pi (GPIO17)**, declarado como
  endstop en la MCU `rpi`. Klipper para los motores con un homing multi-MCU normal (trsync), no
  leyendo por I2C. La interrupción se arma 0.1 s después de arrancar (`blank_time`): el tirón del
  arranque disparaba 1 g.
- **Uso**: `G28` (macro) → `_XY_HOP` (separa 5 mm la cama) + `HOME_ACCEL AXIS=XY` + Z con la sonda
  como siempre. `G28.1 X/Y` **no** se debe usar: busca los interruptores que ya no están.
- **Calibración a 15 mm/s**:

| Eje | Umbral | Libre (sin choque) | Choque, 5 repeticiones |
|---|---|---|---|
| X | 3 g | 1.5 g salta, 2 g no | todas a +0.013 mm (1 paso) |
| Y | 1.5 g | 1.25 g no salta | todas a 0.000 mm; con 2–3 g detecta 0.8–5 mm tarde |

  Y choca "blando" (2 motores en un driver, 2 correas): **las dos correas Y deben ir igual de
  tensas**; si no, un lado toca antes, el carro se tuerce y el choque no llega al umbral (deriva
  acumulada de ~0.85 mm por choque).
- **Comandos de prueba**: `ACCEL_NOISE_TEST AXIS= START= END= [THRESHOLDS=]` (movimiento libre: el
  umbral mínimo "clean" es el usable) y `ACCEL_CRASH_TEST AXIS= [SAMPLES=] [THRESHOLD=]`
  (repetibilidad; ideal ±1 paso). Repetirlos tras tocar correas, velocidad o el montaje del ADXL.
- **Riesgo**: si el ADXL se desconecta o no detecta, el motor empuja contra el marco hasta el
  final del recorrido de homing (~1.2× el eje). Vigilar los primeros homings.

---

## 8. Impacto en el software existente

| Pieza | Con Klipper |
|---|---|
| OctoPrint del PC (`Programas/3d_print`) | Sustituido por Mainsail en la Pi. El USB de la placa pasa a la Pi. |
| `octoprint-k9suite` (BoostKit) | Depende de Marlin (P000/R000, M114/M410, M290, UBL). Equivalentes nativos: cancelar objeto = `[exclude_object]`; babystep = `SET_GCODE_OFFSET Z_ADJUST=… MOVE=1`; ajustes en caliente = `SET_VELOCITY_LIMIT`, `SET_PRESSURE_ADVANCE`, `M220/M221`; calibraciones = `PID_CALIBRATE`, `SHAPER_CALIBRATE`, `TUNING_TOWER`. |
| `octoprint-zpreload` | **Retirado**: la holgura de Z se resolvió mecánicamente (2026-10-15). |
| Perfil Cura | Start: `PRINT_START EXTRUDER={material_print_temperature_layer_0}` · End: `PRINT_END`. Para cancelar objetos con Cura: `enable_object_processing: True` en `[file_manager]` de `moonraker.conf`. |

OctoPrint en la Pi con Klipper es posible (puerto `/tmp/printer`), pero pierde casi todo lo que
aporta BoostKit; Mainsail cubre esas funciones de serie.

---

## 9. Riesgos y pendientes

- `klipper-config/mk4/` es de **partida**: todo lo marcado `VERIFICAR` se confirma en máquina (§6).
- `extras/dht_iio.py` **no probado en hardware** (solo contra sysfs simulado). Los fallos de
  lectura del DHT se cuentan (`read_errors`) y nunca paran la impresora. Alternativa sin código
  propio: sensor I2C nativo de Klipper (AHT20, SHT3x, BME280; pocos euros).
- La humedad se grafica como un "temperature_sensor" (`%HR` con unidad °C en la UI); el valor
  real también está en `printer["dht_iio ambiente"].humidity` para macros/API.
- Vref por PWM: valor mal puesto = motores calientes o pérdida de pasos → medir (§6, paso 2).
- Z 1.2 A en un A4988 con 2 motores en paralelo: vigilar temperatura del driver.
