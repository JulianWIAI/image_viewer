"""
sbs/ai_worker.py
KI-Hintergrundthread (Moondream via Ollama) für den SBS Bildeditor.
"""
import base64, io, urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

class AIWorker(QThread):
    """
    Hintergrund-Thread für die Moondream KI-Bildanalyse.

    WARUM ein separater Thread?
    KI-Analyse dauert 5–30 Sekunden. Würde sie im Haupt-Thread laufen,
    würde die gesamte UI einfrieren (kein Klicken, kein Scrollen möglich).
    → QThread verschiebt die Arbeit in einen Hintergrundprozess.
    → Kommunikation zurück zur UI: result_ready Signal (Thread-sicher).

    Ablauf:
      1. PIL-Bild auf 512×512 verkleinern (schnellere Übertragung)
      2. Als JPEG in Base64 kodieren
      3. HTTP-POST an Ollama API (localhost:11434)
      4. Antwort per Signal an ImageEditor senden

    Voraussetzung: ollama serve + ollama pull moondream
    """
    result_ready   = pyqtSignal(str)   # Sendet Beschreibungstext an UI
    error_occurred = pyqtSignal(str)   # Sendet Fehlermeldung an UI

    def __init__(self, pil_image: "PILImage.Image"):
        """
        Erstellt den Worker mit einer Kopie des zu analysierenden Bildes.

        Parameter:
          pil_image – Das PIL-Bild, das von Moondream beschrieben werden soll.
                      Eine Kopie wird gespeichert damit das Original nicht verändert wird.
        """
        super().__init__()
        self.pil_image = pil_image.copy()

    def run(self):
        """
        Führt die KI-Analyse im Hintergrund-Thread aus.

        Ablauf:
          1. Bild auf 512×512 px verkleinern (schnellere Übertragung)
          2. Als JPEG in Base64 kodieren
          3. HTTP-POST an Ollama API (localhost:11434)
          4. Antworttext per result_ready-Signal an die UI senden
        """
        try:
            img = self.pil_image.copy()
            # Bild für schnellere Übertragung an Ollama verkleinern
            img.thumbnail((512, 512), PILImage.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            # JPEG-Bytes als Base64-String kodieren (Ollama erwartet dieses Format)
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
        except Exception as e:
            self.error_occurred.emit(f"KI-Fehler: {e}")


# ══════════════════════════════════════════════════════════════
#  CROP OVERLAY: Zeichnet Rechteck/Lasso auf dem Bild
# ══════════════════════════════════════════════════════════════
