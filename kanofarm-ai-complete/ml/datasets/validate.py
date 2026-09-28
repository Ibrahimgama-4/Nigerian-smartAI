"""Validate an image-folder dataset: root/<label>/<image>. Reports class balance, tiny classes,
unreadable extensions and cross-class duplicate images (a labelling red flag)."""
import hashlib
from pathlib import Path
EXT = {".jpg", ".jpeg", ".png", ".webp"}

def validate_folder(root, min_per_class=30) -> dict:
    root = Path(root); counts, bad, seen, dup = {}, [], {}, []
    for cls in sorted(p for p in root.iterdir() if p.is_dir()):
        n = 0
        for f in cls.iterdir():
            if f.suffix.lower() not in EXT: bad.append(str(f)); continue
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            if h in seen and seen[h] != cls.name: dup.append({"file": str(f), "also_in": seen[h]})
            seen.setdefault(h, cls.name); n += 1
        counts[cls.name] = n
    small = [c for c, n in counts.items() if n < min_per_class]
    return {"classes": counts, "total": sum(counts.values()), "classes_below_minimum": small,
            "unsupported_files": bad, "cross_class_duplicates": dup,
            "ok": not small and not dup and not bad and bool(counts)}
