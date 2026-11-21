# Imprimir desde OctoPrint (host streaming) vs desde la SD del firmware

Fecha: 2026-06-24. Evaluación para Boost Kit, centrada en la **pausa instantánea
avanzada (hot-tune)**.

## Conclusión

**Hay que imprimir SIEMPRE desde OctoPrint (host streaming).** La impresión desde la
tarjeta SD de la impresora es **incompatible** con el hot-tune, el cancel-object y el
skip-to, porque todos requieren que OctoPrint controle el stream línea a línea.

## Por qué la SD rompe el hot-tune

Al imprimir desde SD (M23 select → M24 start), **el firmware lee el fichero de la SD de
forma autónoma**; OctoPrint solo manda comandos de control (M24/M25/M26/M27) y rastrea el
progreso por los reportes `SD printing byte N/Total` de M27. En concreto (en
`octoprint/util/comm.py`):

- **El hook `gcode.queuing` NO ve las líneas del fichero SD** (`_continue_sending` excluye
  SD; `isStreaming` hace passthrough sin invocar el hook). El interceptor del wrapper no
  tiene comandos que envolver ni tags `filepos:` que mapear.
- **`comm._currentFile.seek(offset)` NO existe en SD**: durante SD, `_currentFile` es
  `PrintingSdFileInformation`, que NO hereda de `PrintingGcodeFileInformation` ni implementa
  `seek()` (solo una propiedad `pos`). El **rebobinado del stream** (clave de la fase de
  recolocación: completar el segmento interrumpido + seguir en la línea siguiente) lanzaría
  `AttributeError`.
- **`_move_log` queda vacío**: se rellena en el `move_handler` SOLO con líneas del job
  (tag `source:file`), que en SD no se emiten → no hay con qué emparejar la pose real ni
  sintetizar el movimiento de finalización.

`M26 S<byte>` (set SD position) es un "seek" del lado del firmware, pero el offset del
`_move_log` es un **offset de byte del fichero local**, no el byte de la SD; sincronizarlos
sería frágil y no replica el modelo del hot-tune.

## Lo que sí cambia entre modos

| | Host streaming (OctoPrint) | Impresión SD (firmware) |
|---|---|---|
| Control línea a línea | Sí | No (firmware autónomo) |
| Hook `gcode.queuing` ve cada línea | Sí | No |
| `_currentFile.seek()` | Sí | No (no existe) |
| Pausa instantánea / hot-tune / skip-to / cancel-object | **Sí** | **No** |
| Pausa/Reanudar básicos (M25/M24) | Sí | Sí |
| Underrun del buffer serie | Posible (mitigable) | Muy improbable |

## Nota para Raspberry Pi (host streaming)

El único inconveniente real del streaming es el riesgo de **underrun** del buffer serie
(stutter) si el host no alimenta a tiempo. En una Pi controlando una sola impresora a
velocidades típicas esto rara vez ocurre. Mitigaciones si aparece:

- Baudrate alto y estable (250000 o 115200 fiable).
- Mantener `serial.ackMax`/buffer adecuados; evitar saturar la Pi con otras tareas.
- El hot-tune (host) es justo lo que aporta valor; merece mantener el streaming.

## Posible futuro (NO implementado)

"Subir a SD como backup" (M28/M29) SIN imprimir desde ahí podría ofrecerse como utilidad
independiente (copia de seguridad en la SD), pero **la impresión seguiría siendo desde
OctoPrint** para conservar el hot-tune. No se ha implementado.
