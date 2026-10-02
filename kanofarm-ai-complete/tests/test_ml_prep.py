import json, sys, tempfile, unittest
from pathlib import Path
from ml.preprocessing.prepare_plantdoc import prepare, norm
from ml.training.build_splits import check_complete
sys.path.insert(0, "scripts")
from model_card_to_sql import to_sql

class Prep(unittest.TestCase):
    def make(self, root):
        for split, cls, n in (("train", "Tomato Early blight leaf", 3), ("test", "Tomato Early blight leaf", 2), ("train", "Corn rust leaf", 2),
                              ("train", "Apple leaf", 2), ("train", "Mystery leaf", 1)):
            d = Path(root) / split / cls; d.mkdir(parents=True, exist_ok=True)
            for i in range(n): (d / f"{i}.jpg").write_bytes(b"x%d%s%s" % (i, split.encode(), cls.encode()))
    def test_prepare_filters_crops_and_reports_unmatched(self):
        with tempfile.TemporaryDirectory() as s, tempfile.TemporaryDirectory() as d:
            self.make(s); r = prepare(Path(s), Path(d), {"tomato"})
            self.assertEqual(r["kept"], {"tomato___early_blight": {"train": 3, "test": 2}})
            self.assertIn("Mystery leaf", r["unmatched_folders"]); self.assertIn("maize", r["crops_skipped"])
            self.assertEqual(json.loads((Path(d) / "label_meta.json").read_text())["tomato___early_blight"]["category"], "disease")
            self.assertFalse((Path(d) / "train" / "maize___rust").exists())
    def test_norm_tolerates_case_and_punctuation(self): self.assertEqual(norm("Bell_pepper  Leaf-Spot"), "bell pepper leaf spot")
    def test_incomplete_splits_are_caught(self):
        with tempfile.TemporaryDirectory() as d:
            for sp in ("train", "val"): (Path(d) / sp / "a").mkdir(parents=True); (Path(d) / sp / "a" / "1.jpg").write_bytes(b"1")
            (Path(d) / "test").mkdir()
            self.assertEqual(check_complete(d), ["a has 0 images in test"])
    def test_drop_and_clean_rebuild(self):
        import subprocess
        with tempfile.TemporaryDirectory() as t:
            for sp, cls, n in (("train", "a", 40), ("test", "a", 5), ("train", "mites", 40)):
                d = Path(t) / "src" / sp / cls; d.mkdir(parents=True)
                for i in range(n): (d / f"{i}.jpg").write_bytes(f"{sp}{cls}{i}".encode())
            base = [sys.executable, "-m", "ml.training.build_splits", "--src", f"{t}/src", "--dst", f"{t}/out", "--presplit"]
            self.assertNotEqual(subprocess.run(base, capture_output=True).returncode, 0)
            self.assertEqual(subprocess.run(base + ["--drop", "mites"], capture_output=True).returncode, 0)
            for sp in ("train", "val", "test"): self.assertEqual([p.name for p in (Path(t) / "out" / sp).iterdir()], ["a"])
    def test_model_card_sql_escapes_and_defaults_unvalidated(self):
        card = {"version": "v1", "architecture": "m", "dataset_names": ["PlantDoc (CC BY 4.0)"], "dataset_version": "2020",
                "trained_at": "2026-09-24T00:00:00+00:00", "classes": ["a", "b"], "metrics": {"accuracy": 0.5, "note": "it's"},
                "notes": "O'Brien"}
        sql = to_sql(card); self.assertIn("false", sql); self.assertIn("O''Brien", sql); self.assertIn("it''s", sql)
        self.assertNotIn("'v1'; drop", sql)
if __name__ == "__main__": unittest.main()

class ClassMismatch(unittest.TestCase):
    def test_none_when_all_splits_match(self):
        from ml.training.class_check import describe_class_mismatch
        self.assertIsNone(describe_class_mismatch("d", ["a", "b"], ["a", "b"], ["a", "b"]))
    def test_names_the_exact_missing_class_and_split(self):
        from ml.training.class_check import describe_class_mismatch
        msg = describe_class_mismatch("data/split", ["a", "b"], ["a", "b"], ["a"])
        self.assertIn("b: train=yes, val=yes, test=MISSING", msg)
        self.assertNotIn("a:", msg)   # 'a' is fine everywhere, so it should not be listed as a problem
        self.assertIn("data/split", msg)   # names the actual path so the fix instructions are concrete
        self.assertIn("Build splits", msg)
    def test_handles_several_classes_missing_from_different_splits(self):
        from ml.training.class_check import describe_class_mismatch
        msg = describe_class_mismatch("d", ["a", "b", "c"], ["a", "c"], ["a", "b"])
        self.assertIn("b: train=yes, val=MISSING, test=yes", msg)
        self.assertIn("c: train=yes, val=yes, test=MISSING", msg)
    def test_order_of_splits_does_not_matter(self):
        from ml.training.class_check import describe_class_mismatch
        self.assertIsNotNone(describe_class_mismatch("d", ["a"], ["a", "b"], ["a"]))
if __name__ == "__main__": unittest.main()
