V# Desarrollo de plugins de OctoPrint — referencia

Resumen práctico de la documentación oficial (docs.octoprint.org, rama master,
OctoPrint 1.11.x / Python 3.13). Anclado al plugin real **TempByStructure**
(`~/.octoprint/plugins/TempByStructure`) que usamos como ejemplo vivo.

## 1. Anatomía de un plugin

Dos formas de empaquetarlo:

- **Single-file**: `~/.octoprint/plugins/miplugin.py`. Sin instalar, solo reiniciar.
- **Paquete** (para distribuir): carpeta `octoprint_miplugin/` + `pyproject.toml`/`setup.py`,
  registrado por `entry_points["octoprint.plugin"]`. Se instala con `pip install -e .`.
  Andamiaje: `octoprint dev plugin:new <id>` y `octoprint dev plugin:install`.

El **identificador** se deriva del nombre de fichero/paquete (`miplugin.py` →
`miplugin`; `octoprint_helloworld` → `helloworld`). Se usa en:
- namespace del logger: `octoprint.plugins.<id>`
- prefijos de plantilla: `plugin_<id>_<nombre>`
- rutas de config y API (`/api/plugin/<id>`)

### Propiedades de control a nivel de módulo

```python
__plugin_name__         = "TempByStructure"      # nombre visible
__plugin_version__      = "1.0.0"
__plugin_pythoncompat__ = ">=3.7,<4"             # CLAVE p/ Py3: sin esto se marca incompatible
__plugin_implementation__ = TempByStructurePlugin()   # instancia de la clase principal (obligatorio)
__plugin_hooks__        = { "<hook>": callback, ... }  # hooks que registra
# opcionales: __plugin_identifier__, __plugin_description__,
#             __plugin_load__ (init en import), __plugin_settings_overlay__
```

`__plugin_pythoncompat__` es lo que decide si OctoPrint marca el plugin como
`* incompatible`. Para Python 3.13 basta `">=3.7,<4"` (o `">=3,<4"`).
**Nota:** en nuestra migración al venv NINGÚN plugin se marcó incompatible — los que
"dejaron de funcionar" simplemente no se reinstalaron en el venv nuevo.

## 2. Mixins vs Hooks

- **Mixin** = clase de la que heredas; te da métodos a sobrescribir y recibe
  propiedades inyectadas (`self._logger`, etc.). Implementación basada en herencia.
- **Hook** = punto de enganche donde registras una función callback; reaccionas a
  eventos sin heredar. Se registran en `__plugin_hooks__`.
- Un mismo plugin puede ser ambos a la vez (TempByStructure lo es).

### Mixins disponibles (los importantes)

| Mixin | Para qué |
|---|---|
| `StartupPlugin` | `on_startup()` / `on_after_startup()` (server ya escuchando) |
| `ShutdownPlugin` | `on_shutdown()` limpieza al cerrar |
| `SettingsPlugin` | `get_settings_defaults()`, `on_settings_save()`, `on_settings_migrate()` |
| `TemplatePlugin` | inyectar UI: navbar, sidebar, tab, settings, wizard, generic |
| `AssetPlugin` | publicar JS/CSS (`get_assets()` → `{"js": [...], "css": [...]}`) |
| `SimpleApiPlugin` | endpoints GET/POST simples en `/api/plugin/<id>` |
| `BlueprintPlugin` | blueprint Flask completo (rutas propias) |
| `EventHandlerPlugin` | `on_event(event, payload)` (eventos internos de OctoPrint) |
| `ProgressPlugin` | progreso de impresión/slicing en incrementos de 1% |
| `WizardPlugin` | diálogos de setup inicial |
| `UiPlugin` | reemplazar TODA la interfaz |
| `ReloadNeedingPlugin` | requiere recargar el frontend al activar/desactivar |
| `RestartNeedingPlugin` | requiere reiniciar OctoPrint al activar/desactivar |

### Hooks de comunicación (los más usados)

```python
# Fases de envío de G-code: queuing, queued, sending, sent
handler(comm, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *a, **kw)
#   -> None | str | tupla(1/2/3) | lista. Permite reescribir/suprimir/expandir comandos.

# Línea recibida del firmware
octoprint.comm.protocol.gcode.received
handler(comm, line, *a, **kw)            # -> línea (mod/orig) o None

# Temperaturas parseadas (¡lo que usa TempByStructure y PlotlyTempGraph!)
octoprint.comm.protocol.temperatures.received
handler(comm, parsed_temperatures, *a, **kw)   # -> dict de temps modificado
#   parsed_temps = { "T0": (actual, target), "B": (actual, target), ... }
#   Inyectar series extra = añadir claves nuevas a ese dict.

# Scripts G-code (before/after print, etc.)
octoprint.comm.protocol.scripts
handler(comm, script_type, script_name, *a, **kw)   # -> prefix/postfix/vars

# Action commands del firmware (// action:...)
octoprint.comm.protocol.action
handler(comm, line, action, name='', params='', *a, **kw)

# Permisos
octoprint.access.permissions
handler(*a, **kw)   # -> lista de dicts {key,name,description,roles,...}

# Config de auto-actualización
octoprint.plugin.softwareupdate.check_config
handler()   # -> dict con la config de update (ver §6)
```

Reglas de hooks: siempre aceptar `*args, **kwargs` (forward-compat). Orden con
tupla `(callback, orden)`. **No bloquear** el loop de comunicación con operaciones largas.

