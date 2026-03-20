"""
sbs/layer.py
Ebenen-Datenklasse für den SBS Bildeditor.
"""

try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

class Layer:
    """
    Repräsentiert eine einzelne Ebene im Ebenen-System.

    Felder:
      image   – PIL RGBA-Bild (der eigentliche Bildinhalt)
      name    – Anzeigename im Ebenen-Panel
      opacity – Deckkraft 0-100 %  (100 = vollständig sichtbar)
      visible – True = sichtbar, False = ausgeblendet
      x, y    – Offset (Position) auf der Leinwand in Pixeln

    Alle Ebenen zusammen ergeben durch Compositing das angezeigte Bild.
    """
    _counter = 0   # globaler Zähler für automatische Namen

    def __init__(self, image, name=None,
                 opacity: int = 100, visible: bool = True,
                 x: int = 0, y: int = 0):
        """
        Erstellt eine neue Ebene.

        Parameter:
          image   – PIL-Image (wird automatisch nach RGBA konvertiert)
          name    – Anzeigename; wenn None wird 'Ebene N' vergeben
          opacity – Deckkraft 0–100 %
          visible – Ob die Ebene sichtbar ist
          x, y    – Offset-Position auf der Leinwand in Pixeln
        """
        Layer._counter += 1
        self.image   = image.convert("RGBA") if image else None
        self.name    = name or f"Ebene {Layer._counter}"
        self.opacity = opacity
        self.visible = visible
        self.x       = x
        self.y       = y


# ══════════════════════════════════════════════════════════════
