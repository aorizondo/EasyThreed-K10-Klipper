/*
 * Boost Kit — Módulo: Gráfico de temperatura (Canvas puro, sin dependencias).
 *
 * Mejoras sobre el gráfico nativo:
 *   - Historial largo con zoom (rueda) y pan (arrastrar).
 *   - Visual: objetivo (target) punteado, real sólido, tooltip con crosshair.
 *   - ANÁLISIS DE PENDIENTE: selecciona un rango temporal y calcula la tasa de
 *     calentamiento/enfriamiento (°C/s) por regresión lineal en ESE tramo
 *     (p. ej. "entre -50s y -25s de los últimos 100s").
 *
 * Datos: OctoPrint llama a fromHistoryData()/fromCurrentData() en todos los
 * viewmodels con data.temps = [{time, tool0:{actual,target}, bed:{...}, ...}].
 * time viene en segundos epoch (se normaliza si llega en ms).
 */
$(function () {
    "use strict";

    // ---- Regresión lineal por mínimos cuadrados (t en segundos) ----
    function linregress(pts) {
        var n = pts.length;
        if (n < 2) return null;
        var st = 0, sv = 0, i;
        for (i = 0; i < n; i++) { st += pts[i].t; sv += pts[i].v; }
        var mt = st / n, mv = sv / n;
        var num = 0, den = 0, ssTot = 0;
        for (i = 0; i < n; i++) {
            var dt = pts[i].t - mt, dvv = pts[i].v - mv;
            num += dt * dvv; den += dt * dt; ssTot += dvv * dvv;
        }
        if (den === 0) return null;
        var slope = num / den, intercept = mv - slope * mt, ssRes = 0;
        for (i = 0; i < n; i++) {
            var pred = slope * pts[i].t + intercept, r = pts[i].v - pred;
            ssRes += r * r;
        }
        return {
            slope: slope, intercept: intercept,
            r2: ssTot > 0 ? 1 - ssRes / ssTot : 1, n: n,
            dt: pts[n - 1].t - pts[0].t, dv: pts[n - 1].v - pts[0].v,
            t0: pts[0].t, t1: pts[n - 1].t
        };
    }

    var PALETTE = {
        tool0: "#e74c3c", tool1: "#e67e22", tool2: "#f1c40f",
        bed: "#3498db", chamber: "#9b59b6"
    };
    var CYCLE = ["#1abc9c", "#2ecc71", "#34495e", "#d35400", "#7f8c8d"];

    function BoostTempViewModel(parameters) {
        var self = this;
        self.settings = parameters[0];
        self.printerProfiles = parameters[1];

        // ---- ¿El perfil de impresora activo tiene cama caliente? ----
        // Si no la tiene, ocultamos la fila/serie de cama y NUNCA enviamos M140/M190
        // (evita el error "No heated bed installed" y sus alertas).
        self.hasHeatedBed = ko.pureComputed(function () {
            try {
                var p = self.printerProfiles.currentProfileData();
                if (p && p.heatedBed) return !!ko.unwrap(p.heatedBed);
            } catch (e) { /* perfil aún no disponible */ }
            return true;   // por defecto asumimos que sí (no romper impresoras con cama)
        });

        // ---- Control de temperatura (set + presets) ----
        self._post = function (cmd, payload) {
            return OctoPrint.simpleApiCommand("boostkit", cmd, payload || {});
        };
        self.hotendSet = ko.observable(0);
        self.bedSet = ko.observable(0);
        self.liveHotend = ko.observable(0); self.liveHotendT = ko.observable(0);
        self.liveBed = ko.observable(0); self.liveBedT = ko.observable(0);
        // (applyHotend/applyBed legacy eliminados: los sliders fijos se reemplazaron por
        // los controles unificados foreach tempControls; hotendSet/bedSet siguen usándose
        // como estado de coolAll/applyOctoProfile.)
        self.coolAll = function () {
            self.hotendSet(0);
            self._post("settemp", { heater: "tool0", value: 0 });
            if (self.hasHeatedBed()) { self.bedSet(0); self._post("settemp", { heater: "bed", value: 0 }); }
        };
        function num(v, d) { var n = parseFloat(v); return isNaN(n) ? d : n; }

        // ===== Controles de temperatura CAPABILITY-AWARE (igual que el tab "Enhanced
        // Controls"): un slider por elemento capacitado en el perfil (hotend(s)/cama/
        // cámara). El máximo sale de Ajustes (temp.*_max). =====
        // Controles de temperatura: FÁBRICA COMPARTIDA (window.BK, bkcore.js) — la misma
        // que usa el panel de control (boostkit.js). Evita la duplicación e incorpora el
        // comportamiento 0=apagado / piso activo 150 / tope 280. El bucle de datos en
        // vivo (ingest) sigue más abajo y opera sobre self.tempControls.
        var _tc = BK.makeTempControls(self.settings, self.printerProfiles, self._post);
        self.tempControls = _tc.controls;
        self.markTempEdit = _tc.markEdit;
        self.onTempInput = _tc.onInput;
        self.applyTempControl = _tc.apply;
        self.rebuildTempControls = _tc.rebuild;

        // Perfiles de temperatura NATIVOS de OctoPrint (Ajustes › Temperaturas), los
        // mismos que muestra el tab. Al aplicarlos se reflejan en los sliders.
        self.octoProfiles = ko.pureComputed(function () {
            try { return self.settings.temperature_profiles() || []; } catch (e) { return []; }
        });
        self.applyOctoProfile = function (p) {
            var t = num(ko.unwrap(p.extruder), 0), b = num(ko.unwrap(p.bed), 0);
            self.hotendSet(t);
            self._post("settemp", { heater: "tool0", value: t });
            if (self.hasHeatedBed()) { self.bedSet(b); self._post("settemp", { heater: "bed", value: b }); }
            self.tempControls().forEach(function (c) {
                if (c.key === "tool0") { c.set(t); c.lastEdit = Date.now(); }
                else if (c.key === "bed" && self.hasHeatedBed()) { c.set(b); c.lastEdit = Date.now(); }
            });
        };

        // Buffer de muestras: [{t, vals:{key:{a,g}}}]
        self.samples = [];
        self.keys = [];                  // orden de series detectadas
        self.colorOf = {};
        self._lastT = 0;

        // Estado de vista (epoch segundos)
        self.windowSec = 120;            // ventana visible por defecto
        self.view = { start: 0, end: 0 };
        self.follow = true;              // seguir en vivo

        // Selección de análisis
        self.sel = null;                 // {t0, t1}
        self._dragging = null;           // {mode:'pan'|'sel', x0, vstart, vend}

        // Observables UI
        self.analyzeMode = ko.observable(false);   // arrastrar = seleccionar
        self.analyzeSeries = ko.observable("tool0");
        self.seriesList = ko.observableArray([]);
        self.live = ko.observable(true);
        self.result = ko.observable(null);         // texto de pendiente
        self.hoverInfo = ko.observable("");

        self._canvas = null;
        self._ctx = null;
        self._needsDraw = false;

        // ---------- Ingesta de datos ----------
        function normTime(t) { return t > 1e12 ? t / 1000.0 : t; }
        // Solo sensores reales: filtra series inyectadas por otros plugins (p.ej. TBS_*)
        function isRealSensor(k) { return /^(tool\d+|bed|chamber)$/.test(k); }

        function ingest(temps, replace) {
            if (!temps || !temps.length) return;
            if (replace) { self.samples = []; self._lastT = 0; }
            for (var i = 0; i < temps.length; i++) {
                var s = temps[i];
                var t = normTime(s.time);
                if (!replace && t <= self._lastT) continue;
                var vals = {};
                for (var k in s) {
                    if (k === "time") continue;
                    var o = s[k];
                    if (k === "bed" && !self.hasHeatedBed()) continue;   // sin cama: no graficar
                    if (o && typeof o === "object" && ("actual" in o || "target" in o) && isRealSensor(k)) {
                        vals[k] = { a: o.actual, g: o.target };
                        if (self.keys.indexOf(k) < 0) registerKey(k);
                    }
                }
                self.samples.push({ t: t, vals: vals });
                self._lastT = t;
            }
            pruneBuffer();
            // lecturas en vivo para el panel de control
            var last = self.samples[self.samples.length - 1];
            if (last) {
                if (last.vals.tool0) { self.liveHotend(round1(last.vals.tool0.a)); self.liveHotendT(round1(last.vals.tool0.g)); }
                if (last.vals.bed) { self.liveBed(round1(last.vals.bed.a)); self.liveBedT(round1(last.vals.bed.g)); }
                if (self.tempControls) self.tempControls().forEach(function (c) {
                    var v = last.vals[c.key];
                    if (!v) return;
                    c.actual(round1(v.a)); c.target(round1(v.g));
                    // enlaza el slider al objetivo reportado (comandos / auto-report),
                    // salvo que el usuario lo esté ajustando ahora mismo.
                    if (Date.now() - (c.lastEdit || 0) > 1500) c.set(round1(v.g));
                });
            }
            if (self.follow) snapToLive();
            self.requestDraw();
        }
        function round1(v) { return v == null ? 0 : Math.round(v * 10) / 10; }

        function registerKey(k) {
            self.keys.push(k);
            var c = PALETTE[k] || CYCLE[(self.keys.length - 1) % CYCLE.length];
            self.colorOf[k] = c;
            // refrescar el selector de serie a analizar
            var list = self.keys.map(function (key) {
                return { key: key, label: key, color: self.colorOf[key] };
            });
            self.seriesList(list);
            if (self.keys.indexOf(self.analyzeSeries()) < 0) self.analyzeSeries(k);
        }

        function pruneBuffer() {
            var hist = parseInt(self.settings.settings.plugins.boostkit.temp.history_seconds(), 10) || 600;
            var cutoff = self._lastT - hist;
            var i = 0;
            while (i < self.samples.length && self.samples[i].t < cutoff) i++;
            if (i > 0) self.samples.splice(0, i);
            if (self.samples.length > 6000) self.samples.splice(0, self.samples.length - 6000);
        }

        function snapToLive() {
            if (!self.samples.length) return;
            self.view.end = self._lastT;
            self.view.start = self.view.end - self.windowSec;
        }

        self.fromHistoryData = function (data) { ingest(data.temps, true); };
        self.fromCurrentData = function (data) { ingest(data.temps, false); };

        // ---------- Controles ----------
        self.toggleLive = function () {
            self.follow = !self.follow;
            self.live(self.follow);
            if (self.follow) { snapToLive(); self.requestDraw(); }
        };
        self.resetView = function () {
            self.follow = true; self.live(true);
            self.sel = null; self.result(null);
            snapToLive(); self.requestDraw();
        };
        self.clearSelection = function () { self.sel = null; self.result(null); self.requestDraw(); };

        self.analyzeSeries.subscribe(function () { recompute(); });

        // ---------- Cálculo de pendiente sobre la selección ----------
        function recompute() {
            if (!self.sel) { self.result(null); return; }
            var key = self.analyzeSeries();
            var lo = Math.min(self.sel.t0, self.sel.t1), hi = Math.max(self.sel.t0, self.sel.t1);
            var pts = [];
            for (var i = 0; i < self.samples.length; i++) {
                var s = self.samples[i];
                if (s.t < lo || s.t > hi) continue;
                var v = s.vals[key];
                if (v && v.a != null) pts.push({ t: s.t, v: v.a });
            }
            var reg = linregress(pts);
            if (!reg) { self.result({ ok: false, key: key }); self.requestDraw(); return; }
            self.result({
                ok: true, key: key,
                slope: reg.slope, dv: reg.dv, dt: reg.dt, r2: reg.r2, n: reg.n,
                reg: reg
            });
            self.requestDraw();
        }

        // ---------- Render ----------
        self.requestDraw = function () {
            if (self._needsDraw) return;
            self._needsDraw = true;
            window.requestAnimationFrame(function () { self._needsDraw = false; draw(); });
        };

        function layout() {
            var c = self._canvas;
            return { L: 46, R: 12, T: 10, B: 24, W: c.clientWidth, H: c.clientHeight };
        }
        function xToPx(t, lo) { var v = self.view; return lo.L + (t - v.start) / (v.end - v.start) * (lo.W - lo.L - lo.R); }
        function pxToT(px, lo) { var v = self.view; return v.start + (px - lo.L) / (lo.W - lo.L - lo.R) * (v.end - v.start); }
        function yToPx(temp, lo, ymin, ymax) { return lo.T + (1 - (temp - ymin) / (ymax - ymin)) * (lo.H - lo.T - lo.B); }

        function visibleYRange(lo) {
            var ymin = Infinity, ymax = -Infinity, i, k;
            for (i = 0; i < self.samples.length; i++) {
                var s = self.samples[i];
                if (s.t < self.view.start - 1 || s.t > self.view.end + 1) continue;
                for (k = 0; k < self.keys.length; k++) {
                    var v = s.vals[self.keys[k]];
                    if (!v) continue;
                    if (v.a != null) { ymin = Math.min(ymin, v.a); ymax = Math.max(ymax, v.a); }
                    if (v.g != null && v.g > 0) { ymin = Math.min(ymin, v.g); ymax = Math.max(ymax, v.g); }
                }
            }
            if (!isFinite(ymin)) { ymin = 0; ymax = 250; }
            var pad = Math.max(5, (ymax - ymin) * 0.08);
            ymin = Math.max(0, ymin - pad); ymax = ymax + pad;
            if (ymax - ymin < 10) ymax = ymin + 10;
            return { ymin: ymin, ymax: ymax };
        }

        function niceTicks(min, max, count) {
            var range = max - min, raw = range / count;
            var mag = Math.pow(10, Math.floor(Math.log10(raw)));
            var norm = raw / mag, step;
            step = norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10;
            step *= mag;
            var ticks = [], t = Math.ceil(min / step) * step;
            for (; t <= max; t += step) ticks.push(t);
            return ticks;
        }

        function draw() {
            var c = self._canvas;
            if (!c || !c.clientWidth) return;
            var dpr = window.devicePixelRatio || 1;
            if (c.width !== c.clientWidth * dpr || c.height !== c.clientHeight * dpr) {
                c.width = c.clientWidth * dpr; c.height = c.clientHeight * dpr;
            }
            var ctx = self._ctx;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            var lo = layout();
            ctx.clearRect(0, 0, lo.W, lo.H);
            if (self.view.end <= self.view.start) { snapToLive(); if (self.view.end <= self.view.start) return; }

            var yr = visibleYRange(lo), ymin = yr.ymin, ymax = yr.ymax;

            // --- Rejilla + eje Y ---
            ctx.font = "10px sans-serif"; ctx.textBaseline = "middle";
            var yticks = niceTicks(ymin, ymax, 6);
            ctx.strokeStyle = "rgba(127,127,127,0.18)"; ctx.fillStyle = "#999";
            ctx.lineWidth = 1; ctx.textAlign = "right";
            for (var i = 0; i < yticks.length; i++) {
                var yp = yToPx(yticks[i], lo, ymin, ymax);
                ctx.beginPath(); ctx.moveTo(lo.L, yp); ctx.lineTo(lo.W - lo.R, yp); ctx.stroke();
                ctx.fillText(Math.round(yticks[i]) + "°", lo.L - 5, yp);
            }
            // --- Eje X (relativo al último dato) ---
            ctx.textAlign = "center"; ctx.textBaseline = "top";
            var span = self.view.end - self.view.start;
            var xstep = span <= 60 ? 10 : span <= 180 ? 30 : span <= 600 ? 60 : span <= 1800 ? 300 : 600;
            var firstRel = Math.ceil((self.view.start - self._lastT) / xstep) * xstep;
            for (var r = firstRel; r <= (self.view.end - self._lastT) + 0.001; r += xstep) {
                var tt = self._lastT + r, xp = xToPx(tt, lo);
                if (xp < lo.L || xp > lo.W - lo.R) continue;
                ctx.strokeStyle = "rgba(127,127,127,0.12)";
                ctx.beginPath(); ctx.moveTo(xp, lo.T); ctx.lineTo(xp, lo.H - lo.B); ctx.stroke();
                ctx.fillStyle = "#999";
                ctx.fillText(r === 0 ? "0s" : r + "s", xp, lo.H - lo.B + 4);
            }

            // --- Banda de selección ---
            if (self.sel) {
                var xa = xToPx(self.sel.t0, lo), xb = xToPx(self.sel.t1, lo);
                ctx.fillStyle = "rgba(52,152,219,0.15)";
                ctx.fillRect(Math.min(xa, xb), lo.T, Math.abs(xb - xa), lo.H - lo.T - lo.B);
                ctx.strokeStyle = "rgba(52,152,219,0.6)"; ctx.setLineDash([3, 3]);
                ctx.beginPath(); ctx.moveTo(xa, lo.T); ctx.lineTo(xa, lo.H - lo.B);
                ctx.moveTo(xb, lo.T); ctx.lineTo(xb, lo.H - lo.B); ctx.stroke();
                ctx.setLineDash([]);
            }

            // --- Series ---
            ctx.save();
            ctx.beginPath(); ctx.rect(lo.L, lo.T, lo.W - lo.L - lo.R, lo.H - lo.T - lo.B); ctx.clip();
            for (var k = 0; k < self.keys.length; k++) {
                var key = self.keys[k], col = self.colorOf[key];
                // target punteado
                drawLine(ctx, lo, ymin, ymax, key, "g", col, true);
                // actual sólido
                drawLine(ctx, lo, ymin, ymax, key, "a", col, false);
            }
            // recta ajustada de la selección
            var res = self.result();
            if (res && res.ok && res.reg) {
                var g = res.reg, col2 = self.colorOf[res.key] || "#000";
                ctx.strokeStyle = "#000"; ctx.lineWidth = 2; ctx.setLineDash([6, 3]); ctx.globalAlpha = 0.8;
                ctx.beginPath();
                ctx.moveTo(xToPx(g.t0, lo), yToPx(g.slope * g.t0 + g.intercept, lo, ymin, ymax));
                ctx.lineTo(xToPx(g.t1, lo), yToPx(g.slope * g.t1 + g.intercept, lo, ymin, ymax));
                ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
            }
            ctx.restore();

            // --- Crosshair / tooltip ---
            if (self._hoverX != null) drawHover(ctx, lo, ymin, ymax);
        }

        function drawLine(ctx, lo, ymin, ymax, key, field, col, dashed) {
            ctx.strokeStyle = col; ctx.lineWidth = dashed ? 1 : 1.8;
            ctx.globalAlpha = dashed ? 0.55 : 1;
            ctx.setLineDash(dashed ? [4, 4] : []);
            ctx.beginPath();
            var started = false;
            for (var i = 0; i < self.samples.length; i++) {
                var s = self.samples[i];
                if (s.t < self.view.start - span() || s.t > self.view.end + span()) continue;
                var v = s.vals[key];
                if (!v || v[field] == null || (field === "g" && v[field] <= 0)) { started = false; continue; }
                var xp = xToPx(s.t, lo), yp = yToPx(v[field], lo, ymin, ymax);
                if (!started) { ctx.moveTo(xp, yp); started = true; } else ctx.lineTo(xp, yp);
            }
            ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
            function span() { return self.view.end - self.view.start; }
        }

        self._hoverX = null;
        function drawHover(ctx, lo, ymin, ymax) {
            var t = pxToT(self._hoverX, lo);
            // muestra más cercana
            var best = null, bd = Infinity;
            for (var i = 0; i < self.samples.length; i++) {
                var d = Math.abs(self.samples[i].t - t);
                if (d < bd) { bd = d; best = self.samples[i]; }
            }
            if (!best) return;
            var xp = xToPx(best.t, lo);
            ctx.strokeStyle = "rgba(127,127,127,0.6)"; ctx.setLineDash([2, 2]);
            ctx.beginPath(); ctx.moveTo(xp, lo.T); ctx.lineTo(xp, lo.H - lo.B); ctx.stroke(); ctx.setLineDash([]);
            var rel = Math.round(best.t - self._lastT);
            var parts = [(rel === 0 ? "ahora" : rel + "s")];
            for (var k = 0; k < self.keys.length; k++) {
                var key = self.keys[k], v = best.vals[key];
                if (!v) continue;
                parts.push(key + " " + (v.a != null ? v.a.toFixed(1) : "–") + "°" +
                    (v.g ? "/" + v.g.toFixed(0) + "°" : ""));
            }
            self.hoverInfo(parts.join("   "));
        }

        // ---------- Eventos de ratón ----------
        function bindCanvas() {
            var c = self._canvas;
            c.addEventListener("wheel", function (e) {
                e.preventDefault();
                var lo = layout(), tc = pxToT(e.offsetX, lo);
                var f = e.deltaY < 0 ? 0.8 : 1.25;
                var ns = tc - (tc - self.view.start) * f, ne = tc + (self.view.end - tc) * f;
                if (ne - ns < 5) return;
                self.view.start = ns; self.view.end = ne;
                self.windowSec = ne - ns;
                self.follow = false; self.live(false);
                self.requestDraw();
            }, { passive: false });

            c.addEventListener("mousedown", function (e) {
                var lo = layout();
                if (self.analyzeMode() || e.shiftKey) {
                    self._dragging = { mode: "sel", t0: pxToT(e.offsetX, lo) };
                    self.sel = { t0: self._dragging.t0, t1: self._dragging.t0 };
                } else {
                    self._dragging = { mode: "pan", x0: e.offsetX, vs: self.view.start, ve: self.view.end };
                    self.follow = false; self.live(false);
                }
            });
            window.addEventListener("mousemove", function (e) {
                if (!self._canvas) return;
                var rect = self._canvas.getBoundingClientRect();
                var ox = e.clientX - rect.left, oy = e.clientY - rect.top;
                var lo = layout();
                if (self._dragging) {
                    if (self._dragging.mode === "pan") {
                        var span = self.view.end - self.view.start;
                        var dpx = ox - self._dragging.x0;
                        var dt = -dpx / (lo.W - lo.L - lo.R) * span;
                        self.view.start = self._dragging.vs + dt;
                        self.view.end = self._dragging.ve + dt;
                    } else {
                        self.sel.t1 = pxToT(ox, lo);
                    }
                    self.requestDraw();
                } else if (ox >= lo.L && ox <= lo.W - lo.R && oy >= lo.T && oy <= lo.H - lo.B) {
                    self._hoverX = ox; self.requestDraw();
                } else if (self._hoverX != null) { self._hoverX = null; self.hoverInfo(""); self.requestDraw(); }
            });
            window.addEventListener("mouseup", function () {
                if (self._dragging && self._dragging.mode === "sel") recompute();
                self._dragging = null;
            });
            c.addEventListener("dblclick", function () { self.resetView(); });
        }

        // ---------- Ciclo de vida ----------
        self.onAfterBinding = function () {
            self._canvas = document.getElementById("bktemp_canvas");
            if (!self._canvas) return;
            self._ctx = self._canvas.getContext("2d");
            try {
                var w = parseInt(self.settings.settings.plugins.boostkit.temp.default_window(), 10);
                if (w > 0) { self.windowSec = w; snapToLive(); }
            } catch (e) { /* settings aún no disponibles */ }
            bindCanvas();
            if (typeof ResizeObserver !== "undefined") {
                new ResizeObserver(function () { self.requestDraw(); }).observe(self._canvas);
            }
            window.addEventListener("resize", function () { self.requestDraw(); });
            self.requestDraw();
        };
        self.onAfterTabChange = function (current) {
            if (current === "#tab_plugin_boostkit_temp") { snapToLiveIfFollow(); self.requestDraw(); }
        };
        function snapToLiveIfFollow() { if (self.follow) snapToLive(); }
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: BoostTempViewModel,
        dependencies: ["settingsViewModel", "printerProfilesViewModel"],
        elements: ["#tab_plugin_boostkit_temp", "#boostkit_card_temp"]
    });
});
