# CAD — Enclosures modulares (OpenSCAD)

Cajas paramétricas para las placas de la frankenprinter. Diseñadas para **imprimir en la K9**
(volumen 100×100×100 mm, boquilla 0.4, **PETG sin cama caliente**), con **tapa atornillada M3**,
**ventiladores de PC estándar** y **acople modular entre cajas**.

## Estado

| Pieza | Estado | Notas |
|-------|--------|-------|
| Librería `lib/enclosure_lib.scad` | ✅ | Módulos reutilizables (caja, bosses, standoffs, ventilador, dovetail, puertos) |
| `k10_enclosure.scad` | 🟡 cotas estimadas | Funcional y watertight; faltan medidas reales (TODO-MEDIR) |
| Caja Arduino Uno + CNC Shield | ⏳ pendiente | |
| Acople cola de milano integrado | ⏳ pendiente | módulos listos en la lib, falta integrarlos en las cajas |

## Cómo generar

```bash
cd cad
./build.sh                 # genera stl/*.stl + renders/*.png de todas las piezas
```
O manualmente una pieza:
```bash
openscad -D 'part="body"' -o salida.stl k10_enclosure.scad           # STL imprimible
xvfb-run -a openscad -D 'part="assembly"' -o vista.png \
   --imgsize=1100,800 --camera=0,0,0,55,0,30,250 k10_enclosure.scad  # preview
```
`part` puede ser `body`, `lid` o `assembly`.

## Verificar imprimibilidad

```bash
python3 -c "import trimesh;m=trimesh.load('stl/k10_body.stl');print(m.extents, m.is_watertight)"
```
Debe dar las 3 dimensiones < 100 mm y `True` (estanco). El build avisa si alguna pieza no es 2-manifold.

## Parámetros que debes medir (TODO-MEDIR)

En `k10_enclosure.scad`, ajústalos con calibre antes de imprimir en serio:

- `PCB_L`, `PCB_W`, `PCB_T` — contorno y grosor de la placa K10.
- `COMP_H` — altura del componente más alto (define el alto interior).
- `HOLE_INSET` / `HOLE_DX` / `HOLE_DY` — posición de los agujeros de montaje del PCB.
- Posición/tamaño de los **puertos** (`port_rect(...)`): USB-C, jack DC, mazo del cabezal, microSD.
  Mídelos respecto al borde y a la altura sobre el PCB.

## Convenciones de diseño

- **Tornillería M3**: pasante 3.4, autorroscante 2.7, inserto térmico 4.2 (en `lib`, ajustables).
- **Holgura FDM** (`FIT`) 0.30 mm — sube/baja según tu calibración PETG.
- **Pared** 2.4 mm, **suelo/tapa** 2.0–2.4 mm.
- **PETG sin cama caliente**: el suelo es una superficie grande → **usa brim/orejas** en el slicer para
  evitar warping; bed limpio + laca/barra de pegamento. Evita corrientes de aire (la propia carcasa ayuda).
- **Ventiladores**: tamaños estándar 40/50/60/80/92/120 con su separación de tornillos en `fan_hole_spacing()`.
  Un **40 mm** sobra para refrigerar los drivers y cabe de sobra en estas cajas.

## Estructura

```
cad/
├── lib/enclosure_lib.scad   # librería paramétrica (la base de todo)
├── k10_enclosure.scad       # caja de la placa K10
├── build.sh                 # genera stl/ + renders/
├── renders/                 # previews PNG (versionados, referencia visual)
└── stl/                     # STL generados (NO versionados; regenerar con build.sh)
```

## Acople entre cajas

La librería incluye `dovetail_male()` / `dovetail_slot()` (cola de milano) para que las cajas se
acoplen lateralmente sin tornillos extra (más una fijación M3 opcional). Falta integrarlo en cada caja
eligiendo en qué caras va el macho y en cuáles la hembra — siguiente iteración.
