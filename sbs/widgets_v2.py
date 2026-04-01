"""
sbs/widgets_v2.py – Legacy / backup copy of the widgets module (not imported).

Reusable Qt widgets for the SBS Image Editor:
  HistogramWidget, LayerPanel, LabeledSlider, ImageCanvas.

Note: This file is superseded by sbs/widgets.py and is kept for reference only.
"""

from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QSlider,
    QPushButton, QScrollArea, QSizePolicy, QFrame
)
from PyQt6.QtGui import (
    QPixmap, QColor, QPainter, QPen, QFont, QImage
)
from PyQt6.QtCore import Qt, QSize, QRect, pyqtSignal

try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from __future__ import annotations
from typing import TYPE_CHECKING

from .utils import pil_to_qpixmap
from .layer import Layer

if TYPE_CHECKING:
    from .overlays import CropOverlay, DrawOverlay, ShapePlacerOverlay

class HistogramWidget(QWidget):
    """
    Zeigt die Rot/Grün/Blau-Verteilung des aktuellen Bildes als Balkendiagramm.

    FUNKTIONSWEISE:
    PIL's image.histogram() liefert 768 Werte: 256 je Kanal (R, G, B).
    Aus Performancegründen wird das Bild vorher auf max. 256×256 px skaliert.
    QPainter zeichnet für jeden der 256 Helligkeitswerte einen senkrechten Strich.

    Typisch für professionelle Editoren wie Lightroom oder Capture One.
    Erklärt beim Prüfungsgespräch: Überbelichtung (Häufung rechts),
    Unterbelichtung (Häufung links), Farbstiche (Kanal-Ungleichgewicht).
    """
    def __init__(self):
        """Erstellt das Histogramm-Widget mit dunklem Hintergrund und fester Höhe."""
        super().__init__()
        self._data = None   # tuple (r_list, g_list, b_list, max_val)
        self.setFixedHeight(90)
        self.setMinimumWidth(200)
        self.setStyleSheet(
            "background:#141414; border:1px solid #2a2a2a; border-radius:4px;")
        self.setToolTip(
            "Histogramm: Rot / Grün / Blau Verteilung\n"
            "Links = dunkle Pixel, Rechts = helle Pixel")

    def update_histogram(self, pil_image):
        """Berechnet das Histogramm aus dem PIL-Bild und triggert Neuzeichnen."""
        if pil_image is None:
            self._data = None
            self.update()
            return
        try:
            thumb = pil_image.copy().convert("RGB")
            thumb.thumbnail((256, 256))
            hist = thumb.histogram()   # 768 Werte: R×256 + G×256 + B×256
            r = hist[0:256]
            g = hist[256:512]
            b = hist[512:768]
            max_val = max(max(r), max(g), max(b)) or 1
            self._data = (r, g, b, max_val)
        except Exception:
            self._data = None
        self.update()

    def paintEvent(self, event):
        """
        Zeichnet R/G/B als übereinanderliegende gefüllte Kurven (QPainterPath).

        REIHENFOLGE: Blau zuerst (hinten), dann Grün, dann Rot (vorne).
        Überlappende Bereiche zeigen die vorderste Farbe.
        Gefüllte Flächen (fillPath) sind zuverlässiger sichtbar als dünne Linien.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(14, 14, 14))

        if not self._data:
            painter.setPen(QPen(QColor(80, 80, 80)))
            painter.setFont(QFont("Arial", 9))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Kein Bild geladen")
            painter.end()
            return

        r_data, g_data, b_data, max_val = self._data
        w, h = self.width(), self.height()
        usable_h = h - 6   # kleiner Puffer unten

        # Blau → Grün → Rot (Rot liegt oben = am deutlichsten sichtbar)
        for data, fill_color in [
            (b_data, QColor(50,  110, 240, 170)),
            (g_data, QColor(50,  200, 80,  170)),
            (r_data, QColor(230, 60,  60,  200)),
        ]:
            path = QPainterPath()
            path.moveTo(0, h)
            for i in range(256):
                x  = i / 255.0 * w
                bh = data[i] / max_val * usable_h
                path.lineTo(x, h - bh)
            path.lineTo(w, h)
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(path, QBrush(fill_color))

        # Kanal-Labels oben links
        painter.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        for txt, col, xp in [("R", QColor(255, 100, 100), 4),
                              ("G", QColor(80,  220, 80),  14),
                              ("B", QColor(100, 150, 255), 24)]:
            painter.setPen(QPen(col))
            painter.drawText(xp, 12, txt)
        painter.end()


# ══════════════════════════════════════════════════════════════
#  EBENEN-PANEL: Liste aller Ebenen mit Steuerung
# ══════════════════════════════════════════════════════════════

class LayerPanel(QWidget):
    """
    Zeigt alle Ebenen des Editors als interaktive Liste.

    Reihenfolge: Oberste Ebene (zuletzt hinzugefügt) oben in der Liste,
    Hintergrund unten — wie in GIMP und Photoshop.

    Pro Ebene:
      👁 Sichtbarkeit  |  Thumbnail  |  Name  |  Deckkraft %  |  🗑 Löschen

    Buttons oben:
      + Neu  |  + Transparent (mit Größenwahl)  |  ⬇ Merge  |  Flatten
    """

    def __init__(self, editor):
        """
        Erstellt das Ebenen-Panel.
        Parameter:
          editor – Referenz auf den ImageEditor (für Zugriff auf layers und active_layer_idx)
        """
        super().__init__()
        self.editor = editor
        self.setStyleSheet("background:#1a1a1a; color:#ddd;")
        self._build_ui()

    def _build_ui(self):
        """Erstellt die Button-Leiste und den scrollbaren Ebenen-Listen-Container."""
        main = QVBoxLayout(self)
        main.setContentsMargins(4, 4, 4, 4)
        main.setSpacing(4)

        # ── Button-Leiste
        btn_row = QHBoxLayout()
        btn_row.setSpacing(3)
        _bs = ("background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
               "border-radius:3px; padding:4px 6px; font-size:10px;")
        for label, slot in [
            ("+ Neu",          self._add_layer),
            ("+ Transparent",  self._add_transparent),
            ("⬇ Merge",        self._merge_down),
            ("Flatten",        self._flatten_all),
            ("🔀 Transform",   self._transform_active),
        ]:
            b = QPushButton(label)
            b.setStyleSheet(_bs)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        main.addLayout(btn_row)

        # ── Ebenen-Liste (scrollbar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background:#111; border:none;")
        self._list_w = QWidget()
        self._list_w.setStyleSheet("background:#111;")
        self._list_layout = QVBoxLayout(self._list_w)
        self._list_layout.setContentsMargins(2, 2, 2, 2)
        self._list_layout.setSpacing(3)
        self._list_layout.addStretch()
        scroll.setWidget(self._list_w)
        main.addWidget(scroll, 1)

    # ── Öffentliche Methode: Liste neu aufbauen ──────────────

    def refresh(self):
        """Alle Zeilen entfernen und neu aus editor.layers aufbauen."""
        # Alle Widgets außer dem abschließenden Stretch löschen
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.deleteLater()

        editor = self.editor
        # Ebenen in umgekehrter Reihenfolge anzeigen (oben = zuletzt hinzugefügt)
        for i in reversed(range(len(editor.layers))):
            row = self._make_row(i, editor.layers[i], i == editor.active_layer_idx)
            self._list_layout.insertWidget(0, row)

    def _make_row(self, idx: int, layer, active: bool) -> QWidget:
        """Erzeugt eine Ebenen-Zeile."""
        row = QWidget()
        row.setFixedHeight(46)
        active_style = ("background:#1a3a5a; border:1px solid #4fc3f7; border-radius:3px;")
        idle_style   = ("background:#222; border:1px solid #333; border-radius:3px;")
        row.setStyleSheet(active_style if active else idle_style)

        hl = QHBoxLayout(row)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(4)

        # Sichtbarkeit
        vis = QPushButton("👁" if layer.visible else "🚫")
        vis.setFixedSize(26, 26)
        vis.setStyleSheet("background:#2a2a2a; border:1px solid #333; border-radius:3px;")
        vis.setToolTip("Ebene ein-/ausblenden")
        vis.clicked.connect(lambda _, i=idx: self._toggle_vis(i))
        hl.addWidget(vis)

        # Thumbnail
        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(40, 32)
        thumb_lbl.setStyleSheet("background:#111; border:1px solid #2a2a2a;")
        if layer.image:
            t = layer.image.copy()
            t.thumbnail((40, 32))
            thumb_lbl.setPixmap(pil_to_qpixmap(t).scaled(
                40, 32,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))
        thumb_lbl.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        thumb_lbl.mousePressEvent = lambda ev, i=idx: self._select_and_transform(i)
        hl.addWidget(thumb_lbl)

        # Name
        name_lbl = QLabel(layer.name)
        name_lbl.setStyleSheet("color:#ddd; font-size:10px;")
        name_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        name_lbl.mousePressEvent = lambda ev, i=idx: self._select(i)
        hl.addWidget(name_lbl, 1)

        # Deckkraft
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setValue(layer.opacity)
        spin.setSuffix("%")
        spin.setFixedWidth(58)
        spin.setStyleSheet("background:#2d2d2d; color:#ddd; border:1px solid #333; padding:1px;")
        spin.setToolTip("Deckkraft (Opacity)")
        spin.valueChanged.connect(lambda v, i=idx: self._set_opacity(i, v))
        hl.addWidget(spin)

        # Löschen
        del_btn = QPushButton("🗑")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet("background:#3a1a1a; border:1px solid #5a2a2a; border-radius:3px;")
        del_btn.setToolTip("Ebene löschen")
        del_btn.clicked.connect(lambda _, i=idx: self._delete(i))
        hl.addWidget(del_btn)

        # Rechtsklick-Kontextmenü auf der gesamten Zeile
        row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        row.customContextMenuRequested.connect(
            lambda pos, i=idx: self._show_context_menu(i))

        return row

    # ── Kontextmenü (Rechtsklick auf Ebene) ──────────────────

    def _show_context_menu(self, idx: int):
        """Rechtsklick-Menü für eine Ebenen-Zeile."""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#252525; color:#ddd; border:1px solid #444; }
            QMenu::item { padding:6px 20px; }
            QMenu::item:selected { background:#0e639c; }
        """)
        menu.addAction("🖼  Bild in diese Ebene laden…",
                       lambda: self._load_image_into(idx))
        menu.addAction("✏️  Umbenennen…",
                       lambda: self._rename(idx))
        menu.addSeparator()
        menu.addAction("⬆  Nach oben verschieben",
                       lambda: self._move_layer(idx, +1))
        menu.addAction("⬇  Nach unten verschieben",
                       lambda: self._move_layer(idx, -1))
        menu.addSeparator()
        menu.addAction("🗑  Ebene löschen",
                       lambda: self._delete(idx))
        menu.exec(self.cursor().pos())

    def _load_image_into(self, idx: int):
        """Bild in eine bestehende Ebene laden (ersetzt deren Inhalt)."""
        path, _ = QFileDialog.getOpenFileName(self, "Bild laden", "",
            "Bilder (*.png *.jpg *.jpeg *.bmp *.webp);;Alle (*)")
        if not path:
            return
        try:
            img = PILImage.open(path).convert("RGBA")
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Fehler", str(e))
            return
        ed = self.editor
        ed._push()
        ed.layers[idx].image = img
        ed.original_pil      = img.copy()
        ed.active_layer_idx  = idx
        ed._update_display()
        self.refresh()

    def _rename(self, idx: int):
        """Ebene umbenennen."""
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "Umbenennen", "Neuer Name:",
            text=self.editor.layers[idx].name)
        if ok and name.strip():
            self.editor.layers[idx].name = name.strip()
            self.refresh()

    def _move_layer(self, idx: int, direction: int):
        """Ebene in der Reihenfolge nach oben (+1) oder unten (-1) verschieben."""
        ed    = self.editor
        new_i = idx + direction
        if new_i < 0 or new_i >= len(ed.layers):
            return
        ed._push()
        ed.layers[idx], ed.layers[new_i] = ed.layers[new_i], ed.layers[idx]
        ed.active_layer_idx = new_i
        ed._update_display()
        self.refresh()

    # ── Aktionen ─────────────────────────────────────────────

    def _select_and_transform(self, idx: int):
        """Thumbnail-Klick: Ebene aktivieren UND sofort Transform-Overlay starten."""
        ed = self.editor
        ed.active_layer_idx = idx
        if ed.layers[idx].image:
            ed.original_pil = ed.layers[idx].image.copy()
        ed._reset_sliders()
        self.refresh()
        ed._start_layer_transform(idx)

    def _select(self, idx: int):
        """Aktiviert eine Ebene und aktualisiert das Original-PIL für den Slider-Reset."""
        ed = self.editor
        ed.active_layer_idx = idx
        if ed.layers[idx].image:
            ed.original_pil = ed.layers[idx].image.copy()
        ed._reset_sliders()
        self.refresh()

    def _toggle_vis(self, idx: int):
        """Schaltet die Sichtbarkeit der Ebene um und aktualisiert die Anzeige."""
        self.editor.layers[idx].visible = not self.editor.layers[idx].visible
        self.editor._update_display()
        self.refresh()

    def _set_opacity(self, idx: int, value: int):
        """Setzt die Deckkraft (0–100%) der Ebene und aktualisiert die Anzeige."""
        self.editor.layers[idx].opacity = value
        self.editor._update_display()

    def _add_layer(self):
        """Neue Ebene hinzufügen — mit optionalem Bild-Ladefilm."""
        ed = self.editor
        if not ed.layers:
            return

        # Dialog: Leere Ebene oder Bild laden?
        dlg = QDialog(self)
        dlg.setWindowTitle("Neue Ebene")
        dlg.setStyleSheet("background:#1a1a1a; color:#ddd;")
        layout = QVBoxLayout(dlg)
        info = QLabel("Was soll die neue Ebene enthalten?")
        info.setStyleSheet("font-size:12px; padding:4px;")
        layout.addWidget(info)
        btn_empty = QPushButton("🗋  Leere transparente Ebene")
        btn_image = QPushButton("🖼  Bild laden…")
        for b in (btn_empty, btn_image):
            b.setStyleSheet("background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
                            "border-radius:4px; padding:8px; font-size:12px;")
        btn_empty.clicked.connect(lambda: dlg.done(1))
        btn_image.clicked.connect(lambda: dlg.done(2))
        layout.addWidget(btn_empty)
        layout.addWidget(btn_image)
        choice = dlg.exec()

        base = ed.layers[0].image
        if choice == 1:
            new_img = PILImage.new("RGBA", (base.width, base.height), (0, 0, 0, 0))
        elif choice == 2:
            path, _ = QFileDialog.getOpenFileName(self, "Bild für neue Ebene", "",
                "Bilder (*.png *.jpg *.jpeg *.bmp *.webp);;Alle (*)")
            if not path:
                return
            try:
                new_img = PILImage.open(path).convert("RGBA")
            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Fehler", str(e))
                return
        else:
            return

        ed._push()
        ed.layers.append(Layer(new_img))
        ed.active_layer_idx = len(ed.layers) - 1
        ed.original_pil = new_img.copy()
        ed._update_display()
        self.refresh()

    def _add_transparent(self):
        """Öffnet einen Dialog für benutzerdefinierte Breite/Höhe und fügt eine transparente Ebene hinzu."""
        ed = self.editor
        dlg = QDialog(self)
        dlg.setWindowTitle("Transparente Ebene")
        dlg.setStyleSheet("background:#1a1a1a; color:#ddd;")
        form = QVBoxLayout(dlg)
        row_w = QHBoxLayout(); row_w.addWidget(QLabel("Breite px:"))
        sp_w = QSpinBox(); sp_w.setRange(1, 8000)
        sp_w.setValue(ed.layers[0].image.width if ed.layers else 800)
        row_w.addWidget(sp_w); form.addLayout(row_w)
        row_h = QHBoxLayout(); row_h.addWidget(QLabel("Höhe  px:"))
        sp_h = QSpinBox(); sp_h.setRange(1, 6000)
        sp_h.setValue(ed.layers[0].image.height if ed.layers else 600)
        row_h.addWidget(sp_h); form.addLayout(row_h)
        row_n = QHBoxLayout(); row_n.addWidget(QLabel("Name:"))
        le_n = QLineEdit("Transparente Ebene"); row_n.addWidget(le_n)
        form.addLayout(row_n)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addWidget(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_img = PILImage.new("RGBA", (sp_w.value(), sp_h.value()), (0, 0, 0, 0))
        ed._push()
        ed.layers.append(Layer(new_img, le_n.text() or "Transparente Ebene"))
        ed.active_layer_idx = len(ed.layers) - 1
        ed.original_pil = new_img.copy()
        ed._update_display()
        self.refresh()

    def _delete(self, idx: int):
        """Löscht die Ebene mit dem angegebenen Index (mindestens eine Ebene bleibt erhalten)."""
        ed = self.editor
        if len(ed.layers) <= 1:
            QMessageBox.information(self, "Info",
                "Mindestens eine Ebene muss vorhanden sein.")
            return
        ed._push()
        ed.layers.pop(idx)
        ed.active_layer_idx = min(ed.active_layer_idx, len(ed.layers) - 1)
        ed.original_pil = ed.layers[ed.active_layer_idx].image.copy()
        ed._update_display()
        self.refresh()

    def _merge_down(self):
        """Aktive Ebene mit der direkt darunter liegenden zusammenführen."""
        ed  = self.editor
        idx = ed.active_layer_idx
        if idx == 0:
            QMessageBox.information(self, "Info",
                "Unterste Ebene — kein Merge Down möglich."); return
        ed._push()
        top    = ed.layers[idx]
        bottom = ed.layers[idx - 1]
        w = max(top.x + top.image.width,  bottom.x + bottom.image.width)
        h = max(top.y + top.image.height, bottom.y + bottom.image.height)
        merged = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
        for lyr in (bottom, top):
            img = lyr.image.copy()
            if lyr.opacity < 100:
                r, g, b, a = img.split()
                a = a.point([int(v * lyr.opacity / 100) for v in range(256)])
                img = PILImage.merge("RGBA", (r, g, b, a))
            merged.paste(img, (lyr.x, lyr.y), img)
        bottom.image   = merged
        bottom.opacity = 100
        ed.layers.pop(idx)
        ed.active_layer_idx = idx - 1
        ed.original_pil = bottom.image.copy()
        ed._update_display()
        self.refresh()

    def _flatten_all(self):
        """Alle Ebenen zu einer einzigen zusammenführen."""
        ed = self.editor
        ed._push()
        flat = ed._composite_layers()
        Layer._counter = 0
        ed.layers = [Layer(flat, "Hintergrund")]
        ed.active_layer_idx = 0
        ed.original_pil = flat.copy()
        ed._update_display()
        self.refresh()

    def _transform_active(self):
        """Transform-Overlay für die aktive Ebene starten."""
        self.editor._start_layer_transform(self.editor.active_layer_idx)


# ══════════════════════════════════════════════════════════════
#  FILTER-VORSCHAU-DIALOG: 5×4 Thumbnail-Grid aller Filter
# ══════════════════════════════════════════════════════════════
class ImageCanvas(QLabel):
    """
    Bildfläche — zentrales Anzeige-Widget des Editors.

    WARUM QLabel statt QWidget?
    QLabel hat eine eingebaute setPixmap()-Methode, die ein QPixmap
    effizient darstellt. Zoom wird durch Skalieren des QPixmap erreicht.

    ZOOM-IMPLEMENTIERUNG:
    Zoom-Faktor (self._zoom) skaliert das angezeigte QPixmap.
    Das PIL-Image bleibt immer in Originalgröße erhalten.
    Beispiel: zoom=2.0 → Bild doppelt so groß angezeigt,
              PIL-Image selbst unverändert.

    OVERLAY-HOST:
    Canvas ist Eltern-Widget aller Overlays (CropOverlay, DrawOverlay,
    ShapePlacerOverlay). Overlays liegen flächendeckend darüber.
    Immer nur ein Overlay gleichzeitig aktiv (self._overlay).
    """

    def __init__(self):
        super().__init__()
        self._pix_orig   = None    # QPixmap des aktuellen Bildes
        self._zoom       = 1.0
        self._overlay: "QWidget | None" = None    # Aktives Overlay (Crop/Draw/Shape/MagicWand)

        self.grabGesture(Qt.GestureType.PinchGesture)
        self.setText("Kein Bild geladen\n\nDatei → Öffnen  oder  Strg+O")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("color: #555; font-size: 15px;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, pix: QPixmap):
        """Setzt ein neues Bild und setzt den Zoom auf 1:1 zurück."""
        self._pix_orig = pix
        self._zoom     = 1.0
        self._render()

    def update_image(self, pix: QPixmap):
        """Aktualisiert das angezeigte Bild ohne den Zoom zurückzusetzen."""
        self._pix_orig = pix
        self._render()

    def set_zoom(self, z: float):
        """Setzt den Zoom-Faktor (Bereich 0.05–20.0) und rendert neu."""
        self._zoom = max(0.05, min(z, 20.0))
        self._render()

    def get_zoom(self) -> float:
        """Gibt den aktuellen Zoom-Faktor zurück."""
        return self._zoom

    def _render(self):
        """Skaliert das Original-Pixmap mit dem aktuellen Zoom-Faktor und zeigt es an."""
        if not self._pix_orig:
            return
        w = int(self._pix_orig.width()  * self._zoom)
        h = int(self._pix_orig.height() * self._zoom)
        scaled = self._pix_orig.scaled(w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)
        self.resize(scaled.width(), scaled.height())
        self.setStyleSheet("")
        # Overlay aktualisieren falls aktiv
        if self._overlay:
            self._overlay.setGeometry(self.rect())

    def start_crop_overlay(self, mode: str) -> "CropOverlay":
        """Neues Crop-Overlay starten."""
        from .overlays import CropOverlay
        if self._overlay:
            self._overlay.close()
        self._overlay = CropOverlay(self, mode)
        self._overlay.cancelled.connect(self._on_overlay_done)
        return self._overlay

    def start_draw_overlay(self, tool: str, color: QColor,
                           size: int, texture=None) -> "DrawOverlay":
        """
        Neues Zeichen-Overlay starten.
        Gibt das Overlay zurück damit der Editor Signale verbinden kann.
        """
        from .overlays import DrawOverlay
        if self._overlay:
            self._overlay.close()
        self._overlay = DrawOverlay(self, tool, color, size, self._zoom, texture=texture)
        self._overlay.setGeometry(self.rect())
        return self._overlay

    def start_shape_placer(self, shape_key: str, size: int,
                           color: QColor) -> "ShapePlacerOverlay":
        """
        Shape-Placer-Overlay starten.
        Nutzer sieht eine Live-Vorschau und platziert die Form per Klick.
        """
        from .overlays import ShapePlacerOverlay
        if self._overlay:
            self._overlay.close()
        self._overlay = ShapePlacerOverlay(self, shape_key, size, color, self._zoom)
        self._overlay.setGeometry(self.rect())
        return self._overlay

    def _on_overlay_done(self):
        """Räumt die Overlay-Referenz auf wenn ein Overlay sich schließt."""
        self._overlay = None

    def event(self, event):
        """Verarbeitet Pinch-Gesten für Touch-Zoom; delegiert alle anderen Events an Qt."""
        if event.type() == event.Type.Gesture:
            pinch = event.gesture(Qt.GestureType.PinchGesture)
            if pinch:
                self.set_zoom(self._zoom * pinch.scaleFactor())
                p = self.parent()
                if p and hasattr(p.parent(), "_update_status"):
                    p.parent()._update_status()
            return True
        return super().event(event)


# ══════════════════════════════════════════════════════════════
#  SLIDER-WIDGET
# ══════════════════════════════════════════════════════════════

class LabeledSlider(QWidget):
    """Wiederverwendbarer Slider mit Label und Wertanzeige."""
    value_changed = pyqtSignal(int)

    def __init__(self, label: str, mn: int, mx: int, default: int):
        """
        Erstellt einen Slider mit Beschriftung und Wertanzeige.
        Parameter:
          label   – Beschriftungstext links
          mn, mx  – Wertebereich
          default – Startwert
        """
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(2)
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #bbb; font-size: 11px;")
        self.val_lbl = QLabel(str(default))
        self.val_lbl.setStyleSheet("color: #4fc3f7; font-size: 11px; font-weight: bold;")
        self.val_lbl.setFixedWidth(32)
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(lbl); row.addStretch(); row.addWidget(self.val_lbl)
        layout.addLayout(row)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(mn, mx); self.slider.setValue(default)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal { height:4px; background:#2d2d2d; border-radius:2px; }
            QSlider::handle:horizontal { width:13px; height:13px; margin:-5px 0;
                background:#4fc3f7; border-radius:7px; }
            QSlider::sub-page:horizontal { background:#4fc3f7; border-radius:2px; }
        """)
        self.slider.valueChanged.connect(lambda v: (self.val_lbl.setText(str(v)), self.value_changed.emit(v)))
        layout.addWidget(self.slider)

    def value(self):
        """Gibt den aktuellen Slider-Wert zurück."""
        return self.slider.value()

    def reset(self, val=100):
        """Setzt den Slider auf val zurück ohne ein Signal auszulösen."""
        self.slider.blockSignals(True)
        self.slider.setValue(val)
        self.val_lbl.setText(str(val))
        self.slider.blockSignals(False)


# ══════════════════════════════════════════════════════════════
#  HAUPTFENSTER
# ══════════════════════════════════════════════════════════════