"""Turn the downloaded PlantDoc CLASSIFICATION folders into  dst/{train,test}/<crop>___<condition>/  and write
label_meta.json for the serving container. Only crops in --crops are kept.
Usage: python -m ml.preprocessing.prepare_plantdoc --src PlantDoc-Dataset --dst data/plantdoc --crops tomato,potato,pepper
The folder names in your download may differ slightly; unmatched folders are REPORTED, never guessed."""
import argparse, json, re, shutil
from pathlib import Path

MAP_FILE = Path(__file__).resolve().parent.parent / "plantdoc_label_map.json"
EXT = {".jpg", ".jpeg", ".png", ".webp"}
norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
slug = lambda s: re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

def label_of(meta): return f"{meta['crop']}___{slug(meta['condition'])}"

def build_lookup(classes: dict) -> dict:
    return {norm(k): v for k, v in classes.items()}

def prepare(src: Path, dst: Path, crops: set, mapping=None) -> dict:
    mapping = mapping or json.loads(MAP_FILE.read_text(encoding="utf-8"))
    lookup, kept, unmatched, skipped = build_lookup(mapping["classes"]), {}, set(), set()
    for f in src.rglob("*"):
        if f.suffix.lower() not in EXT or not f.is_file(): continue
        rel = f.relative_to(src).parts
        split = "test" if any(p.lower() in ("test", "val", "valid") for p in rel[:-1]) else "train"
        meta = lookup.get(norm(f.parent.name))
        if meta is None: unmatched.add(f.parent.name); continue
        if meta["crop"] not in crops: skipped.add(meta["crop"]); continue
        lab = label_of(meta); out = dst / split / lab; out.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out / f.name); kept.setdefault(lab, {"train": 0, "test": 0})[split] += 1
    label_meta = {label_of(m): {"crop": m["crop"], "condition": m["condition"], "category": m["category"]}
                  for m in mapping["classes"].values() if label_of(m) in kept}
    (dst / "label_meta.json").write_text(json.dumps(label_meta, indent=2), encoding="utf-8")
    return {"kept": kept, "unmatched_folders": sorted(unmatched), "crops_skipped": sorted(skipped)}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--src", required=True); ap.add_argument("--dst", required=True)
    ap.add_argument("--crops", default="tomato,potato,pepper"); a = ap.parse_args()
    crops = {c.strip() for c in a.crops.split(",") if c.strip()}
    warn = json.loads(MAP_FILE.read_text())["_warnings"]
    for c in crops & set(warn): print(f"WARNING [{c}]: {warn[c]}")
    print(json.dumps(prepare(Path(a.src), Path(a.dst), crops), indent=2))
