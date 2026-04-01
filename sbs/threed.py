"""
sbs/threed.py
3D-view and depth-map functionality for the SBS image editor:
  DepthWorker, NovelViewWorker, ThreeDViewerWidget, ThreeDModelDialog.
"""
import math, os, io, base64, urllib.request

from PyQt6.QtWidgets import (
    QWidget, QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QSlider, QComboBox, QSizePolicy, QDialogButtonBox, QMessageBox,
    QProgressDialog, QFileDialog, QFrame, QSpinBox
)
from PyQt6.QtGui import (
    QPixmap, QColor, QPainter, QPen, QImage, QFont
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, QPoint, QSize

try:
    from PIL import Image as PILImage, ImageFilter
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .utils import pil_to_qpixmap

class DepthWorker(QThread):
    """
    Background thread for depth-map estimation from a single RGB image.

    Priority order
    --------------
    1. Depth-Anything-V2-Small (via transformers, ~100 MB download, best quality)
    2. MiDaS_small (if torch + timm are available)
    3. Luminance-based approximation (edge detection + smoothing, always available)

    Signals
    -------
    depth_ready(np.ndarray) : Emitted with the estimated depth array (float32, 0–1).
    progress(str)           : Status updates during loading / inference.
    error(str)              : Emitted if estimation fails.
    """
    depth_ready = pyqtSignal(object)   # numpy.ndarray float32 0–1
    progress    = pyqtSignal(str)
    error       = pyqtSignal(str)

    def __init__(self, pil_image):
        """
        Initialise the DepthWorker.

        Parameters
        ----------
        pil_image : PIL.Image
            The image for which a depth map should be estimated.
        """
        super().__init__()
        self.pil_image = pil_image

    def run(self):
        """
        Execute depth estimation in the background thread.

        Normalises the raw estimator output to the [0, 1] range and emits
        the result via the ``depth_ready`` signal.
        """
        try:
            import numpy as np
            raw = self._estimate()
            lo, hi = raw.min(), raw.max()
            depth = ((raw - lo) / (hi - lo + 1e-8)).astype(np.float32)
            self.depth_ready.emit(depth)
        except Exception as e:
            self.error.emit(str(e))

    # Subprocess script: runs in a clean Python process without Qt DLLs loaded.
    _DEPTH_SCRIPT = (
        "import sys, numpy as np\n"
        "from PIL import Image\n"
        "img = Image.open(sys.argv[1]).convert('RGB')\n"
        "try:\n"
        "    from transformers import pipeline\n"
        "    import torch\n"
        "    dev = 0 if torch.cuda.is_available() else -1\n"
        "    pipe = pipeline('depth-estimation',\n"
        "        model='depth-anything/Depth-Anything-V2-Small-hf', device=dev)\n"
        "    try:\n"
        "        r = pipe(img)\n"
        "    except Exception:\n"
        "        pipe = pipeline('depth-estimation',\n"
        "            model='depth-anything/Depth-Anything-V2-Small-hf', device=-1)\n"
        "        r = pipe(img)\n"
        "    np.save(sys.argv[2], np.array(r['depth'], dtype='float32'))\n"
        "    sys.exit(0)\n"
        "except Exception as e:\n"
        "    print(str(e), file=sys.stderr); sys.exit(1)\n"
    )

    def _estimate(self):
        """
        Try depth estimators in priority order, returning a raw float32 array.

        Attempts Depth-Anything-V2 first (via subprocess to avoid Qt DLL
        conflicts), then falls back to a luminance-based approximation.

        Returns
        -------
        np.ndarray
            Raw depth values as float32.  Not yet normalised to [0, 1].
        """
        import sys, os, subprocess, tempfile
        import numpy as np

        # ── Method 1: Depth-Anything-V2 in a subprocess (avoids Qt DLL conflicts) ──
        img_tmp = out_tmp = None
        try:
            self.progress.emit("⏳  Loading Depth-Anything-V2 (subprocess mode) …")
            fd, img_tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            fd, out_tmp = tempfile.mkstemp(suffix=".npy")
            os.close(fd)

            self.pil_image.save(img_tmp)
            self.progress.emit("🔍  Computing AI depth map (Depth-Anything-V2) …")
            res = subprocess.run(
                [sys.executable, "-c", self._DEPTH_SCRIPT, img_tmp, out_tmp],
                capture_output=True, text=True, timeout=300,
            )
            if res.returncode == 0:
                depth = np.load(out_tmp)
                self.progress.emit("✅  Depth-Anything-V2 done.")
                return depth
            self.progress.emit(f"⚠  Depth-Anything-V2 failed: {res.stderr[-120:]}")
        except Exception as _e1:
            self.progress.emit(f"⚠  Subprocess failed: {_e1!s:.120}")
        finally:
            for p in (img_tmp, out_tmp):
                if p:
                    try: os.unlink(p)
                    except OSError: pass

        # ── Method 2: Edge / sharpness approximation (no AI model required) ──────
        self.progress.emit("⚠  No AI model available — using luminance approximation …")
        from PIL import ImageFilter as _IF
        edges = np.array(
            self.pil_image.convert("L").filter(_IF.FIND_EDGES), dtype=np.float32
        )
        smooth_pil = PILImage.fromarray(edges.astype(np.uint8)).filter(_IF.GaussianBlur(8))
        self.progress.emit("✅  Luminance approximation done (low quality).")
        return np.array(smooth_pil, dtype=np.float32)


class NovelViewWorker(QThread):
    """
    Background thread that synthesises an AI-generated back view via zero123plus-v1.1.

    The back view is produced by averaging the two rear-facing tiles (azimuth
    ≈ 150° and ≈ 210°) from the zero123plus 6-view grid, then resizing the result
    to match the original image dimensions.

    Requirements
    ------------
    pip install diffusers transformers accelerate
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

    Signals
    -------
    views_ready(PIL.Image) : Emitted with the synthesised back-view image.
    progress(str)          : Status updates during loading / inference.
    error(str)             : Emitted if synthesis fails.
    """
    views_ready = pyqtSignal(object)   # PIL Image — generated back view
    progress    = pyqtSignal(str)
    error       = pyqtSignal(str)

    def __init__(self, pil_image):
        """
        Initialise the NovelViewWorker.

        Parameters
        ----------
        pil_image : PIL.Image
            The image for which an AI back view should be generated.
        """
        super().__init__()
        self.pil_image = pil_image

    # Subprocess script: runs in a clean Python process without Qt DLLs loaded.
    _Z123_SCRIPT = (
        "import sys, os, functools, numpy as np\n"
        "from PIL import Image\n"
        "img_path, out_path = sys.argv[1], sys.argv[2]\n"
        "img = Image.open(img_path).convert('RGB')\n"
        "if max(img.size) > 512:\n"
        "    img = img.copy(); img.thumbnail((512,512), Image.Resampling.LANCZOS)\n"
        "import torch\n"
        "_orig = torch.load\n"
        "torch.load = functools.partial(_orig, weights_only=False)\n"
        "try:\n"
        "    from diffusers import DiffusionPipeline\n"
        "    pipe = DiffusionPipeline.from_pretrained(\n"
        "        'sudo-ai/zero123plus-v1.1',\n"
        "        custom_pipeline='sudo-ai/zero123plus-pipeline',\n"
        "        torch_dtype=torch.float16,\n"
        "        trust_remote_code=True,\n"
        "    )\n"
        "    device = 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'\n"
        "    pipe = pipe.to(device)\n"
        "    pipe.enable_attention_slicing()\n"
        "    grid = pipe(img, num_inference_steps=30).images[0]\n"
        "    tw, th = grid.width//2, grid.height//3\n"
        "    v150 = np.array(grid.crop((0,th,tw,th*2)), np.float32)\n"
        "    v210 = np.array(grid.crop((tw,th,tw*2,th*2)), np.float32)\n"
        "    back = Image.fromarray(((v150+v210)/2).clip(0,255).astype(np.uint8))\n"
        "    orig = Image.open(img_path)\n"
        "    back.resize(orig.size, Image.Resampling.LANCZOS).save(out_path)\n"
        "    sys.exit(0)\n"
        "except Exception as e:\n"
        "    print(str(e), file=sys.stderr); sys.exit(1)\n"
        "finally:\n"
        "    torch.load = _orig\n"
    )

    def run(self):
        """Start AI back-view generation and emit the result via the ``views_ready`` signal."""
        try:
            back = self._zero123plus()
            self.views_ready.emit(back)
        except Exception as e:
            self.error.emit(str(e))

    # ── zero123plus-v1.1 via subprocess ───────────────────────
    def _zero123plus(self):
        """
        Run zero123plus-v1.1 in a separate Python process.

        Input and output images are exchanged via temporary files on disk to
        avoid Qt DLL conflicts that occur when heavy ML libraries are loaded
        inside a Qt-owned thread.

        Returns
        -------
        PIL.Image
            The synthesised back-view image, converted to RGB.
        """
        import sys, os, subprocess, tempfile

        img_tmp = out_tmp = None
        try:
            self.progress.emit("⏳  Loading zero123plus-v1.1 (subprocess mode) …")
            fd, img_tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            fd, out_tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)

            self.pil_image.save(img_tmp)
            self.progress.emit("🔄  Generating 6 views (zero123plus) — please wait …")
            res = subprocess.run(
                [sys.executable, "-c", self._Z123_SCRIPT, img_tmp, out_tmp],
                capture_output=True, text=True, timeout=1800,
            )
            if res.returncode != 0:
                raise RuntimeError(res.stderr[-500:] or "Subprocess failed (no stderr output)")

            back_pil = PILImage.open(out_tmp).convert("RGB")
            self.progress.emit("✅  AI back view (zero123plus) done.")
            return back_pil
        finally:
            for p in (img_tmp, out_tmp):
                if p:
                    try: os.unlink(p)
                    except OSError: pass

    # ── (deprecated — Zero-1-to-3 XL was removed from diffusers 0.28+) ─────
    def _zero1to3(self):
        """
        Deprecated: uses Zero-1-to-3 XL, which is no longer available in diffusers 0.28+.

        Generates a back view at azimuth=180° and upscales to the original image size.
        Kept for reference only; use ``_zero123plus`` instead.
        """
        import torch
        from diffusers import Zero1to3StableDiffusionPipeline

        self.progress.emit("⏳  Loading Zero-1-to-3 XL (~5 GB) …")
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        pipe = Zero1to3StableDiffusionPipeline.from_pretrained(
            "cvlab-columbia/zero123-xl",
            torch_dtype=dtype,
        )
        if torch.cuda.is_available():
            pipe = pipe.to("cuda")
        elif torch.backends.mps.is_available():
            pipe = pipe.to("mps")
        else:
            pipe = pipe.to("cpu")

        # Zero-1-to-3 expects a 256×256 input image
        img256 = self.pil_image.convert("RGB").resize((256, 256),
                                                       PILImage.Resampling.LANCZOS)

        self.progress.emit("🔄  Generating back view at azimuth=180° (Zero-1-to-3 XL) …")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        result = pipe(
            img256,
            guidance_scale=3.0,
            num_inference_steps=76,
            elevation_cond=torch.Tensor([0.0]).to(device),
            azimuth_cond=torch.Tensor([180.0]).to(device),
            distance_cond=torch.Tensor([1.0]).to(device),
        ).images[0]

        # Upscale to original image dimensions
        result = result.resize(self.pil_image.size, PILImage.Resampling.LANCZOS)
        self.progress.emit("✅  AI back view (Zero-1-to-3 XL) done.")
        return result


