"""
sbs/ai_worker.py – Background thread for AI image analysis (Moondream via Ollama).

The worker runs Moondream vision-model inference in a separate QThread so that
the UI stays responsive during the 5–30 second inference time.
"""
import base64, io, json, urllib.request, urllib.error

from PyQt6.QtCore import QThread, pyqtSignal

try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

class AIWorker(QThread):
    """
    Background thread for Moondream AI image description.

    Why a separate thread?
    AI inference takes 5–30 seconds. Running it on the main thread would
    freeze the entire UI (no clicks, no scrolling).
    → QThread moves the work to a background process.
    → Communication back to the UI uses the result_ready signal (thread-safe).

    Workflow
    --------
    1. Resize the PIL image to 512 × 512 (faster transfer).
    2. Encode as JPEG in Base64 format (required by Ollama API).
    3. HTTP POST to the Ollama API at localhost:11434.
    4. Emit the response text via result_ready signal.

    Prerequisite: ``ollama serve`` must be running and ``moondream`` must be pulled.
    """
    result_ready   = pyqtSignal(str)   # Emits the description text to the UI
    error_occurred = pyqtSignal(str)   # Emits an error message to the UI

    def __init__(self, pil_image: "PILImage.Image"):
        """
        Create the worker with a copy of the image to be analysed.

        Parameters
        ----------
        pil_image : PIL Image that Moondream should describe.
                    A copy is stored so the original is not modified.
        """
        super().__init__()
        self.pil_image = pil_image.copy()

    def run(self):
        """
        Execute AI analysis on the background thread.

        Steps
        -----
        1. Resize the image to 512 × 512 px for faster transfer.
        2. Encode as JPEG in Base64.
        3. HTTP POST to the Ollama API (localhost:11434).
        4. Emit the response text via the result_ready signal.
        """
        try:
            img = self.pil_image.copy()
            # Downscale the image for faster transfer to Ollama
            img.thumbnail((512, 512), PILImage.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            # Encode the JPEG bytes as a Base64 string (required by the Ollama API)
            b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

            payload = {
                "model": "moondream",
                "prompt": "Please describe what you see in this image. Mention the main subject, colors, and background. Write 2-3 complete sentences.",
                "images": [b64],
                "stream": False,
                "options": {"num_predict": 200, "temperature": 0.1}
            }
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
                desc = data.get("response", "Keine Antwort.").strip()
                print(f"[Moondream]: {desc}")
                self.result_ready.emit(desc)
        except ConnectionRefusedError:
            self.error_occurred.emit("Ollama läuft nicht. Bitte Terminal öffnen und 'ollama serve' ausführen.")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.error_occurred.emit(
                    "Modell 'moondream' nicht gefunden.\n"
                    "Bitte im Terminal ausführen:\n\n"
                    "  ollama pull moondream"
                )
            else:
                self.error_occurred.emit(f"KI-Fehler: HTTP {e.code} {e.reason}")
        except Exception as e:
            self.error_occurred.emit(f"KI-Fehler: {e}")


# ══════════════════════════════════════════════════════════════
#  CROP OVERLAY: draws a rectangle / lasso on the image
# ══════════════════════════════════════════════════════════════
