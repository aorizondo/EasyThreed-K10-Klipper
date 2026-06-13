# 06 — Cableado y pinout (referencia rápida)

Tabla maestra de conexiones. Los pines de la K10 marcados con `??` se rellenan tras el
[reversing](10-reversing-pinout-k10.md).

---

## MCU "cnc" — Arduino Uno + CNC Shield V3

| Señal | Pin Klipper | Conector físico shield |
|---|---|---|
| X step / dir | PD2 / PD5 | slot driver X |
| Y step / dir | PD3 / PD6 | slot driver Y |
| Z step / dir | PD4 / PD7 | slot driver Z |
| Enable (común) | !PB0 | EN (los 4 drivers) |
| X endstop | ^PB1 | header X+/X− (D9) |
| Y endstop | ^PB2 | header Y+/Y− (D10) |
| Z endstop | ^PB4 | header Z+/Z− (D12) |
| Sonda Z (si va aquí) | ^PB3 o un endstop libre | ver [09](09-sensores-zprobe.md) |

Motores NEMA17 a los slots X/Y/Z. **Bobinas**: si un motor va al revés o "vibra sin girar", el orden de
los 4 cables del conector está mal; reordena los pares de bobina (típico: A1,A2,B1,B2). Mejor que invertir
en software es cablear bien; el sentido se ajusta con `!` en `dir_pin`.

### Alimentación CNC Shield
- La shield se alimenta por su borna (típico 12–36 V según drivers/motores). **No** la alimentes solo por
  el USB del Arduino: el USB da lógica, los motores necesitan la borna de potencia.
- GND de la fuente de motores común con el Arduino (la shield ya une masas).

---

## MCU "k10" — placa GD32F303 (Opción A)

| Señal | Pin Klipper | Origen |
|---|---|---|
| Extrusor step / dir / enable | k10:P?? / k10:P?? / !k10:P?? | driver HR4988 del extrusor (reversing) |
| Calefactor hotend | k10:P?? | gate del MOSFET HY1403 (reversing) |
| Termistor (ADC) | k10:P?? | divisor del termistor (reversing) |
| Fan de capa | k10:P?? | salida fan (reversing) |
| UART al host | — | PA9(TX)/PA10(RX)/GND → adaptador USB-TTL (ver [04](04-soldadura-uart-k10.md)) |

Cabezal de la K10 = hotend + cartucho calefactor + termistor + motor extrusor + fan. Se quedan **en su
placa**; solo añades el UART y, si hace falta, recableas el fan a un pin controlable.

---

## Masas y alimentación (visión global)

```
   Fuente CNC (12-36V) ──► CNC Shield (motores XYZ) ──┐
                                                       ├── GND COMÚN ──┐
   Fuente K10 (≈12V)  ──► Placa K10 (hotend/extrusor) ┘               │
                                                                       │
   Raspberry Pi (5V propia) ──USB──► Arduino (lógica) ────────────────┤
                            └─USB──► adaptador USB-TTL ── K10 UART ────┘
```
- **Todas las masas (GND) deben estar unidas** entre sí (Pi, Arduino, adaptador, K10, fuentes). Sin masa
  común, la comunicación serie falla y los ADC dan lecturas erráticas.
- **Alimentaciones de potencia separadas** de la lógica (la Pi por su fuente de 5 V/3 A; los motores y el
  calefactor por sus fuentes).
- No mezcles 5 V y 3.3 V en líneas de señal hacia el GD32 (UART/ADC) — ver [04](04-soldadura-uart-k10.md).

---

## Endstops y sonda Z (dónde conectarlos)

- X/Y/Z endstops → en el **MCU cnc** (mismo MCU que los steppers de esos ejes → homing fiable).
- Sonda Z → preferiblemente también en el **MCU cnc**. Si necesitas un pin y no queda libre, considera
  usar `probe:z_virtual_endstop` con un sensor en un endstop de la shield. Detalle en [09](09-sensores-zprobe.md).

> Regla multi-MCU: el endstop de un eje debe estar en el **mismo MCU** que los steppers de ese eje para
> evitar sobrepaso por latencia. Por eso el cabezal (k10) **no** lleva endstops.
