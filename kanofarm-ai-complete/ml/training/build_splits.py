"""Create dst/{train,val,test}/<label>/ .
 default:    --src root/<label>/<img>    content-hash split (duplicates never straddle splits)
 --presplit: --src has train/ and test/ (e.g. PlantDoc's official split). Test stays official; val is carved from train.
CAUTION: PlantDoc crops from the SAME original photo can land in different splits, which inflates scores. Treat test
accuracy as optimistic and prefer the official test split, which the authors kept separate."""
import argparse, shutil
from pathlib import Path
from ml.preprocessing.split import assign_split
from ml.datasets.validate import validate_folder, EXT

def copy_all(pairs, dst):
    for f, split, label in pairs:
        d = Path(dst) / split / label; d.mkdir(parents=True, exist_ok=True); shutil.copy2(f, d / f.name)

def check_complete(dst, min_per_split=1):
    labels = sorted({p.name for s in ("train", "val", "test") for p in (Path(dst) / s).glob("*") if p.is_dir()})
    problems = []
    for s in ("train", "val", "test"):
        for l in labels:
            n = len([x for x in (Path(dst) / s / l).glob("*") if x.suffix.lower() in EXT]) if (Path(dst) / s / l).exists() else 0
            if n < min_per_split: problems.append(f"{l} has {n} images in {s}")
    return problems

def clean_dst(dst):
    """Remove old train/val/test folders so a re-run never mixes in stale data from an earlier attempt."""
    for s in ("train", "val", "test"):
        if (Path(dst) / s).exists(): shutil.rmtree(Path(dst) / s)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--src", required=True); ap.add_argument("--dst", required=True)
    ap.add_argument("--presplit", action="store_true")
    ap.add_argument("--drop", default="", help="comma-separated class folder names to leave out, e.g. tomato___two_spotted_spider_mites")
    a = ap.parse_args(); src = Path(a.src); pairs = []; drop = {c.strip() for c in a.drop.split(",") if c.strip()}
    if a.presplit:
        for f in (src / "train").rglob("*"):
            if f.suffix.lower() in EXT: pairs.append((f, "val" if assign_split(f.read_bytes(), ratios=(0.85, 0.15, 0.0)) != "train" else "train", f.parent.name))
        for f in (src / "test").rglob("*"):
            if f.suffix.lower() in EXT: pairs.append((f, "test", f.parent.name))
    else:
        rep = validate_folder(src); print(rep)
        if not rep["ok"]: raise SystemExit("Dataset validation failed; fix the issues above before training.")
        for cls in src.iterdir():
            if cls.is_dir():
                for f in cls.iterdir():
                    if f.suffix.lower() in EXT: pairs.append((f, assign_split(f.read_bytes()), cls.name))
    pairs = [p for p in pairs if p[2] not in drop]
    if drop: print("dropped classes:", sorted(drop))
    clean_dst(a.dst); copy_all(pairs, a.dst)
    bad = check_complete(a.dst)
    if bad: raise SystemExit("Every class needs images in train, val and test. Add data or drop the class:\n  " + "\n  ".join(bad))
    print("splits ready:", a.dst)
main() if __name__ == "__main__" else None
