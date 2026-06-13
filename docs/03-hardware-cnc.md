# 03 — Hardware del CNC (Arduino Uno + CNC Shield V3)

El CNC casero aporta la **mecánica** (ejes X/Y/Z) y el **MCU principal** de Klipper.

- Mecánica: cartesiana, piezas recicladas de impresoras Epson de cinta (correas timing, gargantas,
  deslizantes y carros). Dimensiones aprox. **280 × 280 × 70 mm** (X × Y × Z).
- Motores: **NEMA17** (paso 1.8° = 200 pasos/vuelta salvo que sean de 0.9°).
- Electrónica: **Arduino Uno (ATmega328P)** + **CNC Shield V3** con drivers **A4988/DRV8825**.
- Actualmente con **GRBL** → se reflashea a **Klipper**.

Referencia directa de este montaje exacto (Uno + CNC Shield V3 con Klipper):
`repos/klipper-drawbot/` y <https://github.com/gwisp2/klipper-drawbot>.

---

## Pinout CNC Shield V3 → nombres Klipper (VERIFICADO)

Correspondencia Arduino → puerto ATmega: **D0–D7 = PD0–PD7**, **D8–D13 = PB0–PB5**, **A0–A5 = PC0–PC5**.
Verificado contra el fuente de GRBL (`cpu_map_atmega328p.h`) y contra klipper-drawbot.

| Función | Pin Arduino | **Klipper** | Notas |
|---|---|---|---|
| X STEP | D2 | **PD2** | |
| Y STEP | D3 | **PD3** | |
| Z STEP | D4 | **PD4** | |
| X DIR | D5 | **PD5** | |
| Y DIR | D6 | **PD6** | |
| Z DIR | D7 | **PD7** | |
| ENABLE (común) | D8 | **!PB0** | activo-bajo (de ahí el `!`); deshabilita los 4 drivers a la vez |
| X endstop | D9 | **PB1** | usar `^PB1` (pull-up) |
| Y endstop | D10 | **PB2** | usar `^PB2` |
| Z endstop | **D12** | **PB4** | ver nota ⚠️ |
| Spindle Enable/PWM | D11 | PB3 | PWM por hardware; en drawbot lo usan para servo |
| Spindle Dir | D13 | PB5 | LED integrado del Uno |
| Coolant | A3 | PC3 | |
| Abort / Hold / Resume | A0 / A1 / A2 | PC0 / PC1 / PC2 | botones GRBL, no se usan en Klipper |

### ⚠️ Endstop Z = PB4 (D12), no PB3
La CNC Shield V3, compilada con `VARIABLE_SPINDLE` (el **default** de GRBL 0.9/1.1), mueve el PWM de
spindle a **D11 (PB3)** y por eso el **Z-limit queda en D12 (PB4)**. klipper-drawbot usa exactamente
`endstop_pin: ^PB4` para Z. **Verifícalo con multímetro**: comprueba a qué pin del Arduino llega el
header "Z+/Z−" de tu shield. Si en tu placa concreta Z va a D11, sería PB3 (pero entonces pierdes el
pin de servo/PWM). Recomendado: **cablea Z a D12 = PB4**.

### El "4º eje" (A) de la shield
En la CNC Shield V3 el slot **A** se **clona** de X/Y/Z por jumpers (no tiene pines step/dir propios
libres). No lo uses como eje independiente sin cablear a mano sacrificando otra función. Para esta
frankenprinter **no hace falta**: el extrusor lo mueve la K10, no la shield.

---

## Drivers A4988 / DRV8825

### Microstepping (jumpers MS1/MS2/MS3 bajo cada driver)
| MS1 | MS2 | MS3 | A4988 | DRV8825 |
|---|---|---|---|---|
| L | L | L | full | full |
| H | L | L | 1/2 | 1/2 |
| L | H | L | 1/4 | 1/4 |
| H | H | L | 1/8 | 1/8 |
| H | H | H | 1/16 | 1/16 |
| L | L | H | 1/16 | 1/32 |

**Recomendado: 1/16** (los 3 jumpers puestos en A4988). No subas a 1/32 con el ATmega328P: el límite
de step rate del AVR puede provocar `Timer too close`. En Klipper pon `microsteps: 16`.

### Corriente (Vref) — mide con el driver montado y alimentado
- **A4988** (Rcs=0.1Ω): `Vref = I_motor × 0.8`. Ej.: 1.2 A → 0.96 V. Ajusta a ~70–85 % de la nominal del motor.
- **DRV8825** (Rcs=0.1Ω): `Vref = I_motor / 2`. Ej.: 1.2 A → 0.6 V.
- Ojo con el resistor de sensado del módulo: "R100"=0.1Ω, "R050"=0.05Ω, "R200"=0.2Ω → cambia la fórmula.
- Mide Vref entre el **tornillo del potenciómetro** y **GND**. Empieza bajo y sube si pierde pasos; si
  el motor o el driver queman, baja.

---

## rotation_distance (cómo lo calcula Klipper)

Klipper **no usa steps/mm**; usa `rotation_distance` = mm que avanza el eje por **una vuelta** del motor.

```
rotation_distance = full_steps_per_rotation × microsteps / (steps_per_mm)
pasos/mm = (200 × microsteps) / rotation_distance         # 200 para NEMA17 1.8°
```

- **Correa GT2 (paso 2 mm) + polea N dientes:** `rotation_distance = 2 × N`. (20T → 40; 16T → 32.)
- **Husillo:** `rotation_distance = paso efectivo` (M5 → 0.8; T8 1 entrada → 2; T8 4 entradas → 8).

### Mecánica Epson reciclada: calíbrala, no la adivines
Las correas/poleas de impresoras Epson **no son estándar** (paso y nº de dientes inciertos). Pon un
valor de partida (p.ej. 40) y **calíbralo midiendo** con el procedimiento de "mover y medir" de
[08 — Calibración](08-calibracion.md). Para Z con husillo recuperado, mismo método en vertical con calibre.

> Pista: muchas correas de carro de Epson son de paso 2 mm; las poleas suelen rondar 16–22 dientes.
> Arranca con `rotation_distance: 40` en X/Y y ajusta tras medir 100–200 mm reales.

---

## Conexión al host

- **Uno genuino** (chip USB ATmega16U2) → `/dev/ttyACM0`.
- **Clon** (CH340) → `/dev/ttyUSB0`.
- Usa siempre el ID estable: `ls /dev/serial/by-id/*` y pon esa ruta en `serial:` del `[mcu]`.
- Si tienes varios CH340 sin serie única, usa `ls /dev/serial/by-path/*` y enchufa siempre en el mismo puerto.

---

## Límites reales del ATmega328P como MCU Klipper

- **Flash 32 KB**: justa → desactiva features en `make menuconfig` para que quepa.
- **RAM 2 KB**: ok para 3 ejes + endstops; no para muchos extras.
- **Step rate**: ~157k pasos/s con 1 stepper, **~99k con 3 simultáneos** (~33k/eje). Con 80 pasos/mm
  son ~410 mm/s teóricos por eje — de sobra para tu mecánica. Si aparece `Timer too close`: baja
  microsteps, velocidad o aceleración.
- **Una sola UART**, sin CAN. ENABLE compartido (no puedes deshabilitar ejes por separado).

Fuentes: <https://www.klipper3d.org/Benchmarks.html> ·
`repos/klipper-drawbot/README.md` ·
GRBL cpu_map: <https://github.com/grbl/grbl/blob/master/grbl/cpu_map/cpu_map_atmega328p.h>
