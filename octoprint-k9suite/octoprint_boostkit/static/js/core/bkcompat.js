/*
 * Boost Kit — Shim de compatibilidad Underscore/Lodash (a prueba de timing).
 *
 * PROBLEMA REAL (diagnosticado 2026-06-19): el `_` global de la página acaba
 * siendo Lodash 4.x (lo confirma `_.flatMapDeep`), que NO tiene `_.contains`.
 * El CORE de OctoPrint trae Lodash 3.10.1 (que SÍ tiene `_.contains`) y lo usa en
 * `ItemListHelper._loadCurrentSortingFromLocalStorage` al CREAR los viewmodels.
 * Cuando un Lodash 4 pisa el `_` del core -> "_.contains is not a function" ->
 * aborta la creación -> "settingsViewModel is missing, can't run UI" (parecía
 * intermitente porque dependía del orden/timing de quién pisa `_`). El Lodash 4 NO
 * está en ningún .js del disco (ni core ni plugins) -> lo inyecta algo dinámico,
 * probablemente una EXTENSIÓN del navegador que ensucia window._.
 *
 * FIX a prueba de timing: interceptamos las asignaciones a `window._` con un
 * getter/setter. Así, venga cuando venga el Lodash que pise `_`, le reañadimos los
 * alias de Underscore que el core necesita (contains/any/all/pluck/where/findWhere).
 * Solo AÑADIMOS métodos ausentes; no tocamos los de Lodash -> nadie se rompe.
 */
(function () {
    "use strict";
    // Métodos de Underscore que Lodash 4 eliminó y que OctoPrint (core y plugins)
    // todavía usan. OctoPrint trae Lodash 3.10.1 (compatible); una extensión lo pisa
    // con Lodash 4. Reañadimos los alias sobre lo que haya.
    function patch(u) {
        try {
            if (!u || (typeof u !== "function" && typeof u !== "object")) return u;
            var def = function (name, fn) { if (typeof u[name] !== "function") u[name] = fn; };
            if (typeof u.includes === "function") { def("contains", u.includes); def("include", u.includes); }
            if (typeof u.some === "function") def("any", u.some);
            if (typeof u.every === "function") def("all", u.every);
            if (typeof u.map === "function") def("pluck", function (c, k) { return u.map(c, k); });
            if (typeof u.find === "function") def("findWhere", function (c, p) { return u.find(c, p); });
            if (typeof u.filter === "function") def("where", function (c, p) { return u.filter(c, p); });
            if (typeof u.keyBy === "function") def("indexBy", u.keyBy);
            def("object", function (list, values) {
                if (values) {
                    if (typeof u.zipObject === "function") return u.zipObject(list, values);
                    var o = {}; for (var i = 0; i < list.length; i++) o[list[i]] = values[i]; return o;
                }
                if (typeof u.fromPairs === "function") return u.fromPairs(list);
                var r = {}; for (var j = 0; j < list.length; j++) r[list[j][0]] = list[j][1]; return r;
            });
        } catch (e) { /* defensivo */ }
        return u;
    }
    function good(u) { return u && typeof u.contains === "function" && typeof u.object === "function"; }

    try {
        var store = patch(window._);
        Object.defineProperty(window, "_", {
            configurable: true,
            enumerable: true,
            get: function () { return store; },
            // Si ya tenemos un `_` compatible (Lodash 3.10.1 de OctoPrint o ya parcheado)
            // y llega un clobber sin esos métodos, lo IGNORAMOS y conservamos el bueno.
            set: function (v) { if (good(store) && !good(patch(v))) return; store = patch(v); }
        });
        if (window.console && console.info) console.info("Boost Kit: interceptor de compat Underscore/Lodash instalado.");
    } catch (e) {
        patch(window._);
        if (window.$) window.$(function () { patch(window._); });
    }

    // Refuerzo anti-extensión: una wallet (MetaMask/TronLink) puede REDEFINIR
    // `window._` con Object.defineProperty, saltándose nuestro interceptor y
    // dejando un Lodash 4 sin `_.contains`. Reaseguramos el parche durante la
    // ventana de arranque (creación de viewmodels de OctoPrint) por si eso ocurre.
    try {
        var ticks = 0;
        var iv = setInterval(function () {
            try {
                if (!good(window._)) patch(window._);   // reañade alias si lo pisaron
            } catch (e) {}
            if (++ticks > 120) clearInterval(iv);        // ~6 s (120 * 50 ms)
        }, 50);
        // y un último reaseguro cuando el DOM está listo (antes del bind real)
        if (window.$) window.$(function () { patch(window._); });
    } catch (e) { /* defensivo */ }
})();
