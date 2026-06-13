// ============================================================================
//  enclosure_lib.scad — Librería paramétrica para enclosures modulares
//  Proyecto frankenprinter K10 + CNC. Unidades: mm.
//
//  Restricciones de impresión (K9): cada pieza <= 100 x 100 x 100 mm,
//  boquilla 0.4, PETG sin cama caliente. Usa assert_printable() para validar.
//
//  Convenciones:
//   - Caja "body": abierta por arriba, PCB sobre standoffs, bosses M3 en esquinas.
//   - "lid": tapa plana atornillada M3, admite ventilador y rejillas.
//   - Acople entre cajas: cola de milano (dovetail) en caras laterales.
// ============================================================================

$fn = 64;
MAX_PRINT = 100;          // límite de la K9
EPS = 0.01;

// ---- Tornillería M3 (ajusta holguras a tu impresora) ----
M3_CLEAR   = 3.4;         // agujero pasante para M3
M3_TAP     = 2.7;         // agujero para autorroscante M3 (sin inserto)
M3_INSERT  = 4.2;         // agujero para inserto térmico M3 (OD ~4.0-4.2)
BOSS_OD    = 6.0;         // diámetro exterior del boss de tornillo
BOSS_OVL   = 1.2;         // solape del boss dentro de la pared (evita caras coincidentes)
HEAD_D     = 6.0;         // diámetro cabeza M3 (avellanado/embutido)

FIT        = 0.30;        // holgura FDM general (PETG)

// ============================================================================
//  Helpers
// ============================================================================

// Prisma de esquinas redondeadas, centrado en XY, desde z=0
module rbox(l, w, h, r=2) {
    linear_extrude(height=h)
        offset(r=r) square([max(l-2*r,EPS), max(w-2*r,EPS)], center=true);
}

// Aviso (no detiene render) si la pieza excede el volumen de la K9
module assert_printable(l, w, h, name="pieza") {
    if (l > MAX_PRINT || w > MAX_PRINT || h > MAX_PRINT)
        echo(str("AVISO: ", name, " excede 100mm -> ", l, " x ", w, " x ", h));
}

// ============================================================================
//  Ventilador de PC estándar — separación de agujeros por tamaño nominal
// ============================================================================
function fan_hole_spacing(s) =
      s==40 ? 32.0
    : s==50 ? 40.0
    : s==60 ? 50.0
    : s==70 ? 61.5
    : s==80 ? 71.5
    : s==92 ? 82.5
    : s==120 ? 105.0
    : s*0.84;                          // aproximación si no es estándar
function fan_bore(s) = s - 3;          // diámetro del paso de aire

// Recorte para montar un ventilador en una pared de grosor 't'.
// Se centra en el origen, atraviesa en +Y (la pared está en el plano XZ).
module fan_cutout(size=40, t=3, hole=M3_TAP) {
    sp = fan_hole_spacing(size);
    rotate([-90,0,0]) {
        cylinder(d=fan_bore(size), h=t+2*EPS, center=true);            // paso de aire
        for (sx=[-1,1], sz=[-1,1])
            translate([sx*sp/2, sz*sp/2, 0])
                cylinder(d=hole, h=t+2*EPS, center=true);              // 4 tornillos
    }
}

// Rejilla protectora (barras) para tapar el paso de aire del ventilador.
// Sólido a intersecar con un disco del tamaño del bore.
module fan_grille(size=40, t=3, bar=2.2, gap=6) {
    sp = fan_hole_spacing(size);
    rotate([-90,0,0])
    intersection() {
        cylinder(d=fan_bore(size)+0.1, h=t, center=true);
        union() {
            n = ceil(size/gap);
            for (i=[-n:n])
                translate([i*gap,0,0]) cube([bar, size, t], center=true);
        }
    }
}

// ============================================================================
//  Acople entre cajas — cola de milano (dovetail) vertical
//   - dovetail_male:  prisma trapezoidal que sobresale de una cara
//   - dovetail_slot:  ranura (a restar) en la cara de la caja contigua
//  Ambos comparten el mismo perfil para encajar (slot algo mayor por FIT).
// ============================================================================
module dovetail_profile(base=10, top=14, height=6, len=30, clearance=0) {
    // trapecio en XZ extruido a lo largo de Y (len). 'top' más ancho que 'base'.
    translate([0,0,0])
    rotate([90,0,0])
    linear_extrude(height=len, center=true)
        polygon([
            [-(base/2+clearance), 0],
            [ (base/2+clearance), 0],
            [ (top/2 +clearance), height+clearance],
            [-(top/2 +clearance), height+clearance]
        ]);
}

// Macho: sobresale +X de la pared derecha
module dovetail_male(len=30, height=6) {
    dovetail_profile(len=len, height=height, clearance=0);
}
// Hembra: ranura a restar (con FIT para que deslice)
module dovetail_slot(len=40, height=6) {
    dovetail_profile(len=len, height=height, clearance=FIT);
}

