# Inventario de plugins OctoPrint (K9) → mapa de consolidación en K9 Suite

Fecha: 2026-06-19. OctoPrint 1.11.7, venv Py3.13. 66 plugins (bundled + terceros).
Objetivo: derivar las funciones útiles de los plugins de terceros a UN solo plugin
(K9 Suite), eliminando duplicados, "malas prácticas" y plugins rotos tras la
migración de venv. **Webcam: FUERA de alcance** (no interesa por ahora).

Leyenda módulos K9 Suite:
- M1 Control en tiempo real (HECHO) · M2 Tracker posición/capa (HECHO) ·
  M3 Nivelación UBL auto (HECHO) · M4 Visor 3D (pendiente) ·
  M5 Suite de calibración (pendiente) · M6 Cancel object (pendiente) ·
  M7 Mano alzada gráfica (pendiente)

## A. Rotos / a DESACTIVAR (causan el fallo de carga intermitente)
| Plugin | Problema | Acción |
|---|---|---|
| `prettygcode` (PrettyGCode 1.2.4) | `controlViewModel._enableWebcam is not a function` durante bindViewModels (acopla webcam, rota en 1.11.7) | Desactivar → reemplaza M4 |
| `TempByStructure` | 404 en su API + `get_current_heating_rate` inexistente + error Plotly cada 5s | Desactivar (custom de terceros, no del usuario) |
| `plotlytempgraph` | ya OFF | dejar OFF |

Estos, al petar en el bootstrap, abortan intermitentemente el binding de Knockout
→ `settingsViewModel is missing, can't run UI`. (Autologin NO es el problema: el
log confirma `Login mechanism: autologin` OK.)

## B. DUPLICAN funciones que K9 Suite YA tiene → quitar al migrar
| Plugin | Función | Reemplazado por |
|---|---|---|
| `marlinslider` (Marlin Slider Controls) | sliders en vivo feed/flow/babystep | **M1** (P000/R000 + M220/M221/M290, más instantáneo) |
| `babystepping` (Baby Stepping) | M290 Z en vivo | **M1** |
| `displayz` (DisplayZ) | altura Z en navbar | **M2** (tracker mode-aware, más correcto) |
| `consolidate_temp_control` | unificar controles de temperatura | absorbible en UI de K9 Suite |

## C. Módulos PEDIDOS pendientes → funciones a tomar de estos plugins
| Plugin | Función a absorber | Módulo destino |
|---|---|---|
| `gcodeviewer` (bundled, 2D/3D) / `prettygcode` / `cnc_gcodeviewer` (OFF) | visor 3D de gcode (SIN webcam) | **M4** |
| `DisplayLayerProgress` | progreso por capa, tiempo restante por capa | **M2/M4** (detección de capa correcta en espiral/relativo) |
| `SkipTo` | saltar a capa N | **M4** (interacción con el visor) |
| `CalibrationTools` | tests PID, first-layer, retracción, temp tower, etc. | **M5** |
| `calibrationcompanion` (Calibration-Companion) | asistentes de calibración | **M5** |
| `CR10_Leveling` / `manual_bed_tramming` | tramming manual de las 4-5 esquinas | **M5** (complementa la nivelación UBL auto de M3) |
| `cancelobject` (Cancel Objects, M486) | cancelar objetos en curso | **M6** |
| `excluderegion` | excluir regiones del área de impresión | **M6** (relacionado) |
| `gridspace` (GridSpace) | generar gcode de formas/rejilla sobre la cama | **M7** (base de mano alzada) |

## D. Funciones ÚTILES de "calidad de vida" a considerar absorber
| Plugin | Función |
|---|---|
| `macro` (Sidebar Macros) / `MacroSteps` (Macro Steps) | botones de macros de gcode personalizados |
| `jogwheel` (Jog Wheel Control) | rueda de jog para mover ejes |
| `simpleemergencystop` (Simple Emergency Stop) | botón de parada de emergencia (M112) |
| `repeatingcommand` (RepeatingCommand) | enviar un comando en bucle (p.ej. M105) |
| `statefulsidebar` (Stateful Sidebar) | recordar estado de paneles del sidebar |
| `PrinterAlerts` | alertas/avisos en eventos de impresión |
| `M300Player` | melodías M300 al terminar/eventos |
| `navbartemp` (Navbar Temperature) | temperaturas en la barra superior |
| `activeconnections` (Active Connections) | ver/gestionar conexiones activas |

## E. Terminal / gcode (nicho; absorber opcional)
`AutoTerminalInput`, `autoscroll`, `multilineterminal`, `marlingcodedocumentation`
(autocompletado/docs de gcode), `terminalmessaging`, `terminalresponse`.

## F. Gestión de archivos (probablemente DEJAR como están)
`filemanager`, `draggable_files`, `autoselect`, `DeleteAfterPrint`, `GcodeEditor`,
`MetadataPreprocessor`, `SlicerSettingsParser`, `UltimakerFormatPackage` (miniaturas
Cura), `file_check`(B).

## G. Filamento
`SpoolManager` (gestión de bobinas) — módulo grande e independiente; evaluar aparte.

## H. UI / tema
`uicustomizer` (UI Customizer — temas/layout). Decisión: ¿K9 Suite trae su propia
estética o se respeta UI Customizer? (No es función, es presentación.)

## I. Infraestructura — NO reabsorber (dejar tal cual)
`portretry`, `PiSupport`, `firmware_check`, `softwareupdate`, `backup`, `logging`,
`discovery`, `appkeys`, `announcements`, `tracking`, `health_check`, `errortracking`,
`eventmanager`, `corewizard`, `classicwebcam`(webcam, FUERA), `virtual_printer`,
`uploadmanager`, `customcontrolmanager`, `action_command_*`, `actioncommands`,
`metadatapreprocessor`, `Autoscroll`/`Autoselect` (ya arriba).

## OFF actualmente
`continuousprint` (cola de impresión continua), `plotlytempgraph`.

---
### Resumen de consolidación
- **M1/M2/M3 hechos** ya reemplazan: marlinslider, babystepping, displayz (y parte de
  consolidate_temp_control). → se pueden desactivar al validar.
- **Pendientes**: M4 visor 3D (sin webcam) ← gcodeviewer/prettygcode/cnc_gcodeviewer +
  DisplayLayerProgress + SkipTo; M5 calibración ← CalibrationTools + calibrationcompanion
  + manual_bed_tramming; M6 cancel ← cancelobject + excluderegion; M7 mano alzada ← gridspace.
- **Desactivar ya** (rotos): prettygcode, TempByStructure.
