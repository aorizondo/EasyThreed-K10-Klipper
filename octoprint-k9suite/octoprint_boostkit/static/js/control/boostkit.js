/*
 * Boost Kit — ViewModel del panel de control.
 * Babystep (M290), feed/flow (M220/M221), hot-tune, temperatura.
 */
$(function () {
    function BoostKitViewModel(parameters) {
        var self = this;

        self.settings = parameters[0];
        self.printerState = parameters[1];
        self.printerProfiles = parameters[2];

        // ¿El perfil activo tiene cama caliente? Si no, ocultamos la fila de cama y
        // no enviamos M140/M190 (evita el error "No heated bed installed").
        self.hasHeatedBed = ko.pureComputed(function () {
            try {
                var p = self.printerProfiles.currentProfileData();
                if (p && p.heatedBed) return !!ko.unwrap(p.heatedBed);
            } catch (e) { /* perfil aún no disponible */ }
            return true;
        });


        // Valores editables
        self.feedrate = ko.observable(100);
        self.flow = ko.observable(100);


        self.babyIncrement = ko.pureComputed(function () {
            var v = parseFloat(self.settings.settings.plugins.boostkit.baby_increment());
            return isNaN(v) ? 0.025 : v;
        });

        self._post = function (command, payload) {
            return OctoPrint.simpleApiCommand("boostkit", command, payload || {});
        };

        // --- Estado del trabajo (para habilitar/deshabilitar pausas) ---
        function psFlag(name) {
            try { return !!(self.printerState[name] && self.printerState[name]()); }
            catch (e) { return false; }
        }
        self.isPaused = ko.pureComputed(function () { return psFlag("isPaused"); });
        self.isPrinting = ko.pureComputed(function () { return psFlag("isPrinting"); });
        self.hasJob = ko.pureComputed(function () { return self.isPrinting() || self.isPaused(); });

        // --- Hot-tune enabled toggle (navbar) ---
        // El observable se sincroniza con settings.plugins.boostkit.hottune.enabled. Cambiarlo
        // persiste el ajuste vía saveSettings; también refleja notificaciones del backend
        // (`{type:"hottune", event:"..."}`) para que el usuario vea instantáneamente si el
        // ciclo hot-tune falló (no_realtime / no_match) o si una acción quedó gated durante
        // impresión (extrude/load/unload).
        self.hotEnabled = ko.observable(true);
        self._hotSyncing = false;
        function _readHotSetting() {
            try {
                var v = self.settings.settings.plugins.boostkit.hottune.enabled();
                self._hotSyncing = true;
                self.hotEnabled(v === undefined || v === null ? true : !!v);
                self._hotSyncing = false;
            } catch (e) { }
        }
        self.hotEnabled.subscribe(function (v) {
            if (self._hotSyncing) return;
            try {
                self.settings.settings.plugins.boostkit.hottune.enabled(!!v);
                self.settings.saveData();
            } catch (e) { }
        });
        self.toggleHotEnabled = function () { self.hotEnabled(!self.hotEnabled()); };


        // --- Pausa nativa de OctoPrint ---
        self.convPause = function () {
            try {
                if (self.isPaused()) OctoPrint.job.resume();
                else OctoPrint.job.pause();
            } catch (e) { /* sin conexión */ }
        };

        // --- Cancelar INSTANTÁNEO + confirmación (estándar) ---
        self.cancelJob = function () {
            if (!confirm("¿Cancelar la impresión? Se detiene al instante (M410) y se cancela.")) return;
            self._post("job_cancel");
        };

        // --- Babystepping ---
        self.babyUp = function () {
            self._post("babystep", { z: self.babyIncrement() });
        };
        self.babyDown = function () {
            self._post("babystep", { z: -self.babyIncrement() });
        };

        // --- Feedrate (M220) ---
        self.applyFeedrate = function () {
            self._post("feedrate", { value: parseInt(self.feedrate(), 10) });
        };
        self.feedUp = function () {
            var step = num(cfg(["sliders", "feed", "step"], 5), 5);
            self.feedrate(Math.min(300, parseInt(self.feedrate(), 10) + step));
        };
        self.feedDown = function () {
            var step = num(cfg(["sliders", "feed", "step"], 5), 5);
            self.feedrate(Math.max(10, parseInt(self.feedrate(), 10) - step));
        };
        self.feedReset = function () {
            self.feedrate(100);
        };

        // --- Flow (M221) ---
        self.applyFlow = function () {
            self._post("flow", { value: parseInt(self.flow(), 10) });
        };
        self.flowUp = function () {
            var step = num(cfg(["sliders", "flow", "step"], 5), 5);
            self.flow(Math.min(200, parseInt(self.flow(), 10) + step));
        };
        self.flowDown = function () {
            var step = num(cfg(["sliders", "flow", "step"], 5), 5);
            self.flow(Math.max(50, parseInt(self.flow(), 10) - step));
        };
        self.flowReset = function () {
            self.flow(100);
        };

        // --- Tracker de posición (lectura en vivo) ---
        self.tx = ko.observable(0);
        self.ty = ko.observable(0);
        self.tz = ko.observable(0);
        self.theight = ko.observable(0);
        self.tlayer = ko.observable(0);
        self.tmode = ko.observable("-");
        // Label de posición en vivo (del reporte de posición del firmware).
        function pfmt(v) { var n = parseFloat(v); return isNaN(n) ? "–" : n.toFixed(1); }
        self.posLabel = ko.pureComputed(function () {
            return "X " + pfmt(self.tx()) + "  Y " + pfmt(self.ty()) + "  Z " + pfmt(self.tz());
        });
        self.absXYZ = ko.observable(true);   // modo de ejes (G90/G91)
        self.absE = ko.observable(true);     // modo de extrusor (M82/M83)

        // --- Nivelación UBL ---
        self.lvlRunning = ko.observable(false);
        self.lvlStatus = ko.observable("idle");
        self.lvlDetail = ko.observable("");

        // --- Capacidades de nivelación (PRINTER-DEPENDIENTES, detectadas al conectar) ---
        self.lvlCaps = ko.observable({ available: null, system: "unknown", grid_x: null, grid_y: null, slots: null });
        self.lvlAvailable = ko.pureComputed(function () { return self.lvlCaps().available !== false; });
        self.lvlHasSlots = ko.pureComputed(function () { return (self.slotCount() | 0) > 0; });
        self.lvlCapsInfo = ko.pureComputed(function () {
            var c = self.lvlCaps();
            if (c.available === false) return "Esta impresora no reporta nivelación por malla";
            if (c.available == null) return "Detectando capacidades…";
            var sys = ({ ubl: "UBL", bilinear: "Bilineal", mbl: "Mesh (MBL)" })[c.system] || c.system;
            var g = (c.grid_x && c.grid_y) ? (c.grid_x + "×" + c.grid_y) : "?";
            var sl = (c.slots == null) ? "?" : c.slots;
            return sys + " · malla " + g + " · " + sl + " slots" + (c.fw ? " · " + c.fw : "");
        });

        // --- Slots de malla + visor topográfico ---
        self.slots = ko.observableArray([]);
        self.slotCount = ko.observable(0);
        self.selSlot = ko.observable(0);
        self.saveName = ko.observable("");
        self._slotZ = {};               // idx -> matriz Z (para visualizar sin activar)
        self._activeMeshZ = null;       // malla activa más reciente
        self._activeMeshOpts = null;
        self.meshSource = ko.observable("active");   // "active" | "slot<idx>"
        self.meshInfo = ko.observable("sin malla");
        self.slotsSummary = ko.pureComputed(function () {
            var f = 0, list = self.slots();
            for (var i = 0; i < list.length; i++) if (list[i].filled) f++;
            return f + "/" + list.length + " llenos · " + (list.length - f) + " vacíos";
        });

        self.applyCaps = function (caps) {
            if (!caps) return;
            self.lvlCaps(caps);
            if (typeof caps.slots === "number" && caps.slots >= 0) self.slotCount(caps.slots);
        };
        self.applySlots = function (list, count) {
            if (typeof count === "number") self.slotCount(count);
            list = list || [];
            self._slotZ = {};
            var out = [];
            for (var i = 0; i < list.length; i++) {
                var s = list[i] || {};
                if (s.z) self._slotZ[i] = s.z;
                out.push({ idx: i, name: s.name || "", filled: !!s.filled, n: s.n || 0, ts: s.ts || 0 });
            }
            self.slots(out);
        };
        self._renderMesh = function (z, opts, label) {
            if (typeof BKMesh === "undefined" || !z) { self.meshInfo("sin datos"); return; }
            var r = BKMesh.setMesh("bkmesh_canvas", z, opts || {});
            if (r) {
                self.meshInfo(label + " · Δ " + ((r.max - r.min) * 1000).toFixed(0) +
                    " µm (" + r.min.toFixed(3) + " … " + r.max.toFixed(3) + " mm)");
            }
        };
        self.viewActive = function () {
            self.meshSource("active");
            if (self._activeMeshZ) self._renderMesh(self._activeMeshZ, self._activeMeshOpts, "Activa");
            else { self.meshInfo("sin malla activa — nivela primero"); self._post("mesh_get"); }
        };
        self.viewSlot = function (idx) {
            self.meshSource("slot" + idx);
            var z = self._slotZ[idx];
            if (z) self._renderMesh(z, null, "Slot #" + idx);
            else self.meshInfo("slot #" + idx + " vacío");
        };
        self.saveMesh = function () {
            if ((self.slotCount() | 0) <= 0) {
                // Sin slots (bilineal/MBL): persistir la única malla activa con M500.
                self._post("gcode", { cmd: "M500" });
                return;
            }
            var slot = parseInt(self.selSlot(), 10) || 0;
            self._post("mesh_save", { slot: slot, name: self.saveName() });
        };
        self.activateSlot = function (idx) { self._post("mesh_activate", { slot: idx }); };
        self.clearSlot = function (idx) {
            if (confirm("¿Vaciar el slot #" + idx + "?")) self._post("mesh_clear", { slot: idx });
        };
        self.redetectCaps = function () { self._post("leveling_caps", { redetect: true }); };
        // Al cargar la UI, pide las capacidades cacheadas (por si la impresora ya
        // estaba conectada antes de abrir/recargar la página).
        self.onStartupComplete = function () {
            self._post("leveling_caps", {});
            self.rebuildTempControls();
            self.rebuildFans();
            // Inyecta el contenido de Estado DENTRO del panel de estado NATIVO de
            // OctoPrint (patrón SkipTo), en vez de una card propia.
            self._mountStatus();
            _readHotSetting();
            self._initJogStep();
        };
        self.onSettingsShown = function () { _readHotSetting(); };
        self.onSettingsHidden = function () { _readHotSetting(); };
        self._mountStatus = function () {
            var tries = 0;
            (function place() {
                var src = document.getElementById("gen_plugin_boostkit_status");
                var dst = document.querySelector("#state .accordion-inner");
                if (src && dst) {
                    if (src.parentNode !== dst) dst.appendChild(src);
                    return;
                }
                if (tries++ < 40) setTimeout(place, 300);
            })();
        };

        self.onDataUpdaterPluginMessage = function (plugin, message) {
            if (plugin !== "boostkit" || !message) return;
            if (message.type === "hottune") {
                // Feedback transitorio del ciclo hot-tune.
                var ev = message.event;
                try {
                    if (ev === "no_realtime") {
                        new PNotify({
                            title: "Hot-tune",
                            text: "Firmware sin realtime reporting (M114 R E timeout) — recolocación solo X/Y",
                            type: "notice", hide: true, delay: 4000
                        });
                    } else if (ev === "no_match") {
                        new PNotify({
                            title: "Hot-tune",
                            text: "No se encontró segmento — posible costura al reanudar",
                            type: "notice", hide: true, delay: 4000
                        });
                    } else if (ev === "error") {
                        new PNotify({
                            title: "Hot-tune",
                            text: "Error: " + (message.reason || "?"),
                            type: "error", hide: true, delay: 5000
                        });
                    } else {
                        new PNotify({
                            title: "Hot-tune",
                            text: "Aplicado en caliente: " + (message.cmds || []).join("  ·  "),
                            type: "info", hide: true, delay: 2500
                        });
                    }
                } catch (e) { /* PNotify no disponible */ }
                return;
            }
            if (message.type === "action_gated") {
                try {
                    new PNotify({
                        title: "Acción bloqueada",
                        text: "'" + (message.cmd || "?") + "' no está disponible durante la impresión",
                        type: "notice", hide: true, delay: 3500
                    });
                } catch (e) { }
                return;
            }
            if (message.type === "trackstate") {
                self.applyTrackState(message.state || {});
            } else if (message.type === "leveling") {
                var l = message.state || {};
                self.lvlRunning(!!l.running);
                self.lvlStatus(l.status || "idle");
                self.lvlDetail(l.detail || "");
            } else if (message.type === "slots") {
                self.applySlots(message.slots, message.count);
            } else if (message.type === "leveling_caps") {
                self.applyCaps(message.caps);
            } else if (message.type === "mesh") {
                var mm = message.mesh || {};
                if (mm.z) {
                    self._activeMeshZ = mm.z;
                    self._activeMeshOpts = { xs: mm.xs, ys: mm.ys };
                    if (self.meshSource() === "active") self._renderMesh(mm.z, self._activeMeshOpts, "Activa");
                }
            } else if (message.type === "limits") {
                self.applyLimits(message.limits || {});
            } else if (message.type === "fwstate") {
                self.applyFwState(message.fw || {});
            } else if (message.type === "objects") {
                self.applyObjects(message);
            } else if (message.type === "skip_result") {
                self.skipBusy(false);
                self.skipMsg(message.ok ? ("Salto OK: " + (message.file || "")) : ("Error: " + (message.msg || "")));
            } else if (message.type === "possync") {
                // cliente recién cargado: estado completo del firmware + modo.
                if (message.state) self.applyTrackState(message.state);
                if (message.limits) self.applyLimits(message.limits);
                if (message.fw) self.applyFwState(message.fw);
                if (message.leveling_caps) self.applyCaps(message.leveling_caps);
                if (message.slots) self.applySlots(message.slots, message.slot_count);
                if (message.leveling) {
                    self.lvlRunning(!!message.leveling.running);
                    self.lvlStatus(message.leveling.status || "idle");
                    self.lvlDetail(message.leveling.detail || "");
                }
                self._applyingFw = true;
                if (message.feed_factor != null) self.feedrate(message.feed_factor);
                if (message.flow_factor != null) self.flow(message.flow_factor);
                self._applyingFw = false;
            }
        };

        self.applyTrackState = function (s) {
            if (s.x != null) self.tx(s.x);
            if (s.y != null) self.ty(s.y);
            self.tz(s.z);
            self.theight(s.height);
            self.tlayer(s.layer);
            self.absXYZ(!!s.abs_xyz);
            self.absE(!!s.abs_e);
            self.tmode(
                (s.abs_xyz ? "XYZ:abs" : "XYZ:rel") + " / " +
                (s.abs_e ? "E:abs" : "E:rel") + (s.vase ? " / vase" : "")
            );
        };

        self.startLevel = function () {
            if (!confirm("¿Sonda DESPLEGADA? La nivelación va a homear y sondear. Asegúrate de que el brazo esté abajo.")) return;
            self._post("level", { home: true });
            self.lvlRunning(true);
            self.lvlStatus("arrancando");
        };
        self.cancelLevel = function () {
            self._post("level_cancel");
        };


        // ================= PANEL DE CONTROL CONSOLIDADO =================
        // Bump de reactividad para cfg(): OctoPrint crea settings.settings (ko.mapping) solo
        // cuando el primer requestData completa, y los computeds que leen cfg se evalúan por
        // primera vez durante el binding del DOM (antes del AJAX) -> sin dependencias KO ->
        // quedan cacheados con el default para siempre. _bkCfgTick se dispara cuando
        // settings.firstRequest (promesa jQuery que fromResponse resuelve DESPUÉS del mapping)
        // completa, forzando la re-evaluación con el árbol ya presente; entonces la hoja
        // observable (p.ej. feed.step) registra su propia dependencia y el slider queda vivo.
        self._bkCfgTick = ko.observable(0);
        self._bkCfgArmed = false;
        function cfg(path, def) {
            try {
                var settings = self.settings;
                if (settings.firstRequest && !self._bkCfgArmed) {
                    self._bkCfgArmed = true;
                    settings.firstRequest.always(function () {
                        // setTimeout: firstRequest puede estar YA resuelto al primer cfg() (el
                        // always dispararía síncronamente DENTRO de la evaluación del computed,
                        // rompiendo KO). Diferirlo garantiza que la re-evaluación ocurra en un
                        // tick aparte, con el árbol de settings ya mapeado.
                        setTimeout(function () {
                            self._bkCfgArmed = false;
                            self._bkCfgTick(self._bkCfgTick() + 1);
                        }, 0);
                    });
                }
                self._bkCfgTick();
                // settings.receiving() (observable del SettingsViewModel) cubre re-cargas de
                // settings posteriores (onEventSettingsUpdated -> requestData -> ko.mapping
                // actualiza las hojas observables, que disparan su propia dependencia).
                if (settings.receiving && ko.isObservable(settings.receiving)) settings.receiving();
                var o = ko.unwrap(settings.settings.plugins.boostkit.control);
                for (var i = 0; i < path.length; i++) {
                    o = ko.unwrap(o);
                    o = o[path[i]];
                }
                return ko.unwrap(o);
            } catch (e) { return def; }
        }
        function num(v, d) { var n = parseFloat(v); return isNaN(n) ? d : n; }
        function round1(v) { return v == null ? 0 : Math.round(v * 10) / 10; }

        // --- Temperaturas en vivo (OctoPrint llama fromCurrentData en todos los VM) ---
        self.hotendActual = ko.observable(0); self.hotendTarget = ko.observable(0);
        self.bedActual = ko.observable(0); self.bedTarget = ko.observable(0);

        // ===== GRÁFICA DE TEMPERATURA (canvas puro) + análisis de pendiente °C/s =====
        // Portado/fusionado del módulo bktemp para vivir dentro del panel de control.
        function tlinregress(pts) {
            var n = pts.length; if (n < 2) return null;
            var st = 0, sv = 0, i;
            for (i = 0; i < n; i++) { st += pts[i].t; sv += pts[i].v; }
            var mt = st / n, mv = sv / n, numr = 0, den = 0, ssTot = 0;
            for (i = 0; i < n; i++) { var dt = pts[i].t - mt, dvv = pts[i].v - mv; numr += dt * dvv; den += dt * dt; ssTot += dvv * dvv; }
            if (den === 0) return null;
            var slope = numr / den, intercept = mv - slope * mt, ssRes = 0;
            for (i = 0; i < n; i++) { var pred = slope * pts[i].t + intercept, rr = pts[i].v - pred; ssRes += rr * rr; }
            return { slope: slope, intercept: intercept, r2: ssTot > 0 ? 1 - ssRes / ssTot : 1, n: n,
                dt: pts[n - 1].t - pts[0].t, dv: pts[n - 1].v - pts[0].v, t0: pts[0].t, t1: pts[n - 1].t };
        }
        var TPAL = { tool0: "#e74c3c", tool1: "#e67e22", tool2: "#f1c40f", bed: "#3498db", chamber: "#9b59b6" };
        var TCYCLE = ["#1abc9c", "#2ecc71", "#34495e", "#d35400", "#7f8c8d"];
        self._tsamples = []; self._tkeys = []; self._tcolor = {}; self._tlastT = 0;
        self._twindow = 120; self._tview = { start: 0, end: 0 }; self._tfollow = true;
        self._tsel = null; self._tdrag = null; self._tcanvas = null; self._tctx = null; self._tneedsDraw = false; self._thoverX = null;
        self.analyzeMode = ko.observable(false);
        self.analyzeSeries = ko.observable("tool0");
        self.seriesList = ko.observableArray([]);
        self.tLive = ko.observable(true);
        self.tResult = ko.observable(null);
        self.tHover = ko.observable("");

        function tNorm(t) { return t > 1e12 ? t / 1000.0 : t; }
        function tReal(k) { return /^(tool\d+|bed|chamber)$/.test(k); }
        function tRegKey(k) {
            self._tkeys.push(k);
            self._tcolor[k] = TPAL[k] || TCYCLE[(self._tkeys.length - 1) % TCYCLE.length];
            self.seriesList(self._tkeys.map(function (key) { return { key: key, label: key, color: self._tcolor[key] }; }));
            if (self._tkeys.indexOf(self.analyzeSeries()) < 0) self.analyzeSeries(k);
        }
        function tPrune() {
            var hist = 600;
            try { hist = parseInt(self.settings.settings.plugins.boostkit.temp.history_seconds(), 10) || 600; } catch (e) { }
            var cut = self._tlastT - hist, i = 0;
            while (i < self._tsamples.length && self._tsamples[i].t < cut) i++;
            if (i > 0) self._tsamples.splice(0, i);
            if (self._tsamples.length > 6000) self._tsamples.splice(0, self._tsamples.length - 6000);
        }
        function tSnap() { if (self._tsamples.length) { self._tview.end = self._tlastT; self._tview.start = self._tview.end - self._twindow; } }
        function tIngest(temps, replace) {
            if (!temps || !temps.length) return;
            if (replace) { self._tsamples = []; self._tlastT = 0; }
            for (var i = 0; i < temps.length; i++) {
                var s = temps[i], t = tNorm(s.time);
                if (!replace && t <= self._tlastT) continue;
                var vals = {};
                for (var k in s) {
                    if (k === "time") continue;
                    var o = s[k];
                    if (o && typeof o === "object" && ("actual" in o || "target" in o) && tReal(k)) {
                        vals[k] = { a: o.actual, g: o.target };
                        if (self._tkeys.indexOf(k) < 0) tRegKey(k);
                    }
                }
                self._tsamples.push({ t: t, vals: vals }); self._tlastT = t;
            }
            tPrune();
            var last = self._tsamples[self._tsamples.length - 1];
            if (last) {
                if (last.vals.tool0) { self.hotendActual(round1(last.vals.tool0.a)); self.hotendTarget(round1(last.vals.tool0.g)); }
                if (last.vals.bed) { self.bedActual(round1(last.vals.bed.a)); self.bedTarget(round1(last.vals.bed.g)); }
                if (self.tempControls) self.tempControls().forEach(function (c) {
                    var v = last.vals[c.key];
                    if (!v) return;
                    c.actual(round1(v.a)); c.target(round1(v.g));
                    // enlaza el slider al objetivo reportado (comandos / auto-report),
                    // salvo que el usuario lo esté ajustando ahora mismo.
                    if (Date.now() - (c.lastEdit || 0) > 1500) c.set(round1(v.g));
                });
            }
            if (self._tfollow) tSnap();
            self.tDraw();
        }
        self.fromHistoryData = function (data) { tIngest(data.temps, true); };
        self.fromCurrentData = function (data) { tIngest(data.temps, false); };

        self.tToggleLive = function () { self._tfollow = !self._tfollow; self.tLive(self._tfollow); if (self._tfollow) { tSnap(); self.tDraw(); } };
        self.tResetView = function () { self._tfollow = true; self.tLive(true); self._tsel = null; self.tResult(null); tSnap(); self.tDraw(); };
        self.tClearSel = function () { self._tsel = null; self.tResult(null); self.tDraw(); };
        self.analyzeSeries.subscribe(function () { tRecompute(); });
        function tRecompute() {
            if (!self._tsel) { self.tResult(null); return; }
            var key = self.analyzeSeries(), lo = Math.min(self._tsel.t0, self._tsel.t1), hi = Math.max(self._tsel.t0, self._tsel.t1), pts = [];
            for (var i = 0; i < self._tsamples.length; i++) {
                var s = self._tsamples[i]; if (s.t < lo || s.t > hi) continue;
                var v = s.vals[key]; if (v && v.a != null) pts.push({ t: s.t, v: v.a });
            }
            var reg = tlinregress(pts);
            if (!reg) { self.tResult({ ok: false, key: key }); self.tDraw(); return; }
            self.tResult({ ok: true, key: key, slope: reg.slope, dv: reg.dv, dt: reg.dt, r2: reg.r2, n: reg.n, reg: reg });
            self.tDraw();
        }
        self.tDraw = function () { if (self._tneedsDraw) return; self._tneedsDraw = true; window.requestAnimationFrame(function () { self._tneedsDraw = false; tDrawNow(); }); };
        function tLayout() { var c = self._tcanvas; return { L: 42, R: 10, T: 8, B: 22, W: c.clientWidth, H: c.clientHeight }; }
        function tX(t, lo) { var v = self._tview; return lo.L + (t - v.start) / (v.end - v.start) * (lo.W - lo.L - lo.R); }
        function tPx2T(px, lo) { var v = self._tview; return v.start + (px - lo.L) / (lo.W - lo.L - lo.R) * (v.end - v.start); }
        function tY(temp, lo, ymin, ymax) { return lo.T + (1 - (temp - ymin) / (ymax - ymin)) * (lo.H - lo.T - lo.B); }
        function tYRange() {
            var ymin = Infinity, ymax = -Infinity, i, k;
            for (i = 0; i < self._tsamples.length; i++) {
                var s = self._tsamples[i]; if (s.t < self._tview.start - 1 || s.t > self._tview.end + 1) continue;
                for (k = 0; k < self._tkeys.length; k++) {
                    var v = s.vals[self._tkeys[k]]; if (!v) continue;
                    if (v.a != null) { ymin = Math.min(ymin, v.a); ymax = Math.max(ymax, v.a); }
                    if (v.g != null && v.g > 0) { ymin = Math.min(ymin, v.g); ymax = Math.max(ymax, v.g); }
                }
            }
            if (!isFinite(ymin)) { ymin = 0; ymax = 250; }
            var pad = Math.max(5, (ymax - ymin) * 0.08); ymin = Math.max(0, ymin - pad); ymax = ymax + pad;
            if (ymax - ymin < 10) ymax = ymin + 10;
            return { ymin: ymin, ymax: ymax };
        }
        function tTicks(min, max, count) {
            var range = max - min, raw = range / count, mag = Math.pow(10, Math.floor(Math.log10(raw))), norm = raw / mag, step;
            step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
            var ticks = [], t = Math.ceil(min / step) * step;
            for (; t <= max; t += step) ticks.push(t);
            return ticks;
        }
        function tSpan() { return self._tview.end - self._tview.start; }
        function tDrawNow() {
            var c = self._tcanvas; if (!c || !c.clientWidth) return;
            var dpr = window.devicePixelRatio || 1;
            if (c.width !== c.clientWidth * dpr || c.height !== c.clientHeight * dpr) { c.width = c.clientWidth * dpr; c.height = c.clientHeight * dpr; }
            var ctx = self._tctx; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            var lo = tLayout(); ctx.clearRect(0, 0, lo.W, lo.H);
            if (self._tview.end <= self._tview.start) { tSnap(); if (self._tview.end <= self._tview.start) return; }
            var yr = tYRange(), ymin = yr.ymin, ymax = yr.ymax;
            ctx.font = "10px sans-serif"; ctx.textBaseline = "middle";
            var yt = tTicks(ymin, ymax, 6);
            ctx.strokeStyle = "rgba(127,127,127,0.18)"; ctx.fillStyle = "#999"; ctx.lineWidth = 1; ctx.textAlign = "right";
            for (var i = 0; i < yt.length; i++) { var yp = tY(yt[i], lo, ymin, ymax); ctx.beginPath(); ctx.moveTo(lo.L, yp); ctx.lineTo(lo.W - lo.R, yp); ctx.stroke(); ctx.fillText(Math.round(yt[i]) + "°", lo.L - 5, yp); }
            ctx.textAlign = "center"; ctx.textBaseline = "top";
            var span = tSpan(), xstep = span <= 60 ? 10 : span <= 180 ? 30 : span <= 600 ? 60 : span <= 1800 ? 300 : 600;
            var firstRel = Math.ceil((self._tview.start - self._tlastT) / xstep) * xstep;
            for (var r = firstRel; r <= (self._tview.end - self._tlastT) + 0.001; r += xstep) {
                var tt = self._tlastT + r, xp = tX(tt, lo); if (xp < lo.L || xp > lo.W - lo.R) continue;
                ctx.strokeStyle = "rgba(127,127,127,0.12)"; ctx.beginPath(); ctx.moveTo(xp, lo.T); ctx.lineTo(xp, lo.H - lo.B); ctx.stroke();
                ctx.fillStyle = "#999"; ctx.fillText(r === 0 ? "0s" : r + "s", xp, lo.H - lo.B + 4);
            }
            if (self._tsel) {
                var xa = tX(self._tsel.t0, lo), xb = tX(self._tsel.t1, lo);
                ctx.fillStyle = "rgba(52,152,219,0.15)"; ctx.fillRect(Math.min(xa, xb), lo.T, Math.abs(xb - xa), lo.H - lo.T - lo.B);
                ctx.strokeStyle = "rgba(52,152,219,0.6)"; ctx.setLineDash([3, 3]); ctx.beginPath();
                ctx.moveTo(xa, lo.T); ctx.lineTo(xa, lo.H - lo.B); ctx.moveTo(xb, lo.T); ctx.lineTo(xb, lo.H - lo.B); ctx.stroke(); ctx.setLineDash([]);
            }
            ctx.save(); ctx.beginPath(); ctx.rect(lo.L, lo.T, lo.W - lo.L - lo.R, lo.H - lo.T - lo.B); ctx.clip();
            for (var k = 0; k < self._tkeys.length; k++) { var key = self._tkeys[k], col = self._tcolor[key]; tLine(ctx, lo, ymin, ymax, key, "g", col, true); tLine(ctx, lo, ymin, ymax, key, "a", col, false); }
            var res = self.tResult();
            if (res && res.ok && res.reg) {
                var g = res.reg; ctx.strokeStyle = "#000"; ctx.lineWidth = 2; ctx.setLineDash([6, 3]); ctx.globalAlpha = 0.8; ctx.beginPath();
                ctx.moveTo(tX(g.t0, lo), tY(g.slope * g.t0 + g.intercept, lo, ymin, ymax)); ctx.lineTo(tX(g.t1, lo), tY(g.slope * g.t1 + g.intercept, lo, ymin, ymax));
                ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
            }
            ctx.restore();
            if (self._thoverX != null) tHoverDraw(ctx, lo, ymin, ymax);
        }
        function tLine(ctx, lo, ymin, ymax, key, field, col, dashed) {
            ctx.strokeStyle = col; ctx.lineWidth = dashed ? 1 : 1.8; ctx.globalAlpha = dashed ? 0.55 : 1; ctx.setLineDash(dashed ? [4, 4] : []);
            ctx.beginPath(); var started = false, sp = tSpan();
            for (var i = 0; i < self._tsamples.length; i++) {
                var s = self._tsamples[i]; if (s.t < self._tview.start - sp || s.t > self._tview.end + sp) continue;
                var v = s.vals[key]; if (!v || v[field] == null || (field === "g" && v[field] <= 0)) { started = false; continue; }
                var xp = tX(s.t, lo), yp = tY(v[field], lo, ymin, ymax);
                if (!started) { ctx.moveTo(xp, yp); started = true; } else ctx.lineTo(xp, yp);
            }
            ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
        }
        function tHoverDraw(ctx, lo, ymin, ymax) {
            var t = tPx2T(self._thoverX, lo), best = null, bd = Infinity;
            for (var i = 0; i < self._tsamples.length; i++) { var d = Math.abs(self._tsamples[i].t - t); if (d < bd) { bd = d; best = self._tsamples[i]; } }
            if (!best) return;
            var xp = tX(best.t, lo);
            ctx.strokeStyle = "rgba(127,127,127,0.6)"; ctx.setLineDash([2, 2]); ctx.beginPath(); ctx.moveTo(xp, lo.T); ctx.lineTo(xp, lo.H - lo.B); ctx.stroke(); ctx.setLineDash([]);
            var rel = Math.round(best.t - self._tlastT), parts = [(rel === 0 ? "ahora" : rel + "s")];
            for (var k = 0; k < self._tkeys.length; k++) { var key = self._tkeys[k], v = best.vals[key]; if (!v) continue; parts.push(key + " " + (v.a != null ? v.a.toFixed(1) : "–") + "°" + (v.g ? "/" + v.g.toFixed(0) + "°" : "")); }
            self.tHover(parts.join("   "));
        }
        self._tBindCanvas = function () {
            var c = self._tcanvas; if (!c || c._bkbound) return; c._bkbound = true;
            c.addEventListener("wheel", function (e) {
                e.preventDefault(); var lo = tLayout(), tc = tPx2T(e.offsetX, lo), f = e.deltaY < 0 ? 0.8 : 1.25;
                var ns = tc - (tc - self._tview.start) * f, ne = tc + (self._tview.end - tc) * f; if (ne - ns < 5) return;
                self._tview.start = ns; self._tview.end = ne; self._twindow = ne - ns; self._tfollow = false; self.tLive(false); self.tDraw();
            }, { passive: false });
            c.addEventListener("mousedown", function (e) {
                var lo = tLayout();
                if (self.analyzeMode() || e.shiftKey) { self._tdrag = { mode: "sel", t0: tPx2T(e.offsetX, lo) }; self._tsel = { t0: self._tdrag.t0, t1: self._tdrag.t0 }; }
                else { self._tdrag = { mode: "pan", x0: e.offsetX, vs: self._tview.start, ve: self._tview.end }; self._tfollow = false; self.tLive(false); }
            });
            window.addEventListener("mousemove", function (e) {
                if (!self._tcanvas) return; var rect = self._tcanvas.getBoundingClientRect(), ox = e.clientX - rect.left, oy = e.clientY - rect.top, lo = tLayout();
                if (self._tdrag) {
                    if (self._tdrag.mode === "pan") { var span = self._tview.end - self._tview.start, dpx = ox - self._tdrag.x0, dt = -dpx / (lo.W - lo.L - lo.R) * span; self._tview.start = self._tdrag.vs + dt; self._tview.end = self._tdrag.ve + dt; }
                    else { self._tsel.t1 = tPx2T(ox, lo); }
                    self.tDraw();
                } else if (ox >= lo.L && ox <= lo.W - lo.R && oy >= lo.T && oy <= lo.H - lo.B) { self._thoverX = ox; self.tDraw(); }
                else if (self._thoverX != null) { self._thoverX = null; self.tHover(""); self.tDraw(); }
            });
            window.addEventListener("mouseup", function () { if (self._tdrag && self._tdrag.mode === "sel") tRecompute(); self._tdrag = null; });
            c.addEventListener("dblclick", function () { self.tResetView(); });
        };
        self._tInit = function () {
            if (self._tcanvas) { self.tDraw(); return; }
            var c = document.getElementById("bk_temp_canvas"); if (!c) return;
            self._tcanvas = c; self._tctx = c.getContext("2d");
            try { var w = parseInt(self.settings.settings.plugins.boostkit.temp.default_window(), 10); if (w > 0) { self._twindow = w; tSnap(); } } catch (e) { }
            self._tBindCanvas();
            if (typeof ResizeObserver !== "undefined") new ResizeObserver(function () { self.tDraw(); }).observe(c);
            self.tDraw();
        };

        // --- Set de temperatura + perfiles de OctoPrint ---
        self.hotendSet = ko.observable(0);
        self.bedSet = ko.observable(0);
        self.applyHotend = function () { self._post("settemp", { heater: "tool0", value: num(self.hotendSet(), 0) }); };
        self.applyBed = function () {
            if (!self.hasHeatedBed()) return;   // sin cama: no enviar M140
            self._post("settemp", { heater: "bed", value: num(self.bedSet(), 0) });
        };
        self.coolDown = function () {
            self._post("settemp", { heater: "tool0", value: 0 });
            if (self.hasHeatedBed()) self._post("settemp", { heater: "bed", value: 0 });
        };
        // Perfiles de temperatura NATIVOS de OctoPrint (Ajustes › Temperaturas).
        self.octoProfiles = ko.pureComputed(function () {
            try { return self.settings.temperature_profiles() || []; } catch (e) { return []; }
        });
        self.applyOctoProfile = function (p) {
            var t = num(ko.unwrap(p.extruder), 0), b = num(ko.unwrap(p.bed), 0);
            self.hotendSet(t);
            self._post("settemp", { heater: "tool0", value: t });
            if (self.hasHeatedBed()) { self.bedSet(b); self._post("settemp", { heater: "bed", value: b }); }
            if (self.tempControls) self.tempControls().forEach(function (c) {
                if (c.key === "tool0") { c.set(t); c.lastEdit = Date.now(); }
                else if (c.key === "bed" && self.hasHeatedBed()) { c.set(b); c.lastEdit = Date.now(); }
            });
        };

        // ===== Controles de temperatura CAPABILITY-AWARE (hotend(s)/cama/cámara) =====
        // Se construyen según el perfil activo de OctoPrint (nº de extrusores, cama y
        // cámara calientes). Si un elemento no está capacitado, ni se muestra ni se
        // setea. El máximo de cada slider sale de Ajustes (temp.*_max): Marlin no
        // reporta los *_MAXTEMP por gcode.
        // Controles de temperatura: FÁBRICA COMPARTIDA (window.BK, en bkcore.js) para no
        // duplicar la lógica con bktemp.js. Incluye el comportamiento 0=apagado / piso
        // activo 150 / tope 280 del hotend. El bucle de datos en vivo (tIngest) sigue
        // aquí y opera sobre self.tempControls.
        var _tc = BK.makeTempControls(self.settings, self.printerProfiles, self._post);
        self.tempControls = _tc.controls;
        self.markTempEdit = _tc.markEdit;      // marca edición (no pisar el slider)
        self.onTempInput = _tc.onInput;        // arrastre: colapsa la zona muerta 1..piso
        self.applyTempControl = _tc.apply;     // confirmar/Set: colapsa y envía el objetivo
        self.rebuildTempControls = _tc.rebuild;

        // --- Jog ---
        self.jogXYSteps = ko.pureComputed(function () { return cfg(["jog_xy"], [0.1, 1, 10, 50]) || []; });
        self.jogZSteps = ko.pureComputed(function () { return cfg(["jog_z"], [0.05, 0.1, 1, 10]) || []; });
        self.jogStepXY = ko.observable(10);
        self.jogStepZ = ko.observable(1);
        self.selXY = function (v) { self.jogStepXY(ko.unwrap(v)); };
        self.selZ = function (v) { self.jogStepZ(ko.unwrap(v)); };
        // Inicialización del paso de jog desde los defaults de settings (índice 2).
        self._initJogStep = function () {
            var stepsXY = self.jogXYSteps(), stepsZ = self.jogZSteps();
            if (stepsXY.length) self.jogStepXY(stepsXY[2] || stepsXY[0]);
            if (stepsZ.length) self.jogStepZ(stepsZ[2] || stepsZ[0]);
        };
        // Feedrate del jog (mm/min) AJUSTABLE por slider+input. Rango configurable
        // (jog_feed_min/max, por defecto 100–50000). Se envía como F del G1; el backend
        // vuelve a clampear. XY y Z tienen su propia velocidad (semilla desde settings).
        self.jogFeedMin = num(cfg(["jog_feed_min"], 100), 100);
        self.jogFeedMax = num(cfg(["jog_feed_max"], 50000), 50000);
        function clampFeed(v) { return Math.max(self.jogFeedMin, Math.min(self.jogFeedMax, Math.round(num(v, 3000)))); }
        self.jogFeedXY = ko.observable(clampFeed(cfg(["jog_speed_xy"], 3000)));
        self.jogFeedZ = ko.observable(clampFeed(cfg(["jog_speed_z"], 600)));
        self.jogFeedXY.subscribe(function (v) { var c = clampFeed(v); if (c !== v) self.jogFeedXY(c); });
        self.jogFeedZ.subscribe(function (v) { var c = clampFeed(v); if (c !== v) self.jogFeedZ(c); });
        self.jog = function (x, y, z) {
            var payload = {};
            if (x) payload.x = x * num(self.jogStepXY(), 10);
            if (y) payload.y = y * num(self.jogStepXY(), 10);
            if (z) { payload.z = z * num(self.jogStepZ(), 1); payload.speed = num(self.jogFeedZ(), 600); }
            else payload.speed = num(self.jogFeedXY(), 3000);
            self._post("jog", payload);
        };
        self.home = function (axes) { self._post("home", axes ? { axes: axes } : {}); };

        self.motorsOff = function () { self._post("motors_off"); };

        // --- Extrusión / filamento ---
        self.extrudeAmounts = ko.pureComputed(function () { return cfg(["extrude_amounts"], [5, 10, 50, 100]) || []; });
        self.extrudeAmount = ko.observable(10);          // input numérico (mm)
        self.extrudeSpeed = ko.observable(num(cfg(["extrude_speed"], 300), 300));  // mm/min
        self.selExtrude = function (v) { self.extrudeAmount(ko.unwrap(v)); };
        self.doExtrude = function () { self._post("extrude", { amount: num(self.extrudeAmount(), 0), speed: num(self.extrudeSpeed(), 300) }); };
        self.doRetract = function () { self._post("extrude", { amount: -num(self.extrudeAmount(), 0), speed: num(self.extrudeSpeed(), 300) }); };
        // Parámetros de carga/descarga editables (semilla desde settings).
        self.loadLen = ko.observable(num(cfg(["load", "len"], 90), 90));
        self.loadSpeed = ko.observable(num(cfg(["load", "speed"], 600), 600));
        self.loadPrime = ko.observable(num(cfg(["load", "prime"], 10), 10));
        self.unloadLen = ko.observable(num(cfg(["unload", "len"], 95), 95));
        self.unloadSpeed = ko.observable(num(cfg(["unload", "speed"], 600), 600));
        self.loadFilament = function () { self._post("load", { len: num(self.loadLen(), 90), speed: num(self.loadSpeed(), 600), prime: num(self.loadPrime(), 10) }); };
        self.unloadFilament = function () { self._post("unload", { len: num(self.unloadLen(), 95), speed: num(self.unloadSpeed(), 600) }); };
        // Retracción/recuperación por firmware (usan los parámetros M207/M208).
        self.fwRetract = function () { self._post("gcode", { cmd: "G10" }); };
        self.fwRecover = function () { self._post("gcode", { cmd: "G11" }); };
        // Autoretract por firmware (M209 S1/S0): convierte las retracciones del
        // gcode en G10/G11 (usando los parámetros M207/M208).
        self.autoRetract = ko.observable(false);
        self.setAutoRetract = function (on) {
            self.autoRetract(!!on);
            self._post("gcode", { cmd: on ? "M209 S1" : "M209 S0" });
        };

        // Extrusión en frío (M302): permitir/prohibir + temperatura mínima.
        self.coldAllow = ko.observable(false);
        self.coldMinTemp = ko.observable(170);
        self.setColdExtrude = function (allow) {
            self.coldAllow(allow);
            self._post("cold_extrude", { allow: allow, min_temp: num(self.coldMinTemp(), 170) });
        };

        // --- Jogwheel (rueda SVG copiada del plugin, enrutada a MI backend que
        //     preserva el modo abs/rel). El template llama jogAxesClick/jogHomeClick. ---
        self.jogHomeClick = function (g) {
            var axes = [];
            if (/X/i.test(g)) axes.push("x");
            if (/Y/i.test(g)) axes.push("y");
            if (/Z/i.test(g)) axes.push("z");
            self._post("home", axes.length ? { axes: axes } : {});
        };
        self.jogAxesClick = function (data) {
            var dir = (data.slice(0, 1) || "").toLowerCase();
            var dist = parseFloat(data.slice(1)) || 0;
            if (!dir || !dist) return;
            var p = {};
            p[dir] = dist;
            p.speed = (dir === "z") ? num(self.jogFeedZ(), 600) : num(self.jogFeedXY(), 3000);
            self._post("jog", p);   // jog mode-preserving del backend
        };

        // --- Numpad como jog (teclado físico -> API) ---------------------------
        // Modo normal (discreto): cada pulsación mueve un paso fijo.
        // Modo realtime: movimiento continuo mientras se mantiene pulsado (M410 al soltar).
        // Durante impresión: completamente desactivado (ambos modos).
        self.realtimeJog = ko.observable(false);
        try {
            var _rt = localStorage.getItem("bk_realtimeJog");
            self.realtimeJog(_rt === "1");
        } catch (e) { }
        self.realtimeJog.subscribe(function (v) {
            try { localStorage.setItem("bk_realtimeJog", v ? "1" : "0"); } catch (e) { }
        });
        self._rtMovingAxis = null;   // eje actual en movimiento realtime (null = parado)

        self.numpadJog = ko.observable(false);
        try {
            var _np = localStorage.getItem("bk_numpadJog");
            if (_np === null) {
                var d = false;
                try { d = !!ko.unwrap(self.settings.settings.plugins.boostkit.numpad.default_on); } catch (e) { }
                self.numpadJog(d);
            } else {
                self.numpadJog(_np === "1");
            }
        } catch (e) { }
        self.numpadJog.subscribe(function (v) {
            try { localStorage.setItem("bk_numpadJog", v ? "1" : "0"); } catch (e) { }
        });
        // Toggle del numpad-jog (bindeado en la card minijog del sidebar). Antes faltaba
        // -> binding roto que podía tumbar los bindings de esa card.
        self.toggleNumpad = function () { self.numpadJog(!self.numpadJog()); };
        self._numpadLast = 0;
        self._numpadActive = function () {
            var el = document.activeElement;
            if (el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable)) return false;
            try {
                if (!self.printerState.isOperational || !self.printerState.isOperational()) return false;
                if (self.printerState.isPrinting && self.printerState.isPrinting()) return false;
            } catch (e) { }
            return true;
        };
        self._numpadIndex = ko.observable(2);
        self._numpadCycleStep = function (dir) {
            var stepsXY = self.jogXYSteps(), stepsZ = self.jogZSteps();
            if (!stepsXY.length || !stepsZ.length) return;
            var len = Math.min(stepsXY.length, stepsZ.length);
            var i = (self._numpadIndex() + dir + len) % len;   // wrap en ambos extremos
            self._numpadIndex(i);
            self.jogStepXY(stepsXY[i]);
            self.jogStepZ(stepsZ[i]);
        };
        // Indicador del paso actual (visible solo en modo JOG del numpad).
        self.stepLabel = ko.pureComputed(function () {
            function fmt(v) {
                var n = num(v, 0);
                return n < 1 ? String(n) : String(Math.round(n));
            }
            return "XY: " + fmt(self.jogStepXY()) + "mm · Z: " + fmt(self.jogStepZ()) + "mm";
        });
        self._onNumpadKey = function (ev) {
            if (!self.numpadJog()) return;
            if (!ev.code || ev.code.indexOf("Numpad") !== 0) return;
            if (!self._numpadActive()) return;

            // --- Modo realtime: keydown inicia movimiento, ignora repeticiones ---
            if (self.realtimeJog()) {
                if (self._rtMovingAxis !== null) { ev.preventDefault(); return; }

                var axis = null, dir = 0;
                switch (ev.code) {
                    case "Numpad8": axis = "y"; dir = 1; break;
                    case "Numpad2": axis = "y"; dir = -1; break;
                    case "Numpad4": axis = "x"; dir = -1; break;
                    case "Numpad6": axis = "x"; dir = 1; break;
                    case "NumpadAdd": axis = "z"; dir = 1; break;
                    case "NumpadSubtract": axis = "z"; dir = -1; break;
                    case "Numpad0": axis = "e"; dir = 1; break;
                    case "NumpadDecimal": axis = "e"; dir = -1; break;
                    case "NumpadDivide": self._numpadCycleStep(-1); ev.preventDefault(); return;
                    case "NumpadMultiply": self._numpadCycleStep(1); ev.preventDefault(); return;
                    case "Numpad5": self.home(["x", "y"]); ev.preventDefault(); return;
                    case "NumpadEnter":
                        if (ev.shiftKey && confirm("¿Home de todos los ejes (G28)?")) self.home();
                        return;
                    default: return;
                }
                self._rtMovingAxis = axis;
                self._post("realtime_move", { axis: axis, dir: dir });
                ev.preventDefault();
                return;
            }

            // --- Modo normal: throttle + jog discreto ---
            var now = Date.now();
            if (now - self._numpadLast < 60) { ev.preventDefault(); return; }
            self._numpadLast = now;
            var handled = true;
            switch (ev.code) {
                case "Numpad8": self.jog(0, 1, 0); break;
                case "Numpad2": self.jog(0, -1, 0); break;
                case "Numpad4": self.jog(-1, 0, 0); break;
                case "Numpad6": self.jog(1, 0, 0); break;
                case "Numpad7": self.jog(-1, 1, 0); break;
                case "Numpad9": self.jog(1, 1, 0); break;
                case "Numpad1": self.jog(-1, -1, 0); break;
                case "Numpad3": self.jog(1, -1, 0); break;
                case "Numpad5": self.home(["x", "y"]); break;
                case "NumpadAdd": self.jog(0, 0, 1); break;
                case "NumpadSubtract": self.jog(0, 0, -1); break;
                case "NumpadDivide": self._numpadCycleStep(-1); break;
                case "NumpadMultiply": self._numpadCycleStep(1); break;
                case "Numpad0": self.doExtrude(); break;
                case "NumpadDecimal": self.doRetract(); break;
                case "NumpadEnter":
                    if (ev.shiftKey && confirm("¿Home de todos los ejes (G28)?")) self.home();
                    else handled = false;
                    break;
                default: handled = false;
            }
            if (handled) { ev.preventDefault(); ev.stopPropagation(); }
        };
        document.addEventListener("keydown", self._onNumpadKey, true);

        // Keyup listener para modo realtime: M410 al soltar la tecla
        document.addEventListener("keyup", function (ev) {
            if (!self.realtimeJog()) return;
            if (!ev.code || ev.code.indexOf("Numpad") !== 0) return;
            if (self._rtMovingAxis === null) return;

            var released = null;
            switch (ev.code) {
                case "Numpad8": case "Numpad2": released = "y"; break;
                case "Numpad4": case "Numpad6": released = "x"; break;
                case "NumpadAdd": case "NumpadSubtract": released = "z"; break;
                case "Numpad0": case "NumpadDecimal": released = "e"; break;
            }
            if (released === self._rtMovingAxis) {
                self._rtMovingAxis = null;
                self._post("realtime_stop");
                ev.preventDefault();
            }
        }, true);

        // --- Límites: aceleración / velocidad (M201/M203/M204/M205) ---
        self.lim = {
            m203: { X: ko.observable(""), Y: ko.observable(""), Z: ko.observable(""), E: ko.observable("") },
            m201: { X: ko.observable(""), Y: ko.observable(""), Z: ko.observable(""), E: ko.observable("") },
            m204: { P: ko.observable(""), R: ko.observable(""), T: ko.observable("") },
            m205: { X: ko.observable(""), Y: ko.observable(""), J: ko.observable("") }
        };
        self.getLimits = function () { self._post("get_limits"); };
        self.applyLimits = function (L) {
            ["m203", "m201", "m204", "m205"].forEach(function (code) {
                var src = L[code.toUpperCase()];
                if (!src) return;
                for (var k in self.lim[code]) if (src[k] != null) self.lim[code][k](src[k]);
            });
        };
        function buildCmd(code, obj) {
            var s = code.toUpperCase();
            for (var k in obj) { var v = obj[k](); if (v !== "" && v != null) s += " " + k + v; }
            return s;
        }
        self.applyM203 = function () { self._post("gcode", { cmd: buildCmd("m203", self.lim.m203) }); };
        self.applyM201 = function () { self._post("gcode", { cmd: buildCmd("m201", self.lim.m201) }); };
        self.applyM204 = function () { self._post("gcode", { cmd: buildCmd("m204", self.lim.m204) }); };
        self.applyM205 = function () { self._post("gcode", { cmd: buildCmd("m205", self.lim.m205) }); };
        self.saveLimits = function () { self._post("gcode", { cmd: "M500" }); };

        // ================= SLIDERS EN VIVO (estilo Marlin Slider) =================
        // Rango/paso configurables en Ajustes. Aplican al soltar (change).
        // Las propiedades internas (min/max/step) de los settings de OctoPrint son
        // observables KO: hay que desenvolver CADA una a número (cfg solo desenvuelve
        // el objeto contenedor).
        function sl(name, def) {
            var o = cfg(["sliders", name], def) || def;
            return {
                min: num(ko.unwrap(o.min), def.min),
                max: num(ko.unwrap(o.max), def.max),
                step: num(ko.unwrap(o.step), def.step)
            };
        }
        self.sliderFeed = ko.pureComputed(function () { return sl("feed", { min: 10, max: 300, step: 5 }); });
        self.sliderFlow = ko.pureComputed(function () { return sl("flow", { min: 50, max: 200, step: 5 }); });
        self.sliderFan = ko.pureComputed(function () { return sl("fan", { min: 0, max: 100, step: 5 }); });
        self.sliderBaby = ko.pureComputed(function () { return sl("baby", { min: -0.5, max: 0.5, step: 0.01 }); });

        // ===== Tope DINÁMICO de los sliders de velocidad/flujo =====
        // Al alcanzar el tope, crece +50% (200→300→450…). Al bajar del 50% del tope,
        // se restablece −33.3% (450→300→200), sin bajar nunca del tope BASE (Ajustes).
        self.feedMax = ko.observable(self.sliderFeed().max);
        self.flowMax = ko.observable(self.sliderFlow().max);
        // mientras un slider está "congelado" (acaba de subir el tope) no se re-ajusta
        // su límite, para que el cursor mantenido en el borde no dispare más subidas.
        self._frozen = { feed: false, flow: false };
        function adjustDynMax(valObs, maxObs, baseMax, key) {
            if (self._frozen[key]) return;
            var v = parseFloat(valObs()) || 0, m = maxObs();
            if (v >= m) maxObs(Math.round(m * 1.5));
            else if (v < m * 0.5 && m > baseMax) maxObs(Math.max(baseMax, Math.round(m * 2 / 3)));
        }
        // si cambia el tope base en Ajustes, no dejar el dinámico por debajo de él.
        self.sliderFeed.subscribe(function (s) { if (self.feedMax() < s.max) self.feedMax(s.max); });
        self.sliderFlow.subscribe(function (s) { if (self.flowMax() < s.max) self.flowMax(s.max); });

        // Al SUBIR el tope (el valor lo alcanzó manteniendo pulsado), "congelar" el
        // slider un instante: se deshabilita y rehabilita para CORTAR el arrastre en
        // curso. Así el thumb cae a su valor y NO persigue al cursor hasta el nuevo
        // tope, evitando la retroalimentación de re-maximizado. El usuario vuelve a
        // pulsar para seguir subiendo.
        function freezeSlider(cls) {
            self._frozen[cls] = true;
            var el = document.querySelector("#boostkit_control_panel .bk-msl-" + cls);
            if (el) { el.disabled = true; try { el.blur(); } catch (e) { } }
            setTimeout(function () { self._frozen[cls] = false; if (el) el.disabled = false; }, 300);
        }
        var _prevFeedMax = self.feedMax(), _prevFlowMax = self.flowMax();
        self.feedMax.subscribe(function (nv) { if (nv > _prevFeedMax) freezeSlider("feed"); _prevFeedMax = nv; });
        self.flowMax.subscribe(function (nv) { if (nv > _prevFlowMax) freezeSlider("flow"); _prevFlowMax = nv; });

        function bkDebounce(fn, ms) { var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); }; }

        // Nudge para los botones ↓/↑ de cada slider (respeta paso/límites del slider).
        function nudge(obs, delta, lo, hi) {
            var v = (parseFloat(obs()) || 0) + delta;
            if (lo != null) v = Math.max(lo, v);
            if (hi != null) v = Math.min(hi, v);
            obs(Math.round(v * 1000) / 1000);
        }
        self.feedNudge = function (d) { var s = self.sliderFeed(); nudge(self.feedrate, d * s.step, s.min, self.feedMax()); };
        self.flowNudge = function (d) { var s = self.sliderFlow(); nudge(self.flow, d * s.step, s.min, self.flowMax()); };
        self.babyNudge = function (d) { var s = self.sliderBaby(); nudge(self.babySlider, d * s.step, s.min, s.max); };
        // ↻ sincronizar: re-lee la config del firmware (M503) -> refresca todos los valores.
        self.syncFromFw = function () { self.scanAll(); };

        // Guard: cuando ACTUALIZAMOS una observable desde el firmware (push/possync)
        // los subscribe NO deben re-enviar el comando (evita bucle de realimentación).
        // El POST va DEBOUNCED para no spamear M220/M221 al arrastrar el slider.
        self._applyingFw = false;
        var _feedPost = bkDebounce(self.applyFeedrate, 180);
        var _flowPost = bkDebounce(self.applyFlow, 180);
        self.feedrate.subscribe(function () {
            adjustDynMax(self.feedrate, self.feedMax, self.sliderFeed().max, "feed");
            if (!self._applyingFw) _feedPost();
        });
        self.flow.subscribe(function () {
            adjustDynMax(self.flow, self.flowMax, self.sliderFlow().max, "flow");
            if (!self._applyingFw) _flowPost();
        });

        // --- Ventiladores (M106/M107). Una tarjeta por ventilador declarado en
        //     Ajustes (control.fans); index = P de M106 (0 = capa principal). ---
        self.fans = ko.observableArray([]);
        self.applyFanCtl = function (f) { self._post("fan", { index: f.index, value: parseInt(f.val(), 10) || 0 }); };
        self.fanCtlNudge = function (f, dir) {
            var s = self.sliderFan(), v = (parseInt(f.val(), 10) || 0) + dir * s.step;
            f.val(Math.max(0, Math.min(100, v)));
        };
        self.fanCtlFull = function (f) { f.val(100); };
        self.fanCtlOff = function (f) { f.val(0); };
        function rebuildFans() {
            var defs = cfg(["fans"], null);
            var prev = {}; self.fans().forEach(function (f) { prev[f.index] = f; });
            var out = [];
            (defs && defs.length ? defs : [{ name: "Ventilador", index: 0 }]).forEach(function (d, i) {
                var idx = (ko.unwrap(d.index) != null ? parseInt(ko.unwrap(d.index), 10) : i) || 0;
                if (prev[idx]) { out.push(prev[idx]); return; }
                var f = { index: idx, name: ko.unwrap(d.name) || ("Ventilador " + idx), val: ko.observable(0) };
                f.val.subscribe(bkDebounce(function () { if (!self._applyingFw) self.applyFanCtl(f); }, 150));
                out.push(f);
            });
            self.fans(out);
        }
        self.rebuildFans = rebuildFans;
        rebuildFans();

        // --- Babystep en vivo (slider, además de los botones ±) ---
        self.babyLive = ko.observable(0);   // acumulado de babystep aplicado (mm)
        self.babySlider = ko.observable(0);
        self.babyApplySlider = function () {
            // el slider fija un OBJETIVO; mandamos el delta respecto a lo acumulado.
            var tgt = parseFloat(self.babySlider()) || 0;
            var delta = tgt - (parseFloat(self.babyLive()) || 0);
            if (Math.abs(delta) > 1e-4) {
                self._post("babystep", { z: delta });
                self.babyLive(tgt);
            }
        };
        self.babySlider.subscribe(function () { if (!self._applyingFw) self.babyApplySlider(); });
        self.babyZero = function () { self.babySlider(0); };

        // ================= BABYSTEP por EJE (X/Y/Z, M290) =================
        // Cada eje: valor acumulado + paso de incremento editable + botones ±.
        // acc = acumulado del cliente; live = valor REAL del firmware (cuando lo
        // reporta: en este firmware el babystep Z == offset Z de la sonda, M851,
        // por BABYSTEP_ZPROBE_OFFSET, y se reporta como "Probe Offset Z..").
        self.babyAxes = {
            x: { acc: ko.observable(0), step: ko.observable(0.05), live: ko.observable(null) },
            y: { acc: ko.observable(0), step: ko.observable(0.05), live: ko.observable(null) },
            z: { acc: ko.observable(0), step: ko.observable(0.01), live: ko.observable(null) }
        };
        // Switch del argumento P del M290. Activo (por defecto) = P0 = NO modificar el
        // offset de la sonda (Probe Z Offset): ajusta la primera capa en caliente sin
        // descalibrar la sonda. Desactivado = M290 pelado (en este fw sí mueve el offset).
        self.babyKeepProbe = ko.observable(true);
        // Muestra el valor del firmware si lo conocemos; si no, el acumulado local.
        self.babyDisplay = function (axis) {
            var a = self.babyAxes[axis];
            var v = a.live() != null ? a.live() : (num(a.acc(), 0));
            return (v >= 0 ? "+" : "") + Number(v).toFixed(3) + " mm";
        };
        self.babyAxis = function (axis, dir) {
            var a = self.babyAxes[axis];
            var d = dir * num(a.step(), 0.01);
            if (!d) return;
            var p = {}; p[axis] = d; p.p0 = !!self.babyKeepProbe();
            self._post("babystep", p);
            a.acc(Math.round((num(a.acc(), 0) + d) * 1000) / 1000);
        };
        self.babyAxisZero = function (axis) {
            var a = self.babyAxes[axis];
            // Si el firmware da el valor real (offset sonda), llevarlo a 0 = mandar
            // el negativo de ese valor; si no, del acumulado local.
            var cur = a.live() != null ? num(a.live(), 0) : num(a.acc(), 0);
            if (Math.abs(cur) > 1e-4) { var p = {}; p[axis] = -cur; p.p0 = !!self.babyKeepProbe(); self._post("babystep", p); }
            a.acc(0);
        };

        // ================= MODO ABSOLUTO / RELATIVO =================
        self.setXYZmode = function (rel) { self._post("set_mode", { target: "xyz", relative: !!rel }); };
        self.setEmode = function (rel) { self._post("set_mode", { target: "e", relative: !!rel }); };

        // ================= PARÁMETROS DE MÁQUINA (escaneo + tracking) =================
        // Definición declarativa: cada tarjeta = un M-code con sus campos. El valor
        // EN VIVO se muestra siempre; el input se siembra una vez y se puede editar.
        function mkField(k, label) {
            return { k: k, label: label || k, val: ko.observable(""), live: ko.observable(null), seeded: false };
        }
        function mkParam(code, label, unit, fields, opts) {
            return {
                code: code, label: label, unit: unit || "",
                fields: fields, note: (opts && opts.note) || ""
            };
        }
        var fullParams = [
            mkParam("M900", "Linear Advance", "", [mkField("K", "K")],
                { note: "Compensación de presión (K)." }),
            mkParam("M207", "FW Retract (G10)", "mm", [mkField("S", "longitud"), mkField("W", "swap"), mkField("F", "vel mm/min"), mkField("Z", "hop")],
                { note: "Parámetros de la retracción por firmware (G10)." }),
            mkParam("M208", "FW Recover (G11)", "mm", [mkField("S", "extra"), mkField("W", "swap"), mkField("F", "vel mm/min")],
                { note: "Parámetros de la recuperación por firmware (G11)." }),
            mkParam("M200", "Extrusión volumétrica", "mm", [mkField("S", "On(1)/Off(0)"), mkField("D", "Ø filamento")],
                { note: "D = diámetro del filamento. D0 desactiva." }),
            mkParam("M907", "Corriente motor", "mA", [mkField("X"), mkField("Y"), mkField("Z"), mkField("E")],
                { note: "Valor directo en mA (no escala 0-255). Verifica con el driver real: en algunos firmwares 700 ≈ 770 mA por calibración interna." }),
            mkParam("M425", "Backlash", "mm", [mkField("X"), mkField("Y"), mkField("Z"), mkField("F", "factor")]),
            mkParam("M851", "Offset de la sonda", "mm", [mkField("X"), mkField("Y"), mkField("Z")],
                { note: "Distancia sonda→boquilla. Z afecta la altura de 1ª capa." }),
            mkParam("M206", "Offset de origen (home)", "mm", [mkField("X"), mkField("Y"), mkField("Z")],
                { note: "Desplaza el origen respecto a la cama (home offset)." }),
            mkParam("M92", "Pasos/mm", "", [mkField("X"), mkField("Y"), mkField("Z"), mkField("E")]),
            mkParam("M420", "Malla / fade", "", [mkField("S", "On(1)/Off(0)"), mkField("Z", "fade mm")]),
        ];
        // M207/M208 (retract/recover firmware) se muestran en la sección EXTRUSIÓN,
        // no en el dashboard general. _allParams se usa para sembrar/actualizar TODO.
        self._allParams = fullParams;
        // M207/M208 -> sección extrusión; M851/M206 -> sección babystepping; resto -> dashboard.
        var EXTRA = { M207: 1, M208: 1, M851: 1, M206: 1 };
        self.machineParams = fullParams.filter(function (p) { return !EXTRA[p.code]; });
        self.extrusionParams = fullParams.filter(function (p) { return p.code === "M207" || p.code === "M208"; });
        self.babyParams = fullParams.filter(function (p) { return p.code === "M851" || p.code === "M206"; });
        // Input shaping (M593) es por eje (X/Y con F y D) -> tarjeta dedicada.
        self.shaper = {
            X: { F: ko.observable(""), D: ko.observable(""), liveF: ko.observable(null), liveD: ko.observable(null), seeded: false },
            Y: { F: ko.observable(""), D: ko.observable(""), liveF: ko.observable(null), liveD: ko.observable(null), seeded: false }
        };

        self.applyFwState = function (fw) {
            if (!fw) return;
            self._allParams.forEach(function (p) {
                var node = fw[p.code];
                if (!node) return;
                p.fields.forEach(function (f) {
                    if (node[f.k] == null) return;
                    f.live(node[f.k]);
                    if (!f.seeded) { f.val(node[f.k]); f.seeded = true; }
                });
            });
            var ms = fw.M593;
            if (ms) {
                ["X", "Y"].forEach(function (ax) {
                    var n = ms[ax]; if (!n) return;
                    var s = self.shaper[ax];
                    if (n.F != null) { s.liveF(n.F); if (!s.seeded) s.F(n.F); }
                    if (n.D != null) { s.liveD(n.D); if (!s.seeded) s.D(n.D); }
                    s.seeded = true;
                });
            }
            // overrides en vivo: ventilador (M106), feed (M220), flow (M221).
            self._applyingFw = true;
            if (fw.M106 && fw.M106.S != null) { var _f0 = self.fans()[0]; if (_f0) _f0.val(Math.round(fw.M106.S * 100 / 255)); }
            if (fw.M220 && fw.M220.S != null) self.feedrate(Math.round(fw.M220.S));
            if (fw.M221 && fw.M221.S != null) self.flow(Math.round(fw.M221.S));
            // extrusión en frío (M302 P0 = permitida) · autoretract (M209 S1 = on)
            if (fw.M302 && fw.M302.P != null) self.coldAllow(fw.M302.P === 0);
            if (fw.M209 && fw.M209.S != null) self.autoRetract(fw.M209.S >= 1);
            // babystep Z <-> offset Z de la sonda (BABYSTEP_ZPROBE_OFFSET): el control
            // de babystep muestra el valor REAL reportado por el firmware.
            if (fw.M851 && fw.M851.Z != null) self.babyAxes.z.live(fw.M851.Z);
            self._applyingFw = false;
        };
        self.resyncParam = function (p) {
            p.fields.forEach(function (f) { if (f.live() != null) f.val(f.live()); });
        };
        self.applyParam = function (p) {
            var s = p.code;
            p.fields.forEach(function (f) {
                var v = f.val();
                if (v !== "" && v != null) s += " " + f.k + v;
            });
            if (s !== p.code) self._post("gcode", { cmd: s });
        };
        self.applyShaper = function (ax) {
            var s = self.shaper[ax];
            var cmd = "M593 " + ax;
            if (s.F() !== "" && s.F() != null) cmd += " F" + s.F();
            if (s.D() !== "" && s.D() != null) cmd += " D" + s.D();
            self._post("gcode", { cmd: cmd });
        };
        self.saveAll = function () { self._post("gcode", { cmd: "M500" }); };
        self.scanAll = function () { self._post("get_limits"); };  // M503 -> escaneo completo

        // El panel vive en su propio tab ("Enhanced Controls", #tab_plugin_boostkit_controls).
        // Los bootstrap-slider se inicializan con ancho 0 si el panel estaba oculto
        // al bindear; hay que recalcular su layout cuando se hace visible.
        self._relayoutSliders = function () {
            try {
                $("#boostkit_control_panel input.bk-msl-slider").each(function () {
                    try { $(this).slider("relayout"); } catch (e) { }
                });
            } catch (e) { }
        };

        // ===== Card de Estado: cancel-object + skip-to + capacidades =====
        self.objects = ko.observableArray([]);   // {index,name,cancelled}
        self.checkpoints = ko.observableArray([]); // {pos,layer,label,kind}
        self.jobCaps = ko.observable({ planar: true, multiaxis: false, axes: [], m486: false });
        self.hasMarkers = ko.observable(false);
        self.skipSel = ko.observable(null);        // checkpoint elegido (índice en la lista)
        self.skipStart = ko.observable(true);      // arrancar print tras el salto
        self.skipBusy = ko.observable(false);
        self.skipMsg = ko.observable("");

        self.applyObjects = function (m) {
            var cancelled = m.cancelled || [];
            self.objects((m.objects || []).map(function (o) {
                return { index: o.index, name: o.name, cancelled: ko.observable(!!o.cancelled) };
            }));
            self.checkpoints((m.checkpoints || []).map(function (c, i) {
                return { idx: i, pos: c.pos, layer: c.layer, label: c.label || ("Capa " + c.layer), kind: c.kind };
            }));
            if (m.caps) self.jobCaps(m.caps);
            self.hasMarkers(!!m.has_markers);
        };

        self.toggleCancelObject = function (o) {
            var willCancel = !o.cancelled();
            if (willCancel && !confirm("¿Cancelar el objeto \"" + o.name + "\"? No se puede deshacer la parte ya omitida.")) return;
            o.cancelled(willCancel);
            self._post(willCancel ? "cancel_object" : "uncancel_object", { index: o.index });
        };

        // Banner de capacidades (no planar / multi-eje desactivan supuestos).
        self.capsBanner = ko.pureComputed(function () {
            var c = self.jobCaps();
            var parts = [];
            if (!c.planar) parts.push("no planar");
            if (c.multiaxis) parts.push((c.axes || []).join("/") + " (multi-eje)");
            if (parts.length) return "Detectado: " + parts.join(", ") + " → lift de reanudación desactivado, skip-to limitado.";
            return "";
        });
        self.canSkip = ko.pureComputed(function () { return self.checkpoints().length > 0; });

        self.doSkipTo = function () {
            var sel = self.skipSel();
            if (sel == null) { self.skipMsg("Elige un checkpoint"); return; }
            if (!confirm("¿Saltar a \"" + self.checkpoints()[sel].label + "\"? Se generará un .skip.gcode" + (self.skipStart() ? " y empezará a imprimir." : "."))) return;
            self.skipBusy(true);
            self.skipMsg("Generando salto…");
            self._post("skip_to", { checkpoint: sel, start_print: self.skipStart() });
        };

        self.cancelPrint = function () {
            if (!confirm("¿Cancelar la impresión completa?")) return;
            OctoPrint.job.cancel();
        };

        // Pide el estado de objetos al cargar (por si ya hay fichero seleccionado).
        try { self._post("list_objects"); } catch (e) { /* aún sin conexión */ }

        // Escaneo automático + relayout + (re)dibujo de la gráfica al mostrar el tab.
        // Comprobamos si el panel se hizo visible (offsetParent != null).
        self._scanned = false;
        self.onAfterTabChange = function () {
            var panel = document.getElementById("boostkit_control_panel");
            if (!panel || panel.offsetParent === null) return;  // panel no visible
            self._relayoutSliders();
            self._tInit();   // el canvas necesita estar visible para medir su ancho
            if (!self._scanned) {
                self._scanned = true;
                if (self.printerState.isOperational && self.printerState.isOperational()) self.scanAll();
            }
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: BoostKitViewModel,
        dependencies: ["settingsViewModel", "printerStateViewModel", "printerProfilesViewModel"],
        elements: ["#boostkit_control_panel", "#navbar_plugin_boostkit", "#boostkit_card_jog", "#gen_plugin_boostkit_status"],
    });
});
