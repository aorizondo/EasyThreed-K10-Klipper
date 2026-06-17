# CNC con GRBL 1.1h (Arduino Uno + CNC Shield V3)

Modo **GRBL** del CNC (alternativa a la vía Klipper, ver [`klipper.config`](klipper.config) +
[`../../docs/11-estado-y-handoff.md`](../../docs/11-estado-y-handoff.md)).

## Compilación y flasheo

```bash
# fuente: gnea/grbl 1.1h en repos/grbl (gitignorado)
cd repos/grbl && make                       # -> grbl.hex (~30.7 KB, cabe justo en 32 KB)
avrdude -c arduino -p atmega328p -P /dev/ttyACM0 -b 115200 -D -U flash:w:grbl.hex:i
```

Banner al conectar (115200 baud): `Grbl 1.1h ['$' for help]`.

## Settings aplicados

| Setting | Valor | Motivo |
|---------|-------|--------|
| `$130 / $131 / $132` | 380 / 360 / 50 | Área de trabajo 38×36×5 cm |
| `$20` (soft limits) | 0 | Sin endstops (requiere homing) |
| `$21` (hard limits) | 0 | Sin endstops |
| `$22` (homing) | 0 | Sin endstops → cero manual antes de cada trabajo |

**Doble motor Y**: por **hardware** (jumpers de clonado del eje A puestos en Y). GRBL ve 3 ejes;
el 2º motor Y lo mueve el socket A clonando la señal de Y.

## steps/mm — PENDIENTE de calibrar

Datos conocidos:
- Motores X/Y = **Epson LX-300, motor de carro = 48 pasos/vuelta (7.5°)** (manual de servicio).
- Microstepping **1/16** → 48×16 = 768 µpasos/vuelta.
- Falta: **paso de correa × dientes de polea** (X/Y) y **paso del tornillo Z** (≈M8: si es varilla
  roscada estándar, lead 1.25 mm → `$102 = 768 / 1.25 ≈ 614`; si fuese husillo T8 lead 8 mm → ≈96).

Fórmulas:
- Correa: `steps/mm = (48 × 16) / (paso_correa_mm × dientes_polea)`
- Tornillo: `steps/mm = (48 × 16) / lead_mm`  (si el motor Z también es de 48 pasos)

### Calibración empírica (lo fiable con motores reciclados)

1. Margen de sobra para no chocar. Vref de los drivers ajustado (no quemar motores).
2. `G91` (relativo), comanda 50 mm: `$J=G21G91X50F400`
3. Mide el desplazamiento **real** con regla/calibre.
4. `nuevo_$100 = $100_actual × (50 / real_mm)`. Aplica `$100=<valor>`. Repite hasta exacto.
5. Igual para Y (`$101`) y Z (`$102`). Ajusta dirección con `$3` si algún eje va invertido.

> Si conservas los `$100/$101/$102` de cuando el CNC ya funcionaba con GRBL, aplícalos directamente
> (se perdieron al reflashear: la EEPROM volvió a defaults $100=$101=$102=250).
