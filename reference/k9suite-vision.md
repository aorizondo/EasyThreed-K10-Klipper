# K9 Suite — Visión completa de la interfaz (override de OctoPrint)

Objetivo del usuario: **mejorar definitivamente la interfaz y las funciones de
OctoPrint** con UN plugin autónomo que hace **override de los tabs nativos** y
añade **tarjetas de sidebar** reorganizables. La maquetación en columnas/cards la
hace el usuario con **UI Customizer**; k9suite aporta el contenido y las funciones.

Componentes OctoPrint que usamos:
- **Tabs** (`type: "tab"`): vista central. Override = ocultar el nativo y poner el nuestro.
- **Sidebar components** (`type: "sidebar"`): cajas plegables de la columna lateral
  ("acordeones tipo tarjeta, fijables"). Stateful Sidebar las recuerda; haremos las
  nuestras sin depender de él.
- **Navbar** (`type: "navbar"`): barra superior.

## A. Tabs (override de nativos)

### A1. Control  ← OVERRIDE del Control nativo (NO tab separado)
Absorbe TODO el actual tab "K9 Suite" (que debe DESAPARECER). Contenido:
- **Jog estilo jogwheel**: dial circular (anillos=paso, homes en esquinas, slider Z,
  Tool E extrude/retract, Motors/Fan). Mejorar visualmente el del plugin jogwheel.
- Pausa/Reanudar instantáneos (P000/R000), feed/flow, extrusión, load/unload,
  babystep, macros, host command, cancel objects, accel/velocidad (M201/3/4/5),
  reanudación smart-pause, nivelación UBL.
- Estado: BUILT como Layout B (jog de botones); FALTA convertir jog a dial + mover aquí.

### A2. Temperatura  ← OVERRIDE del Temperature nativo (ocultar original)
- Gráfico Canvas (zoom/pan + **análisis de pendiente °C/s por rango**) — HECHO.
- FALTA: **control de temperatura** (set hotend/cama, presets, enfriar).

### A3. GCode Viewer  ← se MODIFICA el contenido del tab nativo (NO override/replace)
Decisión final: NO usar `replaces=` (perdería el visor 2D nativo y su carga de
archivos). En su lugar, `bkgcode3d.js` **inyecta por JS** una barra + overlay 3D
DENTRO del `#canvas_container` nativo (estilo cancelobject), con toggle 2D/3D. El
2D nativo se conserva intacto; el 3D se superpone.
- **HECHO (v0.4.0)**: Three.js r108 vendorizado de PrettyGCode (static/js/vendor/:
  three.min, Line2/LineGeometry/LineMaterial/LineSegments2/LineSegmentsGeometry,
  OBJLoader, camera-controls, Lut + models/ExtruderNozzle.obj). Escena con Z arriba,
  cama (grid+plano) desde printerProfiles, nozzle OBJ (con fallback a cono), loop
  `animate()` que solo pinta cuando `needRender` y el tab está visible
  (`canvas.offsetParent`).
- **HECHO — sync cabezal EN VIVO**: backend empuja `tracktrail` (lista de puntos
  [x,y,z,extrudeFlag], throttle `gcode3d.push_interval`=0.12s + RepeatedTimer de
  cola para el último punto) desde `move_handler`; el nozzle 3D sigue el último
  punto. `trackstate` (cambio de Z) actualiza el HUD (X/Y/Z/capa/altura). VERIFICADO
  en vivo con browseros: el nozzle se mueve a la posición real.
- **HECHO — "trail" de los últimos N movimientos**: ring buffer en cliente
  (slider 50–2000), Line2 con color por vértice (extrusión=naranja, travel=gris).
- **HECHO — Modelo 3D del archivo**: botón que reutiliza el gcode YA parseado por el
  visor nativo (`window.GCODE.renderer.getLayer(n)`/`getModelNumLayers`/`getZ`),
  sin reparsear; LineSegments2 por capa, color por altura. Degrada con HUD si el
  visor nativo aún no parseó (en el entorno virtual el parseo nativo no se dispara
  solo; en uso real con archivo cargado funciona).
