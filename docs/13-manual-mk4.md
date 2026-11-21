# 13 — Manual de uso de la MK4 (Klipper)

> Manual de uso diario. El **porqué** de cada decisión y la instalación desde cero están en
> [12 — MK4: migración Marlin → Klipper](12-mk4-klipper.md). Config: `klipper-config/mk4/`.

---

## 0. La máquina en una tabla

| Parte | Qué es |
|---|---|
| Placa | MKS Robin Lite (de la K9) con **Klipper**, 4 drivers A4988 |
| Ordenador | Raspberry Pi 4 (`mk4`): Klipper + Moonraker + **Mainsail** (interfaz web) |
| Ejes | X e Y mueven el cabezal, Z mueve la cama. Y y Z llevan 2 motores en un solo driver |
| Volumen útil | X −40…310, Y −60…320, Z 0…110 (origen = esquina del plato 190×240) |
| Homing X/Y | **Sin finales de carrera**: detecta el choque del carro con el marco con el acelerómetro |
| Homing Z | Sonda de brazo **manual** (la bajas y subes tú cuando lo pide) |
| Acelerómetro | ADXL345 en el cabezal, por I2C a la Pi + cable INT1 |
| Cama | Sin calentar |

---

## 1. Red: conectar la impresora

### 1.1 Wi-Fi (desde la microSD de la Pi)

La Pi lee su configuración de red de un archivo de texto en la microSD **en cada arranque**.

1. Apaga la impresora y saca la microSD **de la Raspberry Pi** (no la de la placa).
2. Métela en cualquier PC. Abre la partición **`bootfs`** (FAT, se ve en Windows/Mac/Linux).
3. Edita **`mk4-red.txt`**:
   ```ini
   [mk4]
   pais = ES

   [Redmi A2+]
   clave = la-contraseña
   prioridad = 10

   [WiFi de casa]
   clave = otra-contraseña
   prioridad = 5
   ```
   - Una sección `[...]` por red, con el **nombre exacto** (mayúsculas y espacios incluidos).
   - `clave`: 8–63 caracteres; vacía = red abierta. `prioridad`: con varias redes a la vista
     gana la más alta.
   - Las redes que borres del archivo se olvidan en el siguiente arranque.
   - **`pais` = `ES`, no `CU`**: con `CU` el chip Wi-Fi de la Pi solo ve los canales 1–11 y un
     hotspot en el canal 12–13 no aparece. Si cambias `pais`, la Pi reinicia sola una vez.
4. Expulsa la tarjeta, vuélvela a poner en la Pi y enciende. En 1–2 min está en la red.

### 1.2 Cable Ethernet

Funciona sin configurar nada:
- **A un router**: coge IP por DHCP.
- **Directo al PC**: en el PC, conexión de cable en modo "compartida" (NetworkManager:
  *Compartida con otros equipos*). La Pi coge una `10.42.0.x`.
- **Sin DHCP** (cable directo sin compartir): la Pi se pone una `169.254.x.x` sola.

Si el enlace va a 10 Mbps o se cae cada pocos segundos, el cable está mal (ya pasó una vez).

### 1.3 Entrar

| Para | Dirección |
|---|---|
| Interfaz web (Mainsail) | **http://mk4.local** |
| Terminal | `ssh antonio@mk4.local` |

Usa siempre `mk4.local`: la IP cambia cuando el hotspot cambia de subred. Si `mk4.local` no
resuelve, mira la IP de `mk4` en la lista de dispositivos del router/hotspot.

---

## 2. Encender y comprobar

1. Enciende la impresora (Pi y placa a la vez). Espera ~1 min.
2. Abre Mainsail. Arriba debe poner **Ready**. En *Máquina → Sistema* aparecen dos MCU:
   `mcu` (la placa) y `rpi` (la Pi, para el acelerómetro).
3. Si sale un error en rojo, lee el mensaje: casi siempre dice qué falta (ver §9).

Tras un `FIRMWARE_RESTART`, un `SAVE_CONFIG` o reiniciar Klipper **se pierde el homing**:
vuelve a hacer `G28` antes de mover.

---

## 3. Homing (`G28`)