// ============================================================================
//  Acoplamiento lateral entre cajas (bolt-together)
//   Agujeros M3 pasantes en las paredes laterales, a una altura (COUPLE_Z) y
//   posiciones (COUPLE_YS) COMUNES a todas las cajas -> cualquier par de cajas
//   contiguas se unen con tornillo+tuerca M3. Coloca los agujeros en pared
//   maciza (fuera de los puertos). Encadenable.
// ============================================================================
COUPLE_Z  = 30;          // altura común de los agujeros de acople (mm desde el suelo)
COUPLE_YS = [-24, 24];   // posiciones en Y (pared maciza en todas las cajas, ~56mm anchas)

module couple_holes(inner, wall, z=COUPLE_Z, ys=COUPLE_YS, d=M3_CLEAR) {
    il=inner[0];
    for (y=ys)
        translate([0, y, z]) rotate([0,90,0])
            cylinder(d=d, h=il+4*wall, center=true);   // atraviesa ambas paredes laterales
}

// ============================================================================
//  Boss de tornillo M3 (columna) con agujero según modo: "tap" | "insert" | "clear"
// ============================================================================
module screw_boss(h, mode="tap", od=BOSS_OD) {
    hole = mode=="insert" ? M3_INSERT : mode=="clear" ? M3_CLEAR : M3_TAP;
    difference() {
        cylinder(d=od, h=h);
        translate([0,0, mode=="insert" ? 1 : -EPS])
            cylinder(d=hole, h=h+2*EPS);
    }
}

// ============================================================================
//  Standoff para sujetar el PCB (con agujero autorroscante)
// ============================================================================
module pcb_standoff(h, od=5, hole=M3_TAP) {
    difference() {
        cylinder(d=od, h=h);
        translate([0,0,-EPS]) cylinder(d=hole, h=h+2*EPS);
    }
}

// ============================================================================
//  Retención del PCB por CONTORNO (sin depender de los agujeros)
//   - pcb_ledge: repisa perimetral donde apoya el borde del PCB (apoyo inferior)
//   - lid_holddown: postes en la tapa que presionan el margen del PCB (sujeción
//     superior). Se colocan en el margen libre de componentes (bordes del PCB).
// ============================================================================
module pcb_ledge(inner, pcb, rest_h, ledge_t=2, overlap=1.5, embed=1.2) {
    il=inner[0]; iw=inner[1];
    translate([0,0,rest_h-ledge_t])
        linear_extrude(ledge_t)
            difference() {
                square([il+2*embed, iw+2*embed], center=true);   // embebe en la pared
                square([pcb[0]-2*overlap, pcb[1]-2*overlap], center=true);
            }
}
// Postes de sujeción colgando de la cara inferior de la tapa (z=0 hacia -z)
module lid_holddown(positions, depth, size=5) {
    for (p=positions)
        translate([p[0], p[1], -depth])
            linear_extrude(depth+EPS) square([size,size], center=true);
}

// ============================================================================
//  CAJA (body): paredes + suelo + bosses de tapa + standoffs de PCB
//  Parámetros:
//   inner = [il, iw, ih]  espacio interior libre (alto = sobre el suelo)
//   wall, floor           grosores
//   rad                   radio de esquinas
//   pcb_holes [[x,y],...] posiciones de standoffs (origen = centro del interior)
//   standoff_h            altura de standoffs
//   boss_inset            cuánto se meten los bosses de esquina hacia dentro
// ============================================================================
module enclosure_body(inner, wall=2.4, floor=2.0, rad=3,
                      pcb_holes=[], standoff_h=4, boss_inset=0,
                      lid_boss_mode="tap", chamfer_bottom=1.2) {
    il=inner[0]; iw=inner[1]; ih=inner[2];
    ol=il+2*wall; ow=iw+2*wall; oh=ih+floor;
    boss_h = ih;                               // bosses suben hasta el borde
    assert_printable(ol, ow, oh, "K10 body");

    difference() {
        union() {
            // carcasa exterior
            difference() {
                rbox(ol, ow, oh, rad);
                translate([0,0,floor]) rbox(il, iw, ih+EPS, max(rad-wall,0.5));
            }
            // bosses de esquina para los tornillos de la tapa (solapan la pared -> fusionados)
            bx = il/2 - (BOSS_OD/2 - BOSS_OVL) - boss_inset;
            by = iw/2 - (BOSS_OD/2 - BOSS_OVL) - boss_inset;
            for (sx=[-1,1], sy=[-1,1])
                translate([sx*bx, sy*by, floor])
                    screw_boss(boss_h, mode=lid_boss_mode);
            // standoffs del PCB
            translate([0,0,floor])
                for (p=pcb_holes) translate([p[0],p[1],0]) pcb_standoff(standoff_h);
        }
        // chaflán inferior exterior (ayuda a la adhesión PETG, menos warping)
        if (chamfer_bottom>0)
            difference() {
                translate([0,0,-EPS]) rbox(ol+2, ow+2, chamfer_bottom+EPS, rad);
                translate([0,0,-EPS-1])
                    rbox(ol-2*chamfer_bottom, ow-2*chamfer_bottom, chamfer_bottom+2, rad);
            }
    }
    // exporta las posiciones de boss para que la tapa cuadre
}