class ThreeDViewerWidget(QWidget):
    """
    Interactive 3D mesh viewer built on Matplotlib (no PyOpenGL or QOpenGLWidget needed).

    Matplotlib renders the depth-map mesh as a textured 3D plot embedded in a
    Qt widget.  The mesh covers both front and back surfaces, connected by seam
    triangles along the silhouette edge, forming a closed watertight model.

    Navigation (handled entirely by Matplotlib)
    --------------------------------------------
    Left-mouse drag  : Rotate the model.
    Right-mouse drag : Zoom in/out.

    The depth scale and invert flag can be updated live via ``update_depth_scale()``,
    which re-renders the mesh without reconstructing the entire widget.
    """

    def __init__(self, pil_image, depth_map, depth_scale=0.3, invert=False,
                 show_back=True, parent=None):
        """
        Initialise the 3D viewer widget.

        Parameters
        ----------
        pil_image : PIL.Image
            Source image used as the mesh texture.
        depth_map : np.ndarray
            Depth map as a float32 array normalised to [0, 1].
        depth_scale : float
            Strength of Z displacement in the mesh.  Higher values produce a
            more pronounced 3D effect.
        invert : bool
            Invert the depth map (swap near/far).  Useful when the estimator
            treats light areas as close rather than distant.
        show_back : bool
            Whether to render the generated back surface of the mesh.
        parent : QWidget, optional
            Parent widget.
        """
        super().__init__(parent)
        self.pil_image   = pil_image
        self.depth_map   = depth_map
        self.depth_scale = depth_scale
        self.invert      = invert
        self.show_back   = show_back
        self._canvas       = None
        self._fig          = None
        self._back_bg_mask = None   # set by set_ai_back(); None = use flipped front mask
        self.setMinimumSize(600, 420)
        self._init_plot()

    def _init_plot(self):
        """
        Initialise the Matplotlib 3D plot widget.

        Scales both the image and depth map down to at most 150×150 grid points
        (to keep rendering fast), builds the XY meshgrid, detects the background
        mask, generates the back texture, and triggers the first ``_draw()`` call.
        """
        import numpy as np

        # Embed the Matplotlib canvas (backend_qtagg available since matplotlib 3.6)
        FigureCanvasQTAgg = None
        for _backend in ("matplotlib.backends.backend_qtagg",
                         "matplotlib.backends.backend_qt5agg"):
            try:
                import importlib
                _mod = importlib.import_module(_backend)
                FigureCanvasQTAgg = _mod.FigureCanvasQTAgg
                break
            except Exception:
                pass
        if FigureCanvasQTAgg is None:
            lbl = QLabel("matplotlib not found.\n  pip install matplotlib")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay = QVBoxLayout(self); lay.addWidget(lbl)
            return
        from matplotlib.figure import Figure

        self._fig    = Figure(facecolor="#0a0a0a")
        self._canvas = FigureCanvasQTAgg(self._fig)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._canvas)

        # Prepare grid data
        iw, ih = self.pil_image.size
        gw = min(150, iw)
        gh = min(150, ih)
        img_s   = self.pil_image.resize((gw, gh), PILImage.Resampling.LANCZOS).convert("RGB")
        dep_s   = PILImage.fromarray((self.depth_map * 255).astype(np.uint8)).resize(
                      (gw, gh), PILImage.Resampling.LANCZOS)
        self._img_np   = np.array(img_s,  dtype=np.float32) / 255.0
        self._depth_np = np.array(dep_s,  dtype=np.float32) / 255.0
        aspect = iw / max(1, ih)
        self._XX, self._YY = np.meshgrid(
            np.linspace(-0.5 * aspect, 0.5 * aspect, gw),
            np.linspace(0.5, -0.5, gh),   # Phase 1: Y top→bottom in image = +Y→−Y in 3D
        )

        # ── Background mask (True = background pixel → transparent) ──────────
        # Computed first so _make_back_texture can use char_mask.
        self._bg_mask = ThreeDViewerWidget._detect_bg(self._img_np)

        # ── Generated back texture — silhouette-ring inpainting ──────────────
        # Instead of a simple flip (which bleeds belly/light colors onto
        # the back), we propagate the colors from the inner silhouette
        # ring outward.  Each back pixel inherits the nearest edge color
        # of the FRONT (orange body → orange back, black hair → black
        # back), giving a coherent result for any character pose.
        self._back_np = ThreeDViewerWidget._make_back_texture(
            self._img_np, ~self._bg_mask)

        self._draw()

    @staticmethod
    def _detect_bg(img_np, threshold=0.13):
        """
        Detect background pixels using corner-colour flood-fill and a near-white test.

        A raw colour-threshold pass is followed by a morphological fill-holes step
        that removes interior false positives (slight transparency or JPEG artefacts
        inside the subject) which would otherwise punch NaN holes through the mesh.

        Parameters
        ----------
        img_np : np.ndarray
            Float32 RGB image array, values in [0, 1], shape (H, W, 3).
        threshold : float
            Maximum mean channel difference from the corner background colour
            for a pixel to be classified as background.

        Returns
        -------
        np.ndarray
            Boolean mask of shape (H, W).  True = background pixel.
        """
        import numpy as np
        # ── Raw threshold pass ────────────────────────────────
        corners = np.array([img_np[0, 0], img_np[0, -1],
                             img_np[-1, 0], img_np[-1, -1]])
        bg = corners.mean(axis=0)                          # (3,)
        diff = np.abs(img_np - bg).mean(axis=2)            # (gh, gw)
        near_bg    = diff < threshold
        near_white = img_np.min(axis=2) > 0.82
        raw_mask   = near_bg | near_white                  # True = background

        # ── Fill interior holes ───────────────────────────────
        # A background-classified pixel that is completely enclosed by
        # character pixels is a false positive (body artefact).  We keep
        # only background pixels that are reachable from the image border
        # through other background pixels — those are truly exterior.
        try:
            from scipy.ndimage import binary_fill_holes
            # ~raw_mask = character (True); fill enclosed False regions
            import numpy as _np
            filled = _np.asarray(binary_fill_holes(~raw_mask), dtype=bool)
            return ~filled
        except ImportError:
            # Pure-numpy fallback: iterative dilation from the border,
            # constrained to raw_mask.  Converges in ≤ h+w steps.
            flood = np.zeros_like(raw_mask)
            flood[0,  :] = raw_mask[0,  :]
            flood[-1, :] = raw_mask[-1, :]
            flood[:,  0] = raw_mask[:,  0]
            flood[:, -1] = raw_mask[:, -1]
            for _ in range(raw_mask.shape[0] + raw_mask.shape[1]):
                pad   = np.pad(flood, 1, constant_values=False)
                grown = (pad[:-2, 1:-1] | pad[2:,  1:-1] |
                         pad[1:-1, :-2] | pad[1:-1, 2:]) & raw_mask
                if np.array_equal(grown, flood):
                    break
                flood = grown
            return flood

    # ── Texture helpers ───────────────────────────────────────
    @staticmethod
    def _dilate_texture(img_np, char_mask, iterations=10):
        """
        Edge-padding: smears character colors outward into the background
        by `iterations` pixels so that seam / boundary UV samples always
        hit solid colour instead of the anti-aliased white halo.

        Primary path: cv2 morphological dilation (fast, large radius).
        Fallback:     vectorised numpy neighbour-average (no extra deps).
        """
        import numpy as np
        try:
            import cv2
            img_u8  = (img_np * 255).astype(np.uint8)
            result  = img_u8.copy()
            filled  = char_mask.astype(np.uint8)          # 1 = has colour
            kernel  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            for _ in range(iterations):
                dilated     = cv2.dilate(result, kernel)
                new_filled  = cv2.dilate(filled, kernel)
                grow_mask   = (new_filled > 0) & (filled == 0)
                if not grow_mask.any():
                    break
                result[grow_mask] = dilated[grow_mask]
                filled = new_filled
            return result.astype(np.float32) / 255.0
        except ImportError:
            # numpy fallback — 4-connected neighbour average
            result = img_np.copy()
            filled = char_mask.copy()
            for _ in range(iterations):
                pad_r = np.pad(result, ((1, 1), (1, 1), (0, 0)), constant_values=0.0)
                pad_f = np.pad(filled.astype(np.float32), 1,      constant_values=0.0)
                nbr_c = (pad_r[:-2, 1:-1] + pad_r[2:,  1:-1] +
                         pad_r[1:-1, :-2] + pad_r[1:-1, 2:])
                nbr_n = (pad_f[:-2, 1:-1] + pad_f[2:,  1:-1] +
                         pad_f[1:-1, :-2] + pad_f[1:-1, 2:])
                to_fill = ~filled & (nbr_n > 0)
                if not to_fill.any():
                    break
                avg    = nbr_c / np.maximum(nbr_n[:, :, None], 1.0)
                result = np.where(to_fill[:, :, None], avg, result)
                filled = filled | to_fill
            return result

    @staticmethod
    def _compute_inset_map(char_mask, inset_px=3):
        """
        Returns (row_map, col_map) — integer arrays of shape (gh, gw).

        For each pixel (i, j):
          • If it is already >= inset_px pixels inside the silhouette,
            row_map[i,j] = i  and  col_map[i,j] = j  (maps to itself).
          • Otherwise it maps to the nearest pixel that IS >= inset_px
            pixels deep — i.e., the nearest solidly-interior pixel.

        Used for UV insetting (seam faces sample solid interior colors)
        and for back-texture generation (avoids anti-aliased edge halo).
        """
        import numpy as np
        try:
            from scipy.ndimage import distance_transform_edt
            dist  = distance_transform_edt(char_mask)   # 0 outside, depth inside
            solid = dist >= inset_px
            if not solid.any():                         # very thin object
                solid = dist >= max(1.0, float(dist.max()))
            _, nn = distance_transform_edt(~solid, return_indices=True)
            return nn[0].astype(np.int32), nn[1].astype(np.int32)
        except ImportError:
            # ── scipy-free fallback: erode inset_px times, then dilate ──
            from PIL import Image as _PIL, ImageFilter as _IFP
            pil = _PIL.fromarray((char_mask * 255).astype(np.uint8))
            for _ in range(inset_px):
                pil = pil.filter(_IFP.MinFilter(3))
            solid = np.array(pil, dtype=bool)
            if not solid.any():
                solid = char_mask
            h, w  = char_mask.shape
            ri    = np.tile(np.arange(h)[:, None], (1, w)).astype(np.int32)
            ci    = np.tile(np.arange(w)[None, :], (h, 1)).astype(np.int32)
            r_map = np.where(solid, ri, -1).astype(np.int32)
            c_map = np.where(solid, ci, -1).astype(np.int32)
            filled = solid.copy()
            for _ in range(h + w):
                to_fill = char_mask & ~filled
                if not to_fill.any():
                    break
                pr = np.pad(r_map, 1, constant_values=-1)
                pc = np.pad(c_map, 1, constant_values=-1)
                pf = np.pad(filled.astype(np.int32), 1, constant_values=0)
                # Accumulate neighbor coordinates (only from filled pixels)
                sr = (pr[:-2,1:-1]*(pf[:-2,1:-1]) + pr[2:,1:-1]*(pf[2:,1:-1]) +
                      pr[1:-1,:-2]*(pf[1:-1,:-2]) + pr[1:-1,2:]*(pf[1:-1,2:]))
                sc = (pc[:-2,1:-1]*(pf[:-2,1:-1]) + pc[2:,1:-1]*(pf[2:,1:-1]) +
                      pc[1:-1,:-2]*(pf[1:-1,:-2]) + pc[1:-1,2:]*(pf[1:-1,2:]))
                cnt = (pf[:-2,1:-1] + pf[2:,1:-1] +
                       pf[1:-1,:-2] + pf[1:-1,2:])
                valid = to_fill & (cnt > 0)
                r_map  = np.where(valid, (sr / np.maximum(cnt, 1)).astype(np.int32), r_map)
                c_map  = np.where(valid, (sc / np.maximum(cnt, 1)).astype(np.int32), c_map)
                filled = filled | valid
            # Any still-unmapped pixel (isolated dot) → self
            r_map = np.where(r_map < 0, ri, r_map)
            c_map = np.where(c_map < 0, ci, c_map)
            return r_map, c_map

    @staticmethod
    def _make_back_texture(img_np, char_mask):
        """
        Builds a solid, fully-opaque backside texture.

        Primary path (cv2):
          1. Erode the silhouette 2px to get a clean interior core.
          2. Use cv2.inpaint (TELEA) to propagate core colours into the
             anti-aliased fringe, giving a seamless solid fill.
          3. Slight Gaussian blur + mild desaturation.
          4. Hard-mask alpha: every character pixel = 1.0, background = 0.

        Fallback (scipy / PIL):
          Nearest-neighbour fill via _compute_inset_map (original method).
        """
        import numpy as np
        char = char_mask

        try:
            import cv2

            img_u8   = (img_np * 255).astype(np.uint8)
            char_u8  = char.astype(np.uint8) * 255

            # ── 1. Eroded interior core ────────────────────────────────
            kernel   = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            interior = cv2.erode(char_u8, kernel, iterations=2)  # 2px solid core

            # ── 2. Start with front image masked to solid interior ─────
            result = img_u8.copy()
            result[interior == 0] = 0   # zero out fringe + background

            # ── 3. Inpaint fringe pixels (char but NOT interior) ───────
            fringe_mask = ((char_u8 > 0) & (interior == 0)).astype(np.uint8) * 255
            if fringe_mask.any():
                result_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
                result_bgr = cv2.inpaint(result_bgr, fringe_mask,
                                         inpaintRadius=5, flags=cv2.INPAINT_TELEA)
                result = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)

            # ── 4. Smooth + mild desaturation ─────────────────────────
            result = cv2.GaussianBlur(result, (3, 3), 1.2)
            back_f = result.astype(np.float32) / 255.0
            gray   = back_f.mean(axis=2, keepdims=True)
            back_f = (back_f * 0.85 + gray * 0.15).clip(0.0, 1.0)

            # ── 5. Hard-mask: no semi-transparent pixels ───────────────
            return np.where(char[:, :, None], back_f, 0.0)

        except ImportError:
            # ── numpy / PIL fallback ───────────────────────────────────
            from PIL import Image as _PIL, ImageFilter as _IFB
            row_ins, col_ins = ThreeDViewerWidget._compute_inset_map(char, inset_px=3)
            back_fill = img_np[row_ins, col_ins] * char[:, :, None]
            pil  = _PIL.fromarray((back_fill * 255).astype(np.uint8))
            pil  = pil.filter(_IFB.GaussianBlur(radius=1.5))
            back = np.array(pil, dtype=np.float32) / 255.0
            gray = back.mean(axis=2, keepdims=True)
            back = (back * 0.85 + gray * 0.15).clip(0.0, 1.0)
            return np.where(char[:, :, None], back, 0.0)

    def _draw(self):
        """
        Render the complete 3D mesh into the Matplotlib figure.
        Builds front- and back-face triangles, seam triangles along the silhouette
        edge, and renders them all as a Poly3DCollection with embedded vertex colours.
        """
        if self._fig is None or self._canvas is None:
            return
        import numpy as np
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        # ── Edge-feathering radius (pixels) ───────────────────
        # Increase for softer/rounder edges; set to 0 to disable.
        _EDGE_FEATHER_PX = 3

        # Alten Rotations-Handler trennen (beide Event-IDs)
        for _attr in ('_rot_cid', '_rot_cid2'):
            cid = getattr(self, _attr, None)
            if cid is not None:
                try: self._canvas.mpl_disconnect(cid)
                except Exception: pass
            setattr(self, _attr, None)

        self._fig.clear()
        ax = self._fig.add_subplot(111, projection="3d")
        ax.set_facecolor("#0a0a0a")
        ax.patch.set_facecolor("#0a0a0a")
        ax.axis("off")

        depth_raw = (1.0 - self._depth_np) if self.invert else self._depth_np
        mask      = getattr(self, '_bg_mask', None)

        # Blur the depth map → smoother mesh surface
        try:
            from PIL import ImageFilter as _IFD
            _dp = PILImage.fromarray((depth_raw * 255).astype(np.uint8))
            depth = np.array(_dp.filter(_IFD.GaussianBlur(radius=1.5)),
                             dtype=np.float32) / 255.0
        except Exception:
            depth = depth_raw

        # Normalize to [0, 1] — guards against AI estimators that output
        # arbitrary float scales which would blow up Z unconditionally.
        _d_min, _d_max = float(depth.min()), float(depth.max())
        if _d_max - _d_min > 1e-6:
            depth = (depth - _d_min) / (_d_max - _d_min)

        # sqrt compression × 0.5 → front and back faces are closer together
        ZZ = np.sqrt(depth) * self.depth_scale * 0.5

        # ── Fix 1: Silhouetten-Kantenramp ─────────────────────
        # Outermost _EDGE_FEATHER_PX layers of character pixels have their Z
        # linearly ramped from 0 → full depth.  This rounds the model edge so
        # it looks like a toy rather than a sharp-edged card.
        # Tweak _EDGE_FEATHER_PX above to change the rounding amount.
        if mask is not None and _EDGE_FEATHER_PX > 0:
            char = (~mask).astype(np.float32)
            dist = np.zeros_like(char)        # 0 = unvisited / background
            cur  = char.copy()
            for step in range(1, _EDGE_FEATHER_PX + 1):
                pad     = np.pad(cur, 1, constant_values=0.0)
                eroded  = (pad[:-2,1:-1] * pad[2:,1:-1]
                           * pad[1:-1,:-2] * pad[1:-1,2:] * cur)
                ring    = cur - eroded          # pixels removed this step
                dist    = np.where(ring > 0, float(step), dist)
                cur     = eroded
            # Interior pixels (never in a ring) get maximum distance
            dist = np.where((cur > 0) & (dist == 0), float(_EDGE_FEATHER_PX), dist)
            feather = np.clip(dist / _EDGE_FEATHER_PX, 0.0, 1.0)
            ZZ = ZZ * feather

        # ── Fix 2: NaN for background → no waterfall faces ──────
        # np.nan causes plot_surface to skip any quad touching that vertex.
        # Previously Z=0 created a visible slanted face at the silhouette.
        if mask is not None:
            ZZ = np.where(mask, np.nan, ZZ)
        ZZ_back = -ZZ   # NaN propagates: background stays absent on back too

        # ── Phase 3: Unified Poly3DCollection (single mesh, no set_visible) ──
        # Builds the same watertight mesh as export_3d so the viewer always
        # matches the exported file.  All faces are always present — no
        # rotation-triggered set_visible swap needed.

        back_img = (self._back_np
                    if (self.show_back and hasattr(self, '_back_np'))
                    else self._img_np)

        gh, gw = ZZ.shape
        fv     = np.isfinite(ZZ)          # (gh, gw) — True = character pixel
        XX_r   = self._XX.ravel()
        YY_r   = self._YY.ravel()
        ZZ_r   = ZZ.ravel()
        ZZB_r  = ZZ_back.ravel()
        img_r  = self._img_np.reshape(-1, 3)
        back_r = back_img.reshape(-1, 3)

        # Quad validity: all 4 corners of a 1×1 grid cell must be character
        qv = (fv[:-1, :-1] & fv[1:, :-1] &
              fv[:-1,  1:] & fv[1:,  1:])          # (gh-1, gw-1)
        ii_q, jj_q = np.where(qv)
        k0 = ii_q * gw + jj_q
        k1 = (ii_q + 1) * gw + jj_q
        k2 = ii_q * gw + (jj_q + 1)
        k3 = (ii_q + 1) * gw + (jj_q + 1)

        def _v(zr, kk):
            """Stack (x, y, z) columns for index array kk."""
            return np.stack([XX_r[kk], YY_r[kk], zr[kk]], axis=1)   # (N, 3)

        # ── Front triangles (CCW, outward +Z) ─────────────────────────────
        # Tri1: k0→k2→k3  │  Tri2: k0→k3→k1
        ft1 = np.stack([_v(ZZ_r, k0), _v(ZZ_r, k2), _v(ZZ_r, k3)], axis=1)
        ft2 = np.stack([_v(ZZ_r, k0), _v(ZZ_r, k3), _v(ZZ_r, k1)], axis=1)
        fc1 = (img_r[k0] + img_r[k2] + img_r[k3]) / 3.0
        fc2 = (img_r[k0] + img_r[k3] + img_r[k1]) / 3.0

        # ── Back triangles (reversed winding, outward −Z) ─────────────────
        # Tri3: k0→k3→k2  │  Tri4: k0→k1→k3
        bt1 = np.stack([_v(ZZB_r, k0), _v(ZZB_r, k3), _v(ZZB_r, k2)], axis=1)
        bt2 = np.stack([_v(ZZB_r, k0), _v(ZZB_r, k1), _v(ZZB_r, k3)], axis=1)
        bc1 = (back_r[k0] + back_r[k3] + back_r[k2]) / 3.0
        bc2 = (back_r[k0] + back_r[k1] + back_r[k3]) / 3.0

        all_tris   = np.concatenate([ft1, ft2, bt1, bt2], axis=0)   # (4N, 3, 3)
        all_colors = np.concatenate([fc1, fc2, bc1, bc2], axis=0)   # (4N, 3)

        # ── Seam triangles (boundary-edge stitching, vectorised) ───────────
        if mask is not None:
            # Pad quad-validity arrays so index arithmetic is uniform
            q_pad_r = np.zeros((gh, gw - 1), dtype=bool)   # right-flank quads
            q_pad_r[:-1, :] = qv                            # qv[i,j] = quad below row i
            q_pad_l = np.zeros((gh, gw - 1), dtype=bool)
            q_pad_l[1:, :] = qv                             # qv[i-1,j] = quad above row i

            q_pad_d = np.zeros((gh - 1, gw), dtype=bool)   # down-flank quads
            q_pad_d[:, :-1] = qv                            # qv[i,j] = quad right of col j
            q_pad_u = np.zeros((gh - 1, gw), dtype=bool)
            q_pad_u[:, 1:] = qv                             # qv[i,j-1] = quad left of col j

            # ── Horizontal boundary edges  (i, j)–(i, j+1) ────────────────
            ep_h = fv[:, :-1] & fv[:, 1:]                  # (gh, gw-1)
            bnd_h = ep_h & (q_pad_l ^ q_pad_r)
            hi, hj = np.where(bnd_h)
            if hi.size:
                hk0 = hi * gw + hj
                hk1 = hi * gw + hj + 1
                hfi  = _v(ZZ_r,  hk0);  hbi  = _v(ZZB_r, hk0)
                hfi1 = _v(ZZ_r,  hk1);  hbi1 = _v(ZZB_r, hk1)
                hcol = (img_r[hk0] * 0.6 + back_r[hk0] * 0.4 +
                        img_r[hk1] * 0.6 + back_r[hk1] * 0.4) / 2.0

                above = q_pad_l[hi, hj]                     # True → bottom edge
                # above: (fi, bi1, fi1) + (fi, bi, bi1)  + back-face duplicates
                a = np.where(above)[0]
                ha_t1 = np.stack([hfi[a], hbi1[a], hfi1[a]], axis=1)
                ha_t2 = np.stack([hfi[a], hbi[a],  hbi1[a]], axis=1)
                ha_t1r = np.stack([hfi1[a], hbi1[a], hfi[a]], axis=1)   # reversed
                ha_t2r = np.stack([hbi1[a], hbi[a],  hfi[a]], axis=1)   # reversed
                ha_c  = np.concatenate([hcol[a]] * 4, axis=0)
                # below: (fi, fi1, bi1) + (fi, bi1, bi)  + back-face duplicates
                b = np.where(~above)[0]
                hb_t1 = np.stack([hfi[b], hfi1[b], hbi1[b]], axis=1)
                hb_t2 = np.stack([hfi[b], hbi1[b], hbi[b]],  axis=1)
                hb_t1r = np.stack([hbi1[b], hfi1[b], hfi[b]], axis=1)   # reversed
                hb_t2r = np.stack([hbi[b],  hbi1[b], hfi[b]], axis=1)   # reversed
                hb_c  = np.concatenate([hcol[b]] * 4, axis=0)

                seam_t = np.concatenate([ha_t1, ha_t2, ha_t1r, ha_t2r,
                                         hb_t1, hb_t2, hb_t1r, hb_t2r], axis=0)
                seam_c = np.concatenate([ha_c, hb_c], axis=0)
                all_tris   = np.concatenate([all_tris, seam_t],   axis=0)
                all_colors = np.concatenate([all_colors, seam_c], axis=0)

            # ── Vertical boundary edges  (i, j)–(i+1, j) ──────────────────
            ep_v = fv[:-1, :] & fv[1:, :]                  # (gh-1, gw)
            bnd_v = ep_v & (q_pad_u ^ q_pad_d)
            vi, vj = np.where(bnd_v)
            if vi.size:
                vk0 = vi * gw + vj
                vk1 = (vi + 1) * gw + vj
                vfi  = _v(ZZ_r,  vk0);  vbi  = _v(ZZB_r, vk0)
                vfi1 = _v(ZZ_r,  vk1);  vbi1 = _v(ZZB_r, vk1)
                vcol = (img_r[vk0] * 0.6 + back_r[vk0] * 0.4 +
                        img_r[vk1] * 0.6 + back_r[vk1] * 0.4) / 2.0

                onright = q_pad_d[vi, vj]                   # True → left boundary
                # right: (fi, bi1, fi1) + (fi, bi, bi1)  + back-face duplicates
                r = np.where(onright)[0]
                vr_t1 = np.stack([vfi[r], vbi1[r], vfi1[r]], axis=1)
                vr_t2 = np.stack([vfi[r], vbi[r],  vbi1[r]], axis=1)
                vr_t1r = np.stack([vfi1[r], vbi1[r], vfi[r]], axis=1)   # reversed
                vr_t2r = np.stack([vbi1[r], vbi[r],  vfi[r]], axis=1)   # reversed
                vr_c  = np.concatenate([vcol[r]] * 4, axis=0)
                # left: (fi, fi1, bi1) + (fi, bi1, bi)  + back-face duplicates
                l = np.where(~onright)[0]
                vl_t1 = np.stack([vfi[l], vfi1[l], vbi1[l]], axis=1)
                vl_t2 = np.stack([vfi[l], vbi1[l], vbi[l]],  axis=1)
                vl_t1r = np.stack([vbi1[l], vfi1[l], vfi[l]], axis=1)   # reversed
                vl_t2r = np.stack([vbi[l],  vbi1[l], vfi[l]], axis=1)   # reversed
                vl_c  = np.concatenate([vcol[l]] * 4, axis=0)

                seam_t = np.concatenate([vr_t1, vr_t2, vr_t1r, vr_t2r,
                                         vl_t1, vl_t2, vl_t1r, vl_t2r], axis=0)
                seam_c = np.concatenate([vr_c, vl_c], axis=0)
                all_tris   = np.concatenate([all_tris, seam_t],   axis=0)
                all_colors = np.concatenate([all_colors, seam_c], axis=0)

        # ── Render unified mesh ────────────────────────────────────────────
        rgba = np.concatenate(
            [np.clip(all_colors, 0.0, 1.0),
             np.ones((len(all_colors), 1), dtype=np.float32)], axis=1)
        coll = Poly3DCollection(all_tris, linewidths=0)
        coll.set_facecolor(rgba)
        ax.add_collection3d(coll)

        # Auto-scale axes to the mesh extent
        pts = all_tris.reshape(-1, 3)
        for setter, col in zip(
                [ax.set_xlim3d, ax.set_ylim3d, ax.set_zlim3d],
                [pts[:, 0],     pts[:, 1],     pts[:, 2]]):
            mn, mx = float(col.min()), float(col.max())
            mid = (mn + mx) / 2
            half = max((mx - mn) / 2, 1e-3)
            setter(mid - half, mid + half)

        _init_az = -60.0
        ax.view_init(elev=20, azim=_init_az)
        self._canvas.draw()

    # ── Shared ZZ helper ──────────────────────────────────────
    def _compute_ZZ(self):
        """
        Re-computes the same ZZ (and ZZ_back) arrays used in _draw(), so
        export_3d() always produces geometry that matches the on-screen view.

        Returns (ZZ, ZZ_back, mask) where background pixels are np.nan.
        """
        import numpy as np

        # Feathering radius — must match _draw()
        _EDGE_FEATHER_PX = 3

        depth_raw = (1.0 - self._depth_np) if self.invert else self._depth_np
        mask      = getattr(self, '_bg_mask', None)

        try:
            from PIL import ImageFilter as _IFD
            _dp = PILImage.fromarray((depth_raw * 255).astype(np.uint8))
            depth = np.array(_dp.filter(_IFD.GaussianBlur(radius=1.5)),
                             dtype=np.float32) / 255.0
        except Exception:
            depth = depth_raw

        # Normalize to [0, 1] — must match _draw()
        _d_min, _d_max = float(depth.min()), float(depth.max())
        if _d_max - _d_min > 1e-6:
            depth = (depth - _d_min) / (_d_max - _d_min)

        ZZ = np.sqrt(depth) * self.depth_scale * 0.5

        if mask is not None and _EDGE_FEATHER_PX > 0:
            char = (~mask).astype(np.float32)
            dist = np.zeros_like(char)
            cur  = char.copy()
            for step in range(1, _EDGE_FEATHER_PX + 1):
                pad    = np.pad(cur, 1, constant_values=0.0)
                eroded = (pad[:-2,1:-1] * pad[2:,1:-1]
                          * pad[1:-1,:-2] * pad[1:-1,2:] * cur)
                ring   = cur - eroded
                dist   = np.where(ring > 0, float(step), dist)
                cur    = eroded
            dist = np.where((cur > 0) & (dist == 0), float(_EDGE_FEATHER_PX), dist)
            ZZ = ZZ * np.clip(dist / _EDGE_FEATHER_PX, 0.0, 1.0)

        if mask is not None:
            ZZ = np.where(mask, np.nan, ZZ)

        return ZZ, -ZZ, mask

    # ── 3D Export ─────────────────────────────────────────────
    def export_3d(self, path: str):
        """
        Exports the current 3D model to *path*.

        Supported formats (chosen by file extension):
          .glb  — Binary glTF  (preferred; requires trimesh)
          .obj  — Wavefront OBJ + .mtl + texture PNG (pure-Python fallback)

        The exported mesh is a closed, textured object with:
          • Front surface  — mapped to the left  half of the texture atlas
          • Back  surface  — mapped to the right half of the texture atlas
          • Side walls     — connecting quads along the silhouette
        """
        import numpy as np
        import os

        ZZ, ZZ_back, mask = self._compute_ZZ()
        gh, gw = ZZ.shape

        img_np  = self._img_np                         # (gh, gw, 3) float32
        back_np = self._back_np if hasattr(self, '_back_np') else img_np[:, ::-1]

        # ── 1. Build vertex + UV lists ─────────────────────────
        # Index grids: -1 where vertex is absent (NaN / background).
        front_idx = np.full((gh, gw), -1, dtype=np.int32)
        back_idx  = np.full((gh, gw), -1, dtype=np.int32)

        verts, uvs = [], []
        _gw1 = max(gw - 1, 1)
        _gh1 = max(gh - 1, 1)

        # Front vertices
        for i in range(gh):
            for j in range(gw):
                if np.isfinite(ZZ[i, j]):
                    front_idx[i, j] = len(verts)
                    verts.append((float(self._XX[i, j]),
                                  float(self._YY[i, j]),
                                  float(ZZ[i, j])))
                    # Atlas left-half UV [u=0..0.5]
                    uvs.append((j / _gw1 * 0.5,
                                1.0 - i / _gh1))

        n_front = len(verts)

        # Back vertices (same XY, flipped Z)
        for i in range(gh):
            for j in range(gw):
                if np.isfinite(ZZ_back[i, j]):
                    back_idx[i, j] = len(verts) - n_front  # offset stored
                    verts.append((float(self._XX[i, j]),
                                  float(self._YY[i, j]),
                                  float(ZZ_back[i, j])))
                    # Atlas right-half UV [u=0.5..1.0]
                    uvs.append((0.5 + j / _gw1 * 0.5,
                                1.0 - i / _gh1))

        # ── 2. Build face lists ────────────────────────────────
        faces = []

        # Front faces — CCW winding (outward normal +Z)
        for i in range(gh - 1):
            for j in range(gw - 1):
                i0 = front_idx[i,   j  ]
                i1 = front_idx[i+1, j  ]
                i2 = front_idx[i,   j+1]
                i3 = front_idx[i+1, j+1]
                if i0 >= 0 and i1 >= 0 and i2 >= 0 and i3 >= 0:
                    faces.append((i0, i2, i3))
                    faces.append((i0, i3, i1))

        # Back faces — reversed winding (outward normal −Z)
        for i in range(gh - 1):
            for j in range(gw - 1):
                b0 = back_idx[i,   j  ]
                b1 = back_idx[i+1, j  ]
                b2 = back_idx[i,   j+1]
                b3 = back_idx[i+1, j+1]
                if b0 >= 0 and b1 >= 0 and b2 >= 0 and b3 >= 0:
                    # Apply n_front offset to convert to global index
                    g0, g1, g2, g3 = (b0+n_front, b1+n_front,
                                      b2+n_front, b3+n_front)
                    faces.append((g0, g3, g2))   # reversed
                    faces.append((g0, g1, g3))

        # ── Phase 2: Boundary-edge stitching (manifold seam) ──────────────
        # seam_fwd — correctly-wound seam faces (used for both GLB and OBJ)
        # seam_dup — reversed duplicates for OBJ (GLB uses doubleSided=True)
        seam_fwd, seam_dup = [], []
        if mask is not None:
            def _fq(ii, jj):
                """True when front quad (ii,jj)→(ii+1,jj+1) has all 4 valid corners."""
                return (0 <= ii < gh - 1 and 0 <= jj < gw - 1 and
                        front_idx[ii,   jj  ] >= 0 and
                        front_idx[ii+1, jj  ] >= 0 and
                        front_idx[ii,   jj+1] >= 0 and
                        front_idx[ii+1, jj+1] >= 0)

            # ── Horizontal boundary edges  (i,j)–(i,j+1) ──────────────────
            for i in range(gh):
                for j in range(gw - 1):
                    if front_idx[i, j] < 0 or front_idx[i, j+1] < 0:
                        continue
                    above = _fq(i - 1, j)
                    below = _fq(i,     j)
                    if above == below:
                        continue
                    fi  = front_idx[i, j]
                    fi1 = front_idx[i, j+1]
                    bi  = back_idx[i, j]   + n_front
                    bi1 = back_idx[i, j+1] + n_front
                    if above:
                        seam_fwd += [(fi, bi1, fi1), (fi, bi, bi1)]
                        seam_dup += [(fi1, bi1, fi), (bi1, bi, fi)]
                    else:
                        seam_fwd += [(fi, fi1, bi1), (fi, bi1, bi)]
                        seam_dup += [(bi1, fi1, fi), (bi, bi1, fi)]

            # ── Vertical boundary edges  (i,j)–(i+1,j) ────────────────────
            for i in range(gh - 1):
                for j in range(gw):
                    if front_idx[i, j] < 0 or front_idx[i+1, j] < 0:
                        continue
                    left  = _fq(i, j - 1)
                    right = _fq(i, j    )
                    if left == right:
                        continue
                    fi  = front_idx[i,   j]
                    fi1 = front_idx[i+1, j]
                    bi  = back_idx[i,   j] + n_front
                    bi1 = back_idx[i+1, j] + n_front
                    if right:
                        seam_fwd += [(fi, bi1, fi1), (fi, bi, bi1)]
                        seam_dup += [(fi1, bi1, fi), (bi1, bi, fi)]
                    else:
                        seam_fwd += [(fi, fi1, bi1), (fi, bi1, bi)]
                        seam_dup += [(bi1, fi1, fi), (bi, bi1, fi)]

        verts_np      = np.array(verts,                          dtype=np.float32)
        uvs_np        = np.array(uvs,                            dtype=np.float32)
        # GLB: forward seam only — doubleSided material removes culling;
        #      clean winding lets trimesh compute smooth vertex normals.
        # OBJ: include reversed duplicates (no doubleSided flag in .mtl).
        faces_glb_np  = np.array(faces + seam_fwd,              dtype=np.int32)
        faces_obj_np  = np.array(faces + seam_fwd + seam_dup,   dtype=np.int32)

        # ── 3. Texture atlas (with edge dilation) ──────────────────────
        # Expand character colors 3 pixels outward into the background so
        # seam-boundary UVs sample solid color instead of the white halo
        # left by anti-aliasing against the background.
        if mask is not None:
            img_dil  = ThreeDViewerWidget._dilate_texture(img_np,  ~mask, iterations=10)
            back_dil = ThreeDViewerWidget._dilate_texture(back_np, ~mask, iterations=10)
        else:
            img_dil, back_dil = img_np, back_np

        atlas_np  = np.concatenate([img_dil, back_dil], axis=1)  # (gh, 2*gw, 3)
        atlas_pil = PILImage.fromarray((atlas_np * 255).astype(np.uint8))

        # ── 4. Export ──────────────────────────────────────────
        ext = os.path.splitext(path)[1].lower()

        # ── GLB via trimesh ────────────────────────────────────
        if ext == '.glb':
            try:
                import trimesh
                import trimesh.visual
                import trimesh.visual.material as _tvm

                # Always export as RGB — an RGBA texture causes some viewers to
                # default to ALPHA_BLEND mode, breaking depth-buffer ordering.
                atlas_rgb = atlas_pil.convert('RGB')

                # PBR material: OPAQUE alpha so the depth buffer is never
                # bypassed, and doubleSided so seam faces are visible from
                # both the inside and outside without back-face culling.
                mat = _tvm.PBRMaterial(
                    baseColorTexture = atlas_rgb,
                    alphaMode        = 'OPAQUE',
                    doubleSided      = True,
                )

                mesh = trimesh.Trimesh(
                    vertices = verts_np,
                    faces    = faces_glb_np,
                    process  = False,
                )
                mesh.visual = trimesh.visual.TextureVisuals(
                    uv       = uvs_np,
                    material = mat,
                )
                mesh.export(path)
                return

            except ImportError:
                # trimesh not installed → fall through to OBJ
                path = os.path.splitext(path)[0] + '.obj'
                ext  = '.obj'

        # ── OBJ + MTL (pure-Python fallback) ──────────────────
        if ext in ('.obj', ''):
            base   = os.path.splitext(path)[0]
            mtl_p  = base + '.mtl'
            tex_p  = base + '_texture.png'

            # Save texture as RGB — no alpha channel in the OBJ texture either
            atlas_pil.convert('RGB').save(tex_p)

            with open(mtl_p, 'w') as f:
                f.write(f"newmtl mat0\n"
                        f"Ka 1 1 1\nKd 1 1 1\nKs 0 0 0\n"
                        f"d 1.0\n"          # fully opaque — no transparency
                        f"illum 2\n"        # full lighting model
                        f"map_Kd {os.path.basename(tex_p)}\n")

            with open(path if path.endswith('.obj') else path+'.obj', 'w') as f:
                f.write(f"mtllib {os.path.basename(mtl_p)}\n")
                f.write("usemtl mat0\n")
                for (x, y, z) in verts_np:
                    f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
                for (u, v) in uvs_np:
                    f.write(f"vt {u:.6f} {v:.6f}\n")
                for (a, b, c) in faces_obj_np:
                    # OBJ is 1-indexed; include UV index = vertex index
                    f.write(f"f {a+1}/{a+1} {b+1}/{b+1} {c+1}/{c+1}\n")

    # ── STL Export (3D-print) ─────────────────────────────────
    def export_stl(self, path: str, target_mm: float = 100.0):
        """
        Exports the current mesh as a binary STL file scaled so that the
        longest bounding-box dimension equals *target_mm* millimetres.

        STL carries no color or UV data; only vertices and face normals
        are written.  The face list uses forward-wound seam faces only
        (no backface duplicates) so the mesh stays manifold/watertight —
        a hard requirement for FDM slicers such as Cura and PrusaSlicer.

        Scale: 100 mm default → desktop-toy / enamel-pin size (~10 cm).
        Slicers interpret STL units as mm, so 100 units ≡ 100 mm.
        """
        import numpy as np
        import os

        ZZ, ZZ_back, mask = self._compute_ZZ()
        gh, gw = ZZ.shape

        # ── Build vertices (no UVs needed for STL) ────────────
        front_idx = np.full((gh, gw), -1, dtype=np.int32)
        back_idx  = np.full((gh, gw), -1, dtype=np.int32)
        verts     = []

        for i in range(gh):
            for j in range(gw):
                if np.isfinite(ZZ[i, j]):
                    front_idx[i, j] = len(verts)
                    verts.append((float(self._XX[i, j]),
                                  float(self._YY[i, j]),
                                  float(ZZ[i, j])))
        n_front = len(verts)

        for i in range(gh):
            for j in range(gw):
                if np.isfinite(ZZ_back[i, j]):
                    back_idx[i, j] = len(verts) - n_front
                    verts.append((float(self._XX[i, j]),
                                  float(self._YY[i, j]),
                                  float(ZZ_back[i, j])))

        # ── Build faces (manifold — forward seam only) ─────────
        faces = []

        for i in range(gh - 1):              # front
            for j in range(gw - 1):
                i0, i1 = front_idx[i, j], front_idx[i+1, j]
                i2, i3 = front_idx[i, j+1], front_idx[i+1, j+1]
                if i0 >= 0 and i1 >= 0 and i2 >= 0 and i3 >= 0:
                    faces += [(i0, i2, i3), (i0, i3, i1)]

        for i in range(gh - 1):              # back (reversed winding)
            for j in range(gw - 1):
                b0 = back_idx[i,   j  ]
                b1 = back_idx[i+1, j  ]
                b2 = back_idx[i,   j+1]
                b3 = back_idx[i+1, j+1]
                if b0 >= 0 and b1 >= 0 and b2 >= 0 and b3 >= 0:
                    g0, g1 = b0+n_front, b1+n_front
                    g2, g3 = b2+n_front, b3+n_front
                    faces += [(g0, g3, g2), (g0, g1, g3)]

        if mask is not None:                 # seam (forward only)
            def _fq(ii, jj):
                return (0 <= ii < gh-1 and 0 <= jj < gw-1 and
                        front_idx[ii, jj] >= 0 and front_idx[ii+1, jj] >= 0 and
                        front_idx[ii, jj+1] >= 0 and front_idx[ii+1, jj+1] >= 0)
            for i in range(gh):
                for j in range(gw - 1):
                    if front_idx[i, j] < 0 or front_idx[i, j+1] < 0:
                        continue
                    above, below = _fq(i-1, j), _fq(i, j)
                    if above == below:
                        continue
                    fi, fi1 = front_idx[i, j], front_idx[i, j+1]
                    bi  = back_idx[i, j]   + n_front
                    bi1 = back_idx[i, j+1] + n_front
                    if above:
                        faces += [(fi, bi1, fi1), (fi, bi, bi1)]
                    else:
                        faces += [(fi, fi1, bi1), (fi, bi1, bi)]
            for i in range(gh - 1):
                for j in range(gw):
                    if front_idx[i, j] < 0 or front_idx[i+1, j] < 0:
                        continue
                    left, right = _fq(i, j-1), _fq(i, j)
                    if left == right:
                        continue
                    fi, fi1 = front_idx[i, j], front_idx[i+1, j]
                    bi  = back_idx[i,   j] + n_front
                    bi1 = back_idx[i+1, j] + n_front
                    if right:
                        faces += [(fi, bi1, fi1), (fi, bi, bi1)]
                    else:
                        faces += [(fi, fi1, bi1), (fi, bi1, bi)]

        verts_np = np.array(verts, dtype=np.float32)
        faces_np = np.array(faces, dtype=np.int32)

        # ── Scale to target_mm on the longest dimension ────────
        lo, hi   = verts_np.min(axis=0), verts_np.max(axis=0)
        max_dim  = float((hi - lo).max())
        scale    = target_mm / max(max_dim, 1e-9)
        verts_mm = verts_np * scale           # units are now millimetres

        # ── Write binary STL ───────────────────────────────────
        try:
            import trimesh as _tm
            mesh = _tm.Trimesh(vertices=verts_mm, faces=faces_np, process=False)
            mesh.export(path)
        except ImportError:
            # stdlib-only fallback: write binary STL with struct
            import struct
            tv = verts_mm[faces_np]           # (N, 3, 3)  triangle vertices
            e1 = tv[:, 1] - tv[:, 0]
            e2 = tv[:, 2] - tv[:, 0]
            normals = np.cross(e1, e2)
            nlen = np.linalg.norm(normals, axis=1, keepdims=True)
            normals = normals / np.where(nlen > 0, nlen, 1.0)
            with open(path, 'wb') as f:
                f.write(b'\x00' * 80)         # 80-byte header
                f.write(struct.pack('<I', len(faces_np)))
                for k in range(len(faces_np)):
                    nx, ny, nz = normals[k]
                    f.write(struct.pack('<3f', nx, ny, nz))
                    for v in tv[k]:
                        f.write(struct.pack('<3f', *v))
                    f.write(struct.pack('<H', 0))

    # ─────────────────────────────────────────────────────────

    def update_depth_scale(self, scale: float, invert: bool = False,
                           show_back: bool = True):
        """Update the depth scale, inversion flag, and back-face setting, then re-render."""
        self.depth_scale = scale
        self.invert      = invert
        self.show_back   = show_back
        self._draw()

    def set_ai_back(self, pil_img):
        """
        Replaces the generated back texture with an AI-synthesised image.

        Processing pipeline (runs before the image touches the mesh):
          1. Resize to grid dimensions.
          2. Detect the AI back's own silhouette (bg mask).
          3. Apply 20-pixel cv2 edge-dilation so seam UVs always hit solid
             character colour instead of the anti-aliased transparent fringe.
          4. Hard-enforce opacity: every silhouette pixel = fully opaque (1.0),
             every background pixel = 0.  No semi-transparent halo survives.
        """
        import numpy as np
        gw = self._img_np.shape[1]
        gh = self._img_np.shape[0]
        back_resized = pil_img.convert("RGB").resize((gw, gh), PILImage.Resampling.LANCZOS)
        back_raw = np.array(back_resized, dtype=np.float32) / 255.0

        # ── 1. Silhouette mask from the AI back itself ─────────────────
        bg_mask  = ThreeDViewerWidget._detect_bg(back_raw)   # True = background
        char_mask = ~bg_mask                                  # True = character

        # ── 2. Edge-pad: smear character colours 20px outward ──────────
        back_dil = ThreeDViewerWidget._dilate_texture(back_raw, char_mask,
                                                      iterations=20)

        # ── 3. Hard opacity: silhouette = 1.0, background = exactly 0 ──
        back_clean = np.where(char_mask[:, :, None], back_dil, 0.0)

        self._back_np      = back_clean
        self._back_bg_mask = bg_mask
        self._draw()


