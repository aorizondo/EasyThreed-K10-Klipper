# EasyThreeD K9 — Cura Printer Profile

Perfil de impresora completo para la EasyThreeD K9 (ET4000PLUS) con firmware
Marlin 2.1.x personalizado.

## Instalación

### Método 1: Copiar archivos manualmente (recomendado)

```bash
# Ajusta la versión de Cura si usas otra (5.9, 5.10, 5.11, 5.13, etc.)
CURA_DIR=~/.local/share/cura/5.13

# Copiar definiciones
cp definitions/easy3d_k9.def.json "$CURA_DIR/definitions/"
cp extruders/easy3d_k9_extruder_0.def.json "$CURA_DIR/extruders/"

# Copiar calidades
mkdir -p "$CURA_DIR/quality/easy3d_k9"
cp quality/easy3d_k9/*.inst.cfg "$CURA_DIR/quality/easy3d_k9/"

# Copiar materiales
cp materials/*.inst.cfg "$CURA_DIR/materials/"

# Copiar variantes (boquillas)
cp variants/*.inst.cfg "$CURA_DIR/variants/"

# Reiniciar Cura
```

> Nota: La ruta `~/.local/share/cura/` es para AppImage/Flatpak. Si usas Cura
> instalado por sistema, la ruta puede ser `/usr/share/cura/resources/`.

### Método 2: Desde el .3mf

1. Abre `EasyThreeD-K9.3mf` en Cura
2. Ve a `Settings → Printer → Manage Printers`
3. Haz clic en `Import` y selecciona el archivo
4. Alternativamente: Cura debería detectar la definición al abrir el .3mf

## Estructura

```
cura-k9-profile/
├── definitions/
│   └── easy3d_k9.def.json         # Máquina: 100×100×100, 24BYJ28
├── extruders/
│   └── easy3d_k9_extruder_0.def.json  # Extrusor Bowden corto
├── quality/easy3d_k9/
│   ├── easy3d_k9_fine.inst.cfg     # 0.12mm — detalle máximo
│   ├── easy3d_k9_normal.inst.cfg   # 0.20mm — balance calidad/velocidad
│   └── easy3d_k9_fast.inst.cfg     # 0.28mm — rápido funcional
├── materials/
│   ├── easy3d_k9_pla.inst.cfg      # PLA 200°C
│   ├── easy3d_k9_petg.inst.cfg     # PETG 235°C
│   ├── easy3d_k9_tpu.inst.cfg      # TPU 225°C
│   ├── easy3d_k9_abs.inst.cfg      # ABS 245°C (draft shield)
│   ├── easy3d_k9_pa.inst.cfg       # PA Nylon 245°C
│   └── easy3d_k9_fishing_line_2mm.inst.cfg  # Cordel pesca 2mm 240°C
├── variants/
│   └── easy3d_k9_0.4.inst.cfg      # Boquilla 0.4mm
└── EasyThreeD-K9.3mf               # Proyecto empaquetado
```

## Parámetros clave

| Parámetro              | Valor              |
|------------------------|--------------------|
| Volumen                | 100 × 100 × 100 mm |
| Velocidad máxima       | 20 mm/s            |
| Aceleración max (XY)   | 150 mm/s²          |
| Aceleración extrusor   | 250 mm/s²          |
| Jerk XY               | 5-8 mm/s           |
| Retracción (firmware)  | 10 mm a 20 mm/s    |
| Coasting              | 0.08 mm³           |
| 1ª capa flujo         | 115%               |
| 1ª capa velocidad     | 4-8 mm/s           |
| Adhesión por defecto   | Raft (2-3 capas)   |
| Extrusión relativa    | Activada            |
| Combing               | Activado            |
| Linear Advance        | 0.16 (en firmware)  |
| Junction Deviation    | 0.03-0.04 (firmware)|

## Start/End G-code

El **Start G-code NO hace homing** (se asume hecho manualmente desde OctoPrint).
Solo purga la boquilla con línea doble en el borde izquierdo:

```
M83 ; Relative extrusion mode
G92 E0
G1 Z5 F300
G1 X2 Y2 F600
G1 Z0.25 F300
G1 X2 Y98 E6 F90   ; Purge line up
G1 X7 Y98 F300
G1 X7 Y2 E4 F90     ; Return purge line
G92 E0
G1 Z5 F300
```

El **End G-code** apaga hotend, estaciona en esquina trasera izquierda:

```
G91
G1 Z10 F300
G90
G1 X0 Y100 F900
M104 S0
M107 S0
M84 X Y E
```

## Firmware esperado

Este perfil asume el firmware Marlin 2.1.x personalizado con:
- Retracción por firmware (G10/G11) — por eso Cura no emite retracciones
- UBL bed leveling
- Z probe
- Junction Deviation (M205 J)
- Linear Advance (M900 K)
- Cancel Object (M486)
- Volumetric extrusion (M200)

Si tu firmware difiere, ajusta la retracción (`retraction_enable = True`)
y el sabor de G-code en la definición.
