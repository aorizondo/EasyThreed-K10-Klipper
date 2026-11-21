// ============================================================================
//  adxl345_k9_mount.scad — Housing del ADXL345 (módulo de 10 pines) para el
//  cabezal de la EasyThreed K9 que usa la MK4. Usa lib/enclosure_lib.scad.
//  Unidades: mm. PETG sin cama caliente (brim).
//
//  Módulo: eleberric "ADXL345 3-Axis Digital Accelerometer" — PCB ~28 x 15 mm,
//  SIN agujeros de montaje, dos tiras de 5 pines en los lados cortos:
//    +X: SCL SDA SDO INT2 INT1        -X: 5V 3V3 GND VS CS
//  ⚠️ Cotas de la ficha ("Approx. 28mm x 15mm") y de fotos: VERIFICA con calibre
//     PCB_*, ROW_INSET y COMP_BOT (TODO-MEDIR) antes de imprimir en serio.
//
//  Montaje (decidido con Antonio):
//   - PCB BOCA ABAJO: componentes hacia el suelo (hueco COMP_BOT), pines hacia
//     ARRIBA para conectores Dupont hembra, encerrados en la tapa.
//   - La PCB queda en sándwich: repisas de la base (bordes largos, cara de
//     componentes) + labio de la tapa (mismos bordes, cara limpia). Sin holgura:
//     un sensor que baila mide su propio traqueteo.
//   - Base a la cara SUPERIOR del cabezal: 2 x M3 avellanado (DIN 7991) desde
//     dentro de la base + arandela y tuerca por dentro de la carcasa. Usa
//     part="template" para marcar los taladros (Ø3.4).
//   - Tapa: 2 x M3x10 en columnas de esquina (autorroscante en la base).
//   - Ningún metal toca la PCB (requisito de Klipper: evitar bucles de masa).
//
//  Ejes: con la PCB boca abajo Z queda invertido. Tras montarlo, en Klipper:
//  AXES_MAP_CALIBRATION y fija axes_map en [adxl345 cabezal] (sensors.cfg).
//  Monta siempre con la flecha "SCL" de la tapa en la misma dirección para que
//  el axes_map siga valiendo.
// ============================================================================

include <lib/enclosure_lib.scad>

// -------- Render: "base" | "lid" | "template" | "assembly" --------
part = "assembly";

// -------- Módulo ADXL345 (TODO-MEDIR) --------
PCB_L      = 28.0;   // largo (eje de las tiras de pines en los extremos)
PCB_W      = 15.0;   // ancho
PCB_T      = 1.6;    // grosor estándar
PCB_FIT    = 0.15;   // holgura del alojamiento (más justa que FIT: no debe bailar)
ROW_INSET  = 1.4;    // centro de pin al borde corto
COMP_BOT   = 2.5;    // hueco bajo la cara de componentes (el más alto ~1.6 mm)
LEDGE_W    = 0.8;    // ancho de repisa/labio sobre el borde largo de la PCB

// -------- Conectores Dupont (pines rectos de 2.54) --------
HDR_SPACER = 2.5;    // separador negro de la tira, sobre la PCB
DUPONT_W   = 2.6;    // ancho de la carcasa hembra de 1 pin
DUPONT_H   = 14.5;   // largo de la carcasa hembra
WIRE_BEND  = 7;      // espacio para doblar los cables hacia la salida

