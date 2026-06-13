// ============================================================================
//  arduino_cnc_enclosure.scad — Caja para Arduino Uno + CNC Shield V3 (MCU CNC)
//  Usa lib/enclosure_lib.scad. Unidades: mm. Imprimible en K9 (<=100mm), PETG.
//
//  El Arduino Uno tiene footprint ESTÁNDAR (no estimado):
//    board 68.6 x 53.4 mm, 4 agujeros M3 (Ø3.2). Coords oficiales (Adafruit/Arduino).
//  El CNC Shield se apila sobre el Uno por los headers; los drivers A4988/DRV8825
//  calientan -> ventilador en la tapa soplando hacia abajo sobre los drivers.
// ============================================================================

include <lib/enclosure_lib.scad>

part = "assembly";   // "body" | "lid" | "assembly"

// -------- Arduino Uno (estándar) --------
UNO_L = 68.6;
UNO_W = 53.4;
UNO_T = 1.6;
// 4 agujeros oficiales, origen esquina -> recentrados al centro del board:
//  A(13.97,2.54) B(66.04,7.62) C(66.04,35.56) D(15.24,50.80) menos (34.29,26.67)
UNO_HOLES = [[-20.32,-24.13],[31.75,-19.05],[31.75,8.89],[-19.05,24.13]];

// -------- Alturas del stack --------
// Uno + headers + CNC Shield + drivers con disipador + holgura/ventilación.
STANDOFF_H = 4;      // separación Uno<->suelo (pines/soldadura por debajo)
COMP_H     = 40;     // alto libre sobre el Uno: stack shield+drivers + flujo de aire

// -------- Caja --------
WALL=2.4; FLOOR=2.0; RAD=3; GAP=1.5;
LID_FAN = 50;        // ventilador PC en la tapa sobre los drivers (50mm)

IL = UNO_L + 2*GAP;
IW = UNO_W + 2*GAP;
IH = STANDOFF_H + UNO_T + COMP_H;
INNER=[IL,IW,IH];
PCB_Z = FLOOR + STANDOFF_H;
cable_z = PCB_Z + 6;

module uno_body() {
    union() {
        difference() {
            enclosure_body(INNER, wall=WALL, floor=FLOOR, rad=RAD,
                           pcb_holes=UNO_HOLES, standoff_h=STANDOFF_H,
                           lid_boss_mode="tap", chamfer_bottom=0);
            // USB-B + jack DC del Uno (borde IZQUIERDO -X): abertura generosa
            // (ambos conectores van en este borde; ajustar si tu clon difiere)
            port_rect(INNER, WALL, "left", 30, 13, pos=-10, zc=PCB_Z+5);
            // Salida de cables de motores/endstops del shield (borde DERECHO +X)
            port_rect(INNER, WALL, "right", 44, 12, pos=0, zc=cable_z);
            // Salida extra de cables (borde TRASERO -Y): endstops/spindle
            port_rect(INNER, WALL, "back", 30, 10, pos=0, zc=cable_z);
            // Acoplamiento lateral con otras cajas (tornillo+tuerca M3)
            couple_holes(INNER, WALL);
        }
    }
}

ARDU_LID_VENTS = true;

if (part=="body") uno_body();
if (part=="lid")
    enclosure_lid(INNER, wall=WALL, rad=RAD, thick=2.4, fan=LID_FAN, vents=ARDU_LID_VENTS);
if (part=="assembly") {
    uno_body();
    color("LightSteelBlue",0.85)
        translate([0,0, FLOOR+IH+2.4 + 14])
            enclosure_lid(INNER, wall=WALL, rad=RAD, thick=2.4, fan=LID_FAN, vents=ARDU_LID_VENTS);
}
