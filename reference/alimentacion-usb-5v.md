# Alimentar el MCU de la K10 por un USB independiente (5V) con protección

Objetivo: un **puerto USB separado** (no el USB-C PD de fábrica) que alimente **solo la lógica
del MCU** (3.3V) y, opcionalmente, lleve los datos de Klipper (D+/D-). Los motores y el
calefactor siguen necesitando 12V aparte; el USB **no** los alimenta.

## Dónde inyectar los 5V: la entrada del AMS1117 (VIN)

La K10 genera su 3.3V con el regulador **AMS1117-3.3** (12V → buck → ~5V → AMS1117 → 3.3V → MCU).
Para alimentar la lógica con 5V del USB, se inyecta en la **entrada del AMS1117 (pin VIN)**, que es
el carril de ~5V. **No** se toca el USB-C PD ni los pads "DC PWR/TYPEC PWR" (esos son de 12V).

### Identificar el pin VIN del AMS1117 (encapsulado SOT-223, 3 patillas + lengüeta)
| Patilla | Función | Cómo reconocerla |
|---------|---------|------------------|
| Lengüeta grande + patilla central | **VOUT = 3.3V** | mide 3.3V a masa con la placa encendida → NO soldar aquí |
| Una patilla lateral | **GND** | mide 0V |
| La otra patilla lateral | **VIN** (entrada) | mide ~5V (o ~12V si va directo) → **AQUÍ van los 5V+** |

> Con la placa alimentada normalmente, mide cada patilla a masa con el multímetro: la de 3.3V es la
> salida (no tocar), la de 0V es GND, y la restante es **VIN** (ahí soldar el 5V+ del USB).
> El AMS1117 tolera de ~4.5V a ~15V en VIN, así que 5V es seguro.

## Protección recomendada (tu pregunta de seguridad) — SÍ, es posible y aconsejable

El **AMS1117 ya es el regulador** que protege la lógica (salida 3.3V estable). Lo que hay que
proteger es la **entrada de 5V**. Cadena recomendada, sencilla y robusta:

```text
  USB 5V+ ──[ Polyfuse 500mA ]──►|──────┬──────────────►  AMS1117 VIN  ──►(regula a 3.3V → MCU)
                              Schottky   │
                              (SS14 /    ├─[ TVS 5.6V ]─┐   (opcional, anti-picos/ESD)
                               1N5819)   ├─[ 10µF ]─────┤   (estabiliza la entrada)
                                         │              │
  USB GND ───────────────────────────────┴──────────────┴──────────────►  GND de la placa
```

- **Polyfuse (PTC) 500 mA**: rearmable; protege contra **cortocircuito / sobrecorriente**.
- **Diodo Schottky (SS14 o 1N5819)**, cátodo hacia la placa: **anti-polaridad inversa** y evita
  retroalimentar si algún día también conectas los 12V. Caída baja (~0.2–0.3V a esta corriente).
- **TVS 5.6V (SMAJ6.0A)** a masa: recorta **sobretensiones/picos** (opcional pero recomendable).
- **Condensador 10µF** a masa: estabiliza la entrada.

Caída total: 5V − polyfuse(~0.1) − Schottky(~0.25) ≈ **4.6V** en VIN → el AMS1117 da 3.3V de sobra
(la lógica consume <100 mA). Mantén el cable corto para no perder más tensión.

### Alternativa "todo en uno"
Un **módulo de protección USB** (sobretensión + sobrecorriente, los venden baratos) intercalado entre
el USB y el VIN del AMS1117 hace lo mismo sin montar componentes sueltos.

## Cableado completo del USB independiente (datos + alimentación)

Un solo conector USB hacia la Pi → alimenta la lógica **y** lleva Klipper por USB:

| Hilo USB | Va a | Notas |
|----------|------|-------|
| **5V+ (rojo)** | AMS1117 **VIN** (con la protección de arriba) | alimenta la lógica 3.3V |
| **D+ (verde)** | MCU **PA12** + **pull-up 1.5kΩ a 3.3V** | datos USB Klipper |
| **D- (blanco)** | MCU **PA11** | datos USB Klipper |
| **GND (negro)** | masa de la placa | común |

> PA11/PA12 están **contiguos a SWDIO (PA13)**, que el reversing identificó. Ver
> [diagrams/k10-soldadura-usb.md](../diagrams/k10-soldadura-usb.md) para localizarlos y la pull-up.
> Guía visual de puntos: [reference/images/k10-usb-solder-guide.jpg](images/k10-usb-solder-guide.jpg).

## Seguridad importante
- Esto alimenta **solo la lógica**. El **calefactor NO** debe activarse sin termistor validado y sin
  los 12V; Klipper lo protege, pero no fuerces calentamiento con USB.
- No intentes mover motores ni calentar desde los 5V del USB (no hay corriente para eso).
- GND **común** entre USB/Pi y placa. Señales a 3.3V hacia el MCU (D+/D-).