`G28` (botón **Home** de Mainsail) hace, por este orden:

1. **Baja la cama 5 mm** para separar boquilla y plato.
2. **X**: el cabezal se separa 5 mm, va a 15 mm/s hacia el marco de X mínimo y **se para al
   tocarlo** (lo detecta el acelerómetro). Retrocede 3 mm.
3. **Y**: igual hacia el marco de Y mínimo.
4. **Z**: aparece un diálogo **"Baja el brazo de la sonda y pulsa Listo"**.
   - Baja el brazo y pulsa **Listo** (o escribe `SONDA_LISTA`). La cama sube hasta que la sonda
     toca y se fija Z.
   - Al terminar sale **"Sondeo terminado. SUBE el brazo"**: súbelo y pulsa **Recogida**
     (`SONDA_GUARDADA`).

Otras formas:
- `G28 X`, `G28 Y`, `G28 X Y`: solo esos ejes (sin sonda).
- `G28 Z`: solo Z (exige X e Y ya homeados).

> ⚠️ **No uses `G28.1 X` / `G28.1 Y`**: es el homing antiguo de Klipper y busca los finales de
> carrera, que ya no están. El motor empujaría contra el marco.
>
> ⚠️ Los primeros homings tras tocar algo, **vigílalos**: si el acelerómetro no detecta el
> choque (cable INT1 suelto), el motor sigue empujando contra el marco hasta acabar el
> recorrido. Si oyes rascar más de 1 s, corta la corriente. Si el que falla es el I2C del
> acelerómetro, el homing da error **antes** de moverse hacia el marco.

---

## 4. Antes de imprimir: calibraciones

Se hacen una vez (y se repiten tras cambios mecánicos). `SAVE_CONFIG` las guarda al final de
`printer.cfg` y reinicia Klipper (hay que volver a hacer `G28`).

| Calibración | Comando | Estado |
|---|---|---|
| Altura de la sonda | `G28` → `SONDA_LISTA` → `PROBE_CALIBRATE` (prueba del papel con `TESTZ Z=-0.1`… → `ACCEPT`) → `SAVE_CONFIG` | **pendiente** (hoy `z_offset: 0.70` heredado de Marlin) |
| Precisión de la sonda | `PROBE_ACCURACY` (con el brazo bajado) | pendiente |
| PID del hotend | `PID_CALIBRATE HEATER=extruder TARGET=210` → `SAVE_CONFIG` | pendiente (PID de Marlin) |
| Extrusor | A 200 °C: marca 120 mm de filamento, `M83`, `G1 E100 F60`, mide lo que queda y corrige `rotation_distance` (`nuevo = actual × extruido_real / 100`) | pendiente |
| Malla de la cama | `BED_MESH_CALIBRATE` (pide la sonda) → `BED_MESH_PROFILE SAVE=default` → `SAVE_CONFIG` | pendiente |
| Input shaper | §6.3 | **hecho** 2026-11-20 |
| Homing por choque | §7 | **hecho** 2026-11-21 |

`BED_MESH_CALIBRATE` usa el mismo diálogo de la sonda que `G28`.

---

## 5. Imprimir

### 5.1 Slicer (Cura u otro)

G-code de inicio y fin de la máquina en el slicer:

```
; Start G-code
PRINT_START EXTRUDER={material_print_temperature_layer_0}

; End G-code
PRINT_END
```

- `PRINT_START` **no hace homing** (como con Marlin): haz `G28` antes de lanzar la impresión.
  Se niega a empezar si falta homing o si la sonda sigue bajada. Carga la malla `default` si
  existe, calienta, purga una línea en el borde y empieza.
- `PRINT_END` retrae, baja la cama 10 mm, aparca el cabezal en la esquina, apaga el hotend, el
  ventilador y los motores.

### 5.2 Lanzar

1. `G28` (con la sonda) — §3.
2. Mainsail → *Archivos G-code* → subir el `.gcode` → *Imprimir*.
3. Durante la impresión: *Pausa / Reanudar / Cancelar*, ajuste de velocidad y flujo, y
   **cancelar un objeto** concreto (`[exclude_object]`; el G-code debe traer las etiquetas de
   objeto: en Moonraker, `enable_object_processing: True` en `[file_manager]`).

