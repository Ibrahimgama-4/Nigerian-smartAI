"""Convert the trained TorchScript model (plant_doctor_v1.zip or a model folder) to ONNX and build a ready-to-deploy
folder for the free Render host. Run in Colab from the cloned repo:
    pip install onnx onnxruntime
    python -m ml.training.export_onnx --zip plant_doctor_v1.zip --out out_onnx
It REFUSES to finish unless ONNX output matches the original PyTorch pipeline on test images (checks the model AND the
numpy preprocessing used by the server)."""
import argparse, json, shutil, tempfile, zipfile
from pathlib import Path

SERVE = Path(__file__).resolve().parent.parent / "serving" / "render"
TOL = 2e-3

def find_model_dir(root: Path) -> Path:
    hits = sorted(root.rglob("model.pt"))
    if not hits: raise SystemExit(f"No model.pt found under {root}")
    return hits[0].parent

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--zip"); g.add_argument("--model-dir")
    ap.add_argument("--out", default="out_onnx"); a = ap.parse_args()
    import numpy as np, torch, onnxruntime as ort
    from PIL import Image
    from torchvision import transforms
    from ml.serving.render.inference import preprocess, softmax

    tmp = None
    if a.zip:
        tmp = tempfile.mkdtemp(); zipfile.ZipFile(a.zip).extractall(tmp); src = find_model_dir(Path(tmp))
    else: src = find_model_dir(Path(a.model_dir))
    card = json.loads((src / "model_card.json").read_text(encoding="utf-8"))
    meta = json.loads((src / "label_meta.json").read_text(encoding="utf-8"))
    out = Path(a.out); mdl = out / "model"; mdl.mkdir(parents=True, exist_ok=True)

    m = torch.jit.load(str(src / "model.pt"), map_location="cpu").eval()
    kw = dict(input_names=["input"], output_names=["logits"], opset_version=17,
              dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}})
    path = str(mdl / "model.onnx")
    try: torch.onnx.export(m, torch.randn(1, 3, 224, 224), path, dynamo=False, **kw)
    except TypeError: torch.onnx.export(m, torch.randn(1, 3, 224, 224), path, **kw)

    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"]); name = sess.get_inputs()[0].name
    tf = transforms.Compose([transforms.Resize(256), transforms.CenterCrop(224), transforms.ToTensor(),
                             transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    rng = np.random.default_rng(0); worst = 0.0
    for size in ((640, 480), (300, 500), (224, 224), (1024, 768), (256, 256)):
        img = Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8))
        with torch.no_grad(): ref = torch.softmax(m(tf(img).unsqueeze(0)), 1)[0].numpy()
        got = softmax(sess.run(None, {name: preprocess(img)})[0])
        worst = max(worst, float(np.abs(ref - got).max()))
    print(f"max probability difference vs PyTorch: {worst:.2e} (limit {TOL:.0e})")
    if worst > TOL: raise SystemExit("ONNX does NOT match the PyTorch model. Do not deploy this. Send this output to the developer.")

    classes = set(card["classes"])
    (mdl / "model_card.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    (mdl / "label_meta.json").write_text(json.dumps({k: v for k, v in meta.items() if k in classes}, indent=2), encoding="utf-8")
    for f in ("Dockerfile", "server.py", "inference.py", "requirements.txt"): shutil.copy2(SERVE / f, out / f)
    z = shutil.make_archive("plant_doctor_onnx", "zip", out)
    print("classes:", len(card["classes"]), "| model.onnx MB:", round(Path(path).stat().st_size / 1e6, 1))
    print("READY:", z)
    if tmp: shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__": main()