- **HECHO — REEMPLAZO real del 2D** (no overlay): con el 3D activo se ocultan el
  canvas nativo, su wrapper (cancelobject) y los sliders (CSS `bk3d-active`), MÁS los
  controles hermanos nativos (layer nav, model/renderer info, barra cancelobject) vía
  clase `bk3d-hostactive`. Toggle 2D↔3D verificado estable (5 ciclos).
- **HECHO — navegación de ratón** (CameraControls: orbit/pan/zoom) verificada.
- **HECHO — cubo de navegación estilo FreeCAD**: canvas 96×96 esquina sup-der; cubo
  con caras etiquetadas (SUP/INF/FRENTE/ATRÁS/IZQ/DER) orientado según la cámara
  (quaternion inverso); clic en cara → snap a esa vista ortogonal (raycast).
- **HECHO — proyección Perspectiva ↔ Isométrica** (PerspectiveCamera/OrthographicCamera);
  al togglear se recrea CameraControls preservando posición/target.
- **HECHO — mini-visor 3D en la tarjeta "Gcode" del sidebar** (la del widget de UI
  Customizer `UICGcodeVWidgetCan`: se oculta su canvas 2D y se inyecta el nuestro):
  misma escena que el tab, SIN controles, cámara isométrica propia, siempre en vivo.
- **ARQUITECTURA CLAVE (resuelto bug negro)**: Three.js r108 NO soporta 2 WebGLRenderer
  sobre la MISMA escena (el 2º queda en negro al compartir buffers GPU). Solución: UN
  renderer WebGL OFFSCREEN que pinta la escena desde cada cámara y se copia con
  `ctx2d.drawImage(gl.domElement)` a cada canvas 2D visible (tab + sidebar + cubo).
  `LineMaterial.resolution` se ajusta por vista. (Gotcha r108: `Quaternion.inverse()`,
  no `.invert()`.)
- **FALTA**: malla UBL activa (cama no plana con la malla de nivelación); afinar la
  posición/orientación de la punta del nozzle OBJ; navegación tipo Orca (sección E).
- Nota convivencia: PrettyGCode (si está activo) carga su propio THREE r108 global;
  ambos coexisten (misma revisión). Al desactivar PrettyGCode, el nuestro queda solo.

### A4. Terminal  ← mejora del nativo
- **Filtros configurables persistentes** (qué filtros van siempre activos; hoy se
  inunda de `wait`/`Not SD printing`/autoreport de temperatura).

## B. Tarjetas de sidebar (nuevas, por utilidad, fijables)
- **Temp**: mini-gráfico + control de temperatura.
- **Manos**: jog + opciones de manos (extrusión, home, motores).
- **Estado** (aumentar el State): info de capa/altura + Pausa/Reanudar/Cancelar.
- Configurables: el usuario decide cuáles fija y dónde (con UI Customizer).

## C. Funciones nuevas
- **Saltar a un punto del gcode** ("rehacer una sección mala"): saltar a una capa/
  punto determinado del gcode. Relacionado con el plugin SkipTo (saltar a capa).
  PENDIENTE ACLARAR semántica: ¿reiniciar el print desde la capa N (tipo SkipTo), o
  salto en vivo durante la impresión para repetir un tramo? (afecta el diseño).