class ThreeDModelDialog(QDialog):
    """
    2D → 3D model viewer (Beta).

    Opens the current image, estimates a depth map using AI (Depth-Anything-V2
    or MiDaS), and displays the result as an interactive 3D mesh.
    """

    def __init__(self, parent, editor):
        """
        Create the 3D model dialog.

        Parameters
        ----------
        parent : QWidget
            Parent widget (ImageEditor main window).
        editor : ImageEditor
            Reference to the ImageEditor, used to access the layer stack.
        """
        super().__init__(parent)
        self.editor   = editor
        self._worker:       "DepthWorker | None"     = None
        self._novel_worker: "NovelViewWorker | None" = None
        self._viewer:       "ThreeDViewerWidget | None" = None
        self._depth_map = None
        self._img       = None

        self.setWindowTitle("🧊  2D → 3D Modell (Beta)")
        self.setModal(True)
        self.resize(1000, 700)
        self.setStyleSheet("background:#1a1a1a; color:#ddd;")
        self._build_ui()

    def _build_ui(self):
        """Build the full UI of the 3D dialog (control panel + 3D viewer container)."""
        root = QHBoxLayout(self)
        root.setSpacing(10)

        # ── Linke Seite: Steuerung ────────────────────────────
        ctrl_w = QWidget(); ctrl_w.setFixedWidth(270)
        ctrl   = QVBoxLayout(ctrl_w); ctrl.setSpacing(8)

        _btn = ("QPushButton { background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
                "border-radius:4px; padding:8px; font-size:12px; } "
                "QPushButton:hover { background:#3a3a3a; }")

        info = QLabel(
            "Konvertiert das aktuelle Bild in ein\n"
            "interaktives 3D-Modell.\n\n"
            "🟢 Beste Qualität (empfohlen):\n"
            "  pip install transformers torch\n"
            "  → Depth-Anything-V2 (~100 MB)\n\n"
            "🟡 Fallback:\n"
            "  pip install torch timm\n"
            "  → MiDaS\n\n"
            "🔴 Immer verfügbar:\n"
            "  Luminanz-Approximation\n"
            "  (geringe Qualität)\n\n"
            "3D-Viewer:\n"
            "  pip install PyOpenGL\n"
            "  pip install PyOpenGL_accelerate"
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "color:#888; font-size:10px; background:#141414; "
            "border-radius:3px; padding:6px;"
        )
        ctrl.addWidget(info)

        # Depth effect strength slider
        sp_s = "background:#2d2d2d; color:#ddd; border:1px solid #333; padding:2px;"
        ctrl.addWidget(QLabel("Tiefen-Effekt Stärke:"))
        self._depth_slider = QSlider(Qt.Orientation.Horizontal)
        self._depth_slider.setRange(5, 30)
        self._depth_slider.setValue(20)
        self._depth_slider.setStyleSheet(
            "QSlider::groove:horizontal { background:#3a3a3a; height:4px; border-radius:2px; }"
            "QSlider::handle:horizontal { background:#4fc3f7; width:14px; height:14px; "
            "margin:-5px 0; border-radius:7px; }"
        )
        self._depth_slider.valueChanged.connect(self._on_depth_slider)
        ctrl.addWidget(self._depth_slider)

        # Invert depth (useful for bright/white backgrounds such as product photos)
        from PyQt6.QtWidgets import QCheckBox
        self._chk_invert = QCheckBox("🔄  Tiefe invertieren")
        self._chk_invert.setStyleSheet("color:#ddd; font-size:11px;")
        self._chk_invert.setToolTip(
            "Aktivieren bei weißem/hellem Hintergrund:\n"
            "Depth-Anything behandelt helle Flächen als 'nah'.\n"
            "Invertieren schiebt den Hintergrund zurück.")
        self._chk_invert.stateChanged.connect(self._on_invert_changed)
        ctrl.addWidget(self._chk_invert)

        self._chk_back = QCheckBox("🔲  Generierte Rückseite")
        self._chk_back.setStyleSheet("color:#ddd; font-size:11px;")
        self._chk_back.setChecked(True)
        self._chk_back.setToolTip(
            "Fügt eine generierte Rückseite hinzu:\n"
            "• Horizontal gespiegelt\n"
            "• Weichgezeichnet (fehlende Details)\n"
            "• Desaturiert + abgedunkelt\n\n"
            "Keine KI — plausible Annäherung.")
        self._chk_back.stateChanged.connect(self._on_invert_changed)
        ctrl.addWidget(self._chk_back)

        # Mesh resolution (grid size)
        ctrl.addWidget(QLabel("Mesh-Auflösung (Grid-Größe):"))
        self._sp_resolution = QSpinBox()
        self._sp_resolution.setRange(50, 300)
        self._sp_resolution.setValue(200)
        self._sp_resolution.setSuffix(" px")
        self._sp_resolution.setStyleSheet(sp_s)
        ctrl.addWidget(self._sp_resolution)

        # Status
        self._lbl_status = QLabel("Bereit.")
        self._lbl_status.setStyleSheet("color:#4fc3f7; font-size:10px;")
        self._lbl_status.setWordWrap(True)
        ctrl.addWidget(self._lbl_status)

        # Starten
        btn_gen = QPushButton("🧊  3D-Modell erstellen")
        btn_gen.setStyleSheet(
            "QPushButton { background:#1a3a1a; color:#90ee90; border:1px solid #2a5a2a; "
            "border-radius:4px; padding:10px; font-size:13px; font-weight:bold; } "
            "QPushButton:hover { background:#2a4a2a; }"
        )
        btn_gen.clicked.connect(self._start)
        ctrl.addWidget(btn_gen)

        # Tiefenkarte anzeigen
        self._btn_show_depth = QPushButton("🗺  Tiefenkarte anzeigen")
        self._btn_show_depth.setStyleSheet(_btn)
        self._btn_show_depth.clicked.connect(self._show_depth_map)
        self._btn_show_depth.setEnabled(False)
        ctrl.addWidget(self._btn_show_depth)

        # AI back-face generation button
        self._btn_ai_back = QPushButton("🤖  KI-Rückseite generieren")
        self._btn_ai_back.setStyleSheet(
            "QPushButton { background:#1a2a3a; color:#7ec8e3; border:1px solid #2a4a5a; "
            "border-radius:4px; padding:8px; font-size:12px; } "
            "QPushButton:hover { background:#2a3a4a; } "
            "QPushButton:disabled { background:#1a1a1a; color:#444; border-color:#2a2a2a; }"
        )
        self._btn_ai_back.setToolTip(
            "Generiert eine echte KI-Rückseite mit Novel View Synthesis.\n\n"
            "Benötigt (einmalig):\n"
            "  pip install diffusers transformers accelerate\n"
            "  pip install torch torchvision --index-url\n"
            "    https://download.pytorch.org/whl/cu128\n\n"
            "Modelle (automatischer Download):\n"
            "  • zero123plus-v1.1   (~1.8 GB, primär)\n"
            "  • Zero-1-to-3 XL     (~5 GB, Fallback)\n\n"
            "NVIDIA-GPU mit ≥4 GB VRAM empfohlen."
        )
        self._btn_ai_back.clicked.connect(self._start_novel_view)
        self._btn_ai_back.setEnabled(False)
        ctrl.addWidget(self._btn_ai_back)

        # Export as 3D file
        self._btn_export = QPushButton("💾  Als 3D exportieren …")
        self._btn_export.setStyleSheet(
            "QPushButton { background:#2a1a3a; color:#c39bd3; border:1px solid #4a2a5a; "
            "border-radius:4px; padding:8px; font-size:12px; } "
            "QPushButton:hover { background:#3a2a4a; } "
            "QPushButton:disabled { background:#1a1a1a; color:#444; border-color:#2a2a2a; }"
        )
        self._btn_export.setToolTip(
            "Exportiert das 3D-Modell als:\n"
            "  • .glb  (Binary glTF — bevorzugt, braucht trimesh)\n"
            "  • .obj  (Wavefront OBJ + MTL + Textur-PNG)\n\n"
            "pip install trimesh  →  für GLB-Export"
        )
        self._btn_export.clicked.connect(self._export_3d)
        self._btn_export.setEnabled(False)
        ctrl.addWidget(self._btn_export)

        # 3D-print STL export button
        self._btn_stl = QPushButton("🖨  Export for 3D Printing (.stl)")
        self._btn_stl.setStyleSheet(
            "QPushButton { background:#2a1a0a; color:#f0a830; border:1px solid #5a3a0a; "
            "border-radius:4px; padding:8px; font-size:12px; } "
            "QPushButton:hover { background:#3a2a0a; } "
            "QPushButton:disabled { background:#1a1a1a; color:#444; border-color:#2a2a2a; }"
        )
        self._btn_stl.setToolTip(
            "Exportiert das Mesh als binäres STL für 3D-Drucker-Slicer\n"
            "(Cura, PrusaSlicer, Bambu Studio, …).\n\n"
            "• Keine Farben / UVs — reine Geometrie\n"
            "• Skaliert auf 100 mm längste Seite\n"
            "• Watertight / manifold — druckfertig\n\n"
            "Benötigt trimesh (empfohlen) oder nutzt stdlib-Fallback."
        )
        self._btn_stl.clicked.connect(self._export_stl)
        self._btn_stl.setEnabled(False)
        ctrl.addWidget(self._btn_stl)

        nav = QLabel("Steuerung:\n• Maus ziehen = Rotation\n• Mausrad = Zoom")
        nav.setStyleSheet("color:#555; font-size:9px;")
        ctrl.addWidget(nav)
        ctrl.addStretch()
        root.addWidget(ctrl_w)

        # ── Right side: persistent container ─────────────────
        # The container stays in the root layout permanently.
        # Only its content (placeholder ↔ viewer) is swapped.
        self._right_panel = QWidget()
        self._right_panel.setMinimumSize(640, 480)
        self._right_lay = QVBoxLayout(self._right_panel)
        self._right_lay.setContentsMargins(0, 0, 0, 0)

        self._placeholder = QLabel(
            "3D-Modell erscheint hier nach der Berechnung.\n\n"
            "Klicke auf '🧊 3D-Modell erstellen'."
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            "background:#0a0a0a; border:1px solid #2a2a2a; color:#444; font-size:14px;"
        )
        self._right_lay.addWidget(self._placeholder)
        root.addWidget(self._right_panel, 1)

    # ── Depth estimation ─────────────────────────────────────

    @staticmethod
    def _preload_torch():
        """
        Pre-load torch on the main thread before a QThread starts.
        On Windows, DLL initialisation fails when torch is first imported
        inside a Qt C++ thread (i.e. not the Python main thread).
        By importing it here first, c10.dll is already in the process
        and the thread import succeeds.
        """
        try:
            import torch          # noqa: F401
            import transformers   # noqa: F401
        except Exception:
            pass

    def _start(self):
        """Start depth estimation for the current composite image in a background thread."""
        if not self.editor.layers:
            return

        self._preload_torch()
        img = self.editor._composite_layers().convert("RGB")
        # Cap at 768 px on the longest side for performance
        if max(img.size) > 768:
            img = img.copy()
            img.thumbnail((768, 768), PILImage.Resampling.LANCZOS)

        self._img = img
        self._lbl_status.setText("⏳ Starte Tiefenschätzung …")

        if self._worker and self._worker.isRunning():
            self._worker.terminate()

        self._worker = DepthWorker(img)
        self._worker.progress.connect(self._lbl_status.setText)
        self._worker.depth_ready.connect(self._on_depth_ready)
        self._worker.error.connect(lambda e: (
            self._lbl_status.setText(f"❌ {e}"),
            QMessageBox.critical(self, "Fehler", e),
        ))
        self._worker.start()

    def _on_depth_ready(self, depth_map):
        """Receive the completed depth map from the worker and initialise the 3D viewer."""
        self._depth_map = depth_map
        self._btn_show_depth.setEnabled(True)
        self._btn_ai_back.setEnabled(True)
        self._lbl_status.setText("✅ Tiefenkarte bereit — lade 3D-Viewer …")
        self._show_viewer()

    def _show_viewer(self):
        """Create a new ThreeDViewerWidget and replace the placeholder in the container."""
        scale = self._depth_slider.value() / 100.0

        # Remove the old viewer from the container
        if self._viewer:
            self._right_lay.removeWidget(self._viewer)
            self._viewer.hide()
            self._viewer.deleteLater()
            self._viewer = None

        # Hide the placeholder label
        self._placeholder.hide()
        self._right_lay.removeWidget(self._placeholder)

        # Insert the new viewer into the container (parent=_right_panel keeps it alive)
        self._viewer = ThreeDViewerWidget(
            self._img, self._depth_map,
            depth_scale=scale,
            invert=self._chk_invert.isChecked(),
            show_back=self._chk_back.isChecked(),
            parent=self._right_panel
        )
        self._right_lay.addWidget(self._viewer)
        self._btn_export.setEnabled(True)
        self._btn_stl.setEnabled(True)
        self._lbl_status.setText(
            "✅ Modell bereit.\n"
            "Maus ziehen = Rotation | Mausrad = Zoom"
        )

    def _on_depth_slider(self, val: int):
        """Update the depth scale in the viewer when the slider is moved."""
        if self._viewer:
            self._viewer.update_depth_scale(val / 100.0,
                                            self._chk_invert.isChecked(),
                                            self._chk_back.isChecked())

    def _on_invert_changed(self):
        """Update the 3D viewer when the invert-depth or show-back checkbox changes."""
        if self._viewer:
            self._viewer.update_depth_scale(self._depth_slider.value() / 100.0,
                                            self._chk_invert.isChecked(),
                                            self._chk_back.isChecked())

    def _start_novel_view(self):
        """Launch the NovelViewWorker to generate an AI back face."""
        if self._img is None:
            return

        self._preload_torch()

        if self._novel_worker and self._novel_worker.isRunning():
            self._novel_worker.terminate()

        self._btn_ai_back.setEnabled(False)
        self._lbl_status.setText("⏳ Starte KI-Rückseiten-Generierung …")

        self._novel_worker = NovelViewWorker(self._img)
        self._novel_worker.progress.connect(self._lbl_status.setText)
        self._novel_worker.views_ready.connect(self._on_novel_view_ready)
        self._novel_worker.error.connect(self._on_novel_view_error)
        self._novel_worker.start()

    def _on_novel_view_ready(self, back_pil):
        """Apply the AI-generated back face to the 3D viewer."""
        self._btn_ai_back.setEnabled(True)
        if self._viewer:
            self._viewer.set_ai_back(back_pil)
            self._lbl_status.setText(
                "✅ KI-Rückseite angewendet!\n"
                "Modell auf ~180° drehen um sie zu sehen."
            )
        else:
            self._lbl_status.setText("✅ KI-Rückseite bereit (3D-Modell noch nicht erstellt).")

    def _on_novel_view_error(self, msg: str):
        """Show an error dialog when AI back-face generation fails."""
        self._btn_ai_back.setEnabled(True)
        self._lbl_status.setText(f"❌ KI-Rückseite fehlgeschlagen.")
        QMessageBox.critical(
            self, "KI-Rückseite fehlgeschlagen",
            f"Novel View Synthesis fehlgeschlagen:\n\n{msg}\n\n"
            "Bitte installieren:\n"
            "  pip install diffusers transformers accelerate\n"
            "  pip install torch torchvision "
            "--index-url https://download.pytorch.org/whl/cu128"
        )

    def _export_3d(self):
        """Export the current 3D mesh as a GLB or OBJ file."""
        if self._viewer is None:
            return
        try:
            import trimesh as _tm  # noqa: F401
            tri_ok = True
        except ImportError:
            tri_ok = False

        filters = []
        if tri_ok:
            filters.append("Binary glTF (*.glb)")
        filters.append("Wavefront OBJ (*.obj)")

        path, _ = QFileDialog.getSaveFileName(
            self, "3D-Modell exportieren", "", ";;".join(filters)
        )
        if not path:
            return

        self._lbl_status.setText("⏳ Exportiere 3D-Modell …")
        QApplication.processEvents()
        try:
            self._viewer.export_3d(path)
            self._lbl_status.setText(f"✅ Exportiert:\n{path}")
        except Exception as e:
            self._lbl_status.setText(f"❌ Export fehlgeschlagen:\n{e}")
            QMessageBox.critical(self, "Export-Fehler", str(e))

    def _export_stl(self):
        """Export the mesh as a print-ready binary STL scaled to 100 mm on the longest side."""
        if self._viewer is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "3D-Druck STL exportieren", "", "STL-Datei (*.stl)"
        )
        if not path:
            return
        if not path.lower().endswith('.stl'):
            path += '.stl'
        self._lbl_status.setText("⏳ Exportiere STL …")
        QApplication.processEvents()
        try:
            self._viewer.export_stl(path)
            self._lbl_status.setText(f"✅ STL exportiert (100 mm):\n{path}")
        except Exception as e:
            self._lbl_status.setText(f"❌ STL-Export fehlgeschlagen:\n{e}")
            QMessageBox.critical(self, "STL-Export-Fehler", str(e))

    def _show_depth_map(self):
        """Show the estimated depth map in a separate dialog (bright = near, dark = far)."""
        if self._depth_map is None:
            return
        import numpy as np
        dm_uint8 = (self._depth_map * 255).astype(np.uint8)
        depth_pil = PILImage.fromarray(dm_uint8).convert("RGB")
        dlg = QDialog(self)
        dlg.setWindowTitle("Tiefenkarte (hell = nah, dunkel = fern)")
        dlg.setStyleSheet("background:#1a1a1a; color:#ddd;")
        lay = QVBoxLayout(dlg)
        lbl = QLabel()
        pix = pil_to_qpixmap(depth_pil)
        lbl.setPixmap(pix.scaled(500, 400, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(lbl)
        dlg.exec()


# ══════════════════════════════════════════════════════════════
#  COLLAGE-EDITOR: Mehrere Bilder zu einer Collage zusammenfügen
# ══════════════════════════════════════════════════════════════
