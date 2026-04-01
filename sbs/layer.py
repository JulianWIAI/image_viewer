"""
sbs/layer.py – Layer data class for the SBS Image Editor.

Each Layer represents a single image plane that is composited with the
other layers to produce the final visible image.
"""

try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

class Layer:
    """
    Represents a single layer in the layer stack.

    Fields
    ------
    image   : PIL RGBA image – the actual pixel content of this layer.
    name    : Display name shown in the layer panel.
    opacity : Opacity 0–100 % (100 = fully visible).
    visible : True = layer is rendered; False = layer is hidden.
    x, y    : Pixel offset (position) of this layer on the canvas.

    All layers are composited together to produce the final displayed image.
    """
    _counter = 0   # global counter used to auto-name new layers

    def __init__(self, image, name=None,
                 opacity: int = 100, visible: bool = True,
                 x: int = 0, y: int = 0):
        """
        Create a new layer.

        Parameters
        ----------
        image   : PIL Image – automatically converted to RGBA mode.
        name    : Display name; when None a name like 'Ebene N' is assigned.
        opacity : Opacity 0–100 %.
        visible : Whether the layer is visible during compositing.
        x, y    : Pixel offset position on the canvas.
        """
        Layer._counter += 1
        self.image   = image.convert("RGBA") if image else None
        self.name    = name or f"Ebene {Layer._counter}"
        self.opacity = opacity
        self.visible = visible
        self.x       = x
        self.y       = y


# ══════════════════════════════════════════════════════════════