// -------- Carcasa --------
WALL       = 2.4;
FLOOR      = 2.4;    // aloja la cabeza avellanada M3 a ras
LID_TOP    = 2.0;
CLAMP      = 0.2;    // precarga del labio sobre la PCB al apretar la tapa
LIP_H      = 1.2;
LID_END_GAP= 1.0;    // holgura extra de la tapa sobre los Dupont en los extremos
COL_D      = 7.0;    // columnas de los tornillos de la tapa
COL_OFF    = 1.0;    // columnas desplazadas en diagonal: el avellanado no invade la cavidad
TAP_DEPTH  = 6.0;    // rosca del autorroscante M3 en la base
LID_GRIP   = 4.0;    // material bajo la cabeza del tornillo de la tapa (M3x10)
CBORE_D    = 6.5;    // avellanado plano para la cabeza del tornillo de la tapa
MOUNT_SP   = 16;     // separación de los 2 tornillos al cabezal
CSK_D      = 6.2;    // cabeza M3 avellanada DIN 7991 (90°)
SLOT_W     = 22;     // salida de cables (pared +Y, hacia la espiral del cabezal)
SLOT_H     = 7;
ZIP_W      = 4;      // ranuras para brida en la tapa (alivio de tensión)
ZIP_T      = 1.8;

// -------- Derivadas --------
PL     = PCB_L + PCB_FIT;                  // alojamiento
PW     = PCB_W + PCB_FIT;
OL     = PL + 2*WALL;                      // exterior
OW     = PW + 2*WALL;
BASE_H = FLOOR + COMP_BOT + PCB_T;         // la cara superior de la PCB enrasa con la base
H_IN   = HDR_SPACER + DUPONT_H + WIRE_BEND;
LID_H  = H_IN + LID_TOP;
TOP_Z  = BASE_H + LID_H;
CORNERS= [[1, 1], [-1, -1]];               // columnas en diagonal
function col_pos(c) = [c[0]*(OL/2 + COL_OFF), c[1]*(OW/2 + COL_OFF)];
CSK_H  = (CSK_D - M3_CLEAR) / 2;           // 90°: profundidad = (D - d) / 2
EDGE_Y = PCB_W/2 - LEDGE_W;                // borde interior de repisa y labio
ROW_X  = PCB_L/2 - ROW_INSET;              // x de cada tira de pines

assert(CSK_H < FLOOR - 0.3, "FLOOR demasiado fino para la cabeza avellanada");
assert(EDGE_Y > 2*2.54 + DUPONT_W/2, "el labio pisaría los Dupont: baja LEDGE_W");

module footprint(h) {
    rbox(OL, OW, h, r=1.5);
    for (c = CORNERS) translate(col_pos(c)) cylinder(d=COL_D, h=h);
}

// ============================================================================
//  Base: se atornilla al cabezal; aloja la PCB boca abajo
// ============================================================================
module adxl_base() {
    difference() {
        footprint(BASE_H);
        translate([-PL/2, -PW/2, FLOOR]) cube([PL, PW, BASE_H]);           // alojamiento
        for (sx = [-1, 1]) translate([sx*MOUNT_SP/2, 0, 0]) {               // M3 al cabezal
            translate([0, 0, -EPS]) cylinder(d=M3_CLEAR, h=FLOOR + 2*EPS);
            translate([0, 0, FLOOR - CSK_H]) cylinder(d1=M3_CLEAR, d2=CSK_D, h=CSK_H + EPS);
        }
        for (c = CORNERS)                                                    // tornillos de la tapa
            translate([each col_pos(c), BASE_H - TAP_DEPTH]) cylinder(d=M3_TAP, h=TAP_DEPTH + EPS);
    }
    // Repisas: apoyan la cara de componentes solo en los bordes largos
    // (las colas de soldadura de los pines quedan en |y| <= 2*2.54, libres)
    for (sy = [-1, 1])
        translate([-PL/2, sy > 0 ? EDGE_Y : -PW/2, FLOOR - EPS])
            cube([PL, PW/2 - EDGE_Y, COMP_BOT + EPS]);
}

