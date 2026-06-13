# Diagrama — Arquitectura del sistema

## Vista lógica (multi-MCU Klipper)

```mermaid
graph TD
    subgraph PI["Raspberry Pi (host)"]
        K["Klipper host<br/>(cinemática, look-ahead,<br/>input shaping, PID)"]
        M["Moonraker (API)"]
        W["Mainsail / Fluidd (web)"]
        W --> M --> K
    end

    K -- "USB serie<br/>/dev/serial/by-id" --> CNC
    K -- "USB serie<br/>(adaptador USB-TTL)" --> K10

    subgraph CNC["MCU 'cnc' — Arduino Uno (ATmega328P) + CNC Shield V3"]
        X["Stepper X + endstop"]
        Y["Stepper Y + endstop"]
        Z["Stepper Z + endstop"]
        P["Sonda Z (opcional)"]
    end

    subgraph K10["MCU 'k10' — placa GD32F303 (cabezal)"]
        E["Extrusor (stepper)"]
        H["Calefactor (MOSFET HY1403)"]
        T["Termistor (ADC)"]
        F["Fan"]
    end

    CNC --> MEC["Mecánica Epson reciclada<br/>NEMA17 · ~280x280x70 mm"]
    K10 --> HEAD["Cabezal K10<br/>hotend + extrusor"]
```

## Vista física (alimentación y masas)

```
   Fuente potencia (12-24V) ──► CNC Shield ──► motores NEMA17 (XYZ)
   Fuente K10 (~12V)        ──► placa K10  ──► calefactor + extrusor
   Fuente 5V/3A             ──► Raspberry Pi
                                  │
                                  ├─USB─► Arduino Uno (lógica + CNC Shield)
                                  └─USB─► adaptador USB-TTL ─UART(3.3V)─► GD32 K10

   ⚠️ TODAS las masas (GND) unidas. Señales al GD32 a 3.3V. Ver docs/06.
```

> Opción B/C: el bloque "MCU k10" se sustituye por un board de impresora con pinout conocido
> (sin reversing ni soldadura SMD). La topología lógica es idéntica.
