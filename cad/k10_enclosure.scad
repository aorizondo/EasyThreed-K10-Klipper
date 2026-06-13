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
PCB_L      = 84.2;   // MEDIDO con calibre
PCB_W      = 54.0;   // MEDIDO con calibre
PCB_T      = 1.6;    // MEDIDO
COMP_H     = 30;     // alto libre sobre el PCB: componente más alto = 10mm (medido)
                     //    + ~20mm de ventilación. Fijado a 30 mm (pedido del usuario).
// Patrón de agujeros: 4 esquinas, Ø3 mm, 77 x 47 mm centro-a-centro (MEDIDO con calibre).
HOLE_DX    = 38.5;   // = 77/2
HOLE_DY    = 23.5;   // = 47/2

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
// Orientación (vista cenital, componentes hacia la tapa):
//   +Y (back)  = 4 conectores de motor (X/Y/Z/Extrusor)
//   +X (right) = mazo VIN + FAN + HE(calefactor) + TH(termistor)
//   +Y... front (-Y... ojo: port_rect "front" = +Y. Aquí "front" lo uso como
//   el borde de usuario): microSD + USB-C(PD) + jack DC
//   -X (left)  = ventilador de refrigeración (sopla a través del PCB)
// Posiciones (pos, mm desde el centro del borde) medidas por fotogrametría
// (motores y microSD: confianza alta; USB-C/DC: aprox., afinar con calibre).
module k10_body() {
    fan_z   = FLOOR + IH*0.5;          // centro del ventilador (cara izquierda -X)
    cable_z = PCB_Z + 5;               // altura de salida de los mazos (nivel conectores)

    union() {
        difference() {
            // PCB montado por TORNILLO en sus 4 agujeros (patrón 77x47 mm, Ø3, medido)
            enclosure_body(INNER, wall=WALL, floor=FLOOR, rad=RAD,
                           pcb_holes=PCB_HOLES, standoff_h=STANDOFF_H,
                           lid_boss_mode="tap", chamfer_bottom=CHAMFER);

            // Ventilador de refrigeración en la cara IZQUIERDA (-X)
            translate([-IL/2-WALL/2, 0, fan_z]) rotate([0,0,90]) fan_cutout(FAN, WALL);

            // --- PUERTOS por borde ---
            // BACK (-Y... port_rect "back"): 4 motores en mazo ancho (x: -27,-11,+2,+16)
            port_rect(INNER, WALL, "back",  56, 9, pos=-5, zc=cable_z);
            // RIGHT (+X): mazo del cabezal/potencia VIN+FAN+HE+TH (apilados en Y)
            port_rect(INNER, WALL, "right", 40, 11, pos=0, zc=cable_z);
            // FRONT (+Y): microSD + USB-C(PD) + jack DC (cara de usuario)
            port_rect(INNER, WALL, "front", 14, 3.5, pos=-12, zc=PCB_Z-1);  // microSD
            port_rect(INNER, WALL, "front", 10, 6,   pos=+6,  zc=cable_z);   // USB-C (PD)
            port_rect(INNER, WALL, "front", 10, 9,   pos=+22, zc=cable_z);   // jack DC 12V
            // USB INDEPENDIENTE (Klipper + 5V): conector USB separado para
            // datos (D+/D- a PA12/PA11) y alimentar el MCU por 5V (a VIN del AMS1117).
            port_rect(INNER, WALL, "front", 13, 7,   pos=-32, zc=cable_z);   // USB extra

            // --- VENTANA DE ACCESO A BOTONES en el suelo ---
            if (BTN_ACCESS)
                translate([BTN_WIN_OFF[0], BTN_WIN_OFF[1], -EPS])
                    linear_extrude(FLOOR+2*EPS)
                        offset(r=1) square([BTN_WIN[0]-2, BTN_WIN[1]-2], center=true);

            // --- ACOPLAMIENTO lateral con otras cajas (tornillo+tuerca M3) ---
            couple_holes(INNER, WALL);
        }
        // rejilla del ventilador (cara izquierda)
        translate([-IL/2-WALL/2, 0, fan_z]) rotate([0,0,90]) fan_grille(FAN, WALL);

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
// PCB montado por tornillo en los 4 agujeros -> la tapa ya no necesita postes de sujeción
if (part=="body")  k10_body();
if (part=="lid")
    enclosure_lid(INNER, wall=WALL, rad=RAD, thick=2.4, vents=true);
if (part=="assembly") {
    k10_body();
    color("LightSteelBlue", 0.85)
        translate([0,0, FLOOR+IH+2.4 + 12])
            enclosure_lid(INNER, wall=WALL, rad=RAD, thick=2.4, vents=true);
}