// ============================================================================
//  Tapa: encierra los Dupont, prensa la PCB con el labio, saca los cables por +Y
//  (se dibuja en su posición de montaje; para imprimir, part="lid" la voltea)
// ============================================================================
module adxl_lid() {
    difference() {
        translate([0, 0, BASE_H]) footprint(LID_H);
        translate([-(PL/2 + LID_END_GAP), -PW/2, BASE_H - EPS])            // cavidad
            cube([PL + 2*LID_END_GAP, PW, H_IN + EPS]);
        for (c = CORNERS) translate(col_pos(c)) {                           // M3x10
            translate([0, 0, BASE_H - EPS]) cylinder(d=M3_CLEAR, h=LID_H + 2*EPS);
            translate([0, 0, BASE_H + LID_GRIP]) cylinder(d=CBORE_D, h=LID_H);
        }
        translate([-SLOT_W/2, PW/2 - EPS, BASE_H + H_IN - SLOT_H])         // salida de cables
            cube([SLOT_W, WALL + 2*EPS, SLOT_H + EPS]);
        for (sx = [-1, 1])                                                   // brida junto a la salida
            translate([sx*SLOT_W/2 - ZIP_W/2, PW/2 - ZIP_T - 1, TOP_Z - LID_TOP - EPS])
                cube([ZIP_W, ZIP_T, LID_TOP + 2*EPS]);
        translate([0, 1.2, TOP_Z - 0.6]) linear_extrude(0.6 + EPS)          // marca de orientación
            text("ADXL345", size=3.2, halign="center", valign="center");
        translate([-2, -4.0, TOP_Z - 0.6]) linear_extrude(0.6 + EPS) {
            translate([2, 0]) text("SCL", size=2.6, halign="left", valign="center");
            polygon([[-8, -0.6], [-1, -0.6], [-1, -1.6], [1.6, 0], [-1, 1.6], [-1, 0.6], [-8, 0.6]]);
        }
    }
    // Labio: pisa la cara limpia de la PCB sobre las mismas franjas que las repisas
    for (sy = [-1, 1])
        translate([-PL/2, sy > 0 ? EDGE_Y : -PW/2, BASE_H - CLAMP])
            cube([PL, PW/2 - EDGE_Y, LIP_H + CLAMP]);
}

// ============================================================================
//  Plantilla de taladro: apóyala en la cara superior del cabezal, flecha hacia
//  la entrada del cable en espiral, y marca/taladra por los dos agujeros
// ============================================================================
module adxl_template() {
    difference() {
        footprint(1.2);
        for (sx = [-1, 1]) translate([sx*MOUNT_SP/2, 0, -EPS]) cylinder(d=M3_CLEAR, h=2);
        translate([0, 0, 0.6]) linear_extrude(1) {
            polygon([[-1, -4], [1, -4], [1, 2], [3, 2], [0, 6], [-3, 2], [-1, 2]]);  // +Y = cable
        }
    }
}

// ============================================================================
//  Vista de montaje: PCB, separadores y Dupont fantasma
// ============================================================================
module ghosts() {
    z0 = FLOOR + COMP_BOT;
    color("royalblue") translate([-PCB_L/2, -PCB_W/2, z0]) cube([PCB_L, PCB_W, PCB_T]);
    for (sx = [-1, 1]) {
        color("dimgray") translate([sx*ROW_X - 1.25, -2.5*2.54, z0 + PCB_T]) cube([2.5, 5*2.54, HDR_SPACER]);
        for (i = [-2:2]) color(["black","red","orange","yellow","green"][i+2])
            translate([sx*ROW_X - DUPONT_W/2, i*2.54 - DUPONT_W/2, z0 + PCB_T + HDR_SPACER])
                cube([DUPONT_W, DUPONT_W, DUPONT_H]);
    }
}

assert_printable(OL + COL_D, OW + COL_D, LID_H, "tapa ADXL");

if (part == "base")     adxl_base();
if (part == "lid")      translate([0, 0, TOP_Z]) rotate([180, 0, 0]) adxl_lid();   // techo sobre la cama
if (part == "template") adxl_template();
if (part == "assembly") {
    color("orange") adxl_base();
    ghosts();
    color("orange", 0.35) adxl_lid();
}
