"""Print the SQL that registers a trained model in the model_versions table.
Usage: python scripts/model_card_to_sql.py ml/models/v1/model_card.json > register.sql   (then run it in Supabase)"""
import json, sys

def q(s): return "'" + str(s).replace("'", "''") + "'"
def arr(xs): return "ARRAY[" + ",".join(q(x) for x in xs) + "]::text[]"

def to_sql(card: dict) -> str:
    return ("insert into model_versions (version, architecture, dataset_names, dataset_version, trained_at, classes, metrics, "
            "validated_on_nigerian_field_data, notes) values (" + ", ".join([
        q(card["version"]), q(card["architecture"]), arr(card["dataset_names"]), q(card["dataset_version"]), q(card["trained_at"]),
        arr(card["classes"]), q(json.dumps(card["metrics"])) + "::jsonb",
        "true" if card.get("validated_on_nigerian_field_data") else "false", q(card.get("notes", ""))]) + ");")

if __name__ == "__main__":
    print(to_sql(json.load(open(sys.argv[1], encoding="utf-8"))))
