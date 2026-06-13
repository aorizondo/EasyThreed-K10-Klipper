# 99 — BOM, riesgos y troubleshooting

## Lista de materiales (BOM)

### Ya tienes
- EasyThreed K10 (donante de cabezal: hotend, cartucho calefactor, termistor, motor extrusor, fan).
- CNC casero: Arduino Uno + CNC Shield V3 + drivers A4988/DRV8825 + 3× NEMA17 + mecánica Epson.
- EasyThreed K9 (se queda como está; no se toca).

### Necesario (común a todas las opciones)
| Item | Aprox. | Notas |
|---|---|---|
| Raspberry Pi (3B+/4/Zero 2W) + microSD ≥16 GB | 15–60 € | Host Klipper |
| Fuente 5 V / 3 A para la Pi | 8 € | Independiente de la potencia |
| Fuente de potencia motores (12–24 V, según drivers) | 10–20 € | Para la CNC Shield |
| Cableado, borneras, fundas | 10 € | |
| Sonda Z (microswitch / inductiva NPN / BLTouch) | 3–35 € | Ver [09](09-sensores-zprobe.md) |

### Específico Opción A (Klipper en la K10)
| Item | Aprox. | Notas |
|---|---|---|
| Adaptador USB-TTL **3.3 V** (FT232/CP2102/CH340) | 3–6 € | **Imprescindible 3.3 V** |
| ST-Link V2 (o Pi Pico como Picoprobe) | 3–8 € | Flasheo/volcado por SWD |
| Cable AWG30 + flux + estaño fino | 8 € | Microsoldadura UART |

### Específico Opción B/C (board dedicado para el cabezal)
| Item | Aprox. | Notas |
|---|---|---|
| Board Klipper-friendly (SKR Mini E3, MKS, o board GD32 Creality 4.2.2) | 15–40 € | Pinout conocido, sin reversing |

### Opcional (afinado)
- Acelerómetro **ADXL345** (~5 €) para input shaping medido.
- Sensor de fin de filamento (~3 €).

---

## Mapa de riesgos

| Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|
| Reversing K10 se atasca (pin no localizable) | Media | Bloquea Opción A | Tener lista la Opción B como plan B; volcar firmware y usar pins de Marlin |
| Dañar el GD32 al soldar UART (5 V, ESD, calor) | Media | Pierdes la K10 | Adaptador 3.3 V; pulso de soldadura corto; pulsera antiestática; verificar pad antes |
| Térmica descontrolada (heater sin termistor validado) | Baja | **Peligro de fuego** | Validar termistor ANTES de permitir calentar; `min_temp/max_temp`; `verify_heater`; no dejar sin supervisión |
| `Timer too close` en ATmega328P | Media | Aborta impresión | microsteps ≤ 1/16; bajar accel/velocidad; no recargar el AVR de extras |
| Volumen Z bajo (~70 mm) | Alta | Limita piezas altas | Diseñar bancada para maximizar Z; asumir piezas bajas; o rediseñar eje Z |
| Mecánica Epson con backlash/holgura | Media | Calidad dimensional | Calibrar midiendo en una dirección; tensar correas; considerar antibacklash en Z |
| CH340 sin serie única (multi-MCU confunde puertos) | Media | Conecta MCU equivocado | `by-path` + puerto USB fijo |
| Masas no comunes | Media | Comms erráticas / ADC ruidoso | Unir todos los GND |

### Seguridad eléctrica y térmica (no opcional)
- **GND común** entre Pi, Arduino, adaptador USB-TTL, K10 y fuentes.
- Señales al GD32 a **3.3 V** (UART/ADC). Adaptador USB-TTL en 3.3 V.
- Activa protecciones térmicas de Klipper: el `[extruder]` con `min_temp/max_temp` correctos, y considera
  `[verify_heater extruder]` (detecta calentamiento anómalo). Nunca dejes el primer calentamiento sin vigilar.
- Fusible/protección en la línea de potencia del calefactor y motores.

---

## Troubleshooting por síntoma

| Síntoma | Causa probable | Solución |
|---|---|---|
| `mcu 'k10': Unable to connect` | TX/RX cruzados, baud, sin GND común, no flasheado | Intercambiar TX/RX; mismo baud host/MCU; GND común; reflashear |
| `Unable to open serial port` | `serial:` incorrecto | `ls /dev/serial/by-id/*` y corregir; `by-path` si CH340 |
| `mcu 'mcu': Command request` / versiones distintas | host y MCU compilados de commits distintos | recompilar y reflashear ambos al mismo commit |
| Motor no se mueve | enable/step/Vref | `STEPPER_BUZZ`; subir Vref; revisar `enable_pin !` |
| Motor al revés | dir | añadir/quitar `!` en `dir_pin` |
| Eje mueve mal la distancia | rotation_distance | calibrar mover-y-medir ([08](08-calibracion.md)) |
| Un eje correcto, otro invertido | cableado bobinas | intercambiar conexión de drivers/motor o `!` dir |
| `ADC out of range` al arrancar | termistor mal/`sensor_pin` o `sensor_type` | validar termistor frío ≈ ambiente; probar otro `sensor_type` |
| Hotend no calienta / PID falla | heater_pin mal, fuente, cartucho | verificar `heater_pin` (doc 10); medir cartucho; PID_CALIBRATE |
| `Timer too close` / `Rescheduled timer in the past` | AVR saturado | bajar microsteps/accel/velocidad |
| `Move out of range` | position_min/max o homing | revisar límites y `position_endstop` |
| Homing Z impreciso (multi-MCU) | endstop/sonda en MCU distinto a steppers Z | poner sonda/endstop Z en el MCU del CNC |
| Web no carga | moonraker/nginx | `systemctl status moonraker`; revisar `cors`/`trusted_clients` en moonraker.conf |

### Logs útiles
```bash
journalctl -u klipper -f
journalctl -u moonraker -f
tail -f ~/printer_data/logs/klippy.log
```

---

## Hitos de validación (haz uno a uno, no todo de golpe)
1. [ ] Host: Mainsail abre y conecta con Moonraker.
2. [ ] CNC flasheado; `[mcu]` conecta; `STEPPER_BUZZ` mueve X/Y/Z.
3. [ ] Homing XYZ con endstops correcto.
4. [ ] rotation_distance XYZ calibrado (100 mm = 100 mm).
5. [ ] (A) K10 con UART + Klipper; `[mcu k10]` conecta.
6. [ ] Termistor K10 lee ambiente coherente.
7. [ ] heater_pin verificado; PID hotend.
8. [ ] Extrusor calibrado (measure & trim).
9. [ ] Sonda Z + z_offset + bed mesh.
10. [ ] Primera línea / primera capa de prueba.
11. [ ] Input shaping.
