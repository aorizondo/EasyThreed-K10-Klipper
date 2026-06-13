# 10 — Metodología para reversear el pinout de la K10

Esta es **la parte más dura** de la Opción A: nadie ha publicado qué pin del GD32F303 va a cada
actuador de la K10. Necesitas mapear, como mínimo:

- Driver del **extrusor** (HR4988): STEP, DIR, ENABLE.
- **Calefactor**: gate del MOSFET HY1403 (`heater_pin`).
- **Termistor**: pin ADC del divisor (`sensor_pin`).
- **Fan**: salida controlable.
- (Opcional) endstops, si quisieras usarlos — pero en esta arquitectura los pones en el CNC.

Tienes tres vías complementarias; combínalas. La 1 es la más rápida si funciona.

---

## Vía 1 — Extraer el pinout del firmware (la más eficiente)

El firmware de fábrica es **Marlin**, y Marlin define todos los pines en un archivo de placa. Si
identificas la placa Marlin que usa la K10, tienes el mapa casi regalado.

1. **Vuelca el firmware** de la K10 por SWD (RDP estaba desactivado en la unidad analizada):
   ```bash
   openocd -f interface/stlink.cfg -f target/stm32f1x.cfg \
           -c "init; reset halt; dump_image k10_original.bin 0x08000000 0x40000; exit"
   ```
   Guárdalo en `reference/`.
2. La K10 compila Marlin con un board tipo `BOARD_CREALITY_V422_GD32_MFL` (Creality 4.2.2 GD32). Abre
   el pins file correspondiente de Marlin y compáralo:
   - En `repos/ECF-Marlin/` (Marlin comunitario EasyThreed) busca los `pins_*.h` de la familia.
   - En Marlin mainline: `Marlin/src/pins/stm32f1/pins_CREALITY_V422.h` y la variante GD32.
   - Busca `HEATER_0_PIN`, `TEMP_0_PIN`, `E0_STEP_PIN`, `E0_DIR_PIN`, `E0_ENABLE_PIN`, `FAN_PIN`,
     `X/Y/Z` endstops.
3. **Traduce** los nombres Marlin (p.ej. `PB0`, `PA1`) directamente a Klipper (mismos nombres `Pxx`).
   ⚠️ Aviso: que la K10 sea "tipo 4.2.2" no garantiza pinout idéntico — EasyThreed pudo recablear. Por
   eso **verifica** cada pin con la Vía 2 antes de aplicar potencia.

> El blog de reversing (palmarci) ya confirma componentes (HR4988, MOSFET HY1403, AMS1117) y los pines
> SWD; úsalo como ancla. URL en [02](02-hardware-k10.md).

---

## Vía 2 — Trazado físico con multímetro (verificación, placa SIN alimentar)

Continuidad (modo pitido) entre el **pin del chip** y el **componente**:

- **Calefactor (`heater_pin`)**: localiza el MOSFET HY1403. Su **gate** va, normalmente vía una resistencia
  de puerta, a un pin del GD32. Pita entre cada pin candidato del MCU y el gate → ese es el `heater_pin`.
  (Source a GND, drain al conector del calefactor.)
- **Termistor (`sensor_pin`)**: localiza el conector del termistor y el divisor (una resistencia ~4.7k a
  3.3 V + el termistor a GND, nodo medio al ADC). Pita del nodo medio a un pin ADC del GD32 (PA0–PA7,
  PB0/PB1, PC0–PC5). Ese es el `sensor_pin`.
- **Driver extrusor (STEP/DIR/EN)**: identifica el HR4988 del extrusor. Sus pines STEP, DIR, ENABLE
  (según datasheet A4988/HR4988) se trazan a pines del GD32. Pita cada uno.
- **Fan**: igual que el calefactor pero el MOSFET/transistor del ventilador.

Anota todo en una tabla (plantilla abajo). Usa la datasheet del HR4988/A4988 para saber qué patilla es
STEP/DIR/EN, y un mapa de pines del GD32F303 LQFP para numerar patillas.

---

## Vía 3 — Sondeo dinámico con Klipper (confirmación final, con cuidado)

Una vez tengas Klipper en la K10 y candidatos del firmware/multímetro, **confirma** sin mecánica ni
calefactor conectados de potencia:

- **Salidas digitales** (heater gate, fan, enable): define un `[output_pin test]` temporal y conmútalo
  con `SET_PIN PIN=test VALUE=1/0` mientras mides con multímetro/LED en la salida. Verás qué conector se activa.
- **ADC (termistor)**: define el `[extruder] sensor_pin` candidato con `sensor_type: Generic 3950` y mira
  si lee temperatura ambiente coherente. Si lee 0 o max → pin equivocado.
- **STEP/DIR del extrusor**: con `STEPPER_BUZZ` (con el motor conectado pero el hotend frío y
  `min_extrude_temp` puenteado solo para test de movimiento — o usa un `[manual_stepper]` temporal).

> ⚠️ Haz el sondeo dinámico **con el calefactor de potencia desconectado** hasta confirmar el `heater_pin`.
> Activar por error un pin que no es el del MOSFET no rompe nada, pero activar el calefactor sin termistor
> validado sí es peligroso (térmica descontrolada). Klipper protege con `min_temp/max_temp`, pero valida
> el termistor **antes** de permitir calentar.

---

## Plantilla de mapa de pines (rellénala)

Copia esto a `reference/k10-pinmap.md` y ve completándolo:

```
# Mapa de pines K10 (GD32F303) — VERIFICAR cada uno antes de aplicar potencia

UART (comms host):   TX=PA9   RX=PA10   GND=___   [Vía: soldado, ver doc 04]

Extrusor STEP:   P____   [firmware:___ | multímetro:OK/NO | klipper:OK/NO]
Extrusor DIR:    P____   [ ... ]
Extrusor ENABLE: P____   (activo-bajo? sí/no -> usar ! )
Calefactor (heater_pin): P____   [gate MOSFET HY1403]
Termistor (sensor_pin):  P____   [ADC; sensor_type probado: __________]
Fan capa:        P____
Fan hotend:      P____   (si existe; o conéctalo a heater_fan)

Endstops (si se usaran en K10, NO recomendado): X=__ Y=__ Z=__
SWD: SWDIO=PA13  SWCLK=PA14  NRST=PB4   (confirmados por reversing publicado)
```

---

## Resultado

Cuando el mapa esté completo y verificado, traslada los valores a
`klipper-config/include/extruder-hotend.cfg` (reemplaza los `P??`). A partir de ahí, calibra térmica y
extrusor según [08](08-calibracion.md).

Si en algún punto te bloqueas (pin que no aparece, chip difícil de trazar), recuerda la salida de bajo
riesgo: **Opción B** ([00](00-RESUMEN-EJECUTIVO.md)) — un board barato con pinout conocido para el cabezal,
reutilizando el hotend/motor de la K10 como piezas.

Fuentes: reversing K10 <https://palmarci.me/blog/2025-05-02-easythreed-k10-3d-printer-main-board-reversing/index.html> ·
Marlin pins <https://github.com/MarlinFirmware/Marlin/tree/2.1.x/Marlin/src/pins/stm32f1> ·
`repos/ECF-Marlin/`
