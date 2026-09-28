"""Deterministic train/val/test split keyed on IMAGE CONTENT HASH, so byte-identical duplicates always land
in the same split (prevents train/test leakage). Near-duplicates (same plant, different photo) still need
grouping by source plant if the dataset provides that."""
import hashlib

def assign_split(content: bytes, seed: str = "kanofarm-v1", ratios=(0.7, 0.15, 0.15)) -> str:
    h = int(hashlib.sha256(seed.encode() + hashlib.sha256(content).digest()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if h < ratios[0]: return "train"
    if h < ratios[0] + ratios[1]: return "val"
    return "test"
