/*
 * Boost Kit — Terminal nativo mejorado (chat eficiente, sin lag).
 *
 * Por qué terminalmessaging laguea: en CADA cambio del log hace
 * $('#terminal-output span:contains("Recv:")') — rescanea TODO el DOM por línea
 * (O(n²)) + reflow. Aquí procesamos SOLO las líneas nuevas (diff por longitud) y
 * construimos las burbujas con DOM directo (sin KO foreach, sin rescans), con
 * filtros persistentes, virtualización (poda de nodos viejos) y autocompletado.
 */
$(function () {
    function BoostTerminalViewModel(parameters) {
        var self = this;
        self.settings = parameters[0];
        self.term = parameters[1];        // terminalViewModel (opcional)

        self._lastEntry = null;   // referencia a la última entrada procesada del log
        self.showTs = false;
        self.chatOn = true;
        self._maxGroups = 600;
        self._lastGroup = null;           // último grupo {dir, el, respEl}
        self._out = null;
        var AFKEY = "bk_termActiveFilters";   // persistencia de la selección de filtros

        function tcfg() {
            try { return self.settings.settings.plugins.boostkit.terminal; } catch (e) { return null; }
        }
        // Usa los FILTROS NATIVOS de OctoPrint: term.filterRegex() = unión de los
        // filtros activos (los checkboxes de Ajustes › Terminal). Los aplicamos para
        // OCULTAR (no solo atenuar) las líneas que casan. La regex nativa espera la
        // línea COMPLETA (con prefijo Send:/Recv:), así que filtramos la cruda.
        function filtered(raw) {
            try {
                var rx = self.term && self.term.filterRegex && self.term.filterRegex();
                return rx ? rx.test(raw) : false;
            } catch (e) { return false; }
        }
        // Persistencia de la selección de filtros nativos (activeFilters) en
        // localStorage -> se mantiene entre recargas (lo que el usuario echaba en falta).
        function restoreActiveFilters() {
            if (!self.term || !self.term.activeFilters) return;
            try {
                var saved = JSON.parse(localStorage.getItem(AFKEY) || "null");
                if (saved && saved.length != null) self.term.activeFilters(saved);
            } catch (e) { }
            self.term.activeFilters.subscribe(function (v) {
                try { localStorage.setItem(AFKEY, JSON.stringify(v)); } catch (e) { }
            });
        }
        function stamp() {
            var d = new Date();
            return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2) + ":" + ("0" + d.getSeconds()).slice(-2);
        }
        function el(tag, cls, text) {
            var e = document.createElement(tag);
            if (cls) e.className = cls;
            if (text != null) e.textContent = text;
            return e;
        }

        function addEntry(entry) {
            var raw = (entry && entry.line != null) ? String(entry.line) : String(entry || "");
            if (!raw || filtered(raw)) return;   // filtro nativo (oculta Send y Recv)
            var warn = entry && entry.type === "warn";
            var dir = /^Send:/.test(raw) ? "send" : (/^Recv:/.test(raw) ? "recv" : "other");
            var txt = raw.replace(/^(Send|Recv):\s?/, "");

            if (dir === "send") {
                var g = el("div", "bkt-group send");
                g.setAttribute("data-cmd", txt);
                var cmd = el("div", "bkt-cmd sent");   // .sent: compat con marlingcodedoc
                if (self.showTs) cmd.appendChild(el("span", "bkt-ts", stamp() + " "));
                cmd.appendChild(el("span", null, txt));
                if (warn) cmd.classList.add("warn");
                g.appendChild(cmd);
                var resp = el("div", "bkt-resp");
                g.appendChild(resp);
                self._out.appendChild(g);
                self._lastGroup = { dir: "send", el: g, respEl: resp };
            } else {
                var line = el("div", "bkt-line" + (warn ? " warn" : ""), txt);
                if (self._lastGroup && self._lastGroup.dir === "send") {
                    self._lastGroup.respEl.appendChild(line);
                } else {
                    var sg = el("div", "bkt-group sys");
                    var r = el("div", "bkt-resp");
                    r.appendChild(line);
                    sg.appendChild(r);
                    self._out.appendChild(sg);
                    self._lastGroup = { dir: "recv", el: sg, respEl: r };
                }
            }
        }

        function trim() {
            while (self._out.childElementCount > self._maxGroups) {
                self._out.removeChild(self._out.firstChild);
            }
        }
        function autoscrollOn() {
            try { return self.term && self.term.autoscrollEnabled ? !!self.term.autoscrollEnabled() : true; }
            catch (e) { return true; }
        }
        var _scrollPending = false;
        var _progScroll = false;         // true mientras el propio código hace scrollTop
        var _atBottomThreshold = 8;      // px (mismo umbral que el terminal nativo)
        function isAtBottom() {
            if (!self._out) return true;
            return (self._out.scrollHeight - self._out.scrollTop - self._out.clientHeight) <= _atBottomThreshold;
        }
        function scrollSoon() {
            if (_scrollPending || !self._out || !autoscrollOn()) return;
            _scrollPending = true;
            setTimeout(function () {
                _scrollPending = false;
                if (!autoscrollOn()) return;
                _progScroll = true;
                self._out.scrollTop = self._out.scrollHeight;
                setTimeout(function () { _progScroll = false; }, 30);
            }, 60);
        }
        function toggleAutoscroll() {
            try {
                var v = !self.term.autoscrollEnabled();
                self.term.autoscrollEnabled(v);
                if (v) scrollSoon();
            } catch (e) { }
        }

        function rebuild() {
            if (!self._out) return;
            self._out.innerHTML = "";
            self._lastGroup = null;
            self._lastEntry = null;
            onLog();
        }

        function onLog() {
            if (!self.term || !self.term.log || !self._out) return;
            var log = self.term.log();
            // El log nativo se RECORTA con slice(-buffer): la longitud se queda fija
            // pero el contenido se desplaza. Por eso NO sirve un índice/longitud;
            // localizamos la última entrada procesada por REFERENCIA y seguimos
            // desde ahí. Si ya no está (recortada), reprocesamos toda la cola actual.
            var start = 0;
            if (self._lastEntry) {
                var idx = log.indexOf(self._lastEntry);
                if (idx >= 0) start = idx + 1;
                else { self._out.innerHTML = ""; self._lastGroup = null; start = 0; }
            }
            for (var i = start; i < log.length; i++) addEntry(log[i]);
            if (log.length) self._lastEntry = log[log.length - 1];
            trim();
            scrollSoon();
        }

        function toggleChat() {
            self.chatOn = !self.chatOn;
            var nat = document.getElementById("terminal-output");
            if (nat) nat.style.display = self.chatOn ? "none" : "";
            self._out.style.display = self.chatOn ? "" : "none";
        }
        function clearChat() {
            self._out.innerHTML = ""; self._lastGroup = null;
            // marca como ya vistas todas las entradas actuales (no las re-renderiza)
            var log = (self.term && self.term.log) ? self.term.log() : [];
            self._lastEntry = log.length ? log[log.length - 1] : null;
        }

        // Desplegable ARRIBA con los checkboxes de los filtros NATIVOS. Es la ÚNICA
        // UI de filtros: el panel nativo de abajo (#terminal-filterpanel) se OCULTA
        // para no duplicar. Marcar actualiza term.activeFilters -> filterRegex ->
        // rebuild + persistencia en localStorage (restoreActiveFilters).
        self._fpanel = null;
        function buildFilterPanel() {
            var panel = el("div", "bkterm-fpanel");
            var defs = []; try { defs = self.term.filters() || []; } catch (e) { }
            if (!defs.length) panel.appendChild(el("div", "bkterm-fnote", "Define filtros en Ajustes › Terminal"));
            var active = [];
            try { active = self.term.activeFilters() || []; } catch (e) { }
            defs.forEach(function (f) {
                var rx = ko.unwrap(f.regex), name = ko.unwrap(f.name);
                var lbl = el("label", "bkterm-fopt");
                var cb = document.createElement("input"); cb.type = "checkbox";
                cb.checked = active.indexOf(rx) >= 0;
                cb.addEventListener("change", function () {
                    var arr = (self.term.activeFilters() || []).slice();
                    var i = arr.indexOf(rx);
                    if (cb.checked && i < 0) arr.push(rx);
                    else if (!cb.checked && i >= 0) arr.splice(i, 1);
                    self.term.activeFilters(arr);
                });
                lbl.appendChild(cb);
                lbl.appendChild(document.createTextNode(" " + name));
                panel.appendChild(lbl);
            });
            return panel;
        }
        function toggleFilters() {
            var btn = document.getElementById("bkterm-filters");
            if (self._fpanel && self._fpanel.parentNode) {
                self._fpanel.parentNode.removeChild(self._fpanel);
                self._fpanel = null;
                if (btn) btn.classList.remove("active");
                return;
            }
            self._fpanel = buildFilterPanel();
            var bar = document.querySelector(".bkterm-bar");
            if (bar) bar.appendChild(self._fpanel);
            if (btn) btn.classList.add("active");
        }

        var GCODES = ["G0","G1","G28","G29","G90","G91","G92","M104","M109","M140","M190",
            "M105","M106","M107","M114","M115","M117","M119","M155","M220","M221","M84",
            "M500","M501","M502","M503","M92","M201","M203","M204","M205","M200","M207","M208",
            "M303","M301","M304","M851","M900","M906","M907","M425","M593","M211","M412"];

        // ¿Está activo marlingcodedocumentation? (su panel se ancla al sendpanel).
        function docPluginPresent() {
            return !!document.getElementById("terminal-marlin-gcode-documentation");
        }
        function injectAutocomplete() {
            // Si el plugin de documentación gcode está presente, NO añadimos nuestro
            // datalist: él aporta su propio autocompletado (más rico) en el input.
            // Evita la fricción de dos autocompletados sobre #terminal-command.
            if (docPluginPresent()) return;
            var inp = document.getElementById("terminal-command");
            if (!inp || document.getElementById("bkterm-gcodes")) return;
            var dl = document.createElement("datalist");
            dl.id = "bkterm-gcodes";
            GCODES.forEach(function (g) { var o = document.createElement("option"); o.value = g + " "; dl.appendChild(o); });
            inp.parentNode.appendChild(dl);
            inp.setAttribute("list", "bkterm-gcodes");
        }

        // Clic en una burbuja de comando: lo coloca en el input y dispara los eventos
        // que marlingcodedocumentation escucha (input/focus/keyup) -> muestra su doc.
        // Sin el plugin, simplemente reedita el comando para reenviarlo.
        function onChatClick(ev) {
            var grp = ev.target.closest && ev.target.closest(".bkt-group.send");
            if (!grp) return;
            var cmd = grp.getAttribute("data-cmd");
            if (!cmd) return;
            var inp = document.getElementById("terminal-command");
            if (!inp) return;
            try {
                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                setter.call(inp, cmd);   // setter nativo -> KO/observers lo ven
            } catch (e) { inp.value = cmd; }
            inp.focus();
            ["input", "keyup", "click"].forEach(function (t) {
                inp.dispatchEvent(new Event(t, { bubbles: true }));
            });
        }

        function setup(nat) {
            if (document.getElementById("bkterm-output")) return;
            var bar = el("div", "bkterm-bar");
            var bChat = el("button", "btn btn-mini", "Chat/raw");
            var bFilt = el("button", "btn btn-mini", "Filtros");
            bFilt.id = "bkterm-filters";
            var bAuto = el("button", "btn btn-mini", "Auto");
            bAuto.id = "bkterm-auto";
            var bClr = el("button", "btn btn-mini", "Limpiar");
            var bClrAll = el("button", "btn btn-mini", "Limpiar todo");
            bChat.addEventListener("click", toggleChat);
            bFilt.addEventListener("click", toggleFilters);
            bAuto.addEventListener("click", toggleAutoscroll);
            bClr.addEventListener("click", clearChat);     // limpia la vista del chat
            bClrAll.addEventListener("click", function () { // limpia el log nativo entero
                try { self.term.clearAllLogs(); } catch (e) { }
            });
            bar.appendChild(bChat); bar.appendChild(bFilt); bar.appendChild(bAuto);
            bar.appendChild(bClr); bar.appendChild(bClrAll);

            // NO usar 'pre-scrollable' (su max-height:340 caparía el resize); el CSS
            // .bkterm ya da height + overflow + resize.
            self._out = el("div", "bkterm");
            self._out.id = "bkterm-output";

            nat.parentNode.insertBefore(bar, nat);
            nat.parentNode.insertBefore(self._out, nat);
            nat.style.display = "none";
            // Oculta los paneles NATIVOS de abajo (filtros, autoscroll, clear): todo
            // vive ahora en la barra/desplegable de arriba, sin duplicar.
            ["terminal-filterpanel", "terminal-autoscroll-panel", "terminal-actions-panel"].forEach(function (id) {
                var e = document.getElementById(id); if (e) e.style.display = "none";
            });

            // --- Altura redimensionable + persistente ---
            try {
                var h = localStorage.getItem("bk_termHeight");
                if (h) self._out.style.height = h;
            } catch (e) { }
            try {
                var _ht = null;
                var ro = new ResizeObserver(function () {
                    var hh = self._out.style.height;
                    if (!hh || hh === _ht) return;     // solo cuando cambia la altura inline (arrastre)
                    _ht = hh;
                    try { localStorage.setItem("bk_termHeight", hh); } catch (e) { }
                });
                ro.observe(self._out);
            } catch (e) { }

            // --- Autoscroll consciente del scroll manual: subir con la rueda desactiva,
            // volver al fondo reactiva. Ignora los scrollTop programáticos (_progScroll).
            self._out.addEventListener("scroll", function () {
                if (_progScroll) return;
                try {
                    var at = isAtBottom();
                    var cur = self.term && self.term.autoscrollEnabled ? !!self.term.autoscrollEnabled() : true;
                    if (at && !cur) self.term.autoscrollEnabled(true);
                    else if (!at && cur) self.term.autoscrollEnabled(false);
                } catch (e) { }
            });

            // --- Autoscroll: restaura el estado persistido + refleja en el botón ---
            try {
                var a = localStorage.getItem("bk_termAutoscroll");
                if (a !== null && self.term.autoscrollEnabled) self.term.autoscrollEnabled(a === "1");
            } catch (e) { }
            function syncAutoBtn() { bAuto.classList.toggle("active", autoscrollOn()); }
            syncAutoBtn();
            try {
                self.term.autoscrollEnabled.subscribe(function (v) {
                    syncAutoBtn();
                    try { localStorage.setItem("bk_termAutoscroll", v ? "1" : "0"); } catch (e) { }
                });
            } catch (e) { }

            self._out.addEventListener("click", onChatClick);
            // Decide el datalist algo más tarde: el panel de marlingcodedocumentation
            // puede registrarse después de nuestro setup.
            setTimeout(injectAutocomplete, 1500);
            // Restaura la selección de filtros (persistida) y re-renderiza el chat
            // cuando cambian los filtros nativos.
            restoreActiveFilters();
            if (self.term && self.term.filterRegex) self.term.filterRegex.subscribe(rebuild);
            if (self.term && self.term.log) {
                self.term.log.subscribe(onLog);
                onLog();
            }
        }

        self.onStartupComplete = function () {
            var c = tcfg();
            try { if (c && ko.unwrap(c.enabled) === false) return; } catch (e) { }
            try { self.showTs = c ? !!ko.unwrap(c.timestamps) : false; } catch (e) { }
            try { var ml = parseInt(ko.unwrap(c.max_lines), 10); if (ml) self._maxGroups = Math.max(100, ml / 2); } catch (e) { }
            var tries = 0;
            (function wait() {
                var nat = document.getElementById("terminal-output");
                if (nat) { setup(nat); return; }
                if (tries++ < 60) setTimeout(wait, 300);
            })();
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: BoostTerminalViewModel,
        dependencies: ["settingsViewModel", "terminalViewModel"],
        optional: ["terminalViewModel"],
        elements: [],
    });
});