### 5.3 Corriente de los motores (`M907`)

Igual que en Marlin, en mA (tope 1500):
```
M907              ; solo informa
M907 X850         ; X e Y comparten canal: X o Y cambian los dos
M907 Z1200 E800
```
Valores de referencia: XY 850, Z 1200, E 800. No se guarda: vuelve al valor de `printer.cfg`
al reiniciar.

---

## 6. Acelerómetro (ADXL345)

### 6.1 Cableado (a la cabecera de 40 pines de la Pi)

| Módulo | Pin de la Pi | Para qué |
|---|---|---|
| `VCC`/`Vin` (5 V) | **2** | alimentación (el módulo tiene su regulador a 3.3 V) |
| `GND` | **6** | masa |
| `SDA` | **3** | datos I2C |
| `SCL` | **5** | reloj I2C |
| `INT1` | **11** (GPIO17) | aviso de choque para el homing X/Y |
| `CS`, `SDO`, `VS`, `INT2`, `3V3` | — | sin conectar |

- Si cambias de módulo y lo alimentas por 5 V: **antes de conectar SDA/SCL**, mide SDA–GND en
  el módulo: debe dar ≈ 3.3 V. Si da 5 V, no lo conectes (dañaría la Pi).
- Fijación rígida al cabezal, sin cinta ni bridas, y que no toque metal.
- Orientación actual: montado **invertido en Z**, con X de la impresora = −X del chip →
  `axes_map: -x, y, -z` en `sensors.cfg`. Si lo montas de otra forma, hay que rehacer §6.2.

> Ojo con los módulos: de los dos primeros, uno era **defectuoso** (respondía pero los tres
> ejes daban siempre el mismo valor) y ninguno funcionaba por SPI. Por eso va por I2C con un
> extra propio (`extras/adxl345_i2c.py`), leído a ~790 muestras/s.

### 6.2 Comprobar que mide

En la consola de Mainsail:

1. **`ACCELEROMETER_QUERY`** con el cabezal quieto → algo como `(-1000, 500, 8900)` mm/s²: la
   gravedad (≈ 9800) cae casi entera en Z. Si los tres ejes dan siempre el mismo número, o
   todos ≈ ±150000, el sensor no mide (módulo roto).
2. **`MEASURE_AXES_NOISE`** → decenas de mm/s² por eje (hoy 33/44/82). Cientos = algo suelto.
3. **Orientación** (esta versión de Klipper no tiene `AXES_MAP_CALIBRATION`). Con X/Y homeados:
   ```
   ACCELEROMETER_MEASURE
   G91
   G1 X30 F6000
   G90
   ACCELEROMETER_MEASURE NAME=ejex
   ```
   El CSV queda en la Pi en `/tmp/adxl345_i2c-cabezal-ejex.csv`. Al arrancar el movimiento
   hacia +X, la aceleración debe subir en la columna **x** con signo **+**. Lo mismo con
   `G1 Y30` → columna **y** positiva. Si sale en otra columna o con otro signo, corrige
   `axes_map` en `sensors.cfg`.

### 6.3 Input shaper (calidad de impresión)

Compensa las vibraciones del cabezal y permite acelerar más sin "fantasmas" en la pieza.

1. `G28`, cama despejada, mano en el interruptor (el cabezal vibra fuerte ~2 min).
2. **`SHAPER_CALIBRATE`** → recomienda un filtro y frecuencia por eje.
3. **`SAVE_CONFIG`** (los guarda y reinicia).
4. Ajusta `max_accel` en `[printer]` de `printer.cfg` por debajo del máximo que sugiere para el
   eje más débil.

Resultado actual (2026-11-20):

| Eje | Filtro | Frecuencia | `max_accel` sugerido |
|---|---|---|---|
| X | `3hump_ei` | 116.4 Hz | ≤ 8900 |
| Y | `2hump_ei` | 52.0 Hz | ≤ 2900 |

→ `max_accel: 2500`. Gráficas en `docs/img/mk4-shaper-*-20261120.png`. Y es el eje débil
(picos a ~25, 43 y 56 Hz): tras tensar correas o reforzar el carro Y, repite la calibración.

