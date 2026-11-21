// ============================================================================
//  arduino_cnc_enclosure.scad — Caja para Arduino Uno + CNC Shield V3 (MCU CNC)
//  Usa lib/enclosure_lib.scad. Unidades: mm. Imprimible en K9 (<=100mm), PETG.
//
//  El Arduino Uno tiene footprint ESTÁNDAR (no estimado):
//    board 68.6 x 53.4 mm, 4 agujeros M3 (Ø3.2). Coords oficiales (Adafruit/Arduino).
//  El CNC Shield se apila sobre el Uno por los headers; los drivers A4988/DRV8825
//  calientan -> ventilador en la tapa soplando hacia abajo sobre los drivers.
//
//  Layout de conectores del Arduino Uno R3 (cara izquierda, vista desde arriba):
//    - USB-B: esquina SUPERIOR izquierda, centro ≈ Y=+18mm del centro del PCB
//    - DC jack: esquina INFERIOR izquierda, centro ≈ Y=-18mm del centro del PCB
//    - Ambos a la misma altura Z sobre el PCB (~7mm sobre la superficie)
//
//  Layout del CNC Shield V3 (apilado sobre el Uno):
//    - Power screw terminal (2 pines, 12-36V): borde izquierdo
//    - Stepper drivers (A4988/DRV8825): sobre el PCB, arriba
//    - Stepper output connectors: borde derecho
//    - Endstop connectors: borde superior
//
//  Mejoras v2:
//   - PCB levantado (STANDOFF_H=8) para espacio bajo la placa
//   - 2 tornillos M3 avellanados desde dentro para fijación a placa externa
//   - USB-B + DC jack Arduino en pared izquierda (posiciones corregidas)
//   - Keyhole port para jack DC de alimentación del shield (panel mount)
//   - Cables de motores/sensores salen por la tapa
//   - Tapa con ventilador 50mm + rejillas de ventilación pasiva
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
// PCB levantado para dejar espacio a las cabezas de tornillos M3 (~2mm) bajo la placa
STANDOFF_H = 8;      // separación Uno<->suelo (espacio para cabezas tornillos M3)
COMP_H     = 45;     // alto libre sobre el Uno: stack shield+drivers + espacio cables

// -------- Caja --------
WALL  = 2.4; FLOOR = 2.4; RAD = 3; GAP = 2.0;
LID_FAN = 50;        // ventilador PC en la tapa sobre los drivers (50mm)

IL = UNO_L + 2*GAP;
IW = UNO_W + 2*GAP;
IH = STANDOFF_H + UNO_T + COMP_H;
INNER = [IL, IW, IH];
PCB_Z = FLOOR + STANDOFF_H;   // Z de la superficie inferior del PCB

// -------- Posiciones de puertos en pared izquierda (-X) --------
// Coords del Arduino Uno R3: USB-B arriba, DC jack abajo, ambos en borde izquierdo.
// En nuestro modelo: +Y = arriba (visto desde arriba), -Y = abajo.
// 'pos' en port_rect para "left" = coordenada Y a lo largo de la pared.
// 'zc' = altura Z del centro del puerto.

// USB-B del Arduino: esquina superior izquierda del PCB.
// Centro ≈ Y=+18mm, Z ≈ PCB表面 + 5.25mm (USB-B tiene ~10.5mm alto)
usb_z = PCB_Z + UNO_T + 5.25;   // ≈ 17.3mm desde suelo
usb_y = 18;                      // posición Y en la pared izquierda

// DC barrel jack del Arduino: esquina inferior izquierda del PCB.
// Centro ≈ Y=-18mm, Z ≈ PCB表面 + 4.5mm (DC jack tiene ~9mm diámetro)
dcjack_z = PCB_Z + UNO_T + 4.5;  // ≈ 16.5mm desde suelo
dcjack_y = -18;                    // posición Y en la pared izquierda

// DC jack de alimentación del shield (panel mount): borde izquierdo del shield.
// Se monta en la pared, más arriba, con cables al terminal del shield.
// Posición: mism Y que el USB pero más arriba para que quepa la brida.
shield_jack_z = PCB_Z + UNO_T + COMP_H * 0.55;  // ≈ 38mm desde suelo
shield_jack_y = 18;                                // misma línea Y que el USB

