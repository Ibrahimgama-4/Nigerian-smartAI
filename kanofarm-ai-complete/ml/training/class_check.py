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
        f"\n\nTwo common causes: (1) the dataset genuinely has no images of that class in that split (PlantDoc's "
        f"official test split lacks some classes) -> add the class to --drop in the 'Build splits' cell; or (2) 'Build splits' "
        f"was skipped or failed earlier (look for 'Every class needs images...') -> re-run 'Prepare data', then "
        f"'Build splits', and confirm it prints 'splits ready: {data_path}' before re-running Train."
    )
