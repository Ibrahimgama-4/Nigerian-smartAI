"""Plant Doctor inference core for the ONNX host (Render etc.). No web framework in here, so it is unit-testable.
Preprocessing reproduces the training eval transform exactly:
  Resize(256) on the shorter side (bilinear) -> CenterCrop(224) -> /255 -> ImageNet mean/std normalisation."""
import base64, binascii, io, json
from pathlib import Path
import numpy as np
from PIL import Image

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
MAX_B64_CHARS = 8_000_000          # ~6 MB of image; the app already downsizes photos before sending


def preprocess(img: Image.Image) -> np.ndarray:
    """PIL image -> float32 array of shape (1, 3, 224, 224)."""
    img = img.convert("RGB")
    w, h = img.size
    if w <= h: nw, nh = 256, int(256 * h / w)
    else: nw, nh = int(256 * w / h), 256
    img = img.resize((nw, nh), Image.BILINEAR)
    left, top = int(round((nw - 224) / 2.0)), int(round((nh - 224) / 2.0))
    img = img.crop((left, top, left + 224, top + 224))
    x = np.asarray(img, dtype=np.float32) / 255.0
    x = (x - MEAN) / STD
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)


def softmax(logits) -> np.ndarray:
    z = np.asarray(logits, dtype=np.float64).reshape(-1)
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


def decode_image(image_b64) -> Image.Image:
    if not isinstance(image_b64, str) or not image_b64: raise ValueError("image_b64 is required")
    if len(image_b64) > MAX_B64_CHARS: raise ValueError("image too large")
    try: raw = base64.b64decode(image_b64, validate=False)
    except (binascii.Error, ValueError) as e: raise ValueError("image_b64 is not valid base64") from e
    try:
        img = Image.open(io.BytesIO(raw)); img.load()
    except Exception as e: raise ValueError("not a readable image") from e
    return img


class Predictor:
    def __init__(self, session, card: dict, label_meta: dict):
        self.session = session
        self.classes = list(card["classes"])
        self.version = card["version"]
        # the notebook copies the label map for every class PlantDoc had; keep only classes the model really has
        self.meta = {k: v for k, v in label_meta.items() if k in set(self.classes)}
        self.input_name = session.get_inputs()[0].name

    @classmethod
    def load(cls, model_dir):
        import onnxruntime as ort
        d = Path(model_dir)
        so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
        sess = ort.InferenceSession(str(d / "model.onnx"), so, providers=["CPUExecutionProvider"])
        card = json.loads((d / "model_card.json").read_text(encoding="utf-8"))
        meta = json.loads((d / "label_meta.json").read_text(encoding="utf-8"))
        return cls(sess, card, meta)

    def predict(self, image_b64) -> dict:
        x = preprocess(decode_image(image_b64))
        logits = self.session.run(None, {self.input_name: x})[0]
        p = softmax(logits)
        if p.shape[0] != len(self.classes): raise RuntimeError("model output size does not match model_card classes")
        return {"model_version": self.version, "probs": {c: float(v) for c, v in zip(self.classes, p)}, "label_meta": self.meta}
