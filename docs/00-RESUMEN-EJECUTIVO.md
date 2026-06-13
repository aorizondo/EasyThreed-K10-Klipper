# 00 — Resumen ejecutivo y matriz de decisión

Lee esto antes de comprar o soldar nada. Resume los hallazgos de la investigación técnica y
te plantea la decisión de arquitectura que condiciona todo lo demás.

---

## Hallazgos críticos (verificados)

### 1. La K10 es el chip correcto para Klipper, la K9 no
| Impresora | MCU | ¿Klipper? |
|-----------|-----|-----------|
| **K10** | **GigaDevice GD32F303RCT6** (Cortex-M4) | ✅ **Sí** — se compila como STM32F103 + bootloader 28 KiB |
| K9 | ARERY **AT32F403ARCT7** (clon STM32F1, Artery) | ❌ No soportado por Klipper |

Sacrificar la K10 y conservar la K9 (que dominas) es, por casualidad, la decisión correcta:
el GD32 de la K10 **sí** corre Klipper; el Artery de la K9 **no**.
Fuente: [reversing K10](https://palmarci.me/blog/2025-05-02-easythreed-k10-3d-printer-main-board-reversing/index.html),
[Hackaday K9](https://hackaday.com/2024/02/12/easythreed-k9-the-value-in-a-e72-aliexpress-fdm-3d-printer/),
[Klipper en GD32F303](https://klipper.discourse.group/t/installing-klipper-on-ender-3-4-2-2-board-and-gd32f303-chip/8165).

### 2. "Soldar el USB de la K10" NO funciona como esperas
- El **USB-C de la K10 es solo alimentación (USB-PD)**: va a un controlador PD (PW6606), **no al MCU**.
- **No hay chip USB-serie** (CH340/CP2102). De fábrica se opera **solo por microSD**.
- Klipper necesita un enlace serie/USB permanente con el host. Para tenerlo en la K10 hay que
  **soldar 3 cables (TX, RX, GND) directamente a pines USART del GD32** (paso 0.5 mm) y conectar
  un **adaptador USB-TTL** (FTDI/CP2102/CH340) hacia la Pi. Ver [04](04-soldadura-uart-k10.md).

### 3. El verdadero coste de la K10 es reversear el pinout
- **Nadie ha publicado** el pinout completo de la K10 ni un `printer.cfg`.
- Tendrás que mapear qué pin del GD32 va a: cada driver HR4988 (step/dir/enable), el MOSFET del
  calefactor, el ADC del termistor, el fan y los endstops. Metodología en [10](10-reversing-pinout-k10.md).
- Tienes a favor: **SWD accesible** (PA13/PA14/NRST), RDP desactivado en la unidad analizada, y un
  **volcado de firmware** público del que extraer pistas.

### 4. El Arduino Uno como MCU de Klipper: viable pero justo
- ATmega328P **sí** está soportado (16 MHz). Referencia directa: [klipper-drawbot](https://github.com/gwisp2/klipper-drawbot) (Uno + CNC Shield V3).
- Límites reales: **flash 32 KB** (hay que desactivar features en menuconfig), **~99k pasos/s con 3 ejes
  simultáneos**, riesgo de `Timer too close` si abusas de microstepping. Quédate en **1/16**.

### 5. Volumen real de la K10
La cama original de la K10 es **100×100×100 mm**. La ganancia de tu CNC (~280×280×70 mm en XY,
menos en Z) es enorme para X/Y, pero **el Z de ~70 mm es bajo** para una impresora — tenlo en cuenta
al diseñar la bancada/portaboquilla. Ver [03](03-hardware-cnc.md).

---

## Matriz de decisión de arquitectura

Tres caminos para gobernar el **cabezal** (hotend + extrusor + termistor + fan). El CNC siempre
hace X/Y/Z con el Arduino+Klipper; lo que cambia es **quién controla el cabezal**.

| | **A — Klipper en la K10** (fiel a tu idea) | **B — Placa dedicada** (pragmático) | **C — Todo en una placa** (máxima simplicidad) |
|---|---|---|---|
| Cabezal lo controla | Placa K10 con Klipper (toolhead MCU) | Una placa barata Klipper-friendly (SKR Mini / MKS / Ender 4.2.2 GD32) | Esa misma placa controla **también** X/Y/Z |
| Arduino+CNC Shield | XYZ | XYZ | Se jubila (o queda de repuesto) |
| Reutiliza la K10 | ✅ Sí (su gracia) | ❌ Solo el hotend/motor como piezas sueltas | ❌ |
| Soldadura SMD en MCU | ✅ Necesaria (UART al GD32) | ❌ No | ❌ No |
| Reversing de pinout | ✅ Necesario (lo más duro) | ❌ No | ❌ No |
| Multi-MCU | Sí (2 MCU) | Sí (2 MCU) | No (1 MCU) |
| Coste € | ~0 (reusas) + adaptador USB-TTL (~3€) | +15–25€ (placa) | +25–40€ (placa con drivers para 4–5 motores) |
| Dificultad | Alta (reversing + microsoldadura) | Media | Baja |
| Riesgo de bloqueo | Alto (pinout desconocido) | Bajo | Muy bajo |
| Valor "maker"/aprendizaje | Máximo | Medio | Bajo |

### Recomendación honesta
- Si esto es un **reto de ingeniería** y disfrutas reverseando: **Opción A**. Toda la documentación
  está preparada para ella (es el "camino principal" del repo). Empieza por validar comms y pinout
  ([10](10-reversing-pinout-k10.md)) **antes** de tocar mecánica.
- Si quieres **una impresora mejor funcionando pronto** con bajo riesgo: **Opción B**. Reutilizas el
  hotend y el motor de la K10 como piezas, y un board barato te da heater+termistor+extrusor sin reversing.
- **Opción C** solo si te cansas del Arduino: un único board decente (p.ej. SKR Mini E3) mueve 4–5
  steppers + hotend + cama y simplifica todo, a costa de no usar ni Arduino ni placa K10.

> Sugerencia de ruta: monta y **valida primero el CNC con Klipper (Opción B-mínima: solo XYZ moviéndose)**.
> Es la parte documentada y de bajo riesgo. Con el CNC ya homing y moviéndose, decides si atacas el
> reversing de la K10 (A) o enchufas un board (B/C) para el cabezal. Así nunca te quedas bloqueado.

---

## Orden de trabajo sugerido

1. **Host listo** → Raspberry Pi con KIAUH + Klipper + Moonraker + Mainsail ([07](07-instalacion-host-pi.md)).
2. **CNC con Klipper** → flashear ATmega328P, mapear CNC Shield, homing y movimiento XYZ ([03](03-hardware-cnc.md), [05](05-flasheo-klipper.md), [06](06-cableado-pines.md)).
3. **Calibrar XYZ** → `rotation_distance` por medición, sentido de giro, endstops ([08](08-calibracion.md)).
4. **Decidir cabezal** → Opción A (reversing K10, [10](10-reversing-pinout-k10.md) + [04](04-soldadura-uart-k10.md)) o B/C (board dedicado).
5. **Multi-MCU** → unir cabezal + XYZ en un `printer.cfg` con prefijos de pin ([01](01-arquitectura.md)).
6. **Sonda Z + bed mesh** → nivelado automático ([09](09-sensores-zprobe.md)).
7. **Afinado** → PID, extrusor, input shaping ([08](08-calibracion.md)).
