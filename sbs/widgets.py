"""
sbs/widgets.py – Reusable Qt widgets for the SBS Image Editor.

Provides:
  HistogramWidget  – RGB histogram display.
  LayerPanel       – Interactive layer list with per-layer controls.
  ImageCanvas      – Central image display widget and overlay host.
  LabeledSlider    – Slider with a label and live value readout.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QSlider,
    QPushButton, QScrollArea, QSizePolicy, QFrame, QSpinBox, QDialog, QFileDialog
)
from PyQt6.QtGui import (
    QPixmap, QColor, QPainter, QPen, QFont, QImage, QCursor, QPainterPath, QBrush
)
from PyQt6.QtCore import Qt, QSize, QRect, pyqtSignal

try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .utils import pil_to_qpixmap
from .layer import Layer

if TYPE_CHECKING:
    from .overlays import CropOverlay, DrawOverlay, ShapePlacerOverlay

class HistogramWidget(QWidget):
    """
    Displays the Red / Green / Blue distribution of the current image as a filled curve chart.

    How it works
    ------------
    PIL's ``image.histogram()`` returns 768 values: 256 per channel (R, G, B).
    For performance the image is first scaled to at most 256 × 256 px.
    QPainter draws a filled QPainterPath for each of the 256 brightness levels.

    This is a standard feature of professional editors such as Lightroom or Capture One.
    The histogram reveals overexposure (peak on the right), underexposure (peak on the
    left), and colour casts (channel imbalance).
    """
    def __init__(self):
        """Create the histogram widget with a dark background and a fixed height."""
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
        """
        Compute the histogram from a PIL image and trigger a repaint.

        Parameters
        ----------
        pil_image : PIL Image to analyse, or None to clear the display.
        """
        if pil_image is None:
            self._data = None
            self.update()
            return
        try:
            thumb = pil_image.copy().convert("RGB")
            thumb.thumbnail((256, 256))
            hist = thumb.histogram()   # 768 values: R×256 + G×256 + B×256
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
        Paint R / G / B as overlapping filled curves (QPainterPath).

        Draw order: Blue first (back), then Green, then Red (front).
        Overlapping areas show the frontmost colour.
        Filled paths (fillPath) are more reliably visible than thin lines.
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
        usable_h = h - 6   # small buffer at the bottom

        # Blue → Green → Red (Red is drawn on top and is therefore most visible)
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

        # Channel labels (top-left corner)
        painter.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        for txt, col, xp in [("R", QColor(255, 100, 100), 4),
                              ("G", QColor(80,  220, 80),  14),
                              ("B", QColor(100, 150, 255), 24)]:
            painter.setPen(QPen(col))
            painter.drawText(xp, 12, txt)
        painter.end()


# ══════════════════════════════════════════════════════════════
#  LAYER PANEL: list of all layers with controls
# ══════════════════════════════════════════════════════════════

class LayerPanel(QWidget):
    """
    Displays all editor layers as an interactive list.

    Layer order: the most recently added layer appears at the top of the
    list; the background layer is at the bottom — matching GIMP and Photoshop.

    Per-layer controls
    ------------------
    👁 Visibility  |  Thumbnail  |  Name  |  Opacity %  |  🗑 Delete

    Top buttons
    -----------
    + New  |  + Transparent (with size dialog)  |  ⬇ Merge  |  Flatten
    """

    def __init__(self, editor):
        """
        Create the layer panel.

        Parameters
        ----------
        editor : Reference to the ImageEditor instance (for access to
                 ``layers`` and ``active_layer_idx``).
        """
        super().__init__()
        self.editor = editor
        self.setStyleSheet("background:#1a1a1a; color:#ddd;")
        self._build_ui()

    def _build_ui(self):
        """Build the button bar and the scrollable layer-list container."""
        main = QVBoxLayout(self)
        main.setContentsMargins(4, 4, 4, 4)
        main.setSpacing(4)

        # ── Button bar
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

        # ── Layer list (scrollable)
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

    # ── Public method: rebuild the list ──────────────────────────

    def refresh(self):
        """Remove all rows and rebuild them from editor.layers."""
        # Delete all widgets except the trailing stretch item
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.deleteLater()

        editor = self.editor
        # Display layers in reverse order so the topmost layer is at the top
        for i in reversed(range(len(editor.layers))):
            row = self._make_row(i, editor.layers[i], i == editor.active_layer_idx)
            self._list_layout.insertWidget(0, row)

    def _make_row(self, idx: int, layer, active: bool) -> QWidget:
        """
        Build a single layer row widget.

        Parameters
        ----------
        idx    : Index of this layer in editor.layers.
        layer  : The Layer object.
        active : True if this is the currently active layer.

        Returns
        -------
        A QWidget containing all per-layer controls.
        """
        row = QWidget()
        row.setFixedHeight(46)
        active_style = ("background:#1a3a5a; border:1px solid #4fc3f7; border-radius:3px;")
        idle_style   = ("background:#222; border:1px solid #333; border-radius:3px;")
        row.setStyleSheet(active_style if active else idle_style)

        hl = QHBoxLayout(row)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(4)

        # Visibility toggle
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

        # Name label
        name_lbl = QLabel(layer.name)
        name_lbl.setStyleSheet("color:#ddd; font-size:10px;")
        name_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        name_lbl.mousePressEvent = lambda ev, i=idx: self._select(i)
        hl.addWidget(name_lbl, 1)

        # Opacity spinner
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setValue(layer.opacity)
        spin.setSuffix("%")
        spin.setFixedWidth(58)
        spin.setStyleSheet("background:#2d2d2d; color:#ddd; border:1px solid #333; padding:1px;")
        spin.setToolTip("Deckkraft (Opacity)")
        spin.valueChanged.connect(lambda v, i=idx: self._set_opacity(i, v))
        hl.addWidget(spin)

        # Delete button
        del_btn = QPushButton("🗑")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet("background:#3a1a1a; border:1px solid #5a2a2a; border-radius:3px;")
        del_btn.setToolTip("Ebene löschen")
        del_btn.clicked.connect(lambda _, i=idx: self._delete(i))
        hl.addWidget(del_btn)

        # Right-click context menu on the entire row
        row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        row.customContextMenuRequested.connect(
            lambda pos, i=idx: self._show_context_menu(i))

        return row

    # ── Context menu (right-click on a layer row) ─────────────────

    def _show_context_menu(self, idx: int):
        """
        Show a context menu for the layer at the given index.

        Parameters
        ----------
        idx : Index of the layer that was right-clicked.
        """
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
        """
        Open a file dialog and load an image into an existing layer,
        replacing its current content.

        Parameters
        ----------
        idx : Index of the target layer.
        """
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
        """
        Open an input dialog to rename a layer.

        Parameters
        ----------
        idx : Index of the layer to rename.
        """
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "Umbenennen", "Neuer Name:",
            text=self.editor.layers[idx].name)
        if ok and name.strip():
            self.editor.layers[idx].name = name.strip()
            self.refresh()

    def _move_layer(self, idx: int, direction: int):
        """
        Move a layer up (+1) or down (-1) in the stack.

        Parameters
        ----------
        idx       : Index of the layer to move.
        direction : +1 to move up, -1 to move down.
        """
        ed    = self.editor
        new_i = idx + direction
        if new_i < 0 or new_i >= len(ed.layers):
            return
        ed._push()
        ed.layers[idx], ed.layers[new_i] = ed.layers[new_i], ed.layers[idx]
        ed.active_layer_idx = new_i
        ed._update_display()
        self.refresh()

    # ── Actions ──────────────────────────────────────────────────

    def _select_and_transform(self, idx: int):
        """
        Thumbnail click: activate the layer AND immediately start the transform overlay.

        Parameters
        ----------
        idx : Index of the layer to activate and transform.
        """
        ed = self.editor
        ed.active_layer_idx = idx
        if ed.layers[idx].image:
            ed.original_pil = ed.layers[idx].image.copy()
        ed._reset_sliders()
        self.refresh()
        ed._start_layer_transform(idx)

    def _select(self, idx: int):
        """
        Activate a layer and update the original PIL reference used for slider reset.

        Parameters
        ----------
        idx : Index of the layer to activate.
        """
        ed = self.editor
        ed.active_layer_idx = idx
        if ed.layers[idx].image:
            ed.original_pil = ed.layers[idx].image.copy()
        ed._reset_sliders()
        self.refresh()

    def _toggle_vis(self, idx: int):
        """
        Toggle the visibility of a layer and refresh the display.

        Parameters
        ----------
        idx : Index of the layer whose visibility to toggle.
        """
        self.editor.layers[idx].visible = not self.editor.layers[idx].visible
        self.editor._update_display()
        self.refresh()

    def _set_opacity(self, idx: int, value: int):
        """
        Set the opacity (0–100 %) of a layer and refresh the display.

        Parameters
        ----------
        idx   : Index of the target layer.
        value : New opacity value (0 = transparent, 100 = fully opaque).
        """
        self.editor.layers[idx].opacity = value
        self.editor._update_display()

    def _add_layer(self):
        """Add a new layer — optionally loading an image from disk."""
        ed = self.editor
        if not ed.layers:
            return

        # Dialog: empty layer or load an image?
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
        """Open a size dialog and add a fully transparent layer with custom dimensions."""
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
        """
        Delete the layer at the given index.  At least one layer is always kept.

        Parameters
        ----------
        idx : Index of the layer to delete.
        """
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
        """Merge the active layer down onto the layer directly below it."""
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
        """Flatten all layers into a single background layer."""
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
        """Start the transform overlay for the currently active layer."""
        self.editor._start_layer_transform(self.editor.active_layer_idx)


# ══════════════════════════════════════════════════════════════
#  FILTER PREVIEW DIALOG: 5×4 thumbnail grid of all filters
# ══════════════════════════════════════════════════════════════
class ImageCanvas(QLabel):
    """
    Central image display widget of the editor and host for all overlay widgets.

    Why QLabel instead of QWidget?
    QLabel has a built-in ``setPixmap()`` method that renders a QPixmap
    efficiently.  Zoom is implemented by scaling the displayed QPixmap while
    the underlying PIL image always stays at its original resolution.

    Zoom implementation
    -------------------
    The zoom factor (``self._zoom``) scales the displayed QPixmap.
    Example: zoom=2.0 → image displayed at double size;
             the PIL image itself is unchanged.

    Overlay host
    ------------
    The canvas is the parent widget of all overlays (CropOverlay, DrawOverlay,
    ShapePlacerOverlay).  Overlays cover the canvas completely.
    Only one overlay can be active at a time (``self._overlay``).
    """

    def __init__(self):
        super().__init__()
        self._pix_orig   = None    # QPixmap of the current image
        self._zoom       = 1.0
        self._overlay: "QWidget | None" = None    # Active overlay (Crop/Draw/Shape/MagicWand)

        self.grabGesture(Qt.GestureType.PinchGesture)
        self.setText("Kein Bild geladen\n\nDatei → Öffnen  oder  Strg+O")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("color: #555; font-size: 15px;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, pix: QPixmap):
        """
        Set a new image and reset the zoom to 1:1.

        Parameters
        ----------
        pix : QPixmap to display.
        """
        self._pix_orig = pix
        self._zoom     = 1.0
        self._render()

    def update_image(self, pix: QPixmap):
        """
        Update the displayed image without resetting the current zoom level.

        Parameters
        ----------
        pix : New QPixmap to display.
        """
        self._pix_orig = pix
        self._render()

    def set_zoom(self, z: float):
        """
        Set the zoom factor (clamped to 0.05–20.0) and re-render.

        Parameters
        ----------
        z : Desired zoom factor.
        """
        self._zoom = max(0.05, min(z, 20.0))
        self._render()

    def get_zoom(self) -> float:
        """Return the current zoom factor."""
        return self._zoom

    def _render(self):
        """Scale the original pixmap by the current zoom factor and display it."""
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
        # Resize the active overlay to match the canvas
        if self._overlay:
            self._overlay.setGeometry(self.rect())

    def start_crop_overlay(self, mode: str) -> "CropOverlay":
        """
        Start a new crop overlay.

        Parameters
        ----------
        mode : 'rect' for rectangle selection, 'lasso' for freehand polygon.

        Returns
        -------
        The newly created CropOverlay.
        """
        from .overlays import CropOverlay
        if self._overlay:
            self._overlay.close()
        self._overlay = CropOverlay(self, mode)
        self._overlay.cancelled.connect(self._on_overlay_done)
        return self._overlay

    def start_draw_overlay(self, tool: str, color: QColor,
                           size: int, texture=None) -> "DrawOverlay":
        """
        Start a new draw overlay.

        Parameters
        ----------
        tool    : Drawing tool name ('pen', 'brush', 'eraser', etc.).
        color   : Active drawing colour.
        size    : Brush size in pixels.
        texture : Optional PIL RGBA image used as a texture stamp.

        Returns
        -------
        The newly created DrawOverlay so the editor can connect signals.
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
        Start the shape-placer overlay.

        The user sees a live preview of the shape under the cursor and places
        it with a single click.

        Parameters
        ----------
        shape_key : Key of the shape to place from SHAPE_LIBRARY.
        size      : Desired shape size in image pixels.
        color     : Drawing colour.

        Returns
        -------
        The newly created ShapePlacerOverlay.
        """
        from .overlays import ShapePlacerOverlay
        if self._overlay:
            self._overlay.close()
        self._overlay = ShapePlacerOverlay(self, shape_key, size, color, self._zoom)
        self._overlay.setGeometry(self.rect())
        return self._overlay

    def _on_overlay_done(self):
        """Clear the overlay reference when an overlay closes itself."""
        self._overlay = None

    def event(self, event):
        """
        Handle pinch gestures for touch-based zoom; delegate all other events to Qt.

        Parameters
        ----------
        event : Qt event object.
        """
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
#  SLIDER WIDGET
# ══════════════════════════════════════════════════════════════

class LabeledSlider(QWidget):
    """Reusable slider with a descriptive label and a live numeric value display."""
    value_changed = pyqtSignal(int)

    def __init__(self, label: str, mn: int, mx: int, default: int):
        """
        Create a slider with a label and value display.

        Parameters
        ----------
        label   : Label text displayed to the left of the slider.
        mn, mx  : Minimum and maximum slider values.
        default : Initial slider value.
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
        """Return the current slider value."""
        return self.slider.value()

    def reset(self, val=100):
        """
        Reset the slider to the given value without emitting a signal.

        Parameters
        ----------
        val : Value to reset to (default 100).
        """
        self.slider.blockSignals(True)
        self.slider.setValue(val)
        self.val_lbl.setText(str(val))
        self.slider.blockSignals(False)


# ══════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════
