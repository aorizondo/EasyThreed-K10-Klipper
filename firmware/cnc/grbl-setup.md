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

## Motores (los 4, rotulados igual)

**STP-42D221-01 (= EM-284)**: NEMA17, **1.8°/paso = 200 pasos/vuelta**, unipolar 5 cables
(conectados como bipolar a los A4988), ~1 A/fase. Microstepping **1/16** → 200×16 = **3200 µpasos/vuelta**.

## steps/mm

| Eje | `$` | Valor | Estado | Notas |
|-----|-----|-------|--------|-------|
| X | `$100` | 78.4 | **arranque (sin calibrar)** | mismo motor/polea que Y; **recablear a bipolar antes** |
| Y | `$101` | **75.45** | **CALIBRADO** (<0.5%) | calibrado a baja velocidad; motor recableado a bipolar |
| Z | `$102` | **2225.8** | **CALIBRADO** (±2 %) | dir invertida `$3=4` |

> ⚠️ **Pérdida de pasos por velocidad**: con las bobinas en serie (alta inductancia), Y pierde pasos
> a F600 (1.2% corto) pero va fino a F100 (0.2%). **Hay que limitar `$111`/`$121` por debajo del
> umbral de pérdida** para mantener <0.5% en trabajo real (barrido de velocidad pendiente de cerrar).
> Alternativa si se queda muy lento: recablear bobinas en **paralelo** (menos inductancia, más corriente).

### Eje Z — calibrado (2026-06-16)

- **Dirección invertida**: `Z+` bajaba → `$3=4`.
- Avance real del tornillo ≈ **1.44 mm/vuelta** (NO 2 mm; la medida a ojo del paso engañaba). Calibrado
  empíricamente: 10 mm comandados → 9.8 mm reales → `$102 = 2225.8`.
- **Backlash ≈ 0.55 mm** al invertir sentido (GRBL 1.1 no lo compensa → tuerca anti-holgura o asumirlo).
- El Vref bajo causaba pérdida de pasos al subir; con **0.71 V** en el trimpot Z dejó de perderlos.

### Calibración empírica X/Y (pendiente)

1. Vref ajustado, margen para no chocar. `$J=G91 G21 X10 F400` (jog 10 mm).
2. Mide el desplazamiento **real**. `nuevo_$100 = $100_actual × (10 / real_mm)`. Repite.
3. Para precisión usa un recorrido largo (menos error relativo). Igual con Y (`$101`).
4. Si un eje va invertido, añádelo a la máscara `$3` (bit0=X, bit1=Y, bit2=Z).
