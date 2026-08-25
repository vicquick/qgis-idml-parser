"""Geometry helpers: units, affine transforms, IDML path building.

IDML facts used here (IDML File Format Specification, ch. 'Spreads'):
- Coordinates are doubles in points (1/72 inch).
- A page item's shape lives in its *inner* coordinate system; the
  ItemTransform attribute ("a b c d tx ty") maps inner -> parent (spread).
- A single page is placed so that its centre sits on the spread origin,
  i.e. Page ItemTransform = "1 0 0 1 -W/2 -H/2" with
  GeometricBounds = "0 0 H W" (y1 x1 y2 x2).
- PathPointType: Anchor (point), LeftDirection (incoming bezier control),
  RightDirection (outgoing control). Straight segment = both == Anchor.
"""

import math

MM2PT = 72.0 / 25.4  # 2.834645669291339


def mm(v):
    """mm -> pt"""
    return v * MM2PT


def fmt(v):
    """Format a coordinate double the way InDesign likes it (no exponent)."""
    if v == int(v):
        return str(int(v))
    return repr(round(float(v), 6))


def matrix_str(a, b, c, d, tx, ty):
    return " ".join(fmt(v) for v in (a, b, c, d, tx, ty))


def identity_at(tx, ty):
    return matrix_str(1, 0, 0, 1, tx, ty)


def rotation_at(deg, tx, ty, cx=0.0, cy=0.0):
    """Rotation by `deg` (clockwise, y-down system = QGIS layout sense)
    about inner-space point (cx, cy), then translate by (tx, ty).

    IDML matrices are row-vector PostScript style in a y-down world, so a
    positive QGIS item rotation (clockwise on screen) maps directly.
    """
    r = math.radians(deg)
    a = math.cos(r)
    b = math.sin(r)
    # rotate about (cx, cy):  T(c) . R . T(-c), then external translate
    tx2 = tx + cx - (cx * a - cy * b)
    ty2 = ty + cy - (cx * b + cy * a)
    return matrix_str(a, b, -b, a, tx2, ty2)


def path_point(anchor, left=None, right=None):
    ax, ay = anchor
    lx, ly = left if left else anchor
    rx, ry = right if right else anchor
    return (
        '<PathPointType Anchor="{} {}" LeftDirection="{} {}" '
        'RightDirection="{} {}"/>'.format(
            fmt(ax), fmt(ay), fmt(lx), fmt(ly), fmt(rx), fmt(ry)
        )
    )


def path_geometry(point_lists, open_path=False):
    """point_lists: list of paths; each path = list of path_point() strings."""
    parts = ["<PathGeometry>"]
    for pts in point_lists:
        parts.append(
            '<GeometryPathType PathOpen="{}">'.format("true" if open_path else "false")
        )
        parts.append("<PathPointArray>")
        parts.extend(pts)
        parts.append("</PathPointArray>")
        parts.append("</GeometryPathType>")
    parts.append("</PathGeometry>")
    return "".join(parts)


def rect_path(w, h):
    """Closed rectangle (0,0)-(w,h) in inner coordinates.

    Counter-clockwise in a y-down system = the order InDesign itself
    writes (TL, BL, BR, TR ... actually TL->BL->BR->TR per spec example)."""
    pts = [
        path_point((0, 0)),
        path_point((0, h)),
        path_point((w, h)),
        path_point((w, 0)),
    ]
    return path_geometry([pts])


KAPPA = 0.5522847498307936


def ellipse_path(w, h):
    """Closed ellipse inscribed in (0,0)-(w,h), 4 bezier nodes (N,E,S,W)."""
    cx, cy = w / 2.0, h / 2.0
    rx, ry = w / 2.0, h / 2.0
    kx, ky = rx * KAPPA, ry * KAPPA
    # nodes: top, right, bottom, left (clockwise in y-down screen sense)
    pts = [
        path_point((cx, 0), left=(cx - kx, 0), right=(cx + kx, 0)),
        path_point((w, cy), left=(w, cy - ky), right=(w, cy + ky)),
        path_point((cx, h), left=(cx + kx, h), right=(cx - kx, h)),
        path_point((0, cy), left=(0, cy + ky), right=(0, cy - ky)),
    ]
    return path_geometry([pts])


def triangle_path(w, h):
    pts = [
        path_point((w / 2.0, 0)),
        path_point((0, h)),
        path_point((w, h)),
    ]
    return path_geometry([pts])


def polygon_path(points, closed=True):
    """points: [(x, y), ...] in inner pt coordinates."""
    pts = [path_point(p) for p in points]
    return path_geometry([pts], open_path=not closed)
