"""Classification metrics in plain Python (no numpy) so evaluation is auditable and testable."""
from collections import Counter

def confusion_matrix(y_true, y_pred, labels):
    idx = {l: i for i, l in enumerate(labels)}
    m = [[0] * len(labels) for _ in labels]
    for t, p in zip(y_true, y_pred):
        m[idx[t]][idx[p]] += 1
    return m

def per_class(y_true, y_pred, labels):
    out = {}
    for l in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == l and p == l)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != l and p == l)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == l and p != l)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[l] = {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4), "support": tp + fn}
    return out

def report(y_true, y_pred, labels):
    if len(y_true) != len(y_pred) or not y_true: raise ValueError("empty or mismatched predictions")
    pc = per_class(y_true, y_pred, labels)
    acc = sum(1 for t, p in zip(y_true, y_pred) if t == p) / len(y_true)
    present = [l for l in labels if pc[l]["support"] > 0]
    macro = lambda k: round(sum(pc[l][k] for l in present) / len(present), 4) if present else 0.0
    return {"n": len(y_true), "accuracy": round(acc, 4), "macro_precision": macro("precision"),
            "macro_recall": macro("recall"), "macro_f1": macro("f1"),
            "per_class": pc, "confusion_matrix": confusion_matrix(y_true, y_pred, labels), "labels": list(labels),
            "class_support": dict(Counter(y_true))}
