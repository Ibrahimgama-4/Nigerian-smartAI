import unittest
from ml.inference.confidence import gate
class T(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(gate({"a": .9, "b": .05})["level"], "high")
        self.assertEqual(gate({"a": .7, "b": .2})["level"], "moderate")
        self.assertEqual(gate({"a": .5, "b": .3})["level"], "low")
    def test_ambiguous_top_two_is_low_even_if_top_high(self):
        r = gate({"a": .86, "b": .80}); self.assertEqual(r["level"], "low"); self.assertFalse(r["show_diagnosis"])
    def test_empty(self): self.assertFalse(gate({})["show_diagnosis"])
if __name__ == "__main__": unittest.main()
