"""Pure logic (no torch import) so it's testable without a GPU/torch environment.
Used by train.py to turn a bare "classes differ between splits" failure into an actionable message."""

def describe_class_mismatch(data_path: str, train_classes: list, val_classes: list, test_classes: list):
    """Returns None if all three splits have identical class sets, else a human-readable, actionable message
    naming exactly which class is missing from which split."""
    tr, va, te = set(train_classes), set(val_classes), set(test_classes)
    if tr == va == te:
        return None
    all_classes = sorted(tr | va | te)
    lines = [
        f"{c}: train={'yes' if c in tr else 'MISSING'}, val={'yes' if c in va else 'MISSING'}, test={'yes' if c in te else 'MISSING'}"
        for c in all_classes if not (c in tr and c in va and c in te)
    ]
    return (
        "Training data is incomplete: these classes do not appear in all three of train/val/test:\n  "
        + "\n  ".join(lines) +
        f"\n\nThis almost always means the 'Build splits' step was skipped, failed, or ran on stale data from "
        f"an earlier attempt. In Colab: re-run the 'Prepare data' cell, then 'Build splits', and confirm it "
        f"prints 'splits ready: {data_path}' with NO 'Every class needs images...' warning, before re-running "
        f"this Train cell."
    )
