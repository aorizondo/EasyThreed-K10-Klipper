/*
 * Boost Kit — Sidebar persistente (reemplazo de Stateful Sidebar).
 *
 * Recuerda qué paneles del sidebar están colapsados/expandidos entre recargas.
 *
 * Por qué Stateful Sidebar falla: determina el estado por la clase `.collapsed`
 * del <a.accordion-toggle>, que en OctoPrint 1.11 queda DESINCRONIZADA con el
 * estado real (p.ej. #connection arranca colapsado sin `.collapsed`; SpoolManager
 * tiene `.collapsed` pero el body abierto). Aquí la fuente de verdad es la clase
 * `in` del <div.accordion-body> (el estado real de Bootstrap collapse), y se
 * observa con MutationObserver en vez de los eventos hidden/shown (poco fiables),
 * persistiendo en localStorage (instantáneo, sin round-trip al servidor).
 */
$(function () {
    function BoostSidebarViewModel() {
        var self = this;
        var KEY = "bk_sidebarState";       // { bodyId: collapsedBool }
        var SIDEBAR = "sidebar";

        function load() {
            try { return JSON.parse(localStorage.getItem(KEY) || "{}") || {}; }
            catch (e) { return {}; }
        }
        function persist() {
            try { localStorage.setItem(KEY, JSON.stringify(self._state)); } catch (e) { }
        }
        self._state = load();
        self._applying = false;            // evita guardar mientras aplicamos clases

        function bodies() {
            var root = document.getElementById(SIDEBAR);
            return root ? root.querySelectorAll("div.accordion-body") : [];
        }
        function toggleFor(id) {
            try {
                return document.querySelector(
                    'a.accordion-toggle[data-target="#' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]');
            } catch (e) { return null; }
        }
        // Estado REAL: colapsado = el body NO tiene la clase `in`.
        function isCollapsed(body) { return !body.classList.contains("in"); }

        function applyOne(body) {
            var id = body.id;
            if (!id || !(id in self._state)) return;   // sin preferencia -> dejar default
            var wantCollapsed = !!self._state[id];
            if (isCollapsed(body) === wantCollapsed) {
                // ya está como queremos: solo sincroniza la clase del toggle.
                syncToggle(id, wantCollapsed);
                return;
            }
            self._applying = true;
            if (wantCollapsed) body.classList.remove("in", "overflow_visible");
            else body.classList.add("in");
            syncToggle(id, wantCollapsed);
            self._applying = false;
        }
        function syncToggle(id, collapsed) {
            var t = toggleFor(id);
            if (!t) return;
            if (collapsed) t.classList.add("collapsed");
            else t.classList.remove("collapsed");
        }
        function applyAll() {
            var bs = bodies();
            for (var i = 0; i < bs.length; i++) applyOne(bs[i]);
        }

        // Observa cada body: cuando cambia su clase `in` (el usuario lo toggleó),
        // recomputa y persiste el estado real.
        function observe(body) {
            if (body._bkSObs || !body.id) return;
            var mo = new MutationObserver(function () {
                if (self._applying) return;
                self._state[body.id] = isCollapsed(body);
                persist();
            });
            mo.observe(body, { attributes: true, attributeFilter: ["class"] });
            body._bkSObs = mo;
        }
        function wire() {
            var bs = bodies();
            for (var i = 0; i < bs.length; i++) observe(bs[i]);
        }

        var _t = null;
        function applyAndWireDebounced() {
            clearTimeout(_t);
            _t = setTimeout(function () { applyAll(); wire(); }, 80);
        }

        self.onStartupComplete = function () {
            var root = document.getElementById(SIDEBAR);
            if (!root) return;   // touchui u otra UI sin sidebar clásico
            applyAll();
            wire();
            // Paneles que inyectan otros plugins DESPUÉS del arranque: reaplica.
            var rootObs = new MutationObserver(applyAndWireDebounced);
            rootObs.observe(root, { childList: true, subtree: true });
            // Pasada extra por si algún panel tarda en montarse.
            setTimeout(function () { applyAll(); wire(); }, 1200);
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: BoostSidebarViewModel,
        dependencies: [],
        elements: [],
    });
});
