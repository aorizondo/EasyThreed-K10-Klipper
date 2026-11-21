/*
 * Boost Kit — Visor 3D dentro del tab GCode Viewer nativo (módulo gcode3d).
 *
 * REEMPLAZA el contenido del visor 2D nativo (no se superpone). Three.js r108
 * (vendorizado de PrettyGCode). UN solo renderer WebGL OFFSCREEN cuyo resultado
 * se copia con drawImage a cada canvas 2D visible (tab + sidebar + gizmo): Three
 * r108 no soporta 2 renderers sobre la misma escena (el 2º se queda en negro).
 *
 *  - Cabezal con ANIMACIÓN fidedigna: se mueve a la velocidad real (feedrate) con
 *    rampa de aceleración, no teletransporta. El trail crece según avanza.
 *  - Cubo de navegación (estilo FreeCAD): caras=ejes, esquinas triangulares=45°,
 *    + botones de rotación para alcanzar caras ocultas. Camera-controls aislado
 *    como BKCameraControls (PrettyGCode trae su propia copia parcheada "CURA").
 *  - Mini-visor en la tarjeta "Gcode" del sidebar SINCRONIZADO con el tab.
 *  - Proyección Perspectiva ↔ Isométrica con transición sin saltos de escala.
 */
$(function () {
    "use strict";

    function BoostGcode3DViewModel(parameters) {
        var self = this;
        // Referencia global para que otras tabs (Calibración → "Ver en 3D") puedan
        // cargar un gcode en este visor sin inyección de dependencias por nombre.
        try { window.bkGcode3D = self; } catch (e) { /* no-op */ }
        self.settings = parameters[0];
        self.profiles = parameters[1];
        self.printerState = parameters[2];

        self._injected = false;
        self._active = false;
        self._inited = false;
        self._needRender = true;
        self._ortho = false;

        self._three = {scene: null, camera: null, renderer: null, controls: null,
                       clock: null, canvas: null, ctx: null, persCam: null, orthoCam: null};
        self._bedGroup = null; self._bedInfo = null;
        self._font = null;            // fuente 3D (helvetiker) cacheada para rótulos
        self._envMap = null;          // environment map procedural para el metal
        self._bedMeshZ = null;        // malla de nivelación activa (para superponer)
        self._bedMargin = 10;         // mm de cama no alcanzable (X-/Y-)
        self._showBed = true;         // dibujar la cama
        self._showBedMesh = false;    // superponer relieve de la malla
        self._nozzle = null;
        self._trailLine = null; self._liveLine = null;
        self._modelGroup = null;
        // navegación/selección del gcode cargado
        self._gmodel = null;     // {moves, layers, maxLayer}
        self._modelPath = null;  // archivo cargado (para persistir/recargar)
        self._modelLo = 0;       // rango de capas VISIBLE del modelo (pelar capas)
        self._modelHi = 1e9;
        self._pendingSel = null; // selección a restaurar tras recargar el modelo
        self._vsTimer = null;    // debounce de guardado de estado del visor
        self._selIdx = -1;       // índice del movimiento seleccionado
        self._selT = 0;          // fracción 0..1 dentro del movimiento (ajuste fino)
        self._selMarker = null;  // marcador 3D del punto seleccionado
        self._selBall = null;    // la bola (tamaño constante en pantalla)
        self._navLayer = 0;

        self._nav = {ctx: null, scene: null, camera: null, gizmo: null, mesh: null,
                     canvas: null, raycaster: null};
        self._side = {ctx: null, canvas: null, host: null};
        self._tabSize = {w: 560, h: 400};
        self._sideSize = {w: 200, h: 170};
        self._lineRes = null;

        // --- animación de movimiento (simulador cinemático cliente) ---
        self._trail = [];                 // puntos COMMITTED [x,y,z,e]
        self._motionQ = [];               // cola de destinos {x,y,z,e,f}
        self._simPos = null;              // THREE.Vector3 posición animada
        self._simVel = 0;                 // mm/s actual
        self._simAccel = 1500;            // mm/s² (se ajusta desde M204 del firmware)
        self._simMaxFeed = 800;           // mm/s tope (se ajusta desde M203)
        self._trailLen = 400;        // límite en nº de movimientos
        self._trailMode = "moves";   // "moves" | "dist"
        self._trailDistMm = 2000;    // límite en mm (= cm*10) recorridos
        self._showTrail = true;
        self._followCam = false;
        // coloreado/ancho del trazado estilo slicer
        self._colorMode = "type";    // type | temp | speed | flow
        self._showTravel = true;     // travels punteados
        self._widthByFlow = true;    // ancho ∝ caudal
        self._tempLo = 150;          // °C azul
        self._tempHi = 300;          // °C rojo
        self._extrudeLines = [];     // LineSegments2 de extrusión (por bucket)
        self._travelLine = null;     // LineSegments2 punteado de travels

        // ---------------------------------------------------------------
        // DOM
        // ---------------------------------------------------------------
        self._inject = function () {
            if (self._injected) return true;
            // Tab propio "Enhanced Viewer" (#bk3d_container). Antes se inyectaba en
            // #canvas_container (GCode Viewer nativo); ahora no se toca el nativo.
            var cc = document.getElementById("bk3d_container");
            if (!cc) return false;
            var overlay = document.createElement("div");
            overlay.id = "bk3d_overlay";
            overlay.className = "bk3d-overlay";
            overlay.innerHTML =
                '<canvas id="bk3d_canvas"></canvas>' +
                '<canvas id="bk3d_navcube" class="bk3d-navcube" title="Cubo de navegación: clic en una cara/esquina"></canvas>' +
                '<div id="bk3d_navbtns" class="bk3d-navbtns">' +
                '  <button data-rot="up" title="Girar arriba">▲</button>' +
                '  <div class="bk3d-navrow">' +
                '    <button data-rot="left" title="Girar izquierda">◀</button>' +
                '    <button data-rot="home" title="Encuadrar">⌂</button>' +
                '    <button data-rot="right" title="Girar derecha">▶</button>' +
                '  </div>' +
                '  <button data-rot="down" title="Girar abajo">▼</button>' +
                '</div>' +
                '<span id="bk3d_projlabel" class="bk3d-projlabel"></span>';
            cc.appendChild(overlay);
            self._three.canvas = overlay.querySelector("#bk3d_canvas");
            self._nav.canvas = overlay.querySelector("#bk3d_navcube");

            var bar = document.createElement("div");
            bar.className = "bk3d-toolbar btn-toolbar";
            bar.innerHTML =
                '<div class="btn-group">' +
                '  <button id="bk3d_toggle" class="btn btn-small btn-primary" type="button"><i class="fas fa-cube"></i> Ver en 3D</button>' +
                '</div>' +
                '<div class="btn-group bk3d-when-active">' +
                '  <button id="bk3d_proj" class="btn btn-small" type="button"><i class="fas fa-video"></i> Perspectiva</button>' +
                '  <button id="bk3d_resetcam" class="btn btn-small" type="button"><i class="fas fa-expand"></i> Encuadrar</button>' +
                '</div>' +
                '<div class="btn-group bk3d-when-active">' +
                '  <button id="bk3d_trail" class="btn btn-small active" type="button"><i class="fas fa-route"></i> Trail</button>' +
                '  <button id="bk3d_model" class="btn btn-small" type="button"><i class="fas fa-layer-group"></i> Modelo 3D</button>' +
                '  <button id="bk3d_follow" class="btn btn-small" type="button"><i class="fas fa-crosshairs"></i> Seguir cabezal</button>' +
                '</div>' +
                '<div class="btn-group bk3d-when-active">' +
                '  <button id="bk3d_bed" class="btn btn-small active" type="button" title="Mostrar la cama (120×120 con el 100×100 alcanzable)"><i class="fas fa-border-all"></i> Cama</button>' +
                '  <button id="bk3d_bedmesh" class="btn btn-small" type="button" title="Superponer la malla de nivelación activa"><i class="fas fa-mountain"></i> Malla</button>' +
                '</div>' +
                '<div class="btn-group bk3d-when-active bk3d-trailctl">' +
                '  <span class="bk3d-trail-label">Últimos</span>' +
                '  <input id="bk3d_nval" type="number" min="1" value="400" title="Límite del trazado">' +
                '  <select id="bk3d_nunit" class="bk3d-sel">' +
                '    <option value="moves">mov.</option>' +
                '    <option value="dist">cm</option>' +
                '  </select>' +
                '</div>' +
                '<div class="btn-group bk3d-when-active bk3d-trailctl">' +
                '  <span class="bk3d-trail-label">Color</span>' +
                '  <select id="bk3d_colormode" class="bk3d-sel">' +
                '    <option value="type">Tipo</option>' +
                '    <option value="temp">Temperatura</option>' +
                '    <option value="speed">Velocidad</option>' +
                '    <option value="flow">Flujo</option>' +
                '  </select>' +
                '  <button id="bk3d_travel" class="btn btn-small active" type="button" title="Mostrar travels (punteado)">Travels</button>' +
                '  <button id="bk3d_wflow" class="btn btn-small active" type="button" title="Ancho según flujo">Ancho∝flujo</button>' +
                '  <span id="bk3d_temprange" class="bk3d-temprange">' +
                '    <input id="bk3d_templo" type="number" value="150" title="°C frío (azul)"> –' +
                '    <input id="bk3d_temphi" type="number" value="300" title="°C caliente (rojo)"> °C' +
                '  </span>' +
                '</div>' +
                '<div class="btn-group bk3d-when-active bk3d-layerctl" id="bk3d_layerctl" style="display:none">' +
                '  <span class="bk3d-trail-label">Capas</span>' +
                '  <input id="bk3d_laymin" class="bk3d-layslider" type="range" min="0" max="0" value="0" title="Primera capa visible">' +
                '  <input id="bk3d_laymax" class="bk3d-layslider" type="range" min="0" max="0" value="0" title="Última capa visible (pela las de arriba para ver/seleccionar el relleno)">' +
                '  <span id="bk3d_laylbl" class="bk3d-trail-label">—</span>' +
                '</div>' +
                '<span id="bk3d_hud" class="bk3d-hud bk3d-when-active"></span>';
            cc.parentNode.insertBefore(bar, cc);

            document.getElementById("bk3d_toggle").addEventListener("click", function () { self.setActive(!self._active); });
            document.getElementById("bk3d_proj").addEventListener("click", function () { self.setProjection(!self._ortho); });
            document.getElementById("bk3d_resetcam").addEventListener("click", function () { self.frameBed(true); });
            document.getElementById("bk3d_bed").addEventListener("click", function () { self.toggleBed(); });
            document.getElementById("bk3d_bedmesh").addEventListener("click", function () { self.toggleBedMesh(); });
            document.getElementById("bk3d_trail").addEventListener("click", function () {
                self._showTrail = !self._showTrail; this.classList.toggle("active", self._showTrail);
                self._rebuildTrail(); self._requestRender(); self._saveViewState();
            });
            document.getElementById("bk3d_model").addEventListener("click", function () { self.toggleModel(); });
            // slider de rango de capas visibles (pelar capas para ver/seleccionar relleno)
            var layTimer = null;
            function onLayerSlider(ev) {
                var lmin = document.getElementById("bk3d_laymin"), lmax = document.getElementById("bk3d_laymax");
                var lo = parseInt(lmin.value, 10) || 0, hi = parseInt(lmax.value, 10) || 0;
                if (lo > hi) {   // no cruzar los mangos
                    if (ev && ev.target === lmin) { hi = lo; lmax.value = hi; } else { lo = hi; lmin.value = lo; }
                }
                self._modelLo = lo; self._modelHi = hi;
                var lbl = document.getElementById("bk3d_laylbl"); if (lbl) lbl.textContent = lo + "–" + hi;
                if (layTimer) clearTimeout(layTimer);
                layTimer = setTimeout(function () { if (self._gmodel) self._buildGModelGeometry(); }, 50);
            }
            document.getElementById("bk3d_laymin").addEventListener("input", onLayerSlider);
            document.getElementById("bk3d_laymax").addEventListener("input", onLayerSlider);
            document.getElementById("bk3d_follow").addEventListener("click", function () {
                self._followCam = !self._followCam; this.classList.toggle("active", self._followCam); self._saveViewState();
            });
            function trailLimitChanged() {
                var v = parseFloat(document.getElementById("bk3d_nval").value) || 1;
                if (self._trailMode === "moves") self._trailLen = Math.max(1, Math.round(v));
                else self._trailDistMm = Math.max(1, v) * 10;   // cm -> mm
                self._trimTrail();
                self._rebuildTrail(); self._requestRender(); self._saveViewState();
            }
            document.getElementById("bk3d_nval").addEventListener("input", trailLimitChanged);
            document.getElementById("bk3d_nunit").addEventListener("change", function () {
                self._trailMode = this.value;
                var inp = document.getElementById("bk3d_nval");
                inp.value = (self._trailMode === "moves") ? self._trailLen : (self._trailDistMm / 10);
                self._trimTrail(); self._rebuildTrail(); self._requestRender(); self._saveViewState();
            });
            document.getElementById("bk3d_colormode").addEventListener("change", function () {
                self._colorMode = this.value;
                document.getElementById("bk3d_temprange").style.display = (this.value === "temp") ? "" : "none";
                self._rebuildTrail(); self._requestRender(); self._saveViewState();
            });
            document.getElementById("bk3d_travel").addEventListener("click", function () {
                self._showTravel = !self._showTravel; this.classList.toggle("active", self._showTravel);
                self._rebuildTrail(); self._requestRender(); self._saveViewState();
            });
            document.getElementById("bk3d_wflow").addEventListener("click", function () {
                self._widthByFlow = !self._widthByFlow; this.classList.toggle("active", self._widthByFlow);
                self._rebuildTrail(); self._requestRender(); self._saveViewState();
            });
            function tempRangeChanged() {
                self._tempLo = parseFloat(document.getElementById("bk3d_templo").value) || 150;
                self._tempHi = parseFloat(document.getElementById("bk3d_temphi").value) || 300;
                if (self._colorMode === "temp") { self._rebuildTrail(); self._requestRender(); }
                self._saveViewState();
            }
            document.getElementById("bk3d_templo").addEventListener("change", tempRangeChanged);
            document.getElementById("bk3d_temphi").addEventListener("change", tempRangeChanged);
            document.getElementById("bk3d_temprange").style.display = "none";
            // botones de rotación junto al cubo (para alcanzar caras ocultas)
            var AZ = Math.PI / 4, POL = Math.PI / 6;   // 45° azimut, 30° polar
            Array.prototype.forEach.call(overlay.querySelectorAll("#bk3d_navbtns button"), function (b) {
                b.addEventListener("click", function () {
                    var c = self._three.controls; if (!c) return;
                    var r = b.getAttribute("data-rot");
                    if (r === "left") c.rotate(AZ, 0, true);
                    else if (r === "right") c.rotate(-AZ, 0, true);
                    else if (r === "up") c.rotate(0, -POL, true);
                    else if (r === "down") c.rotate(0, POL, true);
                    else if (r === "home") self.frameBed(true);
                    self._requestRender();
                });
            });

            // clic en el visor (sin arrastrar) = seleccionar movimiento del modelo
            var down = null;
            self._three.canvas.addEventListener("pointerdown", function (ev) {
                if (ev.button === 0) down = {x: ev.clientX, y: ev.clientY, t: Date.now()};
            });
            self._three.canvas.addEventListener("pointerup", function (ev) {
                if (!down || !self._gmodel) { down = null; return; }
                var dx = ev.clientX - down.x, dy = ev.clientY - down.y;
                if (Math.abs(dx) < 5 && Math.abs(dy) < 5 && Date.now() - down.t < 400) self._pickMove(ev.clientX, ev.clientY);
                down = null;
            });

            self._injected = true;
            return true;
        };

        self.setActive = function (on) {
            if (!self._inject()) return;
            self._active = on;
            var cc = document.getElementById("bk3d_container");
            cc.classList.toggle("bk3d-active", on);
            if (cc.parentNode) cc.parentNode.classList.toggle("bk3d-hostactive", on);
            var bar = document.querySelector(".bk3d-toolbar");
            if (bar) bar.classList.toggle("bk3d-on", on);
            var btn = document.getElementById("bk3d_toggle");
            btn.classList.toggle("btn-primary", !on); btn.classList.toggle("btn-success", on);
            btn.innerHTML = on ? '<i class="fas fa-border-all"></i> Ver en 2D' : '<i class="fas fa-cube"></i> Ver en 3D';
            if (on) { self._ensureInit(); self._resize(); self.frameBed(false); self._rebuildTrail(); self._requestRender(); }
            self._saveViewState();
        };

        // ---------------------------------------------------------------
        // Escena
        // ---------------------------------------------------------------
        self._ensureInit = function () {
            if (self._inited) return;
            if (!window.THREE || !THREE.Line2 || !THREE.LineGeometry || !THREE.OBJLoader || !window.BKCameraControls) {
                setTimeout(self._ensureInit, 150); return;
            }
            var t = self._three;
            t.scene = new THREE.Scene();
            t.scene.background = new THREE.Color(0xf4f5f7);
            t.clock = new THREE.Clock();
            self._simPos = new THREE.Vector3(0, 0, 0);
            // si ya llegó una sincronización de posición antes de construir la escena
            if (self._lastSync) {
                self._simPos.set(self._lastSync.x, self._lastSync.y, self._lastSync.z);
                self._trail = [[self._lastSync.x, self._lastSync.y, self._lastSync.z, 0, 0, self._lastSync.temp, 0]];
            }
            try {
                var s = self.settings.settings.plugins.boostkit;
                if (s && s.gcode3d && s.gcode3d.sim_accel) self._simAccel = parseFloat(s.gcode3d.sim_accel()) || 1500;
            } catch (e) {}

            t.persCam = new THREE.PerspectiveCamera(50, 1, 0.1, 100000);
            t.persCam.up.set(0, 0, 1);
            t.orthoCam = new THREE.OrthographicCamera(-100, 100, 100, -100, -100000, 100000);
            t.orthoCam.up.set(0, 0, 1);
            t.camera = t.persCam;

            t.renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, preserveDrawingBuffer: true});
            t.renderer.setPixelRatio(1);
            t.ctx = t.canvas.getContext("2d"); t.ctx.imageSmoothingEnabled = false;

            // Iluminación para que los cuerpos (nozzle) se vean 3D y metálicos sin
            // sobreexponerse: hemisférica (gradiente cielo/suelo) + 2 direccionales
            // suaves desde ángulos opuestos. El brillo metálico lo aporta el envMap.
            t.scene.add(new THREE.AmbientLight(0xffffff, 0.5));
            t.scene.add(new THREE.HemisphereLight(0xffffff, 0x39404a, 0.65));
            var dir = new THREE.DirectionalLight(0xffffff, 0.6); dir.position.set(1, 1, 2);
            t.scene.add(dir);
            var dir2 = new THREE.DirectionalLight(0xffffff, 0.3); dir2.position.set(-1, -0.5, 1);
            t.scene.add(dir2);

            BKCameraControls.install({THREE: THREE});
            t.controls = self._makeControls(t.camera);

            self._buildBed();
            self._buildNozzle();
            self._buildNavGizmo();

            self._inited = true;
            self._animate();
            self._resize(); self.frameBed(false); self._rebuildTrail(); self._requestRender();

            // El perfil de impresora puede no estar cargado cuando se construye la
            // cama (entonces _bedDims cae al fallback y la cama sale enorme). Nos
            // suscribimos para reconstruirla en cuanto el perfil esté disponible o
            // cambie, así el visor SIEMPRE refleja las dimensiones reales.
            if (!self._profileSub) {
                try {
                    self._profileSub = self.profiles.currentProfileData.subscribe(function () {
                        if (self._inited) { self._buildBed(); self._buildNozzle(); self.frameBed(false); self._requestRender(); }
                    });
                } catch (e) { /* sin printerProfiles: nos quedamos con lo construido */ }
                // y reconstruye ya por si el perfil llegó entre medias
                self._buildBed(); self.frameBed(false); self._requestRender();
            }
        };

        self._makeControls = function (cam) {
            // el botón derecho hace pan -> evita el menú contextual del navegador
            if (!self._three.canvas.__bkNoCtx) {
                self._three.canvas.addEventListener("contextmenu", function (e) { e.preventDefault(); });
                self._three.canvas.__bkNoCtx = true;
            }
            var c = new BKCameraControls(cam, self._three.canvas);
            c.dollyToCursor = true;
            // suavizado para una navegación más agradable
            try { if ("smoothTime" in c) c.smoothTime = 0.08; } catch (e) {}
            try { if ("draggingSmoothTime" in c) c.draggingSmoothTime = 0.05; } catch (e) {}
            try { if ("dampingFactor" in c) c.dampingFactor = 0.08; } catch (e) {}
            try { if ("draggingDampingFactor" in c) c.draggingDampingFactor = 0.25; } catch (e) {}
            try { if ("verticalDragToForward" in c) c.verticalDragToForward = false; } catch (e) {}
            return c;
        };

        // ---------------------------------------------------------------
        // Proyección perspectiva ↔ isométrica (transición sin salto de escala)
        // ---------------------------------------------------------------
        self.setProjection = function (ortho) {
            if (!self._inited || ortho === self._ortho) return;
            var t = self._three;
            var pos = new THREE.Vector3(), tgt = new THREE.Vector3();
            t.controls.getPosition(pos); t.controls.getTarget(tgt);
            var up = t.camera.up.clone();
            t.controls.dispose();
            self._ortho = ortho;

            if (ortho) {
                // ajusta el frustum ortográfico para IGUALAR el tamaño aparente
                // que tiene la perspectiva a la distancia actual (sin "marearse").
                var dist = pos.distanceTo(tgt);
                var halfH = dist * Math.tan(THREE.Math.degToRad(t.persCam.fov) / 2);
                var a = self._tabSize.w / self._tabSize.h;
                t.orthoCam.top = halfH; t.orthoCam.bottom = -halfH;
                t.orthoCam.left = -halfH * a; t.orthoCam.right = halfH * a;
                t.orthoCam.zoom = 1; t.orthoCam.updateProjectionMatrix();
            }
            t.camera = ortho ? t.orthoCam : t.persCam;
            t.camera.up.copy(up);
            t.controls = self._makeControls(t.camera);
            t.controls.setLookAt(pos.x, pos.y, pos.z, tgt.x, tgt.y, tgt.z, false);

            var btn = document.getElementById("bk3d_proj");
            btn.innerHTML = ortho ? '<i class="fas fa-vector-square"></i> Isométrica' : '<i class="fas fa-video"></i> Perspectiva';
            btn.classList.toggle("active", ortho);
            var lbl = document.getElementById("bk3d_projlabel");
            if (lbl) lbl.textContent = ortho ? "ISOMÉTRICA" : "PERSPECTIVA";
            self._needRender = true;
            self._saveViewState();
        };

        // ---------------------------------------------------------------
        // Cama
        // ---------------------------------------------------------------
        self._bedDims = function () {
            var w = 220, d = 220, centered = false;
            try {
                var p = self.profiles.currentProfileData();
                if (p && p.volume) {
                    w = p.volume.width() || w; d = p.volume.depth() || d;
                    centered = (typeof p.volume.origin === "function" && p.volume.origin() === "center");
                }
            } catch (e) {}
            return {w: w, d: d, centered: centered};
        };

        self._buildBed = function () {
            var t = self._three;
            if (self._bedGroup) t.scene.remove(self._bedGroup);
            var b = self._bedDims();          // área ALCANZABLE (perfil de OctoPrint)
            var g = new THREE.Group();
            var cx = b.centered ? 0 : b.w / 2, cy = b.centered ? 0 : b.d / 2;

            if (self._showBed) {
                // Cama FÍSICA real = alcanzable + margen no alcanzable alrededor (la
                // boquilla no llega ahí). Plano grande gris; el origen X0/Y0 queda a
                // `margin` mm del borde físico en X-/Y-.
                var m = self._bedMargin || 0;
                if (m > 0) {
                    var realW = b.w + 2 * m, realD = b.d + 2 * m;
                    var real = new THREE.Mesh(new THREE.PlaneGeometry(realW, realD),
                        new THREE.MeshBasicMaterial({color: 0x2b2f36, transparent: true, opacity: 0.45, side: THREE.DoubleSide}));
                    real.position.set(cx, cy, -0.08);
                    g.add(real);
                }
                // Plano del área alcanzable (más claro).
                var plane = new THREE.Mesh(new THREE.PlaneGeometry(b.w, b.d),
                    new THREE.MeshBasicMaterial({color: 0xffffff, transparent: true, opacity: 0.5, side: THREE.DoubleSide}));
                plane.position.set(cx, cy, -0.05);
                g.add(plane);
                // Borde resaltado del área alcanzable.
                var edge = new THREE.LineSegments(
                    new THREE.EdgesGeometry(new THREE.PlaneGeometry(b.w, b.d)),
                    new THREE.LineBasicMaterial({color: 0x3498db}));
                edge.position.set(cx, cy, 0.01);
                g.add(edge);
                var grid = new THREE.GridHelper(Math.max(b.w, b.d), Math.round(Math.max(b.w, b.d) / 10), 0x4a90d9, 0xcfd6dd);
                grid.material.opacity = 0.55; grid.material.transparent = true; grid.rotation.x = Math.PI / 2;
                grid.position.set(cx, cy, 0);
                g.add(grid);
                g.add(new THREE.AxesHelper(Math.min(b.w, b.d) * 0.15));
            }

            // Relieve de la malla de nivelación activa (opcional).
            if (self._showBedMesh && self._bedMeshZ) self._addBedMeshRelief(g, b);

            self._bedGroup = g; self._bedInfo = b;
            t.scene.add(g);
        };

        // Superpone la malla de nivelación como superficie coloreada por altura,
        // exagerada para que se aprecie el relieve sobre la cama.
        self._addBedMeshRelief = function (g, b) {
            var z = self._bedMeshZ, n = z.length;
            if (!n || !z[0]) return;
            var inset = 1, min = Infinity, max = -Infinity, i, j;
            for (i = 0; i < n; i++) for (j = 0; j < z[i].length; j++) {
                var v = z[i][j]; if (v == null) continue;
                if (v < min) min = v; if (v > max) max = v;
            }
            if (!isFinite(min)) return;
            var range = Math.max(0.02, max - min), exag = (Math.min(b.w, b.d) * 0.12) / range;
            var geo = new THREE.PlaneGeometry(b.w - 2 * inset, b.d - 2 * inset, n - 1, n - 1);
            var lut = (typeof THREE.Lut !== "undefined") ? new THREE.Lut("rainbow", 256) : null;
            if (lut) { lut.setMin(min); lut.setMax(max); }
            var colors = [], pos = geo.attributes.position;
            for (var k = 0; k < pos.count; k++) {
                var row = Math.floor(k / n), col = k % n;
                var zv = (z[row] && z[row][col] != null) ? z[row][col] : min;
                pos.setZ(k, (zv - min) * exag + 0.05);
                var c = lut ? lut.getColor(zv) : new THREE.Color(0x888888);
                colors.push(c.r, c.g, c.b);
            }
            pos.needsUpdate = true; geo.computeVertexNormals();
            geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
            var mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
                vertexColors: true, transparent: true, opacity: 0.75, side: THREE.DoubleSide}));
            var cx = b.centered ? 0 : b.w / 2, cy = b.centered ? 0 : b.d / 2;
            mesh.position.set(cx, cy, 0);
            g.add(mesh);
        };

        self.toggleBed = function () {
            self._showBed = !self._showBed;
            if (self._inited) { self._buildBed(); self._requestRender(); }
            self._syncBedBtns();
        };
        self.toggleBedMesh = function () {
            self._showBedMesh = !self._showBedMesh;
            if (self._showBedMesh && !self._bedMeshZ) {
                try { OctoPrint.simpleApiCommand("boostkit", "mesh_get", {}); } catch (e) {}
            }
            if (self._inited) { self._buildBed(); self._requestRender(); }
            self._syncBedBtns();
        };
        self._syncBedBtns = function () {
            var a = document.getElementById("bk3d_bed"); if (a) a.classList.toggle("active", self._showBed);
            var c = document.getElementById("bk3d_bedmesh"); if (c) c.classList.toggle("active", self._showBedMesh);
        };

        // Environment map procedural (gradiente estudio cielo/suelo) para que el
        // metal REFLEJE algo y se vea como un cuerpo metálico 3D (no plano/opaco).
        self._makeEnvMap = function () {
            if (self._envMap) return self._envMap;
            function grad(top, bot) {
                var s = 128, cv = document.createElement("canvas"); cv.width = cv.height = s;
                var x = cv.getContext("2d"), g = x.createLinearGradient(0, 0, 0, s);
                g.addColorStop(0, top); g.addColorStop(1, bot); x.fillStyle = g; x.fillRect(0, 0, s, s);
                return cv;
            }
            function solid(c) {
                var s = 128, cv = document.createElement("canvas"); cv.width = cv.height = s;
                var x = cv.getContext("2d"); x.fillStyle = c; x.fillRect(0, 0, s, s); return cv;
            }
            var tex = new THREE.CubeTexture([
                grad("#e6ecf3", "#3a3f47"), grad("#e6ecf3", "#3a3f47"),   // +x, -x
                solid("#f4f7fa"), solid("#23272e"),                       // +y (cielo), -y (suelo)
                grad("#e6ecf3", "#3a3f47"), grad("#e6ecf3", "#3a3f47")    // +z, -z
            ]);
            tex.needsUpdate = true;
            self._envMap = tex;
            return tex;
        };
        // Material metálico del nozzle: MeshStandard con metalness alto + envMap
        // (reflejos) => aspecto de metal real con brillos y zonas oscuras.
        self._nozzleMat = function () {
            var kind = "bronze";
            try { kind = self.settings.settings.plugins.boostkit.gcode3d.nozzle_color() || "bronze"; } catch (e) {}
            var env = self._makeEnvMap();
            if (kind === "steel")
                return new THREE.MeshStandardMaterial({color: 0xCBD0D8, metalness: 0.95, roughness: 0.26,
                    envMap: env, envMapIntensity: 1.0});
            return new THREE.MeshStandardMaterial({color: 0xC8893C, metalness: 0.95, roughness: 0.30,
                envMap: env, envMapIntensity: 1.15, emissive: 0x140d04, emissiveIntensity: 0.2});
        };
        // Diámetro del nozzle según el PERFIL de impresora activo (fallback 0.4).
        self._nozzleDiameter = function () {
            try {
                var p = self.profiles.currentProfileData();
                var d = p && p.extruder && p.extruder.nozzleDiameter ? parseFloat(ko.unwrap(p.extruder.nozzleDiameter)) : NaN;
                if (d > 0) return d;
            } catch (e) {}
            return 0.4;
        };
        // Rótulo del diámetro en TEXTO 3D real (estilo PrettyGCode: FontLoader +
        // generateShapes), grabado sobre el cuerpo del nozzle. La fuente se carga
        // una vez y se cachea; el texto se añade al grupo del nozzle cuando llega.
        self._loadFont = function (cb) {
            if (self._font) { cb(self._font); return; }
            try {
                new THREE.FontLoader().load(
                    "plugin/boostkit/static/js/vendor/helvetiker_bold.typeface.json",
                    function (f) { self._font = f; cb(f); }, undefined, function () {});
            } catch (e) {}
        };
        // Graba el diámetro en el CUERPO del nozzle, como una boquilla real lleva
        // troquelado el número (solo "0.4"; la unidad mm se sobreentiende). Es un
        // RELIEVE extruido (no un calco plano) para que la luz le dé sombras.
        self._addNozzleLabel = function (group, box) {
            var show = true;
            try { show = self.settings.settings.plugins.boostkit.gcode3d.nozzle_label() !== false; } catch (e) {}
            if (!show) return;
            self._loadFont(function (font) {
                if (!group) return;
                var old = group.getObjectByName("bk_nozlabel");
                if (old) { group.remove(old); if (old.geometry) old.geometry.dispose(); }
                var d = self._nozzleDiameter();
                var num = d.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");  // 0.40 -> "0.4"
                // dimensiones REALES del cuerpo (bbox en local; o el cono de fallback)
                var minY = box ? box.min.y : -2.2;
                var minZ = box ? box.min.z : 0, maxZ = box ? box.max.z : 8;
                var halfX = box ? Math.max(Math.abs(box.min.x), Math.abs(box.max.x)) : 2.2;
                var bodyH = Math.max(2, maxZ - minZ);
                // tamaño del texto que QUEPA en la cara (ancho de "0.4" ~1.9*th)
                var th = Math.min(bodyH * 0.16, (halfX * 1.7) / 1.9);
                var shapes = font.generateShapes(num, th);
                var geo = new THREE.ExtrudeBufferGeometry(shapes,
                    {depth: th * 0.28, bevelEnabled: false});   // RELIEVE con profundidad
                geo.computeBoundingBox();
                geo.translate(-0.5 * (geo.boundingBox.max.x + geo.boundingBox.min.x),
                              -0.5 * (geo.boundingBox.max.y + geo.boundingBox.min.y), 0);
                geo.rotateX(Math.PI / 2);   // de pie en la cara -Y; relieve hacia -Y (afuera)
                // marca oscura tipo grabado láser, mate (contrasta con el latón brillante)
                var text = new THREE.Mesh(geo, new THREE.MeshStandardMaterial(
                    {color: 0x20242b, metalness: 0.25, roughness: 0.75, side: THREE.FrontSide}));
                // sobre la cara frontal (-Y), en la zona baja del hexágono (llaves)
                text.position.set(0, minY + 0.02, minZ + bodyH * 0.42);
                text.name = "bk_nozlabel";
                group.add(text);
                self._requestRender();
            });
        };
        self._buildNozzle = function () {
            var t = self._three;
            var cone = new THREE.Mesh(new THREE.ConeGeometry(2.2, 8, 20), self._nozzleMat());
            cone.rotation.x = Math.PI; cone.position.set(0, 0, 4);
            var grp = new THREE.Group(); grp.add(cone);
            if (self._nozzle && self._simPos) grp.position.copy(self._simPos);
            if (self._nozzle) t.scene.remove(self._nozzle);
            self._nozzle = grp; t.scene.add(grp); self._addNozzleLabel(grp);
            try {
                new THREE.OBJLoader().load("plugin/boostkit/static/js/vendor/models/ExtruderNozzle.obj",
                    function (obj) {
                        obj.quaternion.setFromEuler(new THREE.Euler(Math.PI / 2, 0, 0)); obj.scale.setScalar(0.1);
                        var mat = self._nozzleMat();
                        obj.traverse(function (c) { if (c instanceof THREE.Mesh) c.material = mat; });
                        // bbox en LOCAL (obj sin padre todavía) -> sin offset de simPos
                        obj.updateMatrixWorld(true);
                        var box = new THREE.Box3().setFromObject(obj);
                        var g2 = new THREE.Group(); g2.add(obj);
                        if (self._simPos) g2.position.copy(self._simPos);
                        t.scene.remove(self._nozzle); t.scene.add(g2); self._nozzle = g2;
                        self._addNozzleLabel(g2, box); self._requestRender();
                    }, undefined, function () {});
            } catch (e) {}
        };

        // ---------------------------------------------------------------
        // Animación de movimiento (simulador cinemático)
        // ---------------------------------------------------------------
        self._enqueuePoints = function (pts) {
            for (var i = 0; i < pts.length; i++) {
                var p = pts[i];
                self._motionQ.push({x: p[0], y: p[1], z: p[2], e: p[3] || 0, f: p[4] || 0,
                                    temp: p[5] || 0, flow: p[6] || 0});
            }
            // si la impresora va MUY por delante (cola enorme), adelanta para no
            // acumular retraso: confirma todo menos los últimos ~200 puntos.
            if (self._motionQ.length > 800) {
                var drop = self._motionQ.length - 200;
                for (var k = 0; k < drop; k++) self._commitPoint(self._motionQ.shift());
                var head = self._motionQ.length ? self._motionQ[0] : null;
                if (head) self._simPos.set(head.x, head.y, head.z);
            }
        };

        self._commitPoint = function (tgt) {
            self._trail.push([tgt.x, tgt.y, tgt.z, tgt.e, tgt.f || 0, tgt.temp || 0, tgt.flow || 0]);
            self._trimTrail();
            self._trailDirty = true;   // se reconstruye una vez por frame
        };

        // poda el trail al límite: nº de movimientos o mm recorridos
        self._trimTrail = function () {
            var tr = self._trail;
            if (self._trailMode === "moves") {
                if (tr.length > self._trailLen) self._trail = tr.slice(tr.length - self._trailLen);
            } else {
                var acc = 0, keepFrom = 0;
                for (var i = tr.length - 1; i > 0; i--) {
                    acc += Math.hypot(tr[i][0] - tr[i - 1][0], tr[i][1] - tr[i - 1][1], tr[i][2] - tr[i - 1][2]);
                    if (acc > self._trailDistMm) { keepFrom = i; break; }
                }
                if (keepFrom > 0) self._trail = tr.slice(keepFrom);
            }
        };

        // avanza el cabezal por la cola durante dt segundos (trapezoidal simple)
        self._stepMotion = function (dt) {
            var q = self._motionQ;
            if (!q.length) { self._simVel = 0; return false; }
            // distancia total restante (para desacelerar y parar al final)
            var rem = 0, prev = self._simPos;
            for (var i = 0; i < q.length; i++) {
                rem += Math.hypot(q[i].x - prev.x, q[i].y - prev.y, q[i].z - prev.z);
                prev = q[i];
            }
            var moved = false, guard = 0;
            while (dt > 1e-5 && q.length && guard++ < 2000) {
                var tgt = q[0], p = self._simPos;
                var dx = tgt.x - p.x, dy = tgt.y - p.y, dz = tgt.z - p.z;
                var dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
                if (dist < 1e-4) { self._commitPoint(tgt); q.shift(); continue; }
                var desired = tgt.f > 0 ? tgt.f / 60 : 50;          // mm/s
                var vStop = Math.sqrt(2 * self._simAccel * Math.max(0, rem)); // poder parar al final
                var vmax = Math.min(desired, vStop, self._simMaxFeed);
                if (self._simVel < vmax) self._simVel = Math.min(vmax, self._simVel + self._simAccel * dt);
                else self._simVel = Math.max(vmax, self._simVel - self._simAccel * dt);
                if (self._simVel < 2) self._simVel = Math.min(vmax, 2);   // arranque mínimo
                var step = self._simVel * dt;
                if (step >= dist) {
                    p.set(tgt.x, tgt.y, tgt.z); self._commitPoint(tgt); q.shift();
                    dt -= dist / Math.max(self._simVel, 1e-3); rem -= dist; moved = true;
                } else {
                    var inv = step / dist; p.x += dx * inv; p.y += dy * inv; p.z += dz * inv;
                    rem -= step; dt = 0; moved = true;
                }
            }
            if (!q.length) self._simVel = 0;
            return moved;
        };

        self._moveNozzleToSim = function () {
            if (!self._nozzle) return;
            self._nozzle.position.copy(self._simPos);
            if (self._followCam && self._three.controls)
                self._three.controls.moveTo(self._simPos.x, self._simPos.y, self._simPos.z, true);
        };

        // ---------------------------------------------------------------
        // Trail estilo slicer: extrusión coloreada (modo) + ancho por flujo +
        // travels punteados. Segmentos disjuntos via LineSegments2.
        // ---------------------------------------------------------------
        self._gradColor = function (v) {
            v = Math.max(0, Math.min(1, v));
            return new THREE.Color().setHSL(0.66 * (1 - v), 1.0, 0.5);   // azul→amarillo→rojo
        };

        // color de un segmento (p = punto de llegada [x,y,z,e,f,temp,flow])
        self._segColor = function (p, fMin, fMax, flMin, flMax) {
            var m = self._colorMode;
            if (m === "temp") return self._gradColor((p[5] - self._tempLo) / Math.max(1, self._tempHi - self._tempLo));
            if (m === "speed") return self._gradColor(fMax > fMin ? (p[4] - fMin) / (fMax - fMin) : 0.5);
            if (m === "flow") return self._gradColor(flMax > flMin ? (p[6] - flMin) / (flMax - flMin) : 0.5);
            return new THREE.Color(0xff6d00);   // "tipo": extrusión naranja
        };

        self._makeSegLine = function (pos, col, width, dashed) {
            var geo = new THREE.LineSegmentsGeometry();
            geo.setPositions(pos); geo.setColors(col);
            var mat = new THREE.LineMaterial({linewidth: width, vertexColors: true, dashed: !!dashed,
                resolution: new THREE.Vector2(self._tabSize.w, self._tabSize.h)});
            if (dashed) { mat.dashSize = 1.4; mat.gapSize = 1.1; }
            var line = new THREE.LineSegments2(geo, mat);
            line.computeLineDistances();
            return line;
        };

        self._clearTrailLines = function () {
            var t = self._three;
            for (var i = 0; i < self._extrudeLines.length; i++) t.scene.remove(self._extrudeLines[i]);
            self._extrudeLines = [];
            if (self._travelLine) { t.scene.remove(self._travelLine); self._travelLine = null; }
        };

        self._rebuildTrail = function () {
            var t = self._three;
            if (!self._inited) return;
            self._clearTrailLines();
            var tr = self._trail;
            if (!self._showTrail || tr.length < 2) { self._requestRender(); return; }

            // rangos para modos velocidad/flujo
            var fMin = 1e9, fMax = 0, flMin = 1e9, flMax = 0, i;
            for (i = 1; i < tr.length; i++) {
                if (!tr[i][3]) continue;
                var f = tr[i][4] || 0, fl = tr[i][6] || 0;
                if (f < fMin) fMin = f; if (f > fMax) fMax = f;
                if (fl < flMin) flMin = fl; if (fl > flMax) flMax = fl;
            }
            var NB = self._widthByFlow ? 3 : 1;        // buckets de ancho
            var exPos = [], exCol = [], b;
            for (b = 0; b < NB; b++) { exPos[b] = []; exCol[b] = []; }
            var tvPos = [], tvCol = [];
            for (i = 1; i < tr.length; i++) {
                var a = tr[i - 1], p = tr[i];
                var dx = p[0] - a[0], dy = p[1] - a[1], dz = p[2] - a[2];
                if (dx * dx + dy * dy + dz * dz < 1e-8) continue;   // evita NaN
                if (p[3]) {
                    var c = self._segColor(p, fMin, fMax, flMin, flMax);
                    b = 0;
                    if (self._widthByFlow && flMax > flMin)
                        b = Math.min(NB - 1, Math.floor(((p[6] - flMin) / (flMax - flMin)) * NB));
                    exPos[b].push(a[0], a[1], a[2], p[0], p[1], p[2]);
                    exCol[b].push(c.r, c.g, c.b, c.r, c.g, c.b);
                } else if (self._showTravel) {
                    var ct = new THREE.Color(0x90a4ae);
                    tvPos.push(a[0], a[1], a[2], p[0], p[1], p[2]);
                    tvCol.push(ct.r, ct.g, ct.b, ct.r, ct.g, ct.b);
                }
            }
            for (b = 0; b < NB; b++) {
                if (exPos[b].length < 6) continue;
                var w = self._widthByFlow ? (2.0 + b * 1.8) : 3.5;
                var line = self._makeSegLine(exPos[b], exCol[b], w, false);
                self._extrudeLines.push(line); t.scene.add(line);
            }
            if (tvPos.length >= 6) { self._travelLine = self._makeSegLine(tvPos, tvCol, 1.4, true); t.scene.add(self._travelLine); }
            self._requestRender();
        };

        // segmento que crece desde el último punto committed hasta el cabezal
        self._updateLive = function () {
            var t = self._three;
            if (self._liveLine) { t.scene.remove(self._liveLine); self._liveLine = null; }
            if (!self._showTrail || !self._trail.length || !self._motionQ.length) return;
            var last = self._trail[self._trail.length - 1], mv = self._motionQ[0];
            var dx = self._simPos.x - last[0], dy = self._simPos.y - last[1], dz = self._simPos.z - last[2];
            if (dx * dx + dy * dy + dz * dz < 1e-8) return;
            var ex = mv.e ? 1 : 0;
            if (!ex && !self._showTravel) return;
            var c = ex ? self._segColor([0, 0, 0, 1, mv.f, mv.temp, mv.flow], mv.f, mv.f, mv.flow, mv.flow)
                       : new THREE.Color(0x90a4ae);
            var geo = new THREE.LineSegmentsGeometry();
            geo.setPositions([last[0], last[1], last[2], self._simPos.x, self._simPos.y, self._simPos.z]);
            geo.setColors([c.r, c.g, c.b, c.r, c.g, c.b]);
            var mat = new THREE.LineMaterial({linewidth: ex ? 3.5 : 1.4, vertexColors: true, dashed: !ex,
                resolution: new THREE.Vector2(self._tabSize.w, self._tabSize.h)});
            if (!ex) { mat.dashSize = 1.4; mat.gapSize = 1.1; }
            var line = new THREE.LineSegments2(geo, mat); line.computeLineDistances();
            self._liveLine = line; t.scene.add(line);
        };

        // ---------------------------------------------------------------
        // Modelo 3D del gcode cargado: PARSER PROPIO mode-aware (necesario para
        // respetar G90/G91 + M82/M83 al seleccionar un punto y operar desde él).
        // ---------------------------------------------------------------
        self.toggleModel = function () {
            if (self._modelGroup) { self._clearModel(); return; }
            self._loadGModel();
        };

        self._clearModel = function () {
            var t = self._three;
            if (self._modelGroup) { t.scene.remove(self._modelGroup); self._modelGroup = null; }
            if (self._selMarker) { t.scene.remove(self._selMarker); self._selMarker = null; self._selBall = null; }
            self._gmodel = null; self._selIdx = -1; self._modelPath = null;
            self._saveViewState();
            document.getElementById("bk3d_model").classList.remove("active");
            var nav = document.getElementById("bk3d_navpanel"); if (nav) nav.style.display = "none";
            self._requestRender();
        };

        self._gcodeFilePath = function () {
            try { if (self.printerState.filepath && self.printerState.filepath()) return self.printerState.filepath(); } catch (e) {}
            try { var g = OctoPrint.coreui.viewmodels.gcodeViewModel; if (g && g.loadedFilepath) return g.loadedFilepath; } catch (e) {}
            return null;
        };

        self._loadGModel = function () {
            var path = self._gcodeFilePath();
            if (!path) { self.hud("Selecciona un archivo gcode para imprimir primero."); return; }
            self._loadGModelPath(path);
        };

        // Carga pública: usada por la calibración ("Ver en 3D"). Activa el visor y carga
        // el gcode del test (recién escrito por el backend).
        self.loadFile = function (path) {
            if (!path) return;
            if (!self._active) self.setActive(true);
            self._loadGModelPath(path);
        };

        self._loadGModelPath = function (path) {
            if (!path) return;
            self.hud("Cargando " + path + " …");
            var direct = "/downloads/files/local/" + path;
            // files.get puede fallar para ficheros recién escritos (sin indexar): caemos
            // a la URL de descarga directa. cache:no-store -> siempre la versión nueva.
            OctoPrint.files.get("local", path).then(function (info) {
                return (info && info.refs && info.refs.download) || direct;
            }, function () { return direct; }).then(function (url) {
                return fetch(url, { cache: "no-store" });
            }).then(function (r) { return r.text(); }).then(function (text) {
                if (text.length > 60 * 1024 * 1024) { self.hud("Archivo demasiado grande (>60MB)."); return; }
                self._gmodel = self._parseGcode(text);
                self._modelPath = path;
                self._initLayerSlider();
                self._buildGModelGeometry();
                self._setupNav();
                // restaura la selección persistida si la hay
                if (self._pendingSel) { self._selectPoint(self._pendingSel.idx, self._pendingSel.t); self._pendingSel = null; }
                self.hud(self._gmodel.moves.length + " movimientos · " + (self._gmodel.maxLayer + 1) + " capas");
                self._saveViewState();
            }).catch(function (e) { self.hud("Error cargando gcode: " + (e.message || e)); });
        };

        // --- parser mode-aware (puerto del tracker) ---
        self._num = function (s, ax) { var m = s.match(new RegExp(ax + "(-?\\d*\\.?\\d+)")); return m ? parseFloat(m[1]) : null; };

        self._parseGcode = function (text) {
            var lines = text.split(/\r?\n/), N = self._num;
            var x = 0, y = 0, z = 0, e = 0, aX = true, aE = true, f = 0, temp = 0, bed = 0, fan = 0, flow = 100;
            var layer = 0, lastLZ = null, TH = 0.04, moves = [], maxZ = 0;
            for (var li = 0; li < lines.length; li++) {
                var raw = lines[li], sc = raw.indexOf(";"); if (sc >= 0) raw = raw.slice(0, sc);
                raw = raw.trim(); if (!raw) continue;
                var up = raw.toUpperCase(), spi = up.indexOf(" "), cmd = spi >= 0 ? up.slice(0, spi) : up;
                if (cmd === "G0" || cmd === "G1") {
                    var x0 = x, y0 = y, z0 = z, e0 = e;
                    var nx = N(up, "X"), ny = N(up, "Y"), nz = N(up, "Z"), ne = N(up, "E"), nf = N(up, "F");
                    if (nx !== null) x = aX ? nx : x + nx;
                    if (ny !== null) y = aX ? ny : y + ny;
                    if (nz !== null) z = aX ? nz : z + nz;
                    if (ne !== null) e = aE ? ne : e + ne;
                    if (nf !== null) f = nf;
                    var de = e - e0, ext = de > 1e-5;
                    if (ext && (lastLZ === null || z >= lastLZ + TH)) { layer++; lastLZ = z; }
                    moves.push({line: li + 1, x0: x0, y0: y0, z0: z0, x: x, y: y, z: z, e: e, de: de,
                                ext: ext, f: f, temp: temp, bed: bed, fan: fan, flow: flow, aX: aX, aE: aE, layer: layer});
                } else if (cmd === "G2" || cmd === "G3") {
                    var arc = self._parseArc(up, cmd === "G2", x, y, z, e, aX, aE, li + 1, f, temp, bed, fan, flow);
                    if (arc && arc.length) {
                        for (var ai = 0; ai < arc.length; ai++) {
                            var m2 = arc[ai];
                            if (m2.ext && (lastLZ === null || m2.z >= lastLZ + TH)) { layer++; lastLZ = m2.z; }
                            m2.layer = layer; moves.push(m2);
                        }
                        var lm = arc[arc.length - 1]; x = lm.x; y = lm.y; z = lm.z; e = lm.e;
                    }
                } else if (cmd === "G90") { aX = true; aE = true; }
                else if (cmd === "G91") { aX = false; aE = false; }
                else if (cmd === "M82") { aE = true; }
                else if (cmd === "M83") { aE = false; }
                else if (cmd === "G92") {
                    var gx = N(up, "X"), gy = N(up, "Y"), gz = N(up, "Z"), ge = N(up, "E");
                    if (gx !== null) x = gx; if (gy !== null) y = gy; if (gz !== null) z = gz; if (ge !== null) e = ge;
                }
                else if (cmd === "M104" || cmd === "M109") { var s1 = N(up, "S"); if (s1 !== null) temp = s1; }
                else if (cmd === "M140" || cmd === "M190") { var s2 = N(up, "S"); if (s2 !== null) bed = s2; }
                else if (cmd === "M106") { var s3 = N(up, "S"); fan = s3 !== null ? s3 : 255; }
                else if (cmd === "M107") { fan = 0; }
                else if (cmd === "M221") { var s4 = N(up, "S"); if (s4 !== null) flow = s4; }
                if (z > maxZ) maxZ = z;
                if (moves.length > 600000) break;   // tope de seguridad
            }
            var layers = [];
            for (var i = 0; i < moves.length; i++) { var L = moves[i].layer; if (layers[L] === undefined) layers[L] = i; }
            return {moves: moves, layers: layers, maxLayer: layer, maxZ: maxZ, lines: lines};
        };

        // interpola un arco G2/G3 en sub-movimientos (mismo criterio que el backend)
        self._parseArc = function (up, cw, sx, sy, sz, se, aX, aE, line, f, temp, bed, fan, flow) {
            var N = self._num;
            var nx = N(up, "X"), ny = N(up, "Y"), nz = N(up, "Z"), ne = N(up, "E");
            var ex = nx !== null ? (aX ? nx : sx + nx) : sx;
            var ey = ny !== null ? (aX ? ny : sy + ny) : sy;
            var ez = nz !== null ? (aX ? nz : sz + nz) : sz;
            var ee = ne !== null ? (aE ? ne : se + ne) : se;
            var ci = N(up, "I"), cj = N(up, "J"), cr = N(up, "R"), cx, cy;
            if (ci !== null || cj !== null) { cx = sx + (ci || 0); cy = sy + (cj || 0); }
            else if (cr !== null) {
                var mx = (sx + ex) / 2, my = (sy + ey) / 2, dx = ex - sx, dy = ey - sy, q = Math.hypot(dx, dy);
                if (q < 1e-6) return null;
                var hh = cr * cr - (q / 2) * (q / 2); hh = hh > 0 ? Math.sqrt(hh) : 0;
                var sgn = ((cr > 0) ^ cw) ? 1 : -1;
                cx = mx + sgn * hh * (-dy / q); cy = my + sgn * hh * (dx / q);
            } else return null;
            var rad = Math.hypot(sx - cx, sy - cy), a0 = Math.atan2(sy - cy, sx - cx), a1 = Math.atan2(ey - cy, ex - cx);
            if (cw) { while (a1 >= a0) a1 -= 2 * Math.PI; } else { while (a1 <= a0) a1 += 2 * Math.PI; }
            var sweep = a1 - a0, n = Math.max(2, Math.min(160, Math.floor(Math.abs(sweep) * rad) + 1));
            var out = [], px = sx, py = sy, pz = sz, pe = se;
            for (var k = 1; k <= n; k++) {
                var fr = k / n, a = a0 + sweep * fr;
                var qx = cx + rad * Math.cos(a), qy = cy + rad * Math.sin(a), qz = sz + (ez - sz) * fr, qe = se + (ee - se) * fr;
                var dE = qe - pe;
                out.push({line: line, x0: px, y0: py, z0: pz, x: qx, y: qy, z: qz, e: qe, de: dE,
                          ext: dE > 1e-6, f: f, temp: temp, bed: bed, fan: fan, flow: flow, aX: aX, aE: aE});
                px = qx; py = qy; pz = qz; pe = qe;
            }
            return out;
        };

        // Construye el modelo como SÓLIDO: cada extrusión es un prisma con sección
        // HEXAGONAL APLASTADA (ancho = extrusión, alto = altura de capa, lados
        // abultados) — como una línea real exprimida por la boquilla y la capa de
        // abajo. Las líneas adyacentes se sueldan en un cuerpo sólido. Coloreado por
        // altura y con normales para que las luces le den volumen.
        // Inicializa el slider de rango de capas al cargar un modelo (0..maxLayer).
        self._initLayerSlider = function () {
            var maxL = self._gmodel ? self._gmodel.maxLayer : 0;
            self._modelLo = 0; self._modelHi = maxL;
            var lmin = document.getElementById("bk3d_laymin"), lmax = document.getElementById("bk3d_laymax");
            var box = document.getElementById("bk3d_layerctl"), lbl = document.getElementById("bk3d_laylbl");
            if (lmin) { lmin.min = 0; lmin.max = maxL; lmin.value = 0; }
            if (lmax) { lmax.min = 0; lmax.max = maxL; lmax.value = maxL; }
            if (lbl) lbl.textContent = "0–" + maxL;
            if (box) box.style.display = "";
        };

        self._buildGModelGeometry = function () {
            var t = self._three;
            if (self._modelGroup) t.scene.remove(self._modelGroup);
            var mv = self._gmodel.moves, maxL = Math.max(1, self._gmodel.maxLayer);
            var lo = self._modelLo, hi = self._modelHi;   // rango de capas visible
            var grp = new THREE.Group();

            // contar extrusiones visibles; modelos enormes -> caer a líneas (rendimiento)
            var nExt = 0;
            for (var i = 0; i < mv.length; i++) if (mv[i].ext && mv[i].layer >= lo && mv[i].layer <= hi) nExt++;

            if (nExt > 60000) {
                var pos = [], col = [];
                for (i = 0; i < mv.length; i++) {
                    var ml = mv[i]; if (!ml.ext || ml.layer < lo || ml.layer > hi) continue;
                    pos.push(ml.x0, ml.y0, ml.z0, ml.x, ml.y, ml.z);
                    var cl = new THREE.Color().setHSL(0.6 * (1 - ml.layer / maxL), 0.85, 0.5);
                    col.push(cl.r, cl.g, cl.b, cl.r, cl.g, cl.b);
                }
                if (pos.length >= 6) {
                    var lg = new THREE.LineSegmentsGeometry(); lg.setPositions(pos); lg.setColors(col);
                    grp.add(new THREE.LineSegments2(lg, new THREE.LineMaterial({linewidth: 2.5,
                        vertexColors: true, resolution: new THREE.Vector2(self._tabSize.w, self._tabSize.h)})));
                }
                self._modelGroup = grp; t.scene.add(grp);
                document.getElementById("bk3d_model").classList.add("active");
                self._requestRender();
                return;
            }

            // dimensiones del filamento depositado
            var W = self._extrusionWidthMm();         // ancho (horizontal)
            var zMin = Infinity, zMax = -Infinity;
            for (i = 0; i < mv.length; i++) { if (!mv[i].ext) continue; var z = mv[i].z; if (z < zMin) zMin = z; if (z > zMax) zMax = z; }
            var H = (isFinite(zMin) && zMax > zMin) ? (zMax - zMin) / maxL : W * 0.5;
            H = Math.max(0.08, Math.min(W * 0.9, H));   // altura de capa razonable
            var hw = W / 2, hh = H / 2;
            // perfil = ELIPSE aplastada (ancho 2hw > alto 2hh): cordón redondeado
            // arriba y abajo, abultado a los lados. Entre cordones adyacentes quedan
            // surcos visibles (textura real del print) en vez de una superficie lisa.
            var prof = [], NP = 8;
            for (var pk = 0; pk < NP; pk++) {
                var pa = Math.PI * 2 * pk / NP;
                prof.push([hw * Math.cos(pa), hh * Math.sin(pa)]);
            }
            var P = prof.length;

            var positions = [], colors = [], indices = [], vbase = 0;
            var dx, dy, dz, len, rx, ry;
            for (i = 0; i < mv.length; i++) {
                var m = mv[i]; if (!m.ext || m.layer < lo || m.layer > hi) continue;
                dx = m.x - m.x0; dy = m.y - m.y0; dz = m.z - m.z0;
                len = Math.sqrt(dx * dx + dy * dy + dz * dz); if (len < 1e-5) continue;
                // "derecha" = perpendicular HORIZONTAL a la dirección (la capa es plana)
                rx = -dy; ry = dx; var rl = Math.sqrt(rx * rx + ry * ry);
                if (rl < 1e-6) { rx = 1; ry = 0; } else { rx /= rl; ry /= rl; }
                var c = new THREE.Color().setHSL(0.6 * (1 - m.layer / maxL), 0.85, 0.5);
                // 2 anillos (en p0 y p1); el filamento queda BAJO la trayectoria z
                for (var r = 0; r < 2; r++) {
                    var bx = r ? m.x : m.x0, by = r ? m.y : m.y0, bz = (r ? m.z : m.z0) - hh;
                    for (var p = 0; p < P; p++) {
                        var u = prof[p][0], v = prof[p][1];
                        positions.push(bx + rx * u, by + ry * u, bz + v);
                        colors.push(c.r, c.g, c.b);
                    }
                }
                for (p = 0; p < P; p++) {
                    var a0 = vbase + p, a1 = vbase + ((p + 1) % P), b0 = vbase + P + p, b1 = vbase + P + ((p + 1) % P);
                    indices.push(a0, b0, b1, a0, b1, a1);
                }
                vbase += 2 * P;
            }
            if (positions.length >= 9) {
                var geo = new THREE.BufferGeometry();
                geo.addAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
                geo.addAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
                geo.setIndex(indices);
                geo.computeVertexNormals();
                grp.add(new THREE.Mesh(geo, new THREE.MeshStandardMaterial(
                    {vertexColors: true, metalness: 0.0, roughness: 0.85, side: THREE.DoubleSide})));
            }
            self._modelGroup = grp; t.scene.add(grp);
            document.getElementById("bk3d_model").classList.add("active");
            self._requestRender();
        };

        // Ancho del cordón (mm). Algo MENOR que el paso entre líneas para que queden
        // ADYACENTES y se distingan unas de otras (con surco), no fusionadas. Factor
        // ajustable en gcode3d.line_width_factor (× Ø del nozzle del perfil).
        self._extrusionWidthMm = function () {
            var f = 0.92;
            try { f = parseFloat(self.settings.settings.plugins.boostkit.gcode3d.line_width_factor()) || 0.92; } catch (e) {}
            return self._nozzleDiameter() * f;
        };
        // Convierte el ancho de extrusión (mm) a píxeles SEGÚN EL ZOOM y lo aplica a
        // los materiales de línea marcados (modelo/trail). Así las líneas tienen el
        // grosor del material extruido y se SUELDAN como un cuerpo sólido a cualquier
        // zoom (al acercar se distinguen; al alejar se funden).
        self._updateLineWidths = function () {
            var t = self._three;
            if (!t.camera || !self._tabSize || !self._tabSize.h) return;
            var pxPerMm;
            if (t.camera.isOrthographicCamera) {
                var wh = (t.camera.top - t.camera.bottom) / (t.camera.zoom || 1);
                pxPerMm = self._tabSize.h / wh;
            } else {
                var tgt = (t.controls && t.controls.getTarget) ? t.controls.getTarget(new THREE.Vector3())
                                                               : new THREE.Vector3();
                var dist = t.camera.position.distanceTo(tgt) || 100;
                var worldH = 2 * Math.tan((t.camera.fov * Math.PI / 180) / 2) * dist;
                pxPerMm = self._tabSize.h / worldH;
            }
            t.scene.traverse(function (o) {
                if (o.material && o.material.isLineMaterial && o.material.userData && o.material.userData.bkWmm) {
                    var lw = o.material.userData.bkWmm * pxPerMm;
                    o.material.linewidth = Math.max(1.2, Math.min(80, lw));
                }
            });
        };

        // --- panel de navegación (capa + punto) y selección ---
        self._setupNav = function () {
            var nav = document.getElementById("bk3d_navpanel");
            if (!nav) {
                nav = document.createElement("div");
                nav.id = "bk3d_navpanel"; nav.className = "bk3d-navpanel";
                nav.innerHTML =
                    '<div class="bk3d-rollers">' +
                    '  <div class="bk3d-roller" data-dial="layer"><div class="bk3d-rlbl">Altura</div><div class="bk3d-rval" id="bk3d_rlayer">0</div><div class="bk3d-rhint">▲▼</div></div>' +
                    '  <div class="bk3d-roller" data-dial="move"><div class="bk3d-rlbl">Mov.</div><div class="bk3d-rval" id="bk3d_rmove">0</div><div class="bk3d-rhint">▲▼</div></div>' +
                    '  <div class="bk3d-roller" data-dial="cm"><div class="bk3d-rlbl">cm</div><div class="bk3d-rval" id="bk3d_rcm">0.0</div><div class="bk3d-rhint">▲▼</div></div>' +
                    '  <div class="bk3d-roller" data-dial="mm"><div class="bk3d-rlbl">mm</div><div class="bk3d-rval" id="bk3d_rmm">0.0</div><div class="bk3d-rhint">▲▼</div></div>' +
                    '  <div class="bk3d-rollaux">' +
                    '    <button id="bk3d_nearest" class="btn btn-small" type="button" title="Seleccionar el punto del gcode más cercano a la posición actual de la boquilla">⌖ Punto ↔ boquilla</button>' +
                    '  </div>' +
                    '</div>' +
                    '<div id="bk3d_navinfo" class="bk3d-navinfo">—</div>' +
                    '<div class="bk3d-navops">' +
                    '  <button id="bk3d_goto" class="btn btn-small" type="button" title="Mover el cabezal aquí (sube Z anti-choque)">↑ Ir aquí</button>' +
                    '  <button id="bk3d_prep" class="btn btn-small" type="button" title="Calentar a las temps del punto, restaurar modos y posicionar">🔥 Preparar</button>' +
                    '  <button id="bk3d_resume" class="btn btn-small btn-warning" type="button" title="Reanudar la impresión desde esta línea (stream)">▶ Reanudar</button>' +
                    '  <a id="bk3d_opstoggle" href="#" class="bk3d-opslink">Operaciones ▾</a>' +
                    '</div>' +
                    '<div id="bk3d_ops" class="bk3d-ops" style="display:none">' +
                    '  <div class="bk3d-oprow"><button data-op="retract">Retraer</button><button data-op="extrude">Extruir</button>' +
                    '    <input id="bk3d_eamt" type="number" value="2" step="0.5"><span>mm @</span><input id="bk3d_espd" type="number" value="300"><span>mm/min</span></div>' +
                    '  <div class="bk3d-oprow"><button data-op="settemp">Hotend</button><input id="bk3d_optemp" type="number" value="200"><span>°</span>' +
                    '    <button data-op="setbed">Cama</button><input id="bk3d_opbed" type="number" value="0"><span>°</span></div>' +
                    '  <div class="bk3d-oprow"><button data-op="setfan">Fan</button><input id="bk3d_opfan" type="number" value="0"><span>%</span>' +
                    '    <button data-op="setspeed">Vel</button><input id="bk3d_opspeed" type="number" value="100"><span>%</span>' +
                    '    <button data-op="setflow">Flujo</button><input id="bk3d_opflow" type="number" value="100"><span>%</span></div>' +
                    '  <div class="bk3d-oprow"><span>Babystep Z</span><button data-op="babydn">−</button><button data-op="babyup">+</button>' +
                    '    <input id="bk3d_opbaby" type="number" value="0.02" step="0.01"><span>mm</span></div>' +
                    '</div>';
                document.getElementById("bk3d_overlay").appendChild(nav);
                // tres rodillos acoplados (altura / movimiento / fino)
                nav.querySelectorAll(".bk3d-roller").forEach(function (el) {
                    var dial = el.getAttribute("data-dial"), acc = 0, lastY = 0, dragging = false, STEP = 16;
                    el.addEventListener("pointerdown", function (ev) { dragging = true; lastY = ev.clientY; acc = 0; el.setPointerCapture(ev.pointerId); ev.preventDefault(); });
                    el.addEventListener("pointermove", function (ev) {
                        if (!dragging) return;
                        acc += (lastY - ev.clientY); lastY = ev.clientY;
                        while (acc >= STEP) { acc -= STEP; self._dial(dial, 1); }
                        while (acc <= -STEP) { acc += STEP; self._dial(dial, -1); }
                    });
                    el.addEventListener("pointerup", function () { dragging = false; });
                    el.addEventListener("pointercancel", function () { dragging = false; });
                    el.addEventListener("wheel", function (ev) { ev.preventDefault(); self._dial(dial, ev.deltaY < 0 ? 1 : -1); });
                });
                document.getElementById("bk3d_nearest").addEventListener("click", function () { self._nearestToNozzle(); });
                document.getElementById("bk3d_goto").addEventListener("click", function () { self._gotoSelectedSafe(); });
                document.getElementById("bk3d_prep").addEventListener("click", function () { self._preparePoint(); });
                document.getElementById("bk3d_resume").addEventListener("click", function () { self._streamFromSelected(); });
                document.getElementById("bk3d_opstoggle").addEventListener("click", function (ev) {
                    ev.preventDefault();
                    var o = document.getElementById("bk3d_ops");
                    o.style.display = (o.style.display === "none") ? "" : "none";
                });
                nav.querySelectorAll("#bk3d_ops button[data-op]").forEach(function (b) {
                    b.addEventListener("click", function () { self._doOp(b.getAttribute("data-op")); });
                });
            }
            nav.style.display = "";
            self._selectPoint(0, 0);
        };

        self._layerCount = function (L) {
            var ly = self._gmodel.layers, start = ly[L] || 0;
            var next = (ly[L + 1] !== undefined) ? ly[L + 1] : self._gmodel.moves.length;
            return {start: start, count: Math.max(0, next - start - 1)};
        };

        // punto interpolado dentro de un movimiento (t = 0..1) y su longitud
        self._movePoint = function (m, t) {
            return {x: m.x0 + (m.x - m.x0) * t, y: m.y0 + (m.y - m.y0) * t, z: m.z0 + (m.z - m.z0) * t};
        };
        self._segLen = function (m) { return Math.hypot(m.x - m.x0, m.y - m.y0, m.z - m.z0); };

        // selección unificada: movimiento idx + fracción t dentro de él
        self._selectPoint = function (idx, t) {
            if (!self._gmodel) return;
            var mv = self._gmodel.moves;
            idx = Math.max(0, Math.min(mv.length - 1, idx));
            t = Math.max(0, Math.min(1, t));
            self._selIdx = idx; self._selT = t;
            var m = mv[idx], p = self._movePoint(m, t);
            self._navLayer = m.layer;
            self._updateRollers(m, t); self._updateSelMarker(p); self._navInfo(m, p); self._requestRender();
            self._saveViewState();
        };

        self._updateRollers = function (m, t) {
            var set = function (id, v) { var e = document.getElementById(id); if (e) e.textContent = v; };
            var lc = self._layerCount(m.layer), mmInto = t * self._segLen(m);
            set("bk3d_rlayer", m.layer);
            set("bk3d_rmove", (self._selIdx - lc.start + 1) + "/" + (lc.count + 1));
            set("bk3d_rcm", (mmInto / 10).toFixed(2));
            set("bk3d_rmm", mmInto.toFixed(1));
        };

        // --- diales acoplados (altura / movimiento / cm / mm) ---
        self._dial = function (dial, dir) {
            if (!self._gmodel) return;
            if (dial === "layer") self._dialLayer(dir);
            else if (dial === "move") self._selectPoint(self._selIdx + dir, 0);
            else if (dial === "cm") self._dialFine(dir * 10);
            else if (dial === "mm") self._dialFine(dir * 1);
        };

        // altura: salta a la capa contigua, al punto más cercano en XY (vertical)
        self._dialLayer = function (dir) {
            var mv = self._gmodel.moves, m = mv[self._selIdx];
            var tL = Math.max(0, Math.min(self._gmodel.maxLayer, m.layer + dir));
            if (tL === m.layer) return;
            var cur = self._movePoint(m, self._selT), lc = self._layerCount(tL), best = lc.start, bd = 1e18;
            for (var i = lc.start; i <= lc.start + lc.count; i++) {
                var mm = mv[i]; if (!mm) continue;
                var d = (mm.x - cur.x) * (mm.x - cur.x) + (mm.y - cur.y) * (mm.y - cur.y);
                if (d < bd) { bd = d; best = i; }
            }
            self._selectPoint(best, 0);
        };

        // fino: avanza/retrocede `mm` por la trayectoria, desbordando suavemente al
        // movimiento siguiente/anterior (esto a su vez mueve los rodillos Mov./Altura).
        self._dialFine = function (mm) {
            var mv = self._gmodel.moves, idx = self._selIdx, t = self._selT, guard = 0;
            while (Math.abs(mm) > 1e-6 && guard++ < 200000) {
                var m = mv[idx], L = self._segLen(m);
                if (L < 1e-6) {
                    if (mm > 0) { if (idx < mv.length - 1) { idx++; t = 0; } else { t = 1; break; } }
                    else { if (idx > 0) { idx--; t = 1; } else { t = 0; break; } }
                    continue;
                }
                var pos = t * L, np = pos + mm;
                if (np >= 0 && np <= L) { t = np / L; mm = 0; }
                else if (np > L) { mm = np - L; if (idx < mv.length - 1) { idx++; t = 0; } else { t = 1; mm = 0; } }
                else { mm = np; if (idx > 0) { idx--; t = 1; } else { t = 0; mm = 0; } }
            }
            self._selectPoint(idx, t);
        };

        // selecciona el punto del gcode más cercano a la posición ACTUAL de la boquilla
        self._nearestToNozzle = function () {
            if (!self._gmodel || !self._simPos) return;
            var p = self._simPos, mv = self._gmodel.moves, best = -1, bestT = 0, bd = 1e18;
            for (var i = 0; i < mv.length; i++) {
                var m = mv[i], dx = m.x - m.x0, dy = m.y - m.y0, dz = m.z - m.z0;
                var L2 = dx * dx + dy * dy + dz * dz, tt = 0;
                if (L2 > 1e-9) tt = Math.max(0, Math.min(1, ((p.x - m.x0) * dx + (p.y - m.y0) * dy + (p.z - m.z0) * dz) / L2));
                var qx = m.x0 + dx * tt, qy = m.y0 + dy * tt, qz = m.z0 + dz * tt;
                var d = (qx - p.x) * (qx - p.x) + (qy - p.y) * (qy - p.y) + (qz - p.z) * (qz - p.z);
                if (d < bd) { bd = d; best = i; bestT = tt; }
            }
            if (best >= 0) { self._selectPoint(best, bestT); self.hud("Punto más cercano a boquilla: línea " + mv[best].line + " (" + Math.sqrt(bd).toFixed(2) + " mm)"); }
        };

        self._updateSelMarker = function (p) {
            var t = self._three;
            if (self._selMarker) t.scene.remove(self._selMarker);
            var g = new THREE.Group();
            var ball = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12),
                new THREE.MeshBasicMaterial({color: 0x2196f3, depthTest: false}));
            ball.position.set(p.x, p.y, p.z); ball.renderOrder = 999; g.add(ball);
            // línea vertical guía hasta la cama (referencia de XY)
            var lg = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(p.x, p.y, 0), new THREE.Vector3(p.x, p.y, p.z)]);
            g.add(new THREE.Line(lg, new THREE.LineBasicMaterial({color: 0x2196f3, transparent: true, opacity: 0.5})));
            self._selMarker = g; self._selBall = ball; t.scene.add(g);
            self._scaleMarker();
        };

        // mantiene la bola del marcador a TAMAÑO CONSTANTE en pantalla (no crece
        // al hacer zoom). ~7 px de radio sin importar perspectiva/orto ni distancia.
        self._scaleMarker = function () {
            var b = self._selBall, cam = self._three.camera;
            if (!b || !cam) return;
            // 3.5 px CSS de radio (la mitad que antes). _tabSize.h está en px de
            // dispositivo, así que multiplicamos el objetivo CSS por el dpr.
            var dpr = Math.min(window.devicePixelRatio || 1, 3);
            var pxR = 3.5 * dpr, h = self._tabSize.h || 500, worldR;
            if (cam.isOrthographicCamera) {
                worldR = (cam.top - cam.bottom) / (cam.zoom || 1) * (pxR / h);
            } else {
                var dist = cam.position.distanceTo(b.position);
                worldR = 2 * dist * Math.tan(THREE.Math.degToRad(cam.fov) / 2) * (pxR / h);
            }
            b.scale.setScalar(Math.max(0.2, worldR));
        };

        self._navInfo = function (m, p) {
            var el = document.getElementById("bk3d_navinfo"); if (!el) return;
            el.innerHTML = "Línea <b>" + m.line + "</b> · X" + p.x.toFixed(1) + " Y" + p.y.toFixed(1) + " Z" + p.z.toFixed(2) +
                " · F" + m.f +
                " · " + (m.aX ? "ABS" : "REL") + " ejes / " + (m.aE ? "ABS" : "REL") + " E" +
                " · T" + m.temp + "° cama" + m.bed + "° fan" + Math.round(m.fan / 2.55) + "% flujo" + m.flow + "%";
            // prellena los campos de operación con los valores del punto
            var set = function (id, val) { var e = document.getElementById(id); if (e) e.value = val; };
            set("bk3d_optemp", m.temp); set("bk3d_opbed", m.bed);
            set("bk3d_opfan", Math.round(m.fan / 2.55)); set("bk3d_opflow", m.flow);
        };

        // selección por clic: movimiento (extrusor) más cercano al rayo del ratón
        self._pickMove = function (clientX, clientY) {
            if (!self._gmodel) return;
            var canvas = self._three.canvas, r = canvas.getBoundingClientRect();
            var ndc = new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
            var ray = new THREE.Raycaster(); ray.setFromCamera(ndc, self._three.camera);
            var mv = self._gmodel.moves, i, m, best = -1;

            // 1) Impacto contra la MALLA sólida (respeta oclusión): el primer hit es la
            //    superficie FRONTAL bajo el cursor. Selecciona el cordón más cercano a
            //    ese punto 3D -> no agarra líneas de atrás.
            var hit = null;
            if (self._modelGroup) {
                var hits = ray.intersectObject(self._modelGroup, true);
                if (hits && hits.length) hit = hits[0].point;
            }
            var lo = self._modelLo, hi = self._modelHi;   // solo capas VISIBLES
            if (hit) {
                var bd = 1e18;
                for (i = 0; i < mv.length; i++) {
                    m = mv[i]; if (!m.ext || m.layer < lo || m.layer > hi) continue;
                    var ax = (m.x0 + m.x) / 2 - hit.x, ay = (m.y0 + m.y) / 2 - hit.y, az = (m.z0 + m.z) / 2 - hit.z;
                    var ad = ax * ax + ay * ay + az * az;
                    if (ad < bd) { bd = ad; best = i; }
                }
                if (best >= 0) { self._selectPoint(best, 0.5); return; }
            }

            // 2) Fallback (modelo en líneas / sin impacto): de los cordones bajo el
            //    cursor, el MÁS CERCANO A LA CÁMARA (delante), no el más cercano al rayo.
            var o = ray.ray.origin, d = ray.ray.direction, tmp = new THREE.Vector3();
            var bestProj = 1e18, THRESH = 4;   // ~2mm del rayo
            for (i = 0; i < mv.length; i++) {
                m = mv[i]; if (!m.ext || m.layer < lo || m.layer > hi) continue;
                tmp.set((m.x0 + m.x) / 2, (m.y0 + m.y) / 2, (m.z0 + m.z) / 2).sub(o);
                var proj = tmp.dot(d); if (proj <= 0) continue;
                var perp2 = tmp.lengthSq() - proj * proj;
                if (perp2 > THRESH) continue;
                if (proj < bestProj) { bestProj = proj; best = i; }
            }
            if (best >= 0) self._selectPoint(best, 0.5);
        };

        // mover el cabezal al punto seleccionado SIN chocar: sube Z por encima de
        // toda la pieza, viaja en XY, y baja a la Z del punto.
        self._selPt = function () {
            var m = self._gmodel.moves[self._selIdx];
            return {m: m, p: self._movePoint(m, self._selT)};
        };

        self._gotoSelectedSafe = function () {
            if (self._selIdx < 0 || !self._gmodel) return;
            var sp = self._selPt(), p = sp.p;
            var safeZ = Math.max(p.z, self._gmodel.maxZ || 0, 5) + 5;
            if (!confirm("Mover el cabezal a X" + p.x.toFixed(1) + " Y" + p.y.toFixed(1) + " Z" + p.z.toFixed(2) +
                         " subiendo antes a Z" + safeZ.toFixed(1) + " para no chocar. ¿Continuar?")) return;
            OctoPrint.control.sendGcode(["G90",
                "G1 Z" + safeZ.toFixed(2) + " F600",
                "G1 X" + p.x.toFixed(2) + " Y" + p.y.toFixed(2) + " F6000",
                "G1 Z" + p.z.toFixed(2) + " F600"]);
            self.hud("Moviendo al punto (Z segura " + safeZ.toFixed(1) + ")…");
        };

        // preámbulo seguro común (calentar + modos + posicionar sin chocar). m =
        // datos del movimiento (temps/modos/flujo), p = punto interpolado (posición).
        self._preamble = function (m, p, withWait) {
            var safeZ = Math.max(p.z, self._gmodel.maxZ || 0, 5) + 5, g = [];
            if (m.bed > 0) g.push("M140 S" + Math.round(m.bed));
            if (m.temp > 0) g.push("M104 S" + Math.round(m.temp));
            g.push("M106 S" + Math.round(m.fan));
            if (withWait && m.temp > 0) g.push("M109 S" + Math.round(m.temp));
            if (withWait && m.bed > 0) g.push("M190 S" + Math.round(m.bed));
            g.push("G90",
                "G1 Z" + safeZ.toFixed(2) + " F600",     // sube por encima de la pieza
                "G1 X" + p.x.toFixed(2) + " Y" + p.y.toFixed(2) + " F6000",  // viaja en XY arriba
                "G1 Z" + p.z.toFixed(2) + " F600");      // baja al punto
            g.push("M221 S" + Math.round(m.flow));
            g.push(m.aX ? "G90" : "G91", m.aE ? "M82" : "M83");   // restaura los modos del punto
            return g;
        };

        self._preparePoint = function () {
            if (self._selIdx < 0 || !self._gmodel) return;
            var sp = self._selPt(), m = sp.m, p = sp.p;
            if (!confirm("Preparar el punto (línea " + m.line + "): calentar hotend a " + m.temp + "° y cama a " +
                m.bed + "°, fan " + Math.round(m.fan / 2.55) + "%, restaurar modos y posicionar el cabezal " +
                "subiendo Z anti-choque. ¿Continuar?")) return;
            OctoPrint.control.sendGcode(self._preamble(m, p, true));
            self.hud("Preparando punto (calentando + posicionando)…");
        };

        self._streamFromSelected = function () {
            if (self._selIdx < 0 || !self._gmodel) return;
            var sp = self._selPt(), m = sp.m, p = sp.p;
            if (!confirm("REANUDAR la impresión desde la línea " + m.line + " (capa " + m.layer + ").\n\n" +
                "Se calienta, se posiciona el cabezal SIN chocar y se imprime el resto del archivo desde ahí.\n" +
                "IMPORTANTE: asume que la máquina conoce su posición (NO se homea, para no arruinar la pieza).\n\n" +
                "¿Generar el gcode de reanudación?")) return;
            var pre = self._preamble(m, p, true);
            pre.unshift("; Boost Kit — reanudar desde línea " + m.line + " (capa " + m.layer + ")");
            pre.push("G92 E" + m.e.toFixed(4), "; --- continúa el gcode original ---");
            var tail = self._gmodel.lines.slice(m.line - 1);   // line es 1-based
            var text = pre.join("\n") + "\n" + tail.join("\n") + "\n";
            var file = new File([new Blob([text], {type: "text/plain"})], "bk_resume.gcode", {type: "text/plain"});
            self.hud("Subiendo gcode de reanudación…");
            try {
                OctoPrint.files.upload("local", file, {select: true, print: false})
                    .done(function () { self.hud("Listo: 'bk_resume.gcode' seleccionada. Revisa y pulsa Imprimir para reanudar."); })
                    .fail(function () { self.hud("Error subiendo el gcode de reanudación."); });
            } catch (e) { self.hud("Error: " + (e.message || e)); }
        };

        self._doOp = function (op) {
            var v = function (id) { return parseFloat(document.getElementById(id).value) || 0; };
            if (op === "retract" || op === "extrude") {
                var amt = v("bk3d_eamt"), spd = v("bk3d_espd"), sign = op === "retract" ? -1 : 1;
                OctoPrint.control.sendGcode(["M83", "G1 E" + (sign * amt).toFixed(2) + " F" + Math.round(spd)]);
            } else if (op === "settemp") OctoPrint.control.sendGcode(["M104 S" + Math.round(v("bk3d_optemp"))]);
            else if (op === "setbed") OctoPrint.control.sendGcode(["M140 S" + Math.round(v("bk3d_opbed"))]);
            else if (op === "setfan") OctoPrint.control.sendGcode(["M106 S" + Math.round(v("bk3d_opfan") * 2.55)]);
            else if (op === "setspeed") OctoPrint.control.sendGcode(["M220 S" + Math.round(v("bk3d_opspeed"))]);
            else if (op === "setflow") OctoPrint.control.sendGcode(["M221 S" + Math.round(v("bk3d_opflow"))]);
            else if (op === "babyup" || op === "babydn")
                OctoPrint.control.sendGcode(["M290 Z" + (v("bk3d_opbaby") * (op === "babyup" ? 1 : -1)).toFixed(3)]);
            self.hud("Operación enviada: " + op);
        };

        // ---------------------------------------------------------------
        // Octaedro de navegación (ejes + aristas + esquinas a 45°)
        // ---------------------------------------------------------------
        self._buildNavGizmo = function () {
            var n = self._nav;
            n.ctx = n.canvas.getContext("2d"); n.canvas.width = 116; n.canvas.height = 116;
            n.scene = new THREE.Scene();
            n.camera = new THREE.OrthographicCamera(-1.9, 1.9, 1.9, -1.9, 0.1, 100);
            n.camera.position.set(0, 0, 6);
            n.scene.add(new THREE.AmbientLight(0xffffff, 0.92));
            var dl = new THREE.DirectionalLight(0xffffff, 0.5); dl.position.set(2, 3, 4); n.scene.add(dl);

            var gizmo = new THREE.Group();
            var H = 0.62;   // semilado del cuerpo del cubo
            // cuerpo del cubo + aristas
            var body = new THREE.Mesh(new THREE.BoxGeometry(2 * H, 2 * H, 2 * H),
                new THREE.MeshStandardMaterial({color: 0xcfd8dc, metalness: 0.05, roughness: 0.85}));
            gizmo.add(body);
            gizmo.add(new THREE.LineSegments(new THREE.EdgesGeometry(body.geometry),
                new THREE.LineBasicMaterial({color: 0x546e7a})));

            var targets = [];
            // 6 caras cuadradas etiquetadas -> vistas de eje
            var faces = [["SUP", [0, 0, 1], 0x4a90d9], ["INF", [0, 0, -1], 0x4a90d9],
                         ["FRENTE", [0, -1, 0], 0x5cb85c], ["ATRÁS", [0, 1, 0], 0x5cb85c],
                         ["DER", [1, 0, 0], 0xd9534f], ["IZQ", [-1, 0, 0], 0xd9534f]];
            faces.forEach(function (f) {
                var d = new THREE.Vector3(f[1][0], f[1][1], f[1][2]);
                var m = new THREE.Mesh(new THREE.PlaneGeometry(0.86, 0.86),
                    new THREE.MeshBasicMaterial({map: self._labelTexture(f[0], f[2]), transparent: true}));
                m.position.copy(d.clone().multiplyScalar(H + 0.01));
                m.lookAt(d.clone().multiplyScalar(10));
                m.userData.dir = d; gizmo.add(m); targets.push(m);
            });
            // 8 triángulos en las esquinas -> vistas a 45° (isométricas)
            var s = [-1, 1];
            s.forEach(function (sx) { s.forEach(function (sy) { s.forEach(function (sz) {
                var d = new THREE.Vector3(sx, sy, sz);
                var tri = new THREE.Mesh(new THREE.CircleGeometry(0.34, 3),
                    new THREE.MeshBasicMaterial({color: 0x90a4ae, side: THREE.DoubleSide}));
                tri.position.copy(d.clone().normalize().multiplyScalar(H * 1.5));
                tri.lookAt(d.clone().multiplyScalar(10));
                tri.userData.dir = d.clone(); gizmo.add(tri); targets.push(tri);
            }); }); });

            n.scene.add(gizmo); n.gizmo = gizmo; n.targets = targets;
            n.raycaster = new THREE.Raycaster();

            n.canvas.addEventListener("click", function (ev) {
                var r = n.canvas.getBoundingClientRect();
                var mx = ((ev.clientX - r.left) / r.width) * 2 - 1, my = -((ev.clientY - r.top) / r.height) * 2 + 1;
                n.raycaster.setFromCamera({x: mx, y: my}, n.camera);
                var hit = n.raycaster.intersectObjects(n.targets, false);
                if (hit.length) self._snapToDir(hit[0].object.userData.dir.clone());
            });
        };

        self._labelTexture = function (txt, color) {
            var c = document.createElement("canvas"); c.width = c.height = 96;
            var x = c.getContext("2d");
            x.fillStyle = "#eceff1"; x.fillRect(0, 0, 96, 96);
            x.strokeStyle = "#" + ("000000" + color.toString(16)).slice(-6);
            x.lineWidth = 5; x.strokeRect(3, 3, 90, 90);
            x.fillStyle = "#37474f"; x.font = "bold 21px sans-serif"; x.textAlign = "center"; x.textBaseline = "middle";
            x.fillText(txt, 48, 50);
            return new THREE.CanvasTexture(c);
        };

        self._snapToDir = function (dir) {
            var b = self._bedInfo; if (!b) return;
            var cx = b.centered ? 0 : b.w / 2, cy = b.centered ? 0 : b.d / 2;
            var span = Math.max(b.w, b.d) * 1.7;
            var d = dir.clone().normalize();
            var px = cx + d.x * span, py = cy + d.y * span, pz = d.z * span;
            // up: vista cenital/inferior pura -> +Y; resto -> +Z
            var up = (dir.x === 0 && dir.y === 0) ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1);
            self._three.camera.up.copy(up);
            self._three.controls.setLookAt(px, py, pz, cx, cy, 0, true);
            self._needRender = true;
        };

        // ---------------------------------------------------------------
        // Render
        // ---------------------------------------------------------------
        self._requestRender = function () { self._needRender = true; };

        self._renderView = function (scene, camera, ctx, w, h, clear) {
            var gl = self._three.renderer;
            if (gl.__w !== w || gl.__h !== h) { gl.setSize(w, h, false); gl.__w = w; gl.__h = h; }
            var res = self._lineRes || (self._lineRes = new THREE.Vector2());
            res.set(w, h);
            scene.traverse(function (o) { if (o.material && o.material.isLineMaterial) o.material.resolution.copy(res); });
            gl.render(scene, camera);
            if (clear) ctx.clearRect(0, 0, w, h);
            ctx.imageSmoothingEnabled = false;   // copia 1:1 nítida (resize resetea esto)
            ctx.drawImage(gl.domElement, 0, 0);
        };

        self._animate = function () {
            requestAnimationFrame(self._animate);
            var t = self._three;
            if (!t.controls || !t.renderer) return;
            var dt = t.clock.getDelta();
            if (dt > 0.1) dt = 0.1;

            // anima el cabezal por la cola de movimientos
            if (self._stepMotion(dt)) { self._moveNozzleToSim(); self._needRender = true; }
            if (self._trailDirty) { self._trailDirty = false; self._rebuildTrail(); }
            if (self._motionQ.length) { self._updateLive(); self._needRender = true; }
            else if (self._liveLine) { t.scene.remove(self._liveLine); self._liveLine = null; }

            if (t.controls.update(dt)) self._needRender = true;
            if (t.camLight) t.camLight.position.copy(t.camera.position);   // brillo que sigue la vista
            if (self._selBall) self._scaleMarker();   // bola a tamaño de pantalla constante

            var sd = self._side;
            var sideVisible = sd.ctx && sd.canvas.offsetParent !== null;
            var tabVisible = self._active && t.canvas && t.canvas.offsetParent !== null;

            if (tabVisible && self._needRender) {
                self._renderView(t.scene, t.camera, t.ctx, self._tabSize.w, self._tabSize.h, false);
                self._renderNavGizmo();
            }
            // sidebar SINCRONIZADO con el tab: misma cámara, ajustando el aspecto.
            if (sideVisible) self._renderSidebar();
            if (tabVisible) self._needRender = false;
        };

        self._renderSidebar = function () {
            var t = self._three, sd = self._side, cam = t.camera, w = self._sideSize.w, h = self._sideSize.h, a = w / h;
            if (cam.isPerspectiveCamera) {
                var sa = cam.aspect; cam.aspect = a; cam.updateProjectionMatrix();
                self._renderView(t.scene, cam, sd.ctx, w, h, false);
                cam.aspect = sa; cam.updateProjectionMatrix();
            } else {
                var l = cam.left, r = cam.right, halfH = cam.top;
                cam.left = -halfH * a; cam.right = halfH * a; cam.updateProjectionMatrix();
                self._renderView(t.scene, cam, sd.ctx, w, h, false);
                cam.left = l; cam.right = r; cam.updateProjectionMatrix();
            }
        };

        self._renderNavGizmo = function () {
            var n = self._nav, t = self._three;
            if (!n.gizmo || !n.ctx) return;
            var q = n.gizmo.quaternion.copy(t.camera.quaternion);
            (q.invert || q.inverse).call(q);
            self._renderView(n.scene, n.camera, n.ctx, 110, 110, true);
        };

        self._resize = function () {
            var t = self._three;
            if (!t.canvas) return;
            var overlay = document.getElementById("bk3d_overlay");
            // El CSS define la FORMA del recuadro (aspect-ratio). El buffer del canvas
            // se renderiza a RESOLUCIÓN DE DISPOSITIVO (devicePixelRatio) para que se
            // vea NÍTIDO en pantallas HiDPI/4K (con pixelRatio=1 salía borroso).
            var dpr = Math.min(window.devicePixelRatio || 1, 3);
            var cw = (overlay && overlay.clientWidth) || 480;
            var ch = (overlay && overlay.clientHeight) || Math.round(cw * 0.625);
            var w = Math.round(cw * dpr), h = Math.round(ch * dpr);
            t.canvas.width = w; t.canvas.height = h; self._tabSize = {w: w, h: h};
            var a = cw / ch;
            t.persCam.aspect = a; t.persCam.updateProjectionMatrix();
            var halfH = t.orthoCam.top;   // preserva la altura (zoom), ajusta el ancho
            t.orthoCam.left = -halfH * a; t.orthoCam.right = halfH * a; t.orthoCam.updateProjectionMatrix();
            self._needRender = true;
        };

        self._sideResize = function () {
            var sd = self._side;
            if (!sd.ctx || !sd.host) return;
            var dpr = Math.min(window.devicePixelRatio || 1, 3);
            var cw = sd.host.clientWidth || 200;
            var ch = sd.host.clientHeight || Math.round(cw * 0.75);   // sigue el CSS
            sd.canvas.width = Math.round(cw * dpr); sd.canvas.height = Math.round(ch * dpr);
            self._sideSize = {w: sd.canvas.width, h: sd.canvas.height};
            self._needRender = true;
        };

        self.frameBed = function (animate) {
            var t = self._three;
            if (!t.controls || !self._bedInfo) return;
            var b = self._bedInfo, cx = b.centered ? 0 : b.w / 2, cy = b.centered ? 0 : b.d / 2, span = Math.max(b.w, b.d);
            t.camera.up.set(0, 0, 1);
            t.controls.setLookAt(cx + span * 0.9, cy - span * 1.0, span * 0.95, cx, cy, 0, !!animate);
            self._needRender = true;
        };

        self.hud = function (m) { var h = document.getElementById("bk3d_hud"); if (h) h.textContent = m || ""; };

        // ---------------------------------------------------------------
        // Sidebar (toma la tarjeta Gcode del widget de UI Customizer)
        // ---------------------------------------------------------------
        self._ensureSidebar = function () {
            if (self._side.ctx) return true;
            if (!self._inited) { setTimeout(self._ensureSidebar, 200); return false; }
            // El mini-visor 3D vive en SU PROPIA tarjeta (BK · Visor 3D), NO secuestra
            // la tarjeta nativa "Gcode" (que sigue siendo el visor 2D de OctoPrint).
            var host = document.getElementById("bk3d_side_host");
            if (!host) { setTimeout(self._ensureSidebar, 400); return false; }
            var canvas = document.createElement("canvas");
            canvas.id = "bk3d_side_canvas"; canvas.className = "bk3d-side-canvas";
            host.appendChild(canvas);
            self._side.canvas = canvas; self._side.host = host; self._side.ctx = canvas.getContext("2d");
            self._side.ctx.imageSmoothingEnabled = false;
            self._sideResize(); self._needRender = true;
            return true;
        };

        // ---------------------------------------------------------------
        // OctoPrint
        // ---------------------------------------------------------------
        self.onStartupComplete = function () {
            var tries = 0;
            (function go() {
                if (self._inject()) {
                    try {
                        var s = self.settings.settings.plugins.boostkit;
                        if (s && s.gcode3d) {
                            var g = s.gcode3d;
                            self._trailLen = parseInt(g.trail_len(), 10) || 400;
                            self._followCam = !!g.follow_cam();
                            if (g.color_mode) self._colorMode = g.color_mode();
                            if (g.show_travel) self._showTravel = !!g.show_travel();
                            if (g.width_by_flow) self._widthByFlow = !!g.width_by_flow();
                            if (g.temp_lo) self._tempLo = parseFloat(g.temp_lo()) || 150;
                            if (g.temp_hi) self._tempHi = parseFloat(g.temp_hi()) || 300;
                            var nv = document.getElementById("bk3d_nval");
                            if (nv) nv.value = (self._trailMode === "moves") ? self._trailLen : (self._trailDistMm / 10);
                            var fb = document.getElementById("bk3d_follow"); if (fb) fb.classList.toggle("active", self._followCam);
                            var cm = document.getElementById("bk3d_colormode"); if (cm) cm.value = self._colorMode;
                            var tv = document.getElementById("bk3d_travel"); if (tv) tv.classList.toggle("active", self._showTravel);
                            var wf = document.getElementById("bk3d_wflow"); if (wf) wf.classList.toggle("active", self._widthByFlow);
                            var tl = document.getElementById("bk3d_templo"); if (tl) tl.value = self._tempLo;
                            var th = document.getElementById("bk3d_temphi"); if (th) th.value = self._tempHi;
                            var tr = document.getElementById("bk3d_temprange"); if (tr) tr.style.display = (self._colorMode === "temp") ? "" : "none";
                            if (g.bed_margin != null) self._bedMargin = parseFloat(g.bed_margin()) || 0;
                            if (g.show_bed != null) self._showBed = !!g.show_bed();
                            if (g.show_bed_mesh != null) self._showBedMesh = !!g.show_bed_mesh();
                            self._syncBedBtns();
                        }
                    } catch (e) {}
                    self._ensureInit(); self._ensureSidebar();
                } else if (++tries < 40) setTimeout(go, 150);
            })();
        };

        self.onTabChange = function (current) {
            if (current !== "#tab_plugin_boostkit_viewer") return;
            // Tab propio del visor: auto-activa el 3D la primera vez para no mostrar
            // una pestaña en blanco; después solo redimensiona/redibuja.
            if (!self._active) self.setActive(true);
            else { self._resize(); self._requestRender(); }
        };

        $(window).on("resize", function () {
            if (self._inited) { if (self._active) self._resize(); self._sideResize(); }
        });

        // --- persistencia del estado del visor (cross-browser, vía API del plugin) ---
        self._saveViewState = function () {
            clearTimeout(self._vsTimer);
            self._vsTimer = setTimeout(function () {
                var st = {
                    modelPath: self._modelPath || null,
                    selIdx: self._selIdx, selT: self._selT,
                    prefs: {
                        active: self._active, ortho: self._ortho,
                        colorMode: self._colorMode, showTravel: self._showTravel, widthByFlow: self._widthByFlow,
                        tempLo: self._tempLo, tempHi: self._tempHi,
                        trailMode: self._trailMode, trailLen: self._trailLen, trailDistMm: self._trailDistMm,
                        followCam: self._followCam
                    }
                };
                try { OctoPrint.simpleApiCommand("boostkit", "viewstate", {state: st}); } catch (e) {}
            }, 800);
        };

        self._applyViewState = function (vs) {
            if (!vs) return;
            var p = vs.prefs || {}, el = function (id) { return document.getElementById(id); },
                tog = function (id, on) { var e = el(id); if (e) e.classList.toggle("active", !!on); };
            if (p.colorMode) { self._colorMode = p.colorMode; var cm = el("bk3d_colormode"); if (cm) cm.value = p.colorMode; }
            if (p.showTravel !== undefined) { self._showTravel = !!p.showTravel; tog("bk3d_travel", p.showTravel); }
            if (p.widthByFlow !== undefined) { self._widthByFlow = !!p.widthByFlow; tog("bk3d_wflow", p.widthByFlow); }
            if (p.tempLo) { self._tempLo = p.tempLo; var tl = el("bk3d_templo"); if (tl) tl.value = p.tempLo; }
            if (p.tempHi) { self._tempHi = p.tempHi; var th = el("bk3d_temphi"); if (th) th.value = p.tempHi; }
            if (p.trailMode) { self._trailMode = p.trailMode; var nu = el("bk3d_nunit"); if (nu) nu.value = p.trailMode; }
            if (p.trailLen) self._trailLen = p.trailLen;
            if (p.trailDistMm) self._trailDistMm = p.trailDistMm;
            var nv = el("bk3d_nval"); if (nv) nv.value = (self._trailMode === "moves") ? self._trailLen : (self._trailDistMm / 10);
            if (p.followCam !== undefined) { self._followCam = !!p.followCam; tog("bk3d_follow", p.followCam); }
            var tr = el("bk3d_temprange"); if (tr) tr.style.display = (self._colorMode === "temp") ? "" : "none";
            if (p.active && !self._active) self.setActive(true);
            if (p.ortho && !self._ortho && self._inited) self.setProjection(true);
            // modelo cargado + selección
            if (vs.modelPath && vs.modelPath !== self._modelPath) {
                self._pendingSel = (vs.selIdx >= 0) ? {idx: vs.selIdx, t: vs.selT || 0} : null;
                self._loadGModelPath(vs.modelPath);
            }
            self._rebuildTrail();
        };

        // aplica los límites del firmware (M204 accel, M203 maxspeed) al simulador
        self._applyLimits = function (L) {
            if (!L) return;
            var m204 = L.M204 || {};
            var acc = m204.T || m204.P || m204.S;
            if (acc && acc > 50) self._simAccel = acc;
            var m203 = L.M203 || {};
            var mf = Math.min(m203.X || 1e9, m203.Y || 1e9);
            if (mf && mf < 1e9) self._simMaxFeed = mf;   // mm/s
        };

        self.onDataUpdaterPluginMessage = function (plugin, message) {
            if (plugin !== "boostkit" || !message) return;
            if (message.type === "tracktrail") {
                if (message.clear) { self._motionQ = []; self._trail = []; self._rebuildTrail(); return; }
                if (message.points && message.points.length) self._enqueuePoints(message.points);
            } else if (message.type === "possync") {
                // sincroniza posición + trazado + estado del visor al cargar/reconectar
                if (message.state) {
                    var p = message.state;
                    self._lastSync = {x: p.x, y: p.y, z: p.z, temp: p.temp || 0};
                    self._motionQ = [];
                    if (self._simPos) self._simPos.set(p.x, p.y, p.z);
                    // restaura el trazado persistido (historial del servidor); si no
                    // hay, siembra con la posición actual (no traza desde el origen).
                    if (message.history && message.history.length) {
                        var h = message.history, lim = (self._trailMode === "moves") ? self._trailLen : 2000;
                        self._trail = h.slice(Math.max(0, h.length - lim)).map(function (q) {
                            return [q[0], q[1], q[2], q[3] || 0, q[4] || 0, q[5] || 0, q[6] || 0];
                        });
                    } else {
                        self._trail = [[p.x, p.y, p.z, 0, 0, p.temp || 0, 0]];
                    }
                    self._moveNozzleToSim();
                    self._rebuildTrail();
                    self.hud("Sincronizado · X " + p.x + " Y " + p.y + " Z " + p.z);
                    self._requestRender();
                }
                self._applyLimits(message.limits);
                self._applyViewState(message.viewstate);
            } else if (message.type === "limits" && message.limits) {
                self._applyLimits(message.limits);
            } else if (message.type === "trackstate" && message.state) {
                var st = message.state;
                self.hud("X " + st.x + "  Y " + st.y + "  Z " + st.z + "  ·  capa " + st.layer + "  ·  alt " + st.height + " mm");
            } else if (message.type === "mesh" && message.mesh && message.mesh.z) {
                // Malla de nivelación activa: guárdala para poder superponerla a la cama.
                self._bedMeshZ = message.mesh.z;
                if (self._showBedMesh && self._inited) { self._buildBed(); self._requestRender(); }
            }
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: BoostGcode3DViewModel,
        dependencies: ["settingsViewModel", "printerProfilesViewModel", "printerStateViewModel"],
        elements: []
    });
});