## Mecanismo de override
Opción elegida: ocultar el tab nativo (el usuario aceptó "poner el original como
hidden"). Decisión pendiente: ¿lo oculta k9suite automáticamente o el usuario vía
UI Customizer? Preferencia de robustez: que k9suite lo haga, para ser autónomo.

## Orden de ataque propuesto
1. Mover K9 Suite→Control + jog tipo dial + ocultar K9 Suite tab.
2. Control de temperatura en tab Temperatura + ocultar Temperature nativo.
3. Tarjetas de sidebar (temp, manos, estado+capa+pausa).
4. Filtros de terminal persistentes.
5. Saltar a punto del gcode.
6. Visor 3D/2D con sync + malla (el más grande, al final).

## D. Sistema de SLIDERS (el usuario los prefiere a teclear números)
Idea (modelo: marlinslider, sync bidireccional reescribiendo gcode.sent/received).
Widget reutilizable `k9-slider` (componente Knockout, sin dependencias): label +
`input[range]` + (opcional) input numérico + unidad. Para los críticos (temperatura)
= slider Y número Y perfiles. Parámetros pedidos y su gcode:
- Jog (paso de movimiento) · Extrusión (cantidad) · Velocidad/feed (M220) ·
  Flujo (M221) · Aceleración (M204 P/R/T) · Junction deviation (M205 J) ·
  Pressure/Linear advance (M900 K, si LIN_ADVANCE en firmware) · Corriente motores
  (M907 — K9 es A4988 con PWM current; TMC sería M906) · Temperatura (M104/M140) ·
  Velocidad de ventilador (M106 S0-255 / %).
Sync bidireccional: leer valor actual (M503 / echos) y reflejarlo en el slider;
al soltar, enviar el gcode. Perfiles guardables (presets) por parámetro.

## E. Navegación de gcode tipo Orca Slicer (saltar a punto)
El usuario quiere: **slider vertical = altura/capa** + **slider horizontal = punto
del trazado dentro de la capa** (como navegar en Orca). Sirve para:
- **Reinicio desde capa/punto N** (print fallido/cancelado; estilo SkipTo: homea,
  calienta, salta el gcode hasta esa capa+trazado).
- **Salto en vivo** durante impresión (mover el puntero del job; arriesgado por
  colisión con material ya depositado — avisar/parar primero).
Se integra con el visor (A3): el slider horizontal recorre los comandos de la capa
y el visor resalta el punto; al confirmar, salta ahí.

## F. Cómo trabajan los plugins existentes (investigado — reutilizar idioms)
Patrón general: los plugins NO poseen el markup; **enganchan los viewModels nativos**
(`controlViewModel`/`temperatureViewModel`/`terminalViewModel`) y reescriben/observan.
- **Override de tab**: `replaces="temperature"` en el template config (plotlytempgraph
  `__init__.py:75-81`). Mecanismo LIMPIO para A2 (Temperatura) y A3 (GCode Viewer).
- **Visor 2D**: `GCODE.renderer` (singleton global, `gcodeviewer/static/js/viewer/
  renderer.js`), Canvas 2D, `setOption`, callback `onViewportChange` (punto de
  enganche de overlays). Cama SOLO rejilla plana (`drawRectangularGrid :606`).
- **Visor 3D**: prettygcode Three.js — nozzle OBJ (`prettygcode.js:708-729`), líneas
  gruesas Line2 por capa (`:1256-1363`), `updateGridMesh :1904` (cama plana opac 0.2).
  Posición vía `PrintHeadSimulator` que parsea `data.logs` (`:230-455`) → **sustituir
  por nuestro `trackstate`**. Bug `_enableWebcam`: llama métodos privados del
  controlViewModel nativo que ya no existen (`:816,825`).
- **Jog**: jogwheel = un SVG 320×260 a mano (`jogwheel.jinja2`), rosa radial de anillos
  (paso por cuña) + fader Z + homes; profundidad por `filter#f1` drop-shadow; envía
  G91/G1/G90 crudo. **Reutilizar el SVG, enrutar a nuestra API jog/home.**
- **Objetos clicables en visor**: cancelobject overlay vía `onViewportChange`
  (`cancelobject.js:438-449`), hit-test `check_point:331`, parser de objetos por
  comentarios de slicer + M486 server-side (`__init__.py:109-163`).
- **Sliders feed/flow**: marlinslider sync bidireccional (`__init__.py:304-306`).
- **Terminal**: terminalmessaging colorea spans sobre `terminalViewModel.log`
  (`:14-16`); multilineterminal = input multilínea + blacklist de filtros; autoscroll
  = clear + pausa autoscroll. Componer los tres en uno con filtros persistentes.
- **Card de estado (SkipTo)**: añade controles al State con type `sidebar`/inyección.
- **customcontrolmanager (bundled)**: UI para botones custom en Control (referencia).
- **Posición real**: NUESTRO tracker (`__init__.py:373-381` push `trackstate`,
  shape en `tracker.py:127-140`) es más fiable que ambos visores. Es la pieza central.

## Estado actual (2026-06-19)
HECHO y verificado en vivo: control en tiempo real (P000/R000), tracker mode-aware,
smart pause/resume, nivelación UBL auto, gráfico de temperatura con análisis de
pendiente, panel de control consolidado Layout B (jog de botones), parser M503.
Ver detalle técnico en la memoria `octoprint-k9suite.md` y plugins en
`octoprint-plugins-inventory.md`.
