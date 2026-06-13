// ============================================================================
//  coupling_demo.scad — Verificación visual del acople lateral entre cajas
//  Coloca la caja K10 y la del Arduino contiguas y comprueba que los agujeros
//  de acople (y=±24, z=30) alinean para pasar un tornillo M3 + tuerca.
//  (Demo con cascarones simplificados; las cajas reales están en sus .scad.)
// ============================================================================
include <lib/enclosure_lib.scad>
FLOOR=2; WALL=2.4;

module shell(il, iw, h, label) {
    difference() {
        rbox(il+2*WALL, iw+2*WALL, h, 3);
        translate([0,0,FLOOR]) rbox(il, iw, h, 2);
        couple_holes([il,iw], WALL);     // mismos COUPLE_Z / COUPLE_YS que las cajas reales
    }
}

// Dimensiones interiores reales de cada caja
K10  = [82.0, 56.0, 39.6];     // IL, IW, H
UNO  = [71.6, 56.4, 47.6];

// Caras que se tocan: +X de la K10 con -X del Arduino
xA = K10[0]/2 + WALL;          // cara derecha exterior de la K10
xB = UNO[0]/2 + WALL;          // cara izquierda exterior del Arduino
gap = 0;                        // las cajas se tocan
shiftB = xA + xB + gap;

color("SteelBlue")  shell(K10[0],K10[1],K10[2], "K10");
translate([shiftB,0,0]) color("IndianRed") shell(UNO[0],UNO[1],UNO[2], "UNO");

// Tornillos M3 de acople (atraviesan las dos paredes que se tocan)
color("Gold")
for (y=COUPLE_YS)
    translate([xA-8, y, COUPLE_Z]) rotate([0,90,0]) cylinder(d=3, h=16, $fn=24);
