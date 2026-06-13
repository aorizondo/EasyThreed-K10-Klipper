# Medidas de la placa K10 por fotogrametría

Como no hay cotas publicadas del PCB de la K10, se estimaron a partir de las fotos cenitales del
reversing usando un **componente de tamaño estándar como regla**. Método reproducible con
`reference/images/` + OpenCV (scripts ad-hoc).

## Método

1. **Referencia de escala = encapsulado del MCU.** El chip se lee como **GD32F303 (GigaDevice, ARM)**.
   Se contaron los pines por lado mediante perfil de intensidad: **~12 pines/lado → LQFP48**
   (no LQFP64). El LQFP48 tiene **cuerpo de 7.0 mm** (lead-span 9.0 mm).
2. Medido el cuerpo del MCU en la foto frontal: **≈167 px** → escala **0.0419 mm/px**
   (cross-check con lead-span 9 mm ≈ 220 px → 0.0409 mm/px; coinciden en ~0.041).
3. Contorno del PCB por perfiles de brillo (placa oscura sobre fondo claro), en ambas caras.

## Resultados

| Magnitud | Valor | Confianza | Notas |
|----------|-------|-----------|-------|
| **Contorno PCB** | **79 × 53 mm** | Alta (±2 mm) | Aspecto 1.50 coincide por las dos caras |
| Grosor PCB | 1.6 mm | Asumido | estándar de industria |
| Patrón de agujeros (4 esquinas) | ~72 × 44 mm centro-a-centro | Media (±2-3 mm) | esquinas inferiores ruidosas (QR/botón) |
| Inset de agujeros desde borde | ~3.5 × 4.5 mm | Media | derivado del patrón |
| **Altura de componentes** | **NO medible** | — | las fotos cenitales no dan altura; usar calibre |

## Escala — verificación cruzada

- Frontal: 1881 × 1258 px × 0.0419 = **78.8 × 52.7 mm** (aspecto 1.495).
- Trasera: 1882 × 1191 px (aspecto 1.580; ligero escorzo vertical o sobresale el botón redondo).
- Ambas dan ~79 mm de ancho → el contorno es robusto.

## Implicación de diseño

- El **contorno (79×53)** es fiable → la caja se dimensiona con seguridad.
- El **patrón de agujeros** NO es lo bastante preciso para alinear standoffs a ciegas.
  → La caja debe **retener el PCB por su contorno** (repisa perimetral + presión de la tapa),
  usando los standoffs solo como apoyo aproximado. Verificar los 4 agujeros con calibre antes de
  fijar tornillos de PCB.
- **COMP_H (altura)**: medir con calibre el componente más alto (condensadores/conectores). Solo
  afecta al alto de la caja, que lleva margen; valor provisional 17 mm.

## Layout de puertos por borde (verificado en fotos)

- Borde con 4 conectores de motor (X/Y/Z/E) | jack DC 12V + USB-C | mazo del cabezal (HE+TH+FAN) |
  ranura microSD | botones del reverso (S3 Retract, S4 Feed, S5 ▲ Print, S6-S9 niveles).
