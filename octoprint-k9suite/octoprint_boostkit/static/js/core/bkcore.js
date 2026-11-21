/*
 * Boost Kit — núcleo compartido (window.BK).
 *
 * Se carga JUSTO DESPUÉS de bkcompat.js y ANTES de los ViewModels (boostkit.js,
 * bktemp.js, …). Aloja helpers reutilizables entre grupos funcionales para no
 * duplicar lógica. Hoy: la fábrica de controles de temperatura, compartida por el
 * panel de control (boostkit.js) y el gráfico de temperatura (bktemp.js).
 */
(function () {
    "use strict";
    var BK = window.BK || {};

    function num(v, d) { var n = parseFloat(v); return isNaN(n) ? d : n; }
    BK.num = BK.num || num;

    /*
     * Controles de temperatura CAPABILITY-AWARE: un control por elemento CAPACITADO
     * en el perfil activo de OctoPrint (hotend(s) / cama / cámara). El tope de cada
     * slider sale de Ajustes (temp.*_max) porque Marlin no reporta los *_MAXTEMP.
     *
     * Comportamiento del HOTEND (petición del usuario): 0 = APAGADO; al salir de 0 el
     * slider salta al PISO ACTIVO (temp.hotend_min_active, 150 por defecto) y sube
     * hasta el tope (temp.hotend_max, 280). La franja 1..piso es zona muerta que
     * colapsa al piso (el 0 exacto se respeta = apagar). Cama/cámara: lineal 0..max.
     *
     * Devuelve { controls (observableArray), tcMax, minActive, markEdit, onInput,
     * apply, rebuild }. El bucle de datos en vivo de cada VM sigue en su sitio y
     * opera sobre `controls` (siembra c.set con el objetivo del firmware SIN colapsar,
     * para respetar lo que la máquina realmente tenga).
     */
    BK.makeTempControls = function (settings, profiles, post) {
        var controls = ko.observableArray([]);

        function tcMax(key) {
            try {
                var t = settings.settings.plugins.boostkit.temp;
                if (key === "bed") return num(ko.unwrap(t.bed_max), 120);
                if (key === "chamber") return num(ko.unwrap(t.chamber_max), 70);
                return num(ko.unwrap(t.hotend_max), 280);
            } catch (e) { return key === "bed" ? 120 : (key === "chamber" ? 70 : 280); }
        }
        function minActive() {
            try { return num(ko.unwrap(settings.settings.plugins.boostkit.temp.hotend_min_active), 150); }
            catch (e) { return 150; }
        }
        // Piso activo SOLO para hotends: 0 se queda en 0 (apagar); 0<v<piso -> piso.
        function snap(c, v) {
            if (String(c.key).indexOf("tool") !== 0) return v;   // cama/cámara: sin piso
            var lo = minActive();
            return (v > 0 && v < lo) ? lo : v;
        }

        function markEdit(c) { c.lastEdit = Date.now(); }
        // Mientras se ARRASTRA el slider: marca edición y colapsa la zona muerta 1..piso.
        function onInput(c) {
            c.lastEdit = Date.now();
            var v0 = num(c.set(), 0), v = snap(c, v0);
            if (v !== v0) c.set(v);
        }
        // Al confirmar (change / botón Set): colapsa y ENVÍA el objetivo (M104/M140 nativo).
        function apply(c) {
            c.lastEdit = Date.now();
            var v = snap(c, num(c.set(), 0));
            c.set(v);
            post("settemp", { heater: c.key, value: v });
        }

        function rebuild() {
            var p = null; try { p = profiles.currentProfileData(); } catch (e) { }
            var count = 1, hb = true, hc = false, shared = false;
            try { count = parseInt(ko.unwrap(p.extruder.count), 10) || 1; } catch (e) { }
            try { shared = !!ko.unwrap(p.extruder.sharedNozzle); } catch (e) { }
            try { hb = !!ko.unwrap(p.heatedBed); } catch (e) { hb = true; }
            try { hc = !!ko.unwrap(p.heatedChamber); } catch (e) { hc = false; }
            if (shared) count = 1;
            var prev = {}; controls().forEach(function (c) { prev[c.key] = c; });
            function mk(key, label) {
                var ex = prev[key];
                if (ex) { ex.label = label; ex.max = tcMax(key); return ex; }
                return { key: key, label: label, max: tcMax(key), lastEdit: 0,
                    set: ko.observable(0), actual: ko.observable(0), target: ko.observable(0) };
            }
            var list = [];
            for (var i = 0; i < count; i++) list.push(mk("tool" + i, count > 1 ? ("Hotend " + i) : "Hotend"));
            if (hb) list.push(mk("bed", "Cama"));
            if (hc) list.push(mk("chamber", "Cámara"));
            controls(list);
        }
        try { profiles.currentProfileData.subscribe(rebuild); } catch (e) { }
        rebuild();

        return {
            controls: controls, tcMax: tcMax, minActive: minActive,
            markEdit: markEdit, onInput: onInput, apply: apply, rebuild: rebuild
        };
    };

    window.BK = BK;
})();
