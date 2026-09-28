import tempfile, unittest
from pathlib import Path
from ml.evaluation.metrics import report, confusion_matrix
from ml.preprocessing.split import assign_split
from ml.datasets.validate import validate_folder

class Metrics(unittest.TestCase):
    def test_report(self):
        r = report(["a","a","b","b"], ["a","b","b","b"], ["a","b"])
        self.assertEqual(r["accuracy"], 0.75)
        self.assertEqual(r["per_class"]["a"]["recall"], 0.5); self.assertEqual(r["per_class"]["b"]["precision"], round(2/3, 4))
        self.assertEqual(r["confusion_matrix"], [[1,1],[0,2]])
    def test_empty_raises(self):
        with self.assertRaises(ValueError): report([], [], ["a"])

class Split(unittest.TestCase):
    def test_deterministic_and_dupes_together(self):
        self.assertEqual(assign_split(b"x"), assign_split(b"x"))
    def test_roughly_balanced(self):
        from collections import Counter
        c = Counter(assign_split(str(i).encode()) for i in range(3000))
        self.assertTrue(2000 < c["train"] < 2200); self.assertTrue(c["val"] > 300 and c["test"] > 300)

class Dataset(unittest.TestCase):
    def test_flags_small_classes_and_cross_class_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            for cls, n in (("a", 3), ("b", 3)):
                (Path(d)/cls).mkdir()
                for i in range(n): (Path(d)/cls/f"{i}.jpg").write_bytes(b"img%d" % i)
            r = validate_folder(d, min_per_class=5)
            self.assertFalse(r["ok"]); self.assertEqual(sorted(r["classes_below_minimum"]), ["a", "b"])
            self.assertTrue(r["cross_class_duplicates"])   # same bytes in both classes
if __name__ == "__main__": unittest.main()