// -------- Cuerpo de la caja --------
module uno_body() {
    union() {
        difference() {
            enclosure_body(INNER, wall=WALL, floor=FLOOR, rad=RAD,
                           pcb_holes=UNO_HOLES, standoff_h=STANDOFF_H,
                           lid_boss_mode="tap", chamfer_bottom=0);

            // === Pared IZQUIERDA (-X): conectores del Arduino + power shield ===

            // USB-B del Arduino: rectangular ~12x10.5mm (con margen FDM).
            // Posición: esquina superior izquierda del PCB.
            port_rect(INNER, WALL, "left", 14, 11, pos=usb_y, zc=usb_z);

            // DC barrel jack del Arduino: rectangular ~9x9mm.
            // Posición: esquina inferior izquierda del PCB.
            port_rect(INNER, WALL, "left", 10, 10, pos=dcjack_y, zc=dcjack_z);

            // Alimentación del CNC Shield: jack DC panel mount con brida.
            // Forma keyhole (cuadrado abajo + semicírculo arriba) + 2 tornillos M3.
            // Montado en la pared izquierda, arriba del USB, para llegar al
            // power terminal del shield con cables cortos.
            keyhole_port(INNER, WALL, "left",
                         w=10, h=10, rh=5.5, pos=shield_jack_y, zc=shield_jack_z,
                         screw_d=M3_CLEAR, screw_spacing=16);

            // === Pared DERECHA (+X): vacía (cables salen por la tapa) ===

            // === Pared TRASERA (-Y): vacía (cables salen por la tapa) ===

            // Acoplamiento lateral con otras cajas (tornillo+tuerca M3)
            couple_holes(INNER, WALL);

            // Montaje al suelo: 2 tornillos M3 avellanados desde dentro
            mount_to_floor_holes(INNER, WALL, FLOOR);
        }
    }
}

// Agujeros de fijación a placa externa: 2x M3 avellanados desde dentro de la caja.
// El avellanado embute la cabeza del tornillo en el suelo para que quede bajo el PCB.
// Los tornillos atraviesan el suelo y se roscan en la placa/base inferior.
module mount_to_floor_holes(inner, wall, floor, spacing_x=30) {
    for (sx=[-1,1])
        translate([sx*spacing_x/2, 0, -EPS]) {
            // Avellanado cónico desde la cara superior del suelo
            translate([0, 0, floor - 2.0])
                cylinder(d1=M3_CLEAR, d2=HEAD_D, h=2.0+EPS, $fn=32);
            // Pasante
            cylinder(d=M3_CLEAR, h=floor+2*EPS, $fn=32);
        }
}

// -------- Tapa personalizada --------
// Ventilador 50mm + rejillas de ventilación pasiva + ranura de salida de cables.
// Los cables de motores y sensores salen por la ranura en el borde +X de la tapa.

module uno_lid() {
    difference() {
        enclosure_lid(INNER, wall=WALL, rad=RAD, thick=2.4, lip=true, lip_h=3,
                      fan=LID_FAN, vents=true);

        // Ranura de salida de cables en el borde +X (motores + sensores)
        cable_exit_slot();
    }
}

// Ranura abierta en el borde +X de la tapa para que los cables de motores
// y sensores salgan hacia arriba sin quedar atrapados entre tapa y cuerpo.
// Corta tanto la placa superior como el labio de centrado.
module cable_exit_slot() {
    ol = IL + 2*WALL;
    // slot_w: ancho de la ranura a lo largo de Y (~40mm para 4 cables de motor + sensores)
    // slot_d: profundidad desde el borde exterior hacia dentro (~15mm)
    slot_w = 40;
    slot_d = 15;
    // Z: corta desde bajo el labio (z=-3) hasta sobre la placa (z=2.4)
    translate([ol/2 - slot_d/2, 0, -0.3])
        cube([slot_d + EPS, slot_w, 6 + EPS], center=true);
}

// -------- Selección de pieza a generar --------
if (part=="body") uno_body();
if (part=="lid")  uno_lid();
if (part=="assembly") {
    uno_body();
    color("LightSteelBlue",0.85)
        translate([0,0, FLOOR+IH+2.4 + 14])
            uno_lid();
}
