#!/usr/bin/env bash
# Genera STL imprimibles + previews PNG de los enclosures.
# Requiere: openscad, xvfb-run. Los STL van a stl/ (regenerables, no versionados);
# los PNG a renders/ (sí versionados como referencia visual).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p stl renders

CS=Tomorrow
png(){ # png <file.scad> <part> <out.png> <camera>
  xvfb-run -a openscad -D "part=\"$2\"" -o "renders/$3" --imgsize=1100,800 \
    --camera="$4" --colorscheme=$CS "$1" >/dev/null 2>&1
}
stl(){ # stl <file.scad> <part> <out.stl>
  openscad -D "part=\"$2\"" -o "stl/$3" "$1" 2>&1 | grep -i "2-manifold" && \
    echo "  !! $3 NO es 2-manifold" || echo "  ok $3"
}

echo "== K10 =="
stl k10_enclosure.scad body k10_body.stl
stl k10_enclosure.scad lid  k10_lid.stl
png k10_enclosure.scad assembly k10_assembly.png "0,0,0,55,0,30,250"
png k10_enclosure.scad body     k10_body_top.png "0,0,0,18,0,0,230"
png k10_enclosure.scad lid      k10_lid.png      "0,0,0,55,0,30,230"

echo "Listo. STL en cad/stl/  ·  PNG en cad/renders/"