// Posiciones de los bosses de esquina (para la tapa)
function corner_boss_xy(inner, boss_inset=0) =
    let(il=inner[0], iw=inner[1],
        bx=il/2 - (BOSS_OD/2 - BOSS_OVL) - boss_inset,
        by=iw/2 - (BOSS_OD/2 - BOSS_OVL) - boss_inset)
    [[-bx,-by],[bx,-by],[bx,by],[-bx,by]];

// ============================================================================
//  TAPA (lid): placa con agujeros de tornillo M3 (avellanados) + labio interior
// ============================================================================
module enclosure_lid(inner, wall=2.4, rad=3, thick=2.4, boss_inset=0,
                     lip=true, lip_h=3, fan=0, vents=false,
                     holddown=[], holddown_depth=0) {
    il=inner[0]; iw=inner[1];
    ol=il+2*wall; ow=iw+2*wall;
    assert_printable(ol, ow, thick+lip_h, "K10 lid");
    holes = corner_boss_xy(inner, boss_inset);

    difference() {
        union() {
            rbox(ol, ow, thick, rad);
            // labio que entra en la caja para centrar la tapa
            if (lip) translate([0,0,-lip_h])
                difference() {
                    rbox(il-FIT, iw-FIT, lip_h+EPS, max(rad-wall,0.5));
                    translate([0,0,-EPS]) rbox(il-FIT-2.4, iw-FIT-2.4, lip_h+1, max(rad-wall-1,0.5));
                }
        }
        // tornillos M3 avellanados
        for (p=holes) translate([p[0],p[1],-lip_h-EPS]) {
            cylinder(d=M3_CLEAR, h=thick+lip_h+1);
            translate([0,0,thick+lip_h-1.6+EPS]) cylinder(d1=M3_CLEAR, d2=HEAD_D, h=1.6);
        }
        // ventilador en la tapa (opcional)
        if (fan>0) translate([0,0,thick/2]) fan_cutout_flat(fan, thick);
        // rejilla de ventilación simple (slots) opcional
        if (vents)
            for (i=[-3:3]) translate([i*5, 0, -EPS])
                cube([2.2, iw*0.5, thick+2*EPS], center=false);
    }
    if (fan>0) translate([0,0,thick/2]) fan_grille_flat(fan, thick);
    // postes de sujeción del PCB (presionan el margen del PCB contra la repisa)
    if (len(holddown)>0) lid_holddown(holddown, holddown_depth);
}

// Variantes de ventilador para superficie horizontal (tapa): atraviesan en Z
module fan_cutout_flat(size=40, t=2.4, hole=M3_TAP) {
    sp = fan_hole_spacing(size);
    cylinder(d=fan_bore(size), h=t+2*EPS, center=true);
    for (sx=[-1,1], sz=[-1,1])
        translate([sx*sp/2, sz*sp/2, 0]) cylinder(d=hole, h=t+2*EPS, center=true);
}
module fan_grille_flat(size=40, t=2.4, bar=2.2, gap=6) {
    intersection() {
        cylinder(d=fan_bore(size)+0.1, h=t, center=true);
        union() {
            n = ceil(size/gap);
            for (i=[-n:n]) translate([i*gap,0,0]) cube([bar, size, t], center=true);
        }
    }
}

// ============================================================================
//  Recorte de puerto genérico (rectangular) en una pared
//   face: "front"(+Y) "back"(-Y) "left"(-X) "right"(+X)
//   pos: desplazamiento a lo largo de la pared; zc: centro en altura
// ============================================================================
module port_rect(inner, wall, face, w, h, pos=0, zc=8, fillet=1) {
    il=inner[0]; iw=inner[1];
    t = wall+2*EPS;
    module slot() rotate([90,0,0]) linear_extrude(t, center=true)
        offset(r=fillet) square([w-2*fillet, h-2*fillet], center=true);
    if (face=="front")  translate([pos,  iw/2+wall/2, zc]) slot();
    if (face=="back")   translate([pos, -iw/2-wall/2, zc]) slot();
    if (face=="right")  translate([ il/2+wall/2, pos, zc]) rotate([0,0,90]) slot();
    if (face=="left")   translate([-il/2-wall/2, pos, zc]) rotate([0,0,90]) slot();
}
