"""Fine-tune a small pretrained CNN and write model.pt + model_card.json.
Run on a GPU machine (e.g. free Google Colab). NOT executed in the authoring sandbox (no PyTorch/network).
Usage:
  python -m ml.training.build_splits --src data/raw --dst data/split
  python -m ml.training.train --data data/split --out ml/models/v1 --dataset-name "PlantDoc (CC BY 4.0)" --dataset-version 2020-03
"""
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
import torch, torch.nn as nn
from torch.utils.data import DataLoader
from torchvision import datasets, transforms, models
from ml.evaluation.metrics import report

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--dataset-name", required=True); ap.add_argument("--dataset-version", required=True)
    ap.add_argument("--epochs", type=int, default=15); ap.add_argument("--batch", type=int, default=32)
    a = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    norm = transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    train_tf = transforms.Compose([transforms.RandomResizedCrop(224, scale=(0.6, 1.0)), transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(0.3, 0.3, 0.3), transforms.RandomRotation(20), transforms.ToTensor(), norm])   # dust/lighting robustness
    eval_tf = transforms.Compose([transforms.Resize(256), transforms.CenterCrop(224), transforms.ToTensor(), norm])
    tr = datasets.ImageFolder(f"{a.data}/train", train_tf)
    va = datasets.ImageFolder(f"{a.data}/val", eval_tf); te = datasets.ImageFolder(f"{a.data}/test", eval_tf)
    assert tr.classes == va.classes == te.classes, "class lists differ between splits"
    m = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.DEFAULT)
    m.classifier[-1] = nn.Linear(m.classifier[-1].in_features, len(tr.classes)); m.to(dev)
    opt = torch.optim.AdamW(m.parameters(), lr=1e-3, weight_decay=1e-4); loss_fn = nn.CrossEntropyLoss()
    best, best_state = 0.0, None
    for ep in range(a.epochs):
        m.train()
        for x, y in DataLoader(tr, a.batch, shuffle=True, num_workers=2):
            opt.zero_grad(); loss_fn(m(x.to(dev)), y.to(dev)).backward(); opt.step()
        acc = evaluate(m, va, dev, a.batch)["accuracy"]; print(f"epoch {ep+1} val_acc={acc:.4f}")
        if acc > best: best, best_state = acc, {k: v.cpu().clone() for k, v in m.state_dict().items()}
    m.load_state_dict(best_state)
    rep = evaluate(m, te, dev, a.batch, full=True)       # test split touched ONCE, after model selection on val
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    m.eval().cpu(); torch.jit.trace(m, torch.randn(1, 3, 224, 224)).save(str(out / "model.pt"))
    card = {"version": out.name, "architecture": "mobilenet_v3_small (ImageNet-pretrained, fine-tuned)",
            "dataset_names": [a.dataset_name], "dataset_version": a.dataset_version,
            "trained_at": datetime.now(timezone.utc).isoformat(), "classes": tr.classes, "metrics": rep,
            "validated_on_nigerian_field_data": False,
            "notes": "Test metrics come from the same dataset distribution. They do NOT show performance on Nigerian farms."}
    (out / "model_card.json").write_text(json.dumps(card, indent=2)); print(json.dumps({k: rep[k] for k in ("n","accuracy","macro_f1")}))

def evaluate(m, ds, dev, bs, full=False):
    m.eval(); yt, yp = [], []
    with torch.no_grad():
        for x, y in DataLoader(ds, bs, num_workers=2):
            yp += m(x.to(dev)).argmax(1).cpu().tolist(); yt += y.tolist()
    names = ds.classes
    return report([names[i] for i in yt], [names[i] for i in yp], names)

if __name__ == "__main__":
    main()
