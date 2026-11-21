/*
 * bkmesh.js — Visor topográfico 3D de la malla de nivelación (Boost Kit).
 *
 * Renderiza una matriz n×n de alturas Z como una superficie 3D coloreada por
 * altura (heatmap + relieve), sobre su propio canvas con una escena Three.js
 * INDEPENDIENTE (el bug de "2 renderers sobre la misma escena" de r108 no aplica
 * porque esta escena es propia y separada de la del visor de gcode).
 *
 * API global:
 *   BKMesh.setMesh(canvasId, zMatrix, {xs, ys, label})  -> dibuja/actualiza
 *   BKMesh.clear(canvasId)
 *
 * Interacción: arrastrar = orbitar, rueda = zoom.
 */
var BKMesh = (function () {
    "use strict";
    var insts = {};   // canvasId -> instancia

    function get(canvasId) {
        var inst = insts[canvasId];
        if (inst) return inst;
        var canvas = document.getElementById(canvasId);
        if (!canvas || typeof THREE === "undefined") return null;

        var scene = new THREE.Scene();
        scene.background = new THREE.Color(0x1b1d22);
        var camera = new THREE.PerspectiveCamera(45, 1, 0.1, 5000);
        var renderer;
        try {
            renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: false });
        } catch (e) { return null; }
        renderer.setPixelRatio(window.devicePixelRatio || 1);

        scene.add(new THREE.AmbientLight(0xffffff, 0.75));
        var dir = new THREE.DirectionalLight(0xffffff, 0.55);
        dir.position.set(1, 1, 2);
        scene.add(dir);

        var group = new THREE.Group();   // contiene superficie + rejilla; se gira
        scene.add(group);

        inst = {
            canvas: canvas, scene: scene, camera: camera, renderer: renderer,
            group: group, surface: null, wire: null, lut: null,
            yaw: -0.7, pitch: 0.9, dist: 180, drag: null, raf: null, span: 100,
        };
        insts[canvasId] = inst;

        // --- interacción ratón ---
        canvas.addEventListener("mousedown", function (e) {
            inst.drag = { x: e.clientX, y: e.clientY };
        });
        window.addEventListener("mousemove", function (e) {
            if (!inst.drag) return;
            inst.yaw += (e.clientX - inst.drag.x) * 0.01;
            inst.pitch += (e.clientY - inst.drag.y) * 0.01;
            inst.pitch = Math.max(0.05, Math.min(Math.PI / 2 - 0.05, inst.pitch));
            inst.drag = { x: e.clientX, y: e.clientY };
            place(inst);
        });
        window.addEventListener("mouseup", function () { inst.drag = null; });
        canvas.addEventListener("wheel", function (e) {
            e.preventDefault();
            inst.dist *= (e.deltaY > 0 ? 1.1 : 0.9);
            // Límites RELATIVOS al tamaño de la malla (span): permite acercar mucho
            // más que el suelo fijo anterior (40) y alejar lo suficiente.
            var sp = inst.span || 100;
            inst.dist = Math.max(sp * 0.08, Math.min(sp * 8, inst.dist));
            place(inst);
        }, { passive: false });

        animate(inst);
        return inst;
    }

    function place(inst) {
        var cx = Math.cos(inst.pitch) * inst.dist;
        inst.camera.position.set(
            Math.sin(inst.yaw) * cx,
            -Math.cos(inst.yaw) * cx,
            Math.sin(inst.pitch) * inst.dist
        );
        inst.camera.up.set(0, 0, 1);
        inst.camera.lookAt(0, 0, 0);
    }

    function resize(inst) {
        var w = inst.canvas.clientWidth || 320, h = inst.canvas.clientHeight || 220;
        if (inst.canvas.width !== w || inst.canvas.height !== h) {
            inst.renderer.setSize(w, h, false);
            inst.camera.aspect = w / h;
            inst.camera.updateProjectionMatrix();
        }
    }

    function animate(inst) {
        inst.raf = requestAnimationFrame(function () { animate(inst); });
        resize(inst);
        inst.renderer.render(inst.scene, inst.camera);
    }

    // Etiqueta de texto como Sprite (billboard: siempre legible al orbitar).
    function makeLabel(text, color, span) {
        var cv = document.createElement("canvas"); cv.width = 256; cv.height = 64;
        var g = cv.getContext("2d");
        g.clearRect(0, 0, 256, 64);
        g.fillStyle = color || "#e6edf4";
        g.font = "bold 40px system-ui, sans-serif";
        g.textAlign = "center"; g.textBaseline = "middle";
        g.fillText(text, 128, 34);
        var tex = new THREE.CanvasTexture(cv);
        tex.minFilter = THREE.LinearFilter;
        var mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
        var s = new THREE.Sprite(mat);
        s.scale.set(span * 0.42, span * 0.105, 1);
        return s;
    }

    function rangeText(arr) {
        if (!arr || arr.length < 2) return "";
        var a = arr[0], b = arr[arr.length - 1];
        var lo = Math.round(Math.min(a, b)), hi = Math.round(Math.max(a, b));
        // Solo mostramos rango si parecen coords de máquina reales (no índices 0..n).
        return (hi - lo > arr.length) ? (" " + lo + "–" + hi) : "";
    }

    function buildAxisLabels(inst, span) {
        if (inst.labels) inst.labels.forEach(function (l) {
            inst.group.remove(l);
            if (l.material.map) l.material.map.dispose();
            l.material.dispose();
        });
        inst.labels = [];
        // Orientación del render: col -> world +X ; row -> world +Y (arriba).
        // En el flujo de nivelación de plato la malla es z[i=X][j=Y], así que:
        //   world +X (horizontal)  = índice de columna j = eje Y de MÁQUINA (ys)
        //   world +Y (profundidad) = índice de fila   i = eje X de MÁQUINA (xs)
        var lblY = makeLabel("Y" + rangeText(inst.ys), "#5fc8ff", span);
        lblY.position.set(span / 2 + span * 0.14, 0, 0);
        var lblX = makeLabel("X" + rangeText(inst.xs), "#ffcf6b", span);
        lblX.position.set(0, span / 2 + span * 0.14, 0);
        inst.group.add(lblY); inst.group.add(lblX);
        inst.labels = [lblY, lblX];
    }

    function buildSurface(inst, z, span) {
        var n = z.length;
        if (!n || !z[0]) return;
        // limpiar anterior
        if (inst.surface) { inst.group.remove(inst.surface); inst.surface.geometry.dispose(); }
        if (inst.wire) { inst.group.remove(inst.wire); inst.wire.geometry.dispose(); }

        // min/max para colorear y exagerar relieve
        var min = Infinity, max = -Infinity, i, j;
        for (i = 0; i < n; i++) for (j = 0; j < z[i].length; j++) {
            var v = z[i][j]; if (v == null) continue;
            if (v < min) min = v; if (v > max) max = v;
        }
        if (!isFinite(min)) { min = 0; max = 0; }
        var range = Math.max(0.02, max - min);
        // exagerar el relieve para que se aprecie (rango Z mapeado a ~span*0.18)
        var zExag = (span * 0.18) / range;

        var GeomCls = THREE.PlaneBufferGeometry || THREE.PlaneGeometry;
        var geo = new GeomCls(span, span, n - 1, n - 1);
        if (!inst.lut) inst.lut = new THREE.Lut("rainbow", 512);
        inst.lut.setMin(min); inst.lut.setMax(max);

        var colors = [];
        var pos = geo.attributes.position;
        // PlaneGeometry recorre filas (y) y columnas (x): índice = row*n + col
        for (var k = 0; k < pos.count; k++) {
            var row = Math.floor(k / n), col = k % n;
            var zv = (z[row] && z[row][col] != null) ? z[row][col] : min;
            pos.setZ(k, (zv - min) * zExag);
            var c = inst.lut.getColor(zv) || new THREE.Color(0x888888);
            colors.push(c.r, c.g, c.b);
        }
        pos.needsUpdate = true;
        geo.computeVertexNormals();
        var setAttr = geo.setAttribute ? geo.setAttribute.bind(geo) : geo.addAttribute.bind(geo);
        setAttr("color", new THREE.Float32BufferAttribute(colors, 3));

        var vcolFlag = (typeof THREE.VertexColors !== "undefined") ? THREE.VertexColors : true;
        var mat = new THREE.MeshLambertMaterial({ vertexColors: vcolFlag, side: THREE.DoubleSide, flatShading: false });
        inst.surface = new THREE.Mesh(geo, mat);
        inst.group.add(inst.surface);

        var wmat = new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.18 });
        inst.wire = new THREE.LineSegments(new THREE.WireframeGeometry(geo), wmat);
        inst.group.add(inst.wire);

        inst.span = span;
        inst.dist = span * 1.7;
        buildAxisLabels(inst, span);
        place(inst);
        return { min: min, max: max, range: range };
    }

    return {
        setMesh: function (canvasId, z, opts) {
            opts = opts || {};
            var inst = get(canvasId);
            if (!inst || !z || !z.length) return null;
            inst.xs = opts.xs; inst.ys = opts.ys;   // para etiquetas de eje + rango
            var span = 100;
            if (opts.xs && opts.xs.length > 1) span = Math.abs(opts.xs[opts.xs.length - 1] - opts.xs[0]) || 100;
            return buildSurface(inst, z, span);
        },
        clear: function (canvasId) {
            var inst = insts[canvasId];
            if (inst && inst.surface) { inst.group.remove(inst.surface); inst.surface = null; }
            if (inst && inst.wire) { inst.group.remove(inst.wire); inst.wire = null; }
        },
    };
})();
try { window.BKMesh = BKMesh; } catch (e) { /* no-op */ }
