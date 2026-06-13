// ============================================================================
//  k10_enclosure.scad — Caja para la placa de la EasyThreed K10 (GD32F303)
//  Usa lib/enclosure_lib.scad. Unidades: mm.  Imprimible en K9 (<=100mm), PETG.
//  Sistema: 12 V / 60 W (confirmado por specs del producto).
//
//  ⚠️ COTAS ESTIMADAS de las fotos del reversing — VERIFICA con calibre y ajusta
//     PCB_*, posiciones de PUERTOS y de la VENTANA DE BOTONES (TODO-MEDIR).
//
//  Orientación del PCB (componentes hacia ARRIBA / tapa; botones hacia ABAJO / suelo):
//    - Borde TRASERO  (-Y): 4 conectores de motor (X/Y/Z/Extrusor)
//    - Borde IZQUIERDO(-X): jack DC 12V + USB-C (entradas de alimentación)
//    - Borde DERECHO  (+X): mazo del cabezal (HE calefactor + termistor + fan)
//    - Borde FRONTAL  (+Y): ranura microSD + ventilador de refrigeración (40mm)
//    - SUELO          (-Z): ventana de acceso a los botones (S3-S8 + botón ▲)
//    - TAPA           (+Z): rejilla de ventilación
// ============================================================================

include <lib/enclosure_lib.scad>

// -------- Render: "body" | "lid" | "assembly" --------
part = "assembly";

// -------- Parámetros de la placa K10 --------
// Medidos por FOTOGRAMETRÍA (escala anclada al MCU GD32F303 LQFP48 = 7.0 mm,
// verificado contando pines). Ver reference/medidas-fotogrametria.md.
PCB_L      = 79;     // contorno PCB largo  (±2 mm, alta confianza)
PCB_W      = 53;     // contorno PCB ancho  (±2 mm, alta confianza)
PCB_T      = 1.6;    // grosor PCB (estándar)
COMP_H     = 17;     // ⚠️ TODO-MEDIR: altura del componente más alto — las fotos
                     //    cenitales NO dan altura. Valor generoso de momento.
// Patrón de agujeros (4 esquinas) ~72 x 44 mm centro-a-centro (±2-3 mm, confianza MEDIA).
// No fiarse a ciegas: la retención principal es por contorno/tapa (ver nota abajo).
HOLE_DX    = 36;     // = ~72/2  (verificar con calibre antes de imprimir definitivo)
HOLE_DY    = 22;     // = ~44/2

// -------- Botones del reverso (TODO-MEDIR posiciones) --------
// La cara de soldadura lleva 8 botones: S3/S4 (lat. derecho), S5-S8 (fila inferior)
// y un botón redondo grande ▲ (play/imprimir). Sobresalen ~5mm del PCB hacia el suelo.
// En modo Klipper estos botones quedan INACTIVOS, pero se les deja holgura (no aplastar)
// y una ventana de acceso opcional por si usas el firmware Marlin de fábrica.
BTN_CLEAR    = 6;       // holgura bajo el PCB para el cuerpo de los botones (>= su altura)
BTN_ACCESS   = true;    // abrir ventana en el suelo para alcanzar los botones
BTN_WIN      = [54, 16];// TODO-MEDIR: [ancho, fondo] de la ventana del cluster inferior
BTN_WIN_OFF  = [0, -14];// TODO-MEDIR: desplazamiento [x,y] del centro de la ventana
FEET         = false;   // pies para levantar la caja (acceso a botones); false = mejor adhesión PETG
FEET_H       = 6;

// -------- Parámetros de caja --------
WALL       = 2.4;
FLOOR      = 2.0;
RAD        = 3;
SIDE_GAP   = 1.5;    // holgura lateral PCB<->pared
STANDOFF_H = max(4, BTN_CLEAR);   // separa el PCB del suelo (holgura para botones)
FAN        = 40;     // ventilador PC: 40/50/60 (40 cabe de sobra)
CHAMFER    = 0;      // chaflán inferior (0 = recto; usa brim en el slicer para PETG)

// Interior derivado
IL = PCB_L + 2*SIDE_GAP;
IW = PCB_W + 2*SIDE_GAP;
IH = STANDOFF_H + PCB_T + COMP_H;     // alto interior libre
INNER = [IL, IW, IH];
PCB_Z = FLOOR + STANDOFF_H;           // cota de la cara inferior del PCB

PCB_HOLES = [[-HOLE_DX,-HOLE_DY],[HOLE_DX,-HOLE_DY],[HOLE_DX,HOLE_DY],[-HOLE_DX,HOLE_DY]];

// ============================================================================
//  Cuerpo
// ============================================================================
module k10_body() {
    fan_z   = FLOOR + IH*0.5;          // centro del ventilador (cara frontal +Y)
    cable_z = PCB_Z + 5;               // altura de salida de los mazos (a nivel de conectores)

    union() {
        difference() {
            enclosure_body(INNER, wall=WALL, floor=FLOOR, rad=RAD,
                           pcb_holes=PCB_HOLES, standoff_h=STANDOFF_H,
                           lid_boss_mode="tap", chamfer_bottom=CHAMFER);

            // Ventilador de refrigeración en la cara FRONTAL (+Y)
            translate([0, IW/2+WALL/2, fan_z]) rotate([0,0,180]) fan_cutout(FAN, WALL);

            // --- PUERTOS por borde (TODO-MEDIR pos/altura exactas) ---
            // TRASERO (-Y): 4 motores en un mazo ancho
            port_rect(INNER, WALL, "back",  46, 9, pos=0,  zc=cable_z);
            // IZQUIERDO (-X): jack DC 12V + USB-C
            port_rect(INNER, WALL, "left",  10, 9, pos=14, zc=cable_z);   // DC barrel
            port_rect(INNER, WALL, "left",  11, 6, pos=-8, zc=cable_z);   // USB-C
            // DERECHO (+X): mazo del cabezal (HE + termistor + fan del hotend)
            port_rect(INNER, WALL, "right", 26, 11, pos=0, zc=cable_z);
            // FRONTAL (+Y): ranura microSD (baja, a un lado del ventilador)
            port_rect(INNER, WALL, "front", 14, 3.5, pos=-26, zc=PCB_Z-1);

            // --- VENTANA DE ACCESO A BOTONES en el suelo ---
            if (BTN_ACCESS)
                translate([BTN_WIN_OFF[0], BTN_WIN_OFF[1], -EPS])
                    linear_extrude(FLOOR+2*EPS)
                        offset(r=1) square([BTN_WIN[0]-2, BTN_WIN[1]-2], center=true);
        }
        // rejilla del ventilador (cara frontal)
        translate([0, IW/2+WALL/2, fan_z]) rotate([0,0,180]) fan_grille(FAN, WALL);

        // pies opcionales (para alcanzar los botones por debajo)
        if (FEET)
            for (sx=[-1,1], sy=[-1,1])
                translate([sx*(IL/2-4), sy*(IW/2-4), -FEET_H])
                    cylinder(d=8, h=FEET_H+EPS);
    }
}

// ============================================================================
//  Render
// ============================================================================
if (part=="body")  k10_body();
if (part=="lid")   enclosure_lid(INNER, wall=WALL, rad=RAD, thick=2.4, vents=true);
if (part=="assembly") {
    k10_body();
    color("LightSteelBlue", 0.85)
        translate([0,0, FLOOR+IH+2.4 + 12]) enclosure_lid(INNER, wall=WALL, rad=RAD, thick=2.4, vents=true);
}