Para generar las gráficas, en la Pi: `~/klipper/scripts/calibrate_shaper.py
/tmp/calibration_data_x_*.csv -o /tmp/shaper_x.png` (y lo mismo con `y`).

---

## 7. Homing X/Y por choque: funcionamiento y calibración

### 7.1 Cómo funciona

El ADXL vigila **un eje cada vez** a 3200 muestras/s. Cuando la aceleración supera un umbral
(el frenazo del carro contra el marco), pone **INT1** a 3.3 V. Ese pin entra en la Pi (GPIO17),
que Klipper usa como final de carrera, así que los motores se paran igual que con un
interruptor. La detección se activa **0.1 s después** de arrancar el movimiento, para que el
tirón del arranque no cuente como choque. El punto del choque es `position_endstop` del eje
(X −40, Y −60).

Parámetros en `[accel_endstop]` de `sensors.cfg`:

| Parámetro | Valor | Qué hace |
|---|---|---|
| `threshold_x` | 3.0 g | umbral del choque en X |
| `threshold_y` | 1.5 g | umbral en Y (el choque en Y es más blando) |
| `speed` | 15 mm/s | velocidad hacia el marco (los umbrales se calibraron a esta) |
| `blank_time` | 0.1 s | tiempo sin vigilar tras arrancar |
| `backoff` | 5 mm | se separa antes de buscar el marco |
| `retract` | 3 mm | retrocede tras el choque |
| `overtravel` | 6 mm | en las pruebas, cuánto puede pasarse antes de dar error |

### 7.2 Comandos

| Comando | Para qué |
|---|---|
| `HOME_ACCEL [AXIS=X\|Y\|XY]` | homing por choque (lo usa `G28`) |
| `ACCEL_NOISE_TEST AXIS=X START=75 END=195 [SPEED=15] [THRESHOLDS=1.5,2,3,4,6,8]` | recorre el eje **sin chocar** con cada umbral: el más bajo que sale `clean` es el mínimo usable |
| `ACCEL_CRASH_TEST AXIS=X [SAMPLES=5] [THRESHOLD=3]` | hace `HOME_ACCEL` y luego choca N veces desde 15 mm: mide la repetibilidad |

`ACCEL_NOISE_TEST` necesita el eje homeado. Si aún no lo está: `M84`, mueve a mano el cabezal
al centro del eje y `SET_KINEMATIC_POSITION X=135` (o `Y=130`). `START`/`END` deben quedar
lejos de los dos extremos.

### 7.3 Recalibrar los umbrales

Hazlo si cambias: correas, velocidad de homing, el montaje o el módulo del acelerómetro, o
si el homing falla.

1. **Ruido sin choque**: `ACCEL_NOISE_TEST` del eje. Apunta el umbral más bajo que salga
   `clean` (hoy X: 2 g; Y: 1.25 g o menos).
2. **Choque**: `ACCEL_CRASH_TEST AXIS=<eje> SAMPLES=5 THRESHOLD=<umbral>` empezando un poco
   por encima del mínimo del paso 1.
3. **Lectura del resultado**:
   - Las 5 en el mismo valor, cerca de 0 (±1 paso = 0.013 mm): **bien**.
   - Valores **negativos que crecen** en cada repetición (−0.8, −1.7, −3.4…): el choque no
     llega al umbral a tiempo y el motor pierde pasos. **Baja el umbral**, o revisa la mecánica
     (en Y: las dos correas deben ir igual de tensas).
   - Valores **positivos** o un `HOME_ACCEL` que se para a mitad de recorrido: falso disparo.
     **Sube el umbral**.
4. Pon el umbral bueno en `threshold_x`/`threshold_y` de `sensors.cfg` y `FIRMWARE_RESTART`.
5. Tras `ACCEL_CRASH_TEST`, haz `G28` antes de imprimir.

Resultados de referencia (2026-11-21, 15 mm/s):

| Eje | Sin choque | Choque (5 veces) |
|---|---|---|
| X | 1.5 g salta, 2 g no | 3 g: todas a +0.013 mm |
| Y | 1.25 g no salta | 1.5 g: todas a 0.000 mm · 2–3 g: detecta 0.8–5 mm tarde |

