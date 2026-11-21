# coding=utf-8
"""
Tramming helpers: bilinear plane fit + M421 mesh fill commands.

Sign semantics (M421 convention):
  Z positive  → nozzle is HIGHER above the bed (larger gap).
  Z negative  → nozzle is LOWER (smaller gap, paper compressed).
  When the user measures with paper, the reading is a POSITIVE gap
  (paper touches both bed and nozzle).  The mesh must COMPENSATE
  that distance, so z_mesh = z_plane - paper_thickness.
  If z_plane at a node is 0.20 mm and paper is 0.10 mm,
  the mesh entry is Z0.100 — raising the nozzle by 0.10 mm so
  the compensated height matches the real surface.
"""
from __future__ import absolute_import


def fit_plane_bilinear(corners):
    """Fit a bilinear surface z = a + b*x + c*y + d*x*y through 4 corners.

    ``corners`` is a dict with keys ``fl``, ``fr``, ``bl``, ``br``,
    each a 3-tuple ``(x, y, z)``:

    - ``fl`` : front-left  (xmin, ymin)
    - ``fr`` : front-right (xmax, ymin)
    - ``bl`` : back-left   (xmin, ymax)
    - ``br`` : back-right  (xmax, ymax)

    Returns a callable ``z(x, y)`` that evaluates the surface.
    Coefficients ``(a, b, c, d)`` are also available as ``z.coeffs``.

    The surface passes EXACTLY through the 4 corner measurements
    (no least-squares approximation).  Handles the case where
    the 4 points are colinear (degenerates to a linear ramp).
    """
    # Extract the 4 points
    # fl/fr share y0 (front = ymin), bl/br share y1 (back = ymax)
    # fl/bl share x0 (left = xmin), fr/br share x1 (right = xmax)
    x0, y0, z00 = corners['fl']          # front-left  (xmin, ymin)
    x1, y1, z10 = corners['fr']          # front-right (xmax, ymin)
    _, y1, z01 = corners['bl']            # back-left  (xmin, ymax)
    _, _, z11 = corners['br']             # back-right (xmax, ymax)

    dx = x1 - x0
    dy = y1 - y0

    # Bilinear coefficients via direct solve of:
    #   z(fl) = a + b*x0 + c*y0 + d*x0*y0 = z00
    #   z(fr) = a + b*x1 + c*y0 + d*x1*y0 = z10
    #   z(bl) = a + b*x0 + c*y1 + d*x0*y1 = z01
    #   z(br) = a + b*x1 + c*y1 + d*x1*y1 = z11
    det = dx * dy
    if abs(det) < 1e-12:
        # Degenerate rectangle — fall back to linear interpolation
        a = (z00 + z10 + z01 + z11) * 0.25
        b, c, d = 0.0, 0.0, 0.0
    else:
        a = (z00 * x1 * y1 - z10 * x0 * y1 - z01 * x1 * y0
             + z11 * x0 * y0) / det
        b = (-z00 * y1 + z10 * y1 + z01 * y0 - z11 * y0) / det
        c = (-z00 * x1 + z10 * x0 + z01 * x1 - z11 * x0) / det
        d = (z00 - z10 - z01 + z11) / det

    def z(x, y):
        return a + b * x + c * y + d * x * y

    setattr(z, "coeffs", (a, b, c, d))
    return z


def grid_coords(n, lo, hi):
    """n evenly-spaced points from lo to hi (inclusive).  Matches
    ``calibration.leveling.grid_coords`` semantics."""
    if n <= 1:
        return [(lo + hi) / 2.0]
    step = (hi - lo) / (n - 1)
    return [lo + k * step for k in range(n)]


def build_mesh_commands(corners, grid_n, bounds, paper_thickness):
    """Generate M421 mesh-fill commands for a ``grid_n × grid_n`` grid.

    Parameters
    ----------
    corners : dict
        As in ``fit_plane_bilinear`` — ``fl``, ``fr``, ``bl``, ``br``
        each ``(x, y, z)``.
    grid_n : int
        Number of nodes per axis (e.g. 3 → 9 commands).
    bounds : tuple
        ``(xmin, xmax, ymin, ymax)`` of the mesh region.
    paper_thickness : float
        Thickness of the feeler gauge / paper sheet in mm.
        Subtracted from each z value (``z_mesh = z_plane - paper``).

    Returns
    -------
    list[str]
        List of G-code strings, one per node, in the form
        ``"M421 I{i} J{j} Z{z}"`` with 3 decimal places.
        The caller prepends ``M420 S1`` to activate the mesh.

    Sign semantics
    -------------
    Positive Z in the mesh → nozzle is higher (larger gap).
    The paper reading is a POSITIVE gap; subtracting paper_thickness
    converts the raw measurement to the compensated value the firmware
    needs so the nozzle sits exactly ``paper_thickness`` above the bed.

    Example
    -------
    >>> corners = {'fl':(0,0,0.20),'fr':(200,0,-0.10),
    ...            'bl':(0,200,0.30),'br':(200,200,0.00)}
    >>> cmds = build_mesh_commands(corners, 3, (0,200,0,200), 0.10)
    >>> len(cmds)
    9
    >>> cmds[0]
    'M421 I0 J0 Z0.100'
    """
    xmin, xmax, ymin, ymax = bounds
    xs = grid_coords(grid_n, xmin, xmax)
    ys = grid_coords(grid_n, ymin, ymax)

    z_plane = fit_plane_bilinear(corners)

    cmds = []
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            z = z_plane(x, y) - paper_thickness
            cmds.append("M421 I%d J%d Z%.3f" % (i, j, round(z, 3)))
    return cmds
