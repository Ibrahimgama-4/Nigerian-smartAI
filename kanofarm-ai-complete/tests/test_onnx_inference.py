import base64, io, unittest
import numpy as np
from PIL import Image
from ml.serving.render.inference import preprocess, softmax, decode_image, Predictor, MEAN, STD

def b64(img, fmt="JPEG"):
    b = io.BytesIO(); img.save(b, fmt); return base64.b64encode(b.getvalue()).decode()

class Inp:
    name = "input"
class StubSession:
    """Stands in for onnxruntime: returns fixed logits and records the input it received."""
    def __init__(self, logits): self.logits, self.seen = logits, None
    def get_inputs(self): return [Inp()]
    def run(self, _outs, feed): self.seen = feed["input"]; return [np.array([self.logits], dtype=np.float32)]

CARD = {"version": "v1", "classes": ["tomato___early_blight", "tomato___healthy", "potato___healthy"]}
META = {"tomato___early_blight": {"crop": "tomato"}, "tomato___healthy": {"crop": "tomato"}, "potato___healthy": {"crop": "potato"},
        "tomato___two_spotted_spider_mites": {"crop": "tomato"}}

class Pre(unittest.TestCase):
    def test_shape_dtype_for_landscape_portrait_and_tiny(self):
        for size in ((640, 480), (300, 500), (224, 224), (100, 100), (2000, 300)):
            x = preprocess(Image.new("RGB", size, (10, 200, 30)))
            self.assertEqual(x.shape, (1, 3, 224, 224)); self.assertEqual(x.dtype, np.float32)
    def test_solid_colour_is_normalised_like_training(self):
        x = preprocess(Image.new("RGB", (400, 300), (255, 0, 128)))
        want = (np.array([255, 0, 128], dtype=np.float32) / 255 - MEAN) / STD
        self.assertTrue(np.allclose(x[0, :, 100, 100], want, atol=1e-5))
    def test_grayscale_and_rgba_become_rgb(self):
        self.assertEqual(preprocess(Image.new("L", (300, 300), 90)).shape, (1, 3, 224, 224))
        self.assertEqual(preprocess(Image.new("RGBA", (300, 300), (1, 2, 3, 4))).shape, (1, 3, 224, 224))
    def test_centre_crop_keeps_the_middle(self):
        a = np.zeros((300, 600, 3), dtype=np.uint8); a[:, 200:400] = 255      # white middle band, black sides
        x = preprocess(Image.fromarray(a))
        self.assertGreater(x[0, 0, 112, 112], 1.5); self.assertGreater(x[0, 0, 112, 40], 1.5)   # inside the band (white)
        self.assertLess(x[0, 0, 112, 5], -1.5)                                                  # left edge of the crop is still black
    def test_softmax_sums_to_one_and_survives_huge_logits(self):
        p = softmax([1000.0, 999.0, -1000.0]); self.assertAlmostEqual(float(p.sum()), 1.0); self.assertFalse(np.isnan(p).any())

class Dec(unittest.TestCase):
    def test_rejects_bad_input(self):
        for bad in (None, "", 123, "!!!notbase64!!!", base64.b64encode(b"not an image").decode()):
            with self.assertRaises(ValueError): decode_image(bad)
    def test_rejects_oversized(self):
        with self.assertRaises(ValueError): decode_image("A" * 8_000_001)
    def test_accepts_png_and_jpeg(self):
        for fmt in ("PNG", "JPEG"): self.assertEqual(decode_image(b64(Image.new("RGB", (30, 30), "red"), fmt)).size, (30, 30))

class Pred(unittest.TestCase):
    def test_response_matches_what_the_app_validates(self):
        from kanofarm.services.model_client import validate_prediction
        s = StubSession([2.0, 0.5, -1.0]); pr = Predictor(s, CARD, META)
        out = pr.predict(b64(Image.new("RGB", (320, 240), "green")))
        v = validate_prediction(out)                                  # the app's own validator must accept it
        self.assertEqual(v["model_version"], "v1"); self.assertAlmostEqual(sum(v["probs"].values()), 1.0, places=6)
        self.assertEqual(max(v["probs"], key=v["probs"].get), "tomato___early_blight")
        self.assertEqual(s.seen.shape, (1, 3, 224, 224))
    def test_label_meta_limited_to_model_classes(self):
        pr = Predictor(StubSession([0, 0, 0]), CARD, META)
        self.assertNotIn("tomato___two_spotted_spider_mites", pr.meta); self.assertEqual(len(pr.meta), 3)
    def test_class_count_mismatch_is_an_error_not_a_guess(self):
        pr = Predictor(StubSession([1.0, 2.0]), CARD, META)
        with self.assertRaises(RuntimeError): pr.predict(b64(Image.new("RGB", (50, 50), "blue")))
if __name__ == "__main__": unittest.main()