### 7.4 Si quitas el acelerómetro

- El **input shaper** sigue funcionando: sus valores están guardados en `printer.cfg`.
- El **homing X/Y deja de funcionar**. Para volver a interruptores: monta los finales de
  carrera (X en `PC13`, Y en `PC0`, ajustados para activarse al tocar el marco) y en
  `macros.cfg` cambia en la macro `G28` la línea `HOME_ACCEL AXIS=…` por
  `G28.1 {"X" if hx else ""} {"Y" if hy else ""}`.

---

## 8. Mantenimiento: qué repetir tras cada cambio

| Cambias… | Repite |
|---|---|
| Correas, poleas, carro | §7.3 (umbrales), §6.3 (shaper) |
| Montaje o módulo del ADXL | §6.2 (comprobar y orientación), §7.3, §6.3 |
| Boquilla, sonda, cama | `PROBE_CALIBRATE`, malla |
| Hotend, termistor | `PID_CALIBRATE` |
| Extrusor | `rotation_distance` del extrusor |
| Velocidad de homing (`speed`) | §7.3 (los umbrales dependen de ella) |

---

## 9. Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| La Pi no aparece en la Wi-Fi | Nombre/clave en `mk4-red.txt` (§1.1); `pais = ES`; hotspot en canal 12–13 con país mal. Prueba por cable (§1.2) |
| `mk4.local` no responde | mDNS falla a ratos con el hotspot: usa la IP del router o el cable directo |
| `mcu 'rpi': Unable to connect` | En la Pi: `sudo systemctl restart klipper-mcu` y luego `FIRMWARE_RESTART` |
| `ADXL345 I2C read failed` / `Remote I/O error` | Cable SDA/SCL/alimentación del acelerómetro suelto. Reasienta los Dupont |
| `ACCELEROMETER_QUERY` da siempre lo mismo o ±150000 | Módulo roto (§6.1) |
| `HOME_ACCEL` se para antes del marco | Falso disparo: sube `threshold_<eje>` (§7.3) |
| `No trigger on accel_endstop after full movement` o el motor rasca contra el marco | No detecta el choque: cable INT1 (pin 11) suelto, umbral demasiado alto o correas desiguales (§7.3) |
| `Sin homing: haz G28…` al imprimir | `PRINT_START` exige `G28` completo (X, Y y Z) |
| `Sonda desplegada…` al imprimir | Sube el brazo y `SONDA_GUARDADA` |
| Tras `SAVE_CONFIG` no se mueve | Se perdió el homing: `G28` |
| Error al cargar con `G28_ACCEL` u otro nombre | Klipper no admite nombres de comando con letra+número al principio (`G28_…`): por eso es `HOME_ACCEL` |

---

## 10. Dónde está cada cosa

| Archivo | Qué |
|---|---|
| `klipper-config/mk4/printer.cfg` | máquina: ejes, hotend, sonda, malla, `max_accel`, bloque `SAVE_CONFIG` |
| `klipper-config/mk4/macros.cfg` | `G28`, sonda manual, `PRINT_START`/`PRINT_END`, `M907` |
| `klipper-config/mk4/sensors.cfg` | MCU `rpi`, acelerómetro, `[resonance_tester]`, `[accel_endstop]` |
| `klipper-config/mk4/extras/` | `adxl345_i2c.py` (ADXL por I2C), `accel_endstop.py` (homing por choque), `adxl345_3w.py` (SPI 3 hilos, sin usar), `dht_iio.py` (sensor ambiente, sin cablear) |
| `klipper-config/mk4/host/` | servicio de red `mk4-red` y plantilla `mk4-red.txt` |
| En la Pi | `~/printer_data/config/` (copia de lo anterior; los `extras/` van enlazados en `~/klipper/klippy/extras/`) |

Los archivos del repo y los de la Pi deben ser iguales: si editas en Mainsail, copia el cambio
al repo.

Pendiente de hardware: acelerómetro de la cama (para diagnosticar la flexión del chasis,
`docs/12` §7) y sensor DHT de ambiente.
