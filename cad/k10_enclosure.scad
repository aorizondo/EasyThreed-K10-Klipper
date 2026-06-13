// ============================================================================
//  k10_enclosure.scad — Caja para la placa de la EasyThreed K10 (GD32F303)
//  Usa lib/enclosure_lib.scad. Unidades: mm.  Imprimible en K9 (<=100mm), PETG.
//
//  ⚠️ COTAS ESTIMADAS de las fotos del reversing — VERIFICA con calibre y ajusta
//     los parámetros PCB_* y los puertos. Marcadas con TODO-MEDIR.
// ============================================================================

include <lib/enclosure_lib.scad>

// -------- Render: "body" | "lid" | "assembly" --------
part = "assembly";

// -------- Parámetros de la placa K10 (TODO-MEDIR) --------
PCB_L      = 80;     // TODO-MEDIR: largo PCB
PCB_W      = 55;     // TODO-MEDIR: ancho PCB
PCB_T      = 1.6;    // grosor PCB
COMP_H     = 18;     // TODO-MEDIR: altura del componente más alto (condensadores/conectores)
HOLE_INSET = 3.5;    // TODO-MEDIR: agujeros de montaje desde el borde del PCB
HOLE_DX    = PCB_L/2 - HOLE_INSET;   // si tu patrón no es simétrico, define a mano
HOLE_DY    = PCB_W/2 - HOLE_INSET;

// -------- Parámetros de caja --------
WALL       = 2.4;
FLOOR      = 2.0;
RAD        = 3;
SIDE_GAP   = 1.5;    // holgura lateral PCB<->pared
STANDOFF_H = 4;      // separa el PCB del suelo (pistas/soldaduras debajo)
FAN        = 40;     // ventilador PC: 40/50/60 (40 cabe de sobra)
CHAMFER    = 0;      // chaflán inferior (0 = recto; usa brim en el slicer para PETG)

// Interior derivado
IL = PCB_L + 2*SIDE_GAP;
IW = PCB_W + 2*SIDE_GAP;
IH = STANDOFF_H + PCB_T + COMP_H;     // alto interior libre
INNER = [IL, IW, IH];

PCB_HOLES = [[-HOLE_DX,-HOLE_DY],[HOLE_DX,-HOLE_DY],[HOLE_DX,HOLE_DY],[-HOLE_DX,HOLE_DY]];

// ============================================================================
//  Cuerpo con ventilador de pared trasera + puertos (placeholders TODO-MEDIR)
// ============================================================================
module k10_body() {
    fan_z = FLOOR + IH*0.5;                  // centro del ventilador en altura
    union() {
        difference() {
            enclosure_body(INNER, wall=WALL, floor=FLOOR, rad=RAD,
                           pcb_holes=PCB_HOLES, standoff_h=STANDOFF_H,
                           lid_boss_mode="tap", chamfer_bottom=CHAMFER);
            // Ventilador en la pared trasera (-Y), aspira/extrae sobre los drivers
            translate([0, -IW/2-WALL/2, fan_z]) fan_cutout(FAN, WALL);

            // --- PUERTOS (TODO-MEDIR posiciones reales) ---
            // USB-C de alimentación (cara frontal)
            port_rect(INNER, WALL, "front", 11, 6, pos= 25, zc=FLOOR+4);
            // Jack DC (cara frontal)
            port_rect(INNER, WALL, "front", 10, 9, pos= 5, zc=FLOOR+5);
            // Salida del mazo de cables del cabezal (hotend/termistor/fan) cara derecha
            port_rect(INNER, WALL, "right", 22, 10, pos= 0, zc=FLOOR+6);
            // Ranura microSD (cara frontal, baja)
            port_rect(INNER, WALL, "front", 14, 3.5, pos=-22, zc=FLOOR+2.5);
        }
        // rejilla del ventilador
        translate([0, -IW/2-WALL/2, fan_z]) fan_grille(FAN, WALL);
    }
}

// ============================================================================
//  Render
// ============================================================================
if (part=="body")  k10_body();
if (part=="lid")   enclosure_lid(INNER, wall=WALL, rad=RAD, thick=2.4, vents=true);
if (part=="assembly") {
    k10_body();
    // tapa colocada en su sitio (separada para ver el conjunto)
    color("LightSteelBlue", 0.85)
        translate([0,0, FLOOR+IH+2.4 + 12]) enclosure_lid(INNER, wall=WALL, rad=RAD, thick=2.4, vents=true);
}
