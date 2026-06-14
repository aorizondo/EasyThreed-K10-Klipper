# Firmware original (OEM) de la EasyThreed K10 — RESPALDO

Restauración de fábrica para volver atrás desde Klipper. **Los binarios NO se versionan**
aquí (firmware propietario de EasyThreed); este README es el puntero a la fuente original
y los hashes para verificar lo que descargues.

## Fuente

Publicado por **palmarci** en su reversing de la placa K10. El propio fabricante le envió
la imagen OEM por email, y coincide **byte a byte** con su dump por SWD de la flash.

- Blog: <https://palmarci.me/blog/2025-05-02-easythreed-k10-3d-printer-main-board-reversing/index.html>
- `from_oem/p10_printer.bin` — firmware de aplicación OEM (lo que va en `0x4000`)
- `from_oem/k10_cfg.txt` — configuración OEM
- `dumps/flash.bin` — volcado completo de la flash (incluye bootloader + app + config)
- `dumps/bl.dump` — bootloader

Descargar con, p. ej.:
```bash
BASE=https://palmarci.me/blog/2025-05-02-easythreed-k10-3d-printer-main-board-reversing
curl -fsSL "$BASE/from_oem/p10_printer.bin" -o p10_printer.bin
curl -fsSL "$BASE/from_oem/k10_cfg.txt"      -o k10_cfg.txt
```

## Hashes (verificación)

| Fichero | Tamaño | SHA256 |
|---------|--------|--------|
| `p10_printer.bin` (OEM app) | 102 083 B | `01ac861d9e81b64623391010f088e87fb8bfdb0a60eae364de903c4a6666f25f` |
| `flash.bin` (dump completo) | 262 144 B | `c41b35850bde8c5777c880316e7faa27b033b7bfcd0c08b72e791419200138bb` |
| `bl.dump` (bootloader) | 18 432 B | `0de376cfd0eb81c479f696fcd719ed7ccfe33ea5a724c7da244101b259169e34` |

Verificado: `p10_printer.bin` == `flash.bin[0x4000 : 0x4000+102083]` (byte a byte).

## Cómo restaurar de fábrica (microSD)

1. Copia `p10_printer.bin` (OEM) a la **raíz** de una microSD FAT32.
   - Opcional: copia también `k10_cfg.txt` para restaurar la config de fábrica.
2. Placa apagada → mete la SD → enciende → espera ~10–30 s.
3. Si el fichero pasa a `p10_printer.CUR`, se restauró. El firmware EasyThreed vuelve a estar activo.

## Nota sobre el tamaño del chip

El dump de palmarci es de un chip de **256 KB** (`flash.bin` = 256 KB; su placa montaba el
`GD32F303RCT6`). Esta placa monta el **`GD32F303CBT6` (128 KB)**, pero la app OEM termina en
115.7 KB y la config en 126 KB, así que **cabe**. Es el mismo modelo de impresora; el riesgo de
incompatibilidad es bajo, pero queda anotado por transparencia.
