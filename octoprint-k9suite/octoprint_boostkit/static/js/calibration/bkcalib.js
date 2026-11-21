/*
 * Boost Kit — Suite de calibración universal.
 * Consolida CalibrationTools + Calibration-Companion: patrones paramétricos
 * (generados en backend según las capacidades reales), PID y e-steps automáticos,
 * UI tipo ecualizador, presets y tracking de tests pasados.
 */
$(function () {
    function BoostCalibViewModel(parameters) {
        var self = this;
        self.settings = parameters[0];
        self.printerState = parameters[1];
        self.printerProfiles = parameters[2];

        self._post = function (cmd, payload) {
            return OctoPrint.simpleApiCommand("boostkit", cmd, payload || {});
        };
        function num(v, d) { var n = parseFloat(v); return isNaN(n) ? d : n; }

        // --- Esquema de tests: cada parámetro es una "banda" del ecualizador ---
        // type: pattern (genera gcode) | pid | esteps
        // Tests de PATRÓN: data-driven desde el backend (calib_tests). Los de
        // FIRMWARE (PID, e-steps) se mantienen aquí (no son patrones editables).
        // Nivelación de cama: sub-herramienta propia (uso frecuente -> primera).
        self._levelingTest = { id: "leveling", name: "Nivelación de cama", type: "leveling", icon: "layer-group" };
        self._fwTests = [
            { id: "pid", name: "PID autotune", type: "pid", icon: "fire" },
            { id: "esteps", name: "E-steps (extrusor)", type: "esteps", icon: "ruler-horizontal" }
        ];
        // Orden del picker: Nivelación · patrones data-driven · PID/e-steps.
        self.TESTS = ko.observableArray([self._levelingTest].concat(self._fwTests));
        self.applyTests = function (list) {
            var patterns = (list || []).map(function (t) {
                var o = {}; for (var k in t) o[k] = t[k];
                // Respeta el `type` explícito del backend (ej. "probe" para backlash G38).
                // Si no lo trae, es un patrón data-driven clásico.
                if (!o.type) o.type = "pattern";
                return o;
            });
            self.TESTS([self._levelingTest].concat(patterns).concat(self._fwTests));
        };

        self.caps = ko.observable(null);
        self.activeTest = ko.observable(null);
        self.params = ko.observableArray([]);    // {k,label,min,max,step,u,val}
        self.presetList = ko.observable({});     // {test:[{name,params}]}
        self.presetName = ko.observable("");
        self.history = ko.observableArray([]);
        self.lastMsg = ko.observable("");

        // Matriz multi-variable: conteo de especímenes / bandas de temp / overflow (los
        // calcula el backend de forma autoritativa y los devuelve en calib_preview).
        self.specimenCount = ko.observable(1);
        self.tempBands = ko.observable(1);
        self.overflow = ko.observable(false);
        self._axisN = function (s, e, st) {
            s = num(s, 0); e = num(e, 0); st = Math.abs(num(st, 1)) || 1;
            return Math.max(1, Math.floor(Math.abs(e - s) / st + 1e-9) + 1);
        };

        // Banner de capacidades
        self.capsBanner = ko.pureComputed(function () {
            var c = self.caps(); if (!c) return "";
            var mode = c.volumetric ? ("VOLUMÉTRICO (Ø" + c.filament + "mm → E en mm³)") : "lineal (E en mm)";
            return "Cama " + c.bed.x + "×" + c.bed.y + " · nozzle " + c.nozzle + "mm · vmax " +
                Math.round(c.vmax.x) + " · accel " + Math.round(c.amax.x) + " · extrusión " + mode;
        });

        self.activeMeta = ko.pureComputed(function () {
            var id = self.activeTest();
            return self.TESTS().filter(function (t) { return t.id === id; })[0] || null;
        });
        self.isPattern = ko.pureComputed(function () { var m = self.activeMeta(); return m && m.type === "pattern"; });
        self.isPid = ko.pureComputed(function () { var m = self.activeMeta(); return m && m.type === "pid"; });
        self.isEsteps = ko.pureComputed(function () { var m = self.activeMeta(); return m && m.type === "esteps"; });
        self.isBacklashProbe = ko.pureComputed(function () { var m = self.activeMeta(); return m && m.type === "probe"; });
        self.isLeveling = ko.pureComputed(function () { var m = self.activeMeta(); return m && m.type === "leveling"; });

        // --- Backlash sweep (G38.2 + M425) ---
        self.blAxis = ko.observable("x");
        self.blM425Start = ko.observable(0.0);
        self.blM425End = ko.observable(0.2);
        self.blM425Step = ko.observable(0.02);
        self.blRefPos = ko.observable(50.0);
        self.blBackOff = ko.observable(5.0);
        self.blTarget = ko.observable(-5.0);
        self.blFeed = ko.observable(300);
        self.blSamplesPer = ko.observable(3);
        self.blBusy = ko.observable(false);
        self.blSamples = ko.observableArray([]);   // [{m425, error}, ...]
        self.blBest = ko.observable(null);
        self.blBestErr = ko.observable(null);
        self.blOrig = ko.observable(null);
        self.runBacklashSweep = function () {
            self.blSamples([]); self.blBest(null); self.blBestErr(null);
            self._post("calib_backlash_start", {
                axis: self.blAxis(),
                m425_start: num(self.blM425Start(), 0),
                m425_end: num(self.blM425End(), 0.2),
                m425_step: num(self.blM425Step(), 0.02),
                ref_pos: num(self.blRefPos(), 50),
                back_off: num(self.blBackOff(), 5),
                target: num(self.blTarget(), -1),
                feed: parseInt(self.blFeed(), 10) || 200,
                samples_per: parseInt(self.blSamplesPer(), 10) || 3,
                save: false,
            });
            self.blBusy(true);
        };
        self.cancelBacklashSweep = function () {
            self._post("calib_backlash_cancel");
        };
        self.applyBestBacklash = function () {
            var b = self.blBest(); if (b == null) return;
            var ax = self.blAxis().toUpperCase();
            // Sintaxis Marlin: `M425 X0.10` = pon backlash de X a 0.10 mm.
            // NO `M425 X Z0.10` (eso pondría Z=0.10 sin tocar X — error).
            self._post("calib_apply", { gcode: ["M425 " + ax + b.toFixed(3)], save: false });
            self.lastMsg("M425 " + ax + b.toFixed(3) + " aplicado");
        };
        self.applyBestBacklashSave = function () {
            var b = self.blBest(); if (b == null) return;
            var ax = self.blAxis().toUpperCase();
            self._post("calib_apply", { gcode: ["M425 " + ax + b.toFixed(3)], save: true });
            self.lastMsg("M425 " + ax + b.toFixed(3) + " + M500");
        };

        // --- Preview (canvas en el cliente) ---
        self.previewTraces = ko.observable(null);
        self.previewErr = ko.observable("");
        self.previewBusy = ko.observable(false);
        self._previewT = null;
        self.requestPreview = function () {
            var m = self.activeMeta();
            if (!m || m.type !== "pattern") return;
            clearTimeout(self._previewT);
            self._previewT = setTimeout(function () {
                self.previewBusy(true);
                var cp = self.collectParams();
                self._post("calib_test_render", { test: self.activeTest(),
                    fixed: cp.fixed, axes: cp.axes,
                    template: self.editOpen() ? self.editTemplate() : null });
            }, 350);
        };
        // Render ISOMÉTRICO 3D del patrón (proyección + color por altura). Ligero: agrupa
        // los segmentos en bandas de altura y dibuja una sola pasada por banda (rápido en Pi).
        self._drawPreview = function () {
            var tr = self.previewTraces();
            var cv = document.getElementById("bkcal-canvas");
            if (!cv) return;
            var ctx = cv.getContext("2d");
            var W = cv.width, H = cv.height;
            ctx.clearRect(0, 0, W, H);
            ctx.fillStyle = "#fbfcfd"; ctx.fillRect(0, 0, W, H);
            if (!tr || !tr.paths || !tr.paths.length) return;
            var b = tr.bounds, maxz = Math.max(0.2, b.maxz || 0.2);
            var C = Math.cos(Math.PI / 6), S = Math.sin(Math.PI / 6);   // iso 30°
            var mx = (b.minx + b.maxx) / 2, my = (b.miny + b.maxy) / 2;
            function proj(p) {
                var x = p[0] - mx, y = p[1] - my, z = p[2] || 0;
                return [(x - y) * C, (x + y) * S - z];                  // [sx, sy(abajo)]
            }
            // bounds proyectados para encuadrar
            var pmnx = 1e9, pmxx = -1e9, pmny = 1e9, pmxy = -1e9;
            tr.paths.forEach(function (path) {
                path.forEach(function (p) {
                    var q = proj(p);
                    if (q[0] < pmnx) pmnx = q[0]; if (q[0] > pmxx) pmxx = q[0];
                    if (q[1] < pmny) pmny = q[1]; if (q[1] > pmxy) pmxy = q[1];
                });
            });
            var pad = 12;
            var s = Math.min((W - 2 * pad) / Math.max(0.1, pmxx - pmnx),
                             (H - 2 * pad) / Math.max(0.1, pmxy - pmny));
            var ox = (W - (pmxx + pmnx) * s) / 2, oy = (H - (pmxy + pmny) * s) / 2;
            var BANDS = 16, buckets = [];
            for (var i = 0; i < BANDS; i++) buckets.push([]);
            tr.paths.forEach(function (path) {
                for (var j = 1; j < path.length; j++) {
                    var zmid = ((path[j - 1][2] || 0) + (path[j][2] || 0)) / 2;
                    var bi = Math.max(0, Math.min(BANDS - 1, Math.floor((zmid / maxz) * BANDS)));
                    buckets[bi].push(proj(path[j - 1]), proj(path[j]));
                }
            });
            ctx.lineWidth = 0.9; ctx.lineJoin = "round"; ctx.lineCap = "round";
            for (var bi = 0; bi < BANDS; bi++) {            // de abajo (azul) a arriba (rojo)
                var seg = buckets[bi]; if (!seg.length) continue;
                ctx.strokeStyle = "hsl(" + Math.round(0.66 * (1 - bi / (BANDS - 1)) * 360) + ",70%,48%)";
                ctx.beginPath();
                for (var k = 0; k < seg.length; k += 2) {
                    ctx.moveTo(seg[k][0] * s + ox, seg[k][1] * s + oy);
                    ctx.lineTo(seg[k + 1][0] * s + ox, seg[k + 1][1] * s + oy);
                }
                ctx.stroke();
            }
        };

        // "Ver en 3D": pide el render con fichero y lo carga en el Enhanced Viewer.
        self._wantView3d = false;
        self.view3d = function () {
            var m = self.activeMeta();
            if (!m || m.type !== "pattern") return;
            self._wantView3d = true;
            self.lastMsg("Preparando vista 3D…");
            var cp = self.collectParams();
            self._post("calib_test_render", { test: self.activeTest(), fixed: cp.fixed, axes: cp.axes,
                template: self.editOpen() ? self.editTemplate() : null, want_file: true });
        };

        self.selectTest = function (t) {
            self.activeTest(t.id);
            self.params((t.params || []).map(function (p) {
                var kind = p.kind || "size";
                var o = { k: p.k, label: p.label, min: p.min, max: p.max, step: p.step, u: p.u,
                          kind: kind, canVary: (kind === "temp" || kind === "fast" || kind === "geom"),
                          vary: ko.observable(!!p.vary), val: ko.observable(p["def"]),
                          vstart: ko.observable(p.vstart != null ? p.vstart : p["def"]),
                          vend: ko.observable(p.vend != null ? p.vend : p["def"]),
                          vstep: ko.observable(p.vstep != null ? p.vstep : p.step) };
                o.nvalues = ko.pureComputed(function () { return self._axisN(o.vstart(), o.vend(), o.vstep()); });
                ["vary", "val", "vstart", "vend", "vstep"].forEach(function (key) {
                    o[key].subscribe(function () { self.requestPreview(); });
                });
                return o;
            }));
            self.presetName("");
            self.editTemplate(t.template || "");
            self.editParamsJson(JSON.stringify(t.params || [], null, 1));
            self.editId(t.id || ""); self.editName(t.name || "");
            self.editOpen(false);
            self.previewTraces(null); self.previewErr("");
            self.requestPreview();
            if (t.type === "leveling") setTimeout(self._drawBed, 30);
        };

        // --- Editor de tests (plantilla Jinja + esquema de params) ---
        self.editOpen = ko.observable(false);
        self.editTemplate = ko.observable("");
        self.editParamsJson = ko.observable("[]");
        self.editId = ko.observable("");
        self.editName = ko.observable("");
        self.toggleEditor = function () {
            self.editOpen(!self.editOpen());
            if (self.editOpen()) self.requestPreview();   // previsualiza la plantilla editada
        };
        self.tryTemplate = function () { self.requestPreview(); };
        self.saveTest = function () {
            var m = self.activeMeta(); if (!m) return;
            var params;
            try { params = JSON.parse(self.editParamsJson()); }
            catch (e) { self.lastMsg("Esquema de params inválido (JSON)"); return; }
            var id = (self.editId() || m.id || "").trim().toLowerCase().replace(/[^a-z0-9_]/g, "");
            if (!id) { self.lastMsg("Falta el id del test"); return; }
            var def = { id: id, name: (self.editName() || m.name || id), icon: m.icon || "vial",
                        help: m.help || "", params: params, template: self.editTemplate() };
            self._post("calib_test_save", { def: def });
            self.lastMsg("Test '" + id + "' guardado");
        };
        self.duplicateTest = function () {
            var m = self.activeMeta(); if (!m) return;
            self.editId((m.id || "test") + "_copy"); self.editName((m.name || "") + " (copia)");
            self.editOpen(true);
        };
        self.deleteTest = function () {
            var t = self.activeTest(); if (!t) return;
            if (!confirm("¿Borrar el test '" + t + "'? (los built-in vuelven al defecto)")) return;
            self._post("calib_test_delete", { id: t });
        };

        // Separa en fixed (valor único) + axes (variados con rango/paso) para la matriz.
        self.collectParams = function () {
            var fixed = {}, axes = [];
            self.params().forEach(function (p) {
                if (p.canVary && p.vary()) {
                    axes.push({ k: p.k, start: num(p.vstart(), 0), end: num(p.vend(), 0), step: num(p.vstep(), 1) });
                } else {
                    fixed[p.k] = num(p.val(), p["def"]);
                }
            });
            return { fixed: fixed, axes: axes };
        };

        self.generate = function (doPrint) {
            var t = self.activeTest(); if (!t) return;
            self.lastMsg("Generando…");
            var cp = self.collectParams();
            self._post("calib_run", { test: t, fixed: cp.fixed, axes: cp.axes, print: !!doPrint });
        };

        // --- Presets ---
        self.currentPresets = ko.pureComputed(function () {
            var t = self.activeTest(); var d = self.presetList() || {};
            return (d[t] || []);
        });
        self.savePreset = function () {
            var t = self.activeTest(); var n = (self.presetName() || "").trim();
            if (!t || !n) { self.lastMsg("Pon un nombre al preset"); return; }
            self._post("calib_preset_save", { test: t, name: n, params: self.collectParams() });
        };
        self.loadPreset = function (pr) {
            self.params().forEach(function (p) {
                if (pr.params && pr.params[p.k] != null) p.val(pr.params[p.k]);
            });
            self.lastMsg("Preset '" + pr.name + "' cargado");
        };
        self.deletePreset = function (pr) {
            self._post("calib_preset_delete", { test: self.activeTest(), name: pr.name });
        };

        // --- PID ---
        self.pidHeater = ko.observable("hotend");
        self.pidCycles = ko.observable(8);
        self.pidTemp = ko.observable(200);
        self.pidFan = ko.observable(255);
        self.pidResult = ko.observable(null);
        self.pidBusy = ko.observable(false);
        self.runPid = function (save) {
            self.pidResult(null); self.pidBusy(true);
            self._post("calib_pid", { heater: self.pidHeater(), cycles: num(self.pidCycles(), 8),
                temp: num(self.pidTemp(), 200), fan: num(self.pidFan(), 255), save: !!save });
        };

        // --- E-steps ---
        self.esTemp = ko.observable(210);
        self.esLength = ko.observable(100);
        self.esSpeed = ko.observable(3);
        self.esMark = ko.observable(120);
        self.esRemained = ko.observable(20);
        self.esCurrent = ko.observable(null);
        self.esNew = ko.pureComputed(function () {
            var cur = num(self.esCurrent(), 0), mark = num(self.esMark(), 0), rem = num(self.esRemained(), 0);
            var extruded = mark - rem;
            if (cur <= 0 || extruded <= 0) return null;
            return Math.round(cur * mark / extruded * 100) / 100;
        });
        self.esReadCurrent = function () { self._post("calib_esteps_read"); };
        self.esExtrude = function () {
            self._post("calib_esteps_extrude", { temp: num(self.esTemp(), 210),
                length: num(self.esLength(), 100), speed: num(self.esSpeed(), 3) });
        };
        self.esApply = function (save) {
            var v = self.esNew(); if (!v) { self.lastMsg("Falta medir"); return; }
            self._post("calib_apply", { gcode: ["M92 E" + v], save: !!save });
            self.lastMsg("Aplicado M92 E" + v + (save ? " + M500" : ""));
        };

        // --- Historial / tracking ---
        self.markResult = function (h, result) {
            self._post("calib_history_mark", { id: h.id, result: result });
        };
        self.histSummary = function (h) {
            var p = h.params || {};
            return Object.keys(p).map(function (k) { return k + "=" + p[k]; }).join(" ");
        };

        // ===================== Nivelación de cama =====================
        // Sondea SOLO el rectángulo del plato (más pequeño que la cama declarada),
        // ajusta un plano y rellena los nodos de la malla del firmware por M421.
        self.lvBedX = ko.observable(350); self.lvBedY = ko.observable(380);   // recorrido
        self.lvX0 = ko.observable(50); self.lvY0 = ko.observable(25);
        self.lvW = ko.observable(190); self.lvH = ko.observable(240); self.lvInset = ko.observable(5);
        self.lvGridN = ko.observable(3); self.lvReps = ko.observable(1);   // 1: el firmware ya hace MULTIPLE_PROBING(4)
        self.lvClearance = ko.observable(8);       // mm de despegue entre puntos (sonda manual cuelga)
        self.lvApplyBacklash = ko.observable(true); // setear backlash Z medido tras nivelar
        self.lvBacklash = ko.observable(null);      // { z, applied } del último run
        // Robustez del ajuste (rechazo de bultos de pegamento) + shift de inclinación.
        self.lvFit = ko.observable(null);           // métricas del último ajuste de plano
        self.lvRobust = ko.observable(true);        // rechazar outliers al ajustar
        self.lvRejectK = ko.observable(2.5);        // umbral de rechazo = k·MAD
        self.lvCeiling = ko.observable(0.30);       // techo duro (mm): bulto mayor -> fuera
        self.lvProbeRetries = ko.observable(1);     // reintentos ante Probing Failed (inclinación)
        self.lvModel = ko.observable("plane");     // "plane" | "warp"
        self.lvMethod = ko.observable("g30_plate"); // "g30_plate" | "g29"
        self.lvRunning = ko.observable(false); self.lvStatus = ko.observable("idle"); self.lvDetail = ko.observable("");
        self.lvCoverage = ko.observable("");
        self.meshInfo = ko.observable("");
        self.lvCaps = ko.observable({ available: null, system: "unknown", grid_x: null, grid_y: null, slots: null, fw: null });
        self.lvFwN = ko.pureComputed(function () { var c = self.lvCaps(); return (c && c.grid_x) ? c.grid_x : 7; });
        self.lvCapsInfo = ko.pureComputed(function () {
            var c = self.lvCaps();
            if (c.available === false) return "esta impresora no reporta malla";
            if (c.available == null) return "detectando…";
            var sys = ({ ubl: "UBL", bilinear: "Bilineal", mbl: "MBL" })[c.system] || c.system;
            var g = (c.grid_x && c.grid_y) ? (c.grid_x + "×" + c.grid_y) : "?";
            return sys + " · " + g + " · " + (c.slots == null ? "?" : c.slots) + " slots" + (c.fw ? " · " + c.fw : "");
        });
        self.lvGridLabel = ko.pureComputed(function () { var n = self.lvGridN() | 0; return n + "×" + n + " · " + (n * n) + " pts"; });
        self.lvProgress = ko.pureComputed(function () {
            var m = (self.lvDetail() || "").match(/(\d+)\s*\/\s*(\d+)/);
            if (m) return Math.round(m[1] / Math.max(1, m[2]) * 100);
            return self.lvRunning() ? 6 : 0;
        });
        self.lvModelNote = ko.pureComputed(function () {
            return self.lvModel() === "warp"
                ? "Con alabeo: bilineal dentro del área sondeada; fuera se extrapola con la tendencia del plano."
                : "Cristal plano: 3–5 puntos definen el plano; los 49 nodos se rellenan exactos.";
        });
        self._outliers = [];   // bultos rechazados (coords de máquina) para marcar en el mapa
        self.lvFitInfo = ko.pureComputed(function () {
            var f = self.lvFit(); if (!f) return "";
            var s = "RMS " + f.rms_um + " µm · incl. X " + f.tilt_x_mm + " / Y " + f.tilt_y_mm + " mm";
            if (f.rejected) s += " · " + f.rejected + " bulto" + (f.rejected > 1 ? "s" : "") + " descartado" + (f.rejected > 1 ? "s" : "");
            return s + " · " + f.quality;
        });

        // Slots de malla + visor topográfico (mismo backend que la nivelación previa)
        self.slots = ko.observableArray([]); self.slotCount = ko.observable(0);
        self.selSlot = ko.observable(0); self.saveName = ko.observable("");
        self._slotZ = {}; self._activeMeshZ = null;
        self.slotsSummary = ko.pureComputed(function () {
            var f = 0, l = self.slots(); for (var i = 0; i < l.length; i++) if (l[i].filled) f++;
            return f + "/" + l.length + " llenos · " + (l.length - f) + " vacíos";
        });
        self.lvApplyCaps = function (caps) {
            if (!caps) return; self.lvCaps(caps);
            if (typeof caps.slots === "number" && caps.slots >= 0) self.slotCount(caps.slots);
            self._drawBed();
        };
        self.lvApplySlots = function (list, count) {
            if (typeof count === "number") self.slotCount(count);
            list = list || []; self._slotZ = {}; var out = [];
            for (var i = 0; i < list.length; i++) {
                var s = list[i] || {}; if (s.z) self._slotZ[i] = s.z;
                out.push({ idx: i, name: s.name || "", filled: !!s.filled, n: s.n || 0 });
            }
            self.slots(out);
        };
        self._renderMesh = function (z, opts, label) {
            if (typeof BKMesh === "undefined" || !z) { self.meshInfo("sin datos"); return; }
            var r = BKMesh.setMesh("bkmesh_canvas", z, opts || {});
            if (r) self.meshInfo(label + " · Δ " + ((r.max - r.min) * 1000).toFixed(0) + " µm ("
                + r.min.toFixed(3) + " … " + r.max.toFixed(3) + " mm)");
        };
        self.startLevel = function () {
            if (!confirm("¿Sonda DESPLEGADA? Va a homear y sondear el plato. Deja el brazo abajo.")) return;
            self._post("level", {
                home: true, method: self.lvMethod(), model: self.lvModel(), autoconfirm: true,
                grid_n_probe: self.lvGridN() | 0, probe_repeats: self.lvReps() | 0,
                probe_clearance: num(self.lvClearance(), 8),
                apply_backlash: !!self.lvApplyBacklash(),
                robust_reject: !!self.lvRobust(), reject_k: num(self.lvRejectK(), 2.5),
                reject_ceiling_mm: num(self.lvCeiling(), 0.30),
                probe_retries: self.lvProbeRetries() | 0,
                probe_area: {
                    x0: num(self.lvX0(), 50), y0: num(self.lvY0(), 25),
                    w: num(self.lvW(), 190), h: num(self.lvH(), 240), inset: num(self.lvInset(), 5)
                }
            });
            self.lvRunning(true); self.lvStatus("arrancando"); self.lvDetail(""); self.lvFit(null); self._outliers = [];
        };
        self.cancelLevel = function () { self._post("level_cancel"); };
        self.saveMesh = function () {
            if ((self.slotCount() | 0) <= 0) { self._post("gcode", { cmd: "M500" }); self.lastMsg("M500 (malla única guardada)"); return; }
            self._post("mesh_save", { slot: parseInt(self.selSlot(), 10) || 0, name: self.saveName() });
        };
        self.activateSlot = function (idx) { self._post("mesh_activate", { slot: idx }); };
        self.clearSlot = function (idx) { if (confirm("¿Vaciar el slot #" + idx + "?")) self._post("mesh_clear", { slot: idx }); };
        self.redetectCaps = function () { self._post("leveling_caps", { redetect: true }); };

        // --- Editor manual de malla (sin sonda) ---
        self.lvMode = ko.observable("auto");   // "auto" (sonda) | "manual" (editor)
        self.lvSel = ko.observable(null);      // {i,j} nodo seleccionado
        self.lvSelZ = ko.observable(null);     // Z actual del nodo seleccionado
        self.lvZStep = ko.observable(0.05);    // paso de jog Z del editor
        self.isManual = ko.pureComputed(function () { return self.lvMode() === "manual"; });
        self.lvSelLabel = ko.pureComputed(function () {
            var s = self.lvSel(); if (!s) return "ninguno";
            var x = self._fx ? Math.round(self._fx[s.i]) : "?", y = self._fy ? Math.round(self._fy[s.j]) : "?";
            return "(" + s.i + "," + s.j + ") · X" + x + " Y" + y;
        });
        self.setMode = function (m) {
            if (self.lvMode() === m) return;
            self.lvMode(m);
            if (m === "manual") { self._post("gcode", { cmd: "M420 S0" }); self._post("mesh_get"); self.lastMsg("Editor manual: compensación OFF (M420 S0)"); }
            else { self._post("gcode", { cmd: "M420 S1" }); self.lastMsg("Compensación ON (M420 S1)"); }
            setTimeout(self._drawBed, 20);
        };
        self.selectNode = function (i, j) {
            self.lvSel({ i: i, j: j });
            self.lvSelZ((self._activeMeshZ && self._activeMeshZ[i] && self._activeMeshZ[i][j] != null) ? self._activeMeshZ[i][j] : null);
            self._drawBed();
        };
        self.gotoNode = function () {
            var s = self.lvSel(); if (!s || !self._fx) { self.lastMsg("Selecciona un nodo en el mapa"); return; }
            self._post("mesh_goto", { x: self._fx[s.i], y: self._fy[s.j], z_clear: num(self.lvClearance(), 8) });
        };
        self.jogZ = function (dir) { self._post("jog", { z: dir * num(self.lvZStep(), 0.05) }); };
        self.fixNode = function () {
            var s = self.lvSel(); if (!s) { self.lastMsg("Selecciona un nodo primero"); return; }
            self._post("mesh_set_point", { i: s.i, j: s.j });
        };

        // Mapa de cama: marco (recorrido) + plato + puntos de sondeo + nodos del firmware.
        self._lin = function (a, b, k) { var o = []; if (k < 2) return [(a + b) / 2]; for (var i = 0; i < k; i++) o.push(a + (b - a) * i / (k - 1)); return o; };
        self._drawBed = function () {
            var cv = document.getElementById("bklvl-bed"); if (!cv) return;
            var ctx = cv.getContext("2d");
            var BEDX = num(self.lvBedX(), 350), BEDY = num(self.lvBedY(), 380);
            var W = cv.width, H = cv.height, padL = 28, padB = 18, padT = 8, padR = 10;
            var iw = W - padL - padR, ih = H - padT - padB;
            function X(x) { return padL + x / BEDX * iw; } function Y(y) { return padT + (1 - y / BEDY) * ih; }
            ctx.clearRect(0, 0, W, H);
            ctx.fillStyle = "#ffffff"; ctx.strokeStyle = "#cfd8dc"; ctx.lineWidth = 1.4;
            ctx.beginPath(); ctx.rect(X(0), Y(BEDY), iw, ih); ctx.fill(); ctx.stroke();
            ctx.strokeStyle = "#eceff1"; ctx.fillStyle = "#90a4ae"; ctx.lineWidth = 1;
            ctx.font = "10px ui-monospace,Menlo,monospace"; ctx.textAlign = "center"; ctx.textBaseline = "top";
            for (var gx = 0; gx <= BEDX; gx += 100) { ctx.beginPath(); ctx.moveTo(X(gx), Y(0)); ctx.lineTo(X(gx), Y(BEDY)); ctx.stroke(); ctx.fillText(gx, X(gx), Y(0) + 3); }
            ctx.textAlign = "right"; ctx.textBaseline = "middle";
            for (var gy = 0; gy <= BEDY; gy += 100) { ctx.beginPath(); ctx.moveTo(X(0), Y(gy)); ctx.lineTo(X(BEDX), Y(gy)); ctx.stroke(); ctx.fillText(gy, X(0) - 3, Y(gy)); }
            var x0 = num(self.lvX0(), 50), y0 = num(self.lvY0(), 25), w = num(self.lvW(), 190), h = num(self.lvH(), 240), ins = num(self.lvInset(), 5);
            ctx.fillStyle = "rgba(43,108,176,.10)"; ctx.strokeStyle = "#2b6cb0"; ctx.lineWidth = 1.5;
            ctx.beginPath(); ctx.rect(X(x0), Y(y0 + h), w / BEDX * iw, h / BEDY * ih); ctx.fill(); ctx.stroke();
            var n = self.lvFwN() | 0, fx = self._lin(1, BEDX - 1, n), fy = self._lin(1, BEDY - 1, n), inN = 0;
            self._fx = fx; self._fy = fy;   // nodos del firmware (para editor manual)
            self._bedGeom = { padL: padL, padT: padT, iw: iw, ih: ih, BEDX: BEDX, BEDY: BEDY };
            for (var i = 0; i < n; i++) for (var j = 0; j < n; j++) {
                var nx = fx[i], ny = fy[j], insd = (nx >= x0 && nx <= x0 + w && ny >= y0 && ny <= y0 + h);
                if (insd) inN++;
                var px = X(nx), py = Y(ny);
                if (insd) { ctx.fillStyle = "#2b6cb0"; ctx.beginPath(); ctx.arc(px, py, 2.4, 0, 7); ctx.fill(); }
                else { ctx.strokeStyle = "#90a4ae"; ctx.setLineDash([2, 2]); ctx.beginPath(); ctx.arc(px, py, 2.4, 0, 7); ctx.stroke(); ctx.setLineDash([]); }
            }
            var gn = self.lvGridN() | 0, pxs = self._lin(x0 + ins, x0 + w - ins, gn), pys = self._lin(y0 + ins, y0 + h - ins, gn);
            for (var a = 0; a < gn; a++) for (var b = 0; b < gn; b++) {
                var qx = X(pxs[a]), qy = Y(pys[b]);
                ctx.strokeStyle = "#4a90d9"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(qx, qy, 4.5, 0, 7); ctx.stroke();
                ctx.lineWidth = 1.1; ctx.beginPath(); ctx.moveTo(qx - 2.4, qy); ctx.lineTo(qx + 2.4, qy); ctx.moveTo(qx, qy - 2.4); ctx.lineTo(qx, qy + 2.4); ctx.stroke();
            }
            // Bultos de pegamento rechazados por el ajuste robusto (coords de máquina).
            if (self._outliers && self._outliers.length) {
                ctx.strokeStyle = "#e74c3c"; ctx.lineWidth = 2;
                for (var o = 0; o < self._outliers.length; o++) {
                    var ox = X(self._outliers[o].x), oy = Y(self._outliers[o].y);
                    ctx.beginPath(); ctx.arc(ox, oy, 6, 0, 7); ctx.stroke();
                    ctx.beginPath(); ctx.moveTo(ox - 4, oy - 4); ctx.lineTo(ox + 4, oy + 4);
                    ctx.moveTo(ox + 4, oy - 4); ctx.lineTo(ox - 4, oy + 4); ctx.stroke();
                }
            }
            // Editor manual: resaltar el nodo seleccionado.
            var sel = self.lvSel();
            if (sel && self.isManual()) {
                var sx = X(fx[sel.i]), sy = Y(fy[sel.j]);
                ctx.strokeStyle = "#27ae60"; ctx.lineWidth = 2.5;
                ctx.beginPath(); ctx.arc(sx, sy, 7, 0, 7); ctx.stroke();
            }
            self.lvCoverage(inN + " / " + (n * n) + " nodos en el plato");

            // Click en el mapa -> seleccionar el nodo más cercano (solo modo manual).
            if (!cv._bkClickAttached) {
                cv._bkClickAttached = true;
                cv.style.cursor = "crosshair";
                cv.addEventListener("click", function (e) {
                    if (!self.isManual() || !self._bedGeom || !self._fx) return;
                    var g = self._bedGeom, r = cv.getBoundingClientRect();
                    var cxp = (e.clientX - r.left) * (cv.width / r.width);
                    var cyp = (e.clientY - r.top) * (cv.height / r.height);
                    var mx = (cxp - g.padL) / g.iw * g.BEDX;
                    var my = (1 - (cyp - g.padT) / g.ih) * g.BEDY;
                    var best = null, bd = 1e18;
                    for (var i = 0; i < self._fx.length; i++) for (var j = 0; j < self._fy.length; j++) {
                        var d = (self._fx[i] - mx) * (self._fx[i] - mx) + (self._fy[j] - my) * (self._fy[j] - my);
                        if (d < bd) { bd = d; best = { i: i, j: j }; }
                    }
                    if (best) self.selectNode(best.i, best.j);
                });
            }
        };
        ["lvX0", "lvY0", "lvW", "lvH", "lvInset", "lvGridN"].forEach(function (k) { self[k].subscribe(function () { self._drawBed(); }); });


        // ===================== Wizard de tramado manual =====================
        // Observed by T8 (boostkit_calib_tab.jinja2 wizard section).
        // Contract: tramStart tramActive tramCorner tramGoCorner tramZJog
        //          tramRegister tramResults tramPaper tramSave tramCancel
        //          tramStatus tramStepZ

        self.tramActive = ko.observable(false);
        self.tramCorner = ko.observable(0);        // 0=fl, 1=fr, 2=bl, 3=br
        self.tramOrder = ["fl", "fr", "bl", "br"];
        self.tramResults = ko.observableArray([null, null, null, null]);
        self.tramPaper = ko.observable(0.10);   // mm
        self.tramStepZ = ko.observable(0.05);   // fine Z step mm
        self.tramStatus = ko.observable("");

        // Corner labels for user-facing messages.
        function cornerLabel(i) {
            var labels = ["Front Left", "Front Right", "Back Left", "Back Right"];
            return labels[i] || ("" + i);
        }

        // Internal: reset wizard state after cancel/save.
        function tramEnd() {
            self.tramActive(false);
            self.tramCorner(0);
            self.tramResults([null, null, null, null]);
        }

        // Gate: block tram if printer is currently printing.
        self.tramStart = function () {
            if (self.printerState.isPrinting && self.printerState.isPrinting()) {
                new PNotify({ title: "Acción bloqueada", text: "No se puede tramar durante la impresión", type: "error" });
                return;
            }
            if (!confirm("¿Iniciar tramado manual? Se hará home y se desactivará la compensación (M420 S0).")) return;
            self._post("tram_start", { home_first: true });
            self.tramActive(true);
            self.tramStatus("Wizard iniciado. Primera esquina: Front Left");
            tramGoCorner(0);
        };

        // Navigate to corner i (0-3); guard out-of-range.
        function tramGoCorner(i) {
            if (i < 0 || i > 3) return;
            self.tramCorner(i);
            self._post("tram_goto", { corner: self.tramOrder[i] });
            self.tramStatus("Esquina: " + cornerLabel(i) + " — baja Z con los botones ↓/↑ hasta que el papel roce, luego Registra");
        }

        // Exposed for the UI (T8 binds tramGoCorner directly).
        self.tramGoCorner = tramGoCorner;

        // Jog Z by tramStepZ * dir (dir = +1 or -1).
        self.tramZJog = function (dir) {
            self._post("tram_z", { z: self.tramStepZ() * dir });
        };

        // Register Z at current corner; store result or show error.
        self.tramRegister = function () {
            self._post("tram_register", { corner: self.tramOrder[self.tramCorner()] })
                .done(function (r) {
                    if (r && r.z != null) {
                        self.tramResults()[self.tramCorner()] = r.z;
                        self.tramResults.valueHasMutated();
                        self.tramStatus("Z en " + cornerLabel(self.tramCorner()) + ": " + r.z.toFixed(3) + " mm — ajusta el tornillo si es necesario");
                    } else {
                        self.tramStatus("Error: " + (r && r.error ? r.error : "respuesta inválida"));
                    }
                });
        };

        // Save mesh: require all 4 corners registered; subtract paper thickness.
        self.tramSave = function () {
            var results = self.tramResults();
            for (var i = 0; i < 4; i++) { if (results[i] == null) {
                self.tramStatus("Registra las 4 esquinas antes de guardar"); return;
            } }
            if (!confirm("¿Guardar malla interpolada con los 4 puntos? Se restará el grosor de papel.")) return;
            self._post("tram_mesh_save", {
                z_fl: results[0], z_fr: results[1], z_bl: results[2], z_br: results[3],
                paper_thickness: self.tramPaper()
            }).done(function () {
                tramEnd();
                self.tramStatus("Malla guardada y activada (M421 + M420 S1)");
            });
        };

        // Cancel wizard and restore compensation.
        self.tramCancel = function () {
            self._post("tram_cancel");
            tramEnd();
            self.tramStatus("Tramado cancelado — compensación reactivada (M420 S1)");
        };

        // --- Mensajes del backend ---

        self.onDataUpdaterPluginMessage = function (plugin, message) {
            if (plugin !== "boostkit" || !message) return;
            if (message.type === "leveling") {
                var l = message.state || {}; self.lvRunning(!!l.running); self.lvStatus(l.status || "idle"); self.lvDetail(l.detail || ""); self._drawBed();
            } else if (message.type === "slots") {
                self.lvApplySlots(message.slots, message.count);
            } else if (message.type === "leveling_caps") {
                self.lvApplyCaps(message.caps);
            } else if (message.type === "mesh") {
                var mm = message.mesh || {}; if (mm.z) { self._activeMeshZ = mm.z; self._meshXs = mm.xs; self._meshYs = mm.ys; self._renderMesh(mm.z, { xs: mm.xs, ys: mm.ys }, "Activa"); }
            } else if (message.type === "mesh_edit") {
                if (message.event === "set") {
                    if (self._activeMeshZ && self._activeMeshZ[message.i]) {
                        self._activeMeshZ[message.i][message.j] = message.z;
                        self._renderMesh(self._activeMeshZ, { xs: self._meshXs, ys: self._meshYs }, "Editada");
                    }
                    self.lvSelZ(message.z);
                    self.lastMsg("Nodo (" + message.i + "," + message.j + ") fijado a Z" + message.z.toFixed(3));
                    self._drawBed();
                } else if (message.event === "error") { self.lastMsg("Editor: " + (message.msg || "error")); }
            } else if (message.type === "leveling_fit") {
                self.lvFit(message);
                self._outliers = message.outliers || [];
                self._drawBed();
            } else if (message.type === "leveling_backlash") {
                self.lvBacklash({ z: message.z, applied: !!message.applied });
            } else if (message.type === "calib_caps") {
                self.caps(message.caps);
            } else if (message.type === "calib_tests") {
                self.applyTests(message.tests || []);
                if (!self.activeTest() && self.TESTS().length) self.selectTest(self.TESTS()[0]);
            } else if (message.type === "calib_preview") {
                self.previewBusy(false);
                if (message.count != null) self.specimenCount(message.count);
                if (message.bands != null) self.tempBands(message.bands);
                self.overflow(!!message.overflow);
                if (message.error) { self.previewErr(message.error); self.previewTraces(null); self._wantView3d = false; }
                else {
                    self.previewErr(""); self.previewTraces(message.traces); self._drawPreview();
                    // Si se pidió "Ver en 3D", carga el fichero en el Enhanced Viewer + activa su tab.
                    if (message.file && self._wantView3d) {
                        self._wantView3d = false;
                        try {
                            if (window.bkGcode3D) window.bkGcode3D.loadFile(message.file);
                            var lnk = document.querySelector('a[href="#tab_plugin_boostkit_viewer"]');
                            if (lnk) lnk.click();
                            self.lastMsg("Cargado en el visor 3D");
                        } catch (e) { self.lastMsg("No se pudo abrir el visor 3D"); }
                    }
                }
            } else if (message.type === "calib_presets") {
                self.presetList(message.presets || {});
            } else if (message.type === "calib_history") {
                self.history(message.history || []);
            } else if (message.type === "calib") {
                self._onCalibEvent(message);
            }
        };
        self._onCalibEvent = function (m) {
            switch (m.event) {
                case "generated":
                    self.lastMsg("Generado " + m.file + " (" + m.lines + " líneas)" + (m.started ? " · imprimiendo" : ""));
                    break;
                case "gen_error": self.lastMsg("Error: " + m.msg); break;
                case "pid_started": self.lastMsg("PID en curso…"); break;
                case "pid_done":
                    self.pidBusy(false); self.pidResult(m.pid);
                    self.lastMsg("PID " + m.heater + ": P" + m.pid.p + " I" + m.pid.i + " D" + m.pid.d + (m.saved ? " (guardado)" : ""));
                    break;
                case "pid_timeout": self.pidBusy(false); self.lastMsg("PID: sin respuesta (timeout)"); break;
                case "pid_error": self.pidBusy(false); self.lastMsg("PID error: " + m.msg); break;
                case "esteps_m92": if (m.m92 && m.m92.e != null) self.esCurrent(m.m92.e); break;
                case "esteps_extruded": self.lastMsg("Extruidos " + m.length + " mm — mide el sobrante"); break;
                case "backlash_started":
                    self.blOrig(m.m425_orig);
                    self.lastMsg("Backlash " + (m.axis || "").toUpperCase() + " en curso… (M425 orig=" + (m.m425_orig != null ? m.m425_orig.toFixed(3) : "?") + ")");
                    break;
                case "backlash_sample":
                    self.blSamples.push({ m425: m.m425, error: m.error });
                    break;
                case "backlash_done":
                    self.blBusy(false);
                    self.blBest(m.best_m425);
                    self.blBestErr(m.best_error);
                    self.lastMsg("Backlash " + (m.axis || "").toUpperCase() + " · mejor M425=" + (m.best_m425 != null ? m.best_m425.toFixed(3) : "n/a") + " mm (err=" + (m.best_error != null ? m.best_error.toFixed(4) : "?") + ")");
                    break;
                case "backlash_cancelled":
                    self.blBusy(false);
                    self.lastMsg("Backlash " + (m.axis || "").toUpperCase() + " cancelado");
                    break;
                case "backlash_error":
                    self.lastMsg("Backlash error: " + (m.msg || "?") + (m.m425 != null ? " @M425=" + m.m425.toFixed(3) : ""));
                    break;
            }
        };

        self._loaded = false;
        self.onAfterTabChange = function (current) {
            if (current !== "#tab_plugin_boostkit_calib") return;
            self._post("calib_caps");
            self._post("leveling_caps", {});   // capacidades de nivelación (slots, malla)
            self._post("mesh_slots");
            setTimeout(self._drawBed, 30);
            if (!self._loaded) {
                self._loaded = true;
                self._post("calib_tests");      // tests data-driven (incluye plantillas)
                self._post("calib_presets");
                self._post("calib_history");
            } else if (self.activeTest()) {
                self.requestPreview();           // re-dibuja al volver al tab
            }
        };
        self.onStartupComplete = function () { self._post("calib_caps"); self._post("calib_tests"); self._post("leveling_caps", {}); };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: BoostCalibViewModel,
        dependencies: ["settingsViewModel", "printerStateViewModel", "printerProfilesViewModel"],
        elements: ["#tab_plugin_boostkit_calib"],
    });
});