## 3. Propiedades inyectadas (disponibles en `self._...`)

Se inyectan automáticamente ANTES de ejecutar cualquier método del mixin.

| Propiedad | Qué da |
|---|---|
| `self._identifier` | id del plugin |
| `self._plugin_name` / `self._plugin_version` | metadatos |
| `self._basefolder` | carpeta de instalación (ficheros relativos) |
| `self._logger` | logger `octoprint.plugins.<id>` |
| `self._settings` | settings del plugin (solo si es SettingsPlugin) |
| `self._plugin_manager` | gestor de plugins; **`send_plugin_message()`** p/ push al frontend |
| `self._printer` | control y estado de la impresora (commands, jog, etc.) |
| `self._printer_profile_manager` | perfiles de impresora |
| `self._event_bus` | EventManager (emitir/escuchar eventos) |
| `self._file_manager` | gestión de ficheros/uploads |
| `self._analysis_queue` | análisis de G-code |
| `self._slicing_manager` | slicing |
| `self._user_manager` | usuarios/permisos |
| `self._connectivity_checker` | chequeo de conectividad |
| `get_plugin_data_folder()` | carpeta de datos persistente del plugin (preferir sobre `_datafolder`) |

## 4. Ciclo de vida

1. Instanciación + introspección de `__plugin_implementation__`.
2. Inyección de propiedades.
3. Registro de hooks/helpers, aplicación de settings overlays.
4. `on_startup()` → (server arranca) → `on_after_startup()`.
5. Runtime (eventos, hooks, requests).
6. `on_shutdown()` al cerrar.
Orden entre plugins: los que devuelven `get_sorting_key()` != None van primero.

## 5. Frontend (TemplatePlugin + AssetPlugin + ViewModel JS)

Backend publica JS/CSS con `AssetPlugin.get_assets()` y plantillas con `TemplatePlugin`.
El JS es un **ViewModel KnockoutJS** registrado así:

```javascript
OCTOPRINT_VIEWMODELS.push({
    construct: TempByStructureViewModel,
    dependencies: ["settingsViewModel", "temperatureViewModel"],
    optional: ["plotlyTempGraphViewModel"],
    elements: ["#tab_plugin_tempbystructure"]
});

function TempByStructureViewModel(parameters) {
    var self = this;
    self.settings = parameters[0];
    self.temp     = parameters[1];

    // Callbacks estándar:
    self.onBeforeBinding = function() {};
    self.onAfterBinding  = function() {};
    self.onStartupComplete = function() {};
    self.fromCurrentData = function(data) {};   // estado impresora (push periódico)
    self.fromHistoryData = function(data) {};   // histórico (temps, terminal)
    self.onDataUpdaterPluginMessage = function(plugin, message) {
        if (plugin !== "tempbystructure") return;
        // actualizar observables -> KnockoutJS refresca el DOM
    };
}
```

**Push backend → frontend:**
```python
self._plugin_manager.send_plugin_message(self._identifier, {"key": "value"})
```
Llega a `onDataUpdaterPluginMessage(plugin, message)`. Binding con
`data-bind="text: miObservable"` en el div de la plantilla.

Tipos de plantilla (`get_template_configs()`): `navbar`, `sidebar`, `tab`,
`settings`, `wizard`, `generic`. Nombre por convención: `<id>_<tipo>.jinja2`.

## 6. Auto-actualización (y el 404 que vemos en el log)

```python
def get_update_information(self):
    return { "tempbystructure": {
        "displayName": "TempByStructure",
        "displayVersion": self._plugin_version,
        "type": "github_release",          # o "github_commit"
        "user": "<usuario>", "repo": "<repo>",
        "current": self._plugin_version,
        "pip": "https://github.com/<u>/<r>/archive/{target_version}.zip",
    }}
```

Si `user/repo` apunta a un repo inexistente → `GitHub API error: Not Found (HTTP 404)`
en el log (cosmético). Es justo lo que pasa con TempByStructure y terminalresponse:
están registrados para update contra repos que no existen. Soluciones: corregir
user/repo, o quitar el hook `softwareupdate.check_config`.

## 7. TempByStructure — mapa del plugin (ejemplo vivo)

Ficheros: `__init__.py` (clase + hooks), `thermal_calibrator.py` (clase
`ThermalCalibrator`), `timing_engine.py`, `realtime_monitor.py`,
`gcode_processor.py`, `event_manager.py`, `static/`, `templates/`.

- **Mixins**: SettingsPlugin, TemplatePlugin, AssetPlugin, EventHandlerPlugin,
  StartupPlugin, RestartNeedingPlugin.
- **Hooks**: `gcode.queuing`, `gcode.received`, `temperatures.received`,
  `softwareupdate.check_config`.
- **Idea**: intercepta temperaturas, predice tiempos de calentamiento/enfriamiento
  con `ThermalCalibrator`, e inyecta series extra (`TBS_Predicted`, `TBS_Rate`,
  `TBS_Structure`) en el dict de temperaturas para que PlotlyTempGraph las grafique.

### Bug actual (pendiente, NO arreglado aún)

`__init__.py:498` llama `self.calibrator.get_current_heating_rate()`, método que
**no existe** en `ThermalCalibrator`. Métodos reales relacionados:
`get_thermal_rate(current_temp, target_temp, fan_speed=0)`,
`_get_heating_rate(temp, fan_speed)`, `get_calibration_stats()`.
Además depende de que PlotlyTempGraph esté activo (ahora está en `_disabled`).
