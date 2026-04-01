"""
sbs/utils.py – Utility functions and shape library (SHAPE_LIBRARY) for the SBS Image Editor.

Provides:
  • pil_to_qpixmap()   – converts a PIL image to a Qt QPixmap for display.
  • SHAPE_LIBRARY      – dictionary of 8 vector shapes stored as normalised coordinates.
  • draw_shape_on_pil() – renders a shape from SHAPE_LIBRARY onto a PIL image.
"""
import math

from PyQt6.QtGui import QPixmap, QImage

try:
    from PIL import Image as PILImage, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

def pil_to_qpixmap(img: "PILImage.Image") -> QPixmap:
    """
    Convert a PIL Image to a QPixmap for display in Qt widgets.

    Why this function?
    PyQt6 cannot display PIL images directly.  PIL stores pixels as raw
    Python bytes while Qt requires QImage / QPixmap.
    Conversion path: PIL → raw RGBA bytes → QImage → QPixmap.

    RGBA = 4 channels: Red, Green, Blue, Alpha (transparency).

    Parameters
    ----------
    img : PIL Image in any mode (will be converted to RGBA if necessary).

    Returns
    -------
    QPixmap ready for use with QLabel.setPixmap() or similar.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qi = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qi)


# ══════════════════════════════════════════════════════════════
#  SHAPE LIBRARY: Text-to-Drawing feature
#
#  CONCEPT:
#  Instead of using an AI model (e.g. Stable Diffusion, too slow for
#  a school project), shapes are stored as normalised vector coordinates.
#
#  NORMALISATION:
#  All points lie in the range 0.0–1.0, independent of image size.
#  When drawing, they are scaled by the desired 'size' parameter.
#  Advantage: one shape definition works for all sizes (30 px to 400 px).
#
#  ADVANTAGES over real text-to-image:
#  ✓ No model download (several GB)
#  ✓ Instant output (no waiting)
#  ✓ Fully offline
#  ✓ Deterministic (always the same output)
#  ✓ Transparent and easy to understand
# ══════════════════════════════════════════════════════════════

def _scale_pts(pts, cx, cy, size):
    """
    Scale normalised points (range 0–1) to real pixel coordinates.

    Parameters
    ----------
    pts  : List of (x, y) tuples in the 0–1 normalised range.
    cx   : X coordinate of the shape centre in pixels.
    cy   : Y coordinate of the shape centre in pixels.
    size : Width/height of the shape bounding box in pixels.

    Returns
    -------
    List of (px, py) integer pixel coordinate tuples.
    """
    half = size / 2
    return [(int(cx + (x - 0.5) * size),
             int(cy + (y - 0.5) * size)) for x, y in pts]


# Each shape is a dict with:
#   'label'  : Display name in the dropdown
#   'emoji'  : Emoji for the button
#   'type'   : 'polygon', 'lines', or 'compound'
#   'parts'  : List of (type, points) tuples for compound shapes
SHAPE_LIBRARY = {

    "🏠 Haus": {
        "label": "🏠 Haus",
        "parts": [
            # Floor plan (rectangle)
            ("polygon", [(0.1, 0.5), (0.9, 0.5), (0.9, 0.95), (0.1, 0.95)]),
            # Roof (triangle)
            ("polygon", [(0.0, 0.52), (0.5, 0.05), (1.0, 0.52)]),
            # Door
            ("polygon", [(0.38, 0.95), (0.62, 0.95), (0.62, 0.68), (0.38, 0.68)]),
            # Left window
            ("polygon", [(0.15, 0.6), (0.32, 0.6), (0.32, 0.75), (0.15, 0.75)]),
            # Right window
            ("polygon", [(0.68, 0.6), (0.85, 0.6), (0.85, 0.75), (0.68, 0.75)]),
        ]
    },

    "☀️ Sonne": {
        "label": "☀️ Sonne",
        "parts": [
            # Circle (approximated as a 32-gon)
            ("circle", (0.5, 0.5, 0.28)),   # cx, cy, radius (normalised)
            # 8 rays
            ("line", [(0.50, 0.08), (0.50, 0.18)]),
            ("line", [(0.50, 0.82), (0.50, 0.92)]),
            ("line", [(0.08, 0.50), (0.18, 0.50)]),
            ("line", [(0.82, 0.50), (0.92, 0.50)]),
            ("line", [(0.18, 0.18), (0.25, 0.25)]),
            ("line", [(0.75, 0.75), (0.82, 0.82)]),
            ("line", [(0.82, 0.18), (0.75, 0.25)]),
            ("line", [(0.18, 0.82), (0.25, 0.75)]),
        ]
    },

    "⭐ Stern": {
        "label": "⭐ Stern",
        "parts": [
            # 5-pointed star (alternating outer and inner vertices)
            ("polygon", [
                (0.500, 0.050),  # top
                (0.594, 0.345),
                (0.905, 0.345),  # upper right
                (0.655, 0.527),
                (0.755, 0.820),  # lower right
                (0.500, 0.645),
                (0.245, 0.820),  # lower left
                (0.345, 0.527),
                (0.095, 0.345),  # upper left
                (0.406, 0.345),
            ]),
        ]
    },

    "❤️ Herz": {
        "label": "❤️ Herz",
        "parts": [
            # Heart shape as a Bézier-curve approximation (16 points)
            ("polygon", [
                (0.500, 0.850),
                (0.150, 0.520),
                (0.050, 0.380),
                (0.050, 0.260),
                (0.120, 0.160),
                (0.250, 0.130),
                (0.350, 0.160),
                (0.430, 0.230),
                (0.500, 0.320),
                (0.570, 0.230),
                (0.650, 0.160),
                (0.750, 0.130),
                (0.880, 0.160),
                (0.950, 0.260),
                (0.950, 0.380),
                (0.850, 0.520),
            ]),
        ]
    },

    "🌸 Blume": {
        "label": "🌸 Blume",
        "parts": [
            # Centre circle
            ("circle", (0.5, 0.5, 0.12)),
            # 6 petals as ellipses (cx, cy, rx, ry, angle)
            ("petal", (0.50, 0.25, 0.10, 0.18, 0)),
            ("petal", (0.50, 0.75, 0.10, 0.18, 0)),
            ("petal", (0.25, 0.50, 0.18, 0.10, 0)),
            ("petal", (0.75, 0.50, 0.18, 0.10, 0)),
            ("petal", (0.29, 0.29, 0.10, 0.18, 45)),
            ("petal", (0.71, 0.71, 0.10, 0.18, 45)),
            ("petal", (0.71, 0.29, 0.10, 0.18, -45)),
            ("petal", (0.29, 0.71, 0.10, 0.18, -45)),
            # Stem
            ("line", [(0.50, 0.88), (0.50, 1.0)]),
            ("line", [(0.50, 0.95), (0.35, 0.82)]),   # left leaf
            ("line", [(0.50, 0.92), (0.65, 0.79)]),   # right leaf
        ]
    },

    "🚗 Auto": {
        "label": "🚗 Auto",
        "parts": [
            # Lower body (rectangle)
            ("polygon", [(0.05, 0.55), (0.95, 0.55), (0.95, 0.80), (0.05, 0.80)]),
            # Roof (rounded trapezoid)
            ("polygon", [(0.20, 0.55), (0.80, 0.55), (0.70, 0.30), (0.30, 0.30)]),
            # Windscreen
            ("polygon", [(0.32, 0.53), (0.50, 0.53), (0.50, 0.33), (0.35, 0.33)]),
            # Rear window
            ("polygon", [(0.52, 0.53), (0.68, 0.53), (0.65, 0.33), (0.52, 0.33)]),
            # Left wheel (circle)
            ("circle", (0.22, 0.82, 0.11)),
            # Right wheel
            ("circle", (0.78, 0.82, 0.11)),
            # Left rim
            ("circle", (0.22, 0.82, 0.05)),
            # Right rim
            ("circle", (0.78, 0.82, 0.05)),
        ]
    },

    "🌲 Baum": {
        "label": "🌲 Baum",
        "parts": [
            # Trunk
            ("polygon", [(0.42, 0.75), (0.58, 0.75), (0.58, 0.95), (0.42, 0.95)]),
            # Lower triangle (large)
            ("polygon", [(0.10, 0.75), (0.90, 0.75), (0.50, 0.45)]),
            # Middle triangle
            ("polygon", [(0.18, 0.52), (0.82, 0.52), (0.50, 0.25)]),
            # Upper triangle (small)
            ("polygon", [(0.26, 0.32), (0.74, 0.32), (0.50, 0.08)]),
        ]
    },

    "➡️ Pfeil": {
        "label": "➡️ Pfeil",
        "parts": [
            # Arrow shaft
            ("polygon", [(0.05, 0.38), (0.60, 0.38), (0.60, 0.62), (0.05, 0.62)]),
            # Arrowhead
            ("polygon", [(0.60, 0.18), (0.95, 0.50), (0.60, 0.82)]),
        ]
    },
}


def draw_shape_on_pil(img: "PILImage.Image", shape_key: str,
                      cx: int, cy: int, size: int,
                      color: tuple, line_width: int = 2) -> "PILImage.Image":
    """
    Draw a shape from SHAPE_LIBRARY onto a PIL image.

    Parameters
    ----------
    img        : Target PIL image (modified in place and returned).
    shape_key  : Key in SHAPE_LIBRARY (e.g. '🏠 Haus').
    cx, cy     : Centre of the shape in image pixels.
    size       : Width/height of the shape bounding box in pixels.
    color      : RGBA tuple (r, g, b, a).
    line_width : Stroke width in pixels.

    Returns
    -------
    The modified PIL image.
    """
    shape = SHAPE_LIBRARY.get(shape_key)
    if not shape:
        return img

    d = ImageDraw.Draw(img, "RGBA")
    fill_color = (color[0], color[1], color[2], 80)   # Semi-transparent fill
    outline    = (color[0], color[1], color[2], 255)  # Fully opaque outline

    for part_type, data in shape["parts"]:

        if part_type == "polygon":
            # Scale normalised points to image pixel coordinates
            pts = _scale_pts(data, cx, cy, size)
            d.polygon(pts, outline=outline, fill=fill_color)

        elif part_type == "line":
            pts = _scale_pts(data, cx, cy, size)
            d.line(pts, fill=outline, width=max(1, line_width))

        elif part_type == "circle":
            # data = (norm_cx, norm_cy, norm_radius)
            ncx, ncy, nr = data
            px = int(cx + (ncx - 0.5) * size)
            py = int(cy + (ncy - 0.5) * size)
            pr = int(nr * size)
            d.ellipse([px - pr, py - pr, px + pr, py + pr],
                      outline=outline, fill=fill_color)

        elif part_type == "petal":
            # data = (norm_cx, norm_cy, norm_rx, norm_ry, angle_deg)
            ncx, ncy, nrx, nry, angle = data
            px  = int(cx + (ncx - 0.5) * size)
            py  = int(cy + (ncy - 0.5) * size)
            prx = int(nrx * size)
            pry = int(nry * size)
            d.ellipse([px - prx, py - pry, px + prx, py + pry],
                      outline=outline, fill=fill_color)

    return img


# ══════════════════════════════════════════════════════════════
#  AI WORKER (Moondream via Ollama)
# ══════════════════════════════════════════════════════════════
