"""Guards against the classic 'code writes a column the migration never created' bug, which the endpoint tests (they use a
fake database) cannot catch. Reads the real migration files and compares column names with what the API sends."""
import re
import unittest
from datetime import date
from pathlib import Path

from kanofarm.services import validation as v

MIGRATIONS = Path(__file__).resolve().parent.parent / "database" / "migrations"
SQL = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS.glob("*.sql")))
NOT_COLUMNS = {"constraint", "unique", "check", "primary", "foreign", "exclude"}


def columns(table: str) -> set:
    """Columns from 'create table <table> (...)' plus any 'alter table <table> add column [if not exists] <col>'."""
    m = re.search(rf"create table {table}\s*\((.*?)\n\);", SQL, re.S | re.I)
    assert m, f"table {table} not found in migrations"
    cols = set()
    body = re.sub(r"--[^\n]*", "", m.group(1))               # drop comments, then split on top-level commas (several columns can share a line)
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += ch == "("; depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur); cur = ""
        else:
            cur += ch
    parts.append(cur)
    for part in parts:
        first = re.split(r"[\s(]", part.strip(), maxsplit=1)[0].lower()
        if first and first not in NOT_COLUMNS and re.fullmatch(r"[a-z_][a-z0-9_]*", first):
            cols.add(first)
    for a in re.finditer(rf"alter table {table}\s+add column(?: if not exists)?\s+([a-z_][a-z0-9_]*)", SQL, re.I):
        cols.add(a.group(1).lower())
    return cols


class PayloadsMatchSchema(unittest.TestCase):
    def check(self, table, payload, extra=()):
        missing = (set(payload) | set(extra)) - columns(table)
        self.assertFalse(missing, f"API sends columns that {table} does not have: {sorted(missing)}")

    def test_alert_prefs(self):
        self.check("alert_prefs", v.alert_prefs_payload({}), extra=("user_id",))

    def test_push_subscription(self):
        sub = v.push_subscription_payload({"endpoint": "https://push.example.com/" + "a" * 40, "keys": {"p256dh": "p" * 40, "auth": "a" * 16},
                                           "p256dh": "p" * 40, "auth": "a" * 16})
        self.check("push_subscriptions", sub, extra=("user_id",))

    def test_finance_entry(self):
        p = v.finance_payload({"kind": "expense", "category": "seed", "amount_ngn": 1000, "entry_date": date.today().isoformat(),
                               "client_id": "11111111-1111-4111-8111-111111111111"})
        self.check("farm_finance_entries", p, extra=("farm_id",))

    def test_observation(self):
        p = v.observation_payload({"kind": "note", "observed_on": date.today().isoformat(), "text": "x",
                                   "client_id": "11111111-1111-4111-8111-111111111111"})
        self.check("farm_observations", p, extra=("farm_id",))

    def test_soil_record(self):
        self.check("soil_records", v.soil_payload({"ph": 6, "p_bray1_ppm": 1, "k_exch_cmolkg": 0.1, "lab_name": "x", "recorded_on": date.today().isoformat()}), extra=("farm_id",))

    def test_input_report(self):
        r = v.input_report_payload({"kind": "seed", "product_name": "Maize seed", "problem": "Would not germinate"})
        self.check("input_reports", r, extra=("user_id", "status"))
        self.check("input_reports", v.input_report_review_payload({"status": "closed"}), extra=("reviewed_by", "reviewed_at"))

    def test_fertilizer_recommendation(self):
        p = v.fertilizer_reco_payload({"zone": "all", "crop_slug": "maize", "n_kg_ha": 1, "source_name": "Some source", "last_verified": "2026-01-01", "expires_at": "2027-01-01"})
        self.check("fertilizer_recommendations", p, extra=("entered_by",))

    def test_planting_plan_tasks_and_farm_crop_inputs(self):
        from datetime import date, timedelta
        from kanofarm.services import planting_plan as pp
        plan = pp.build_plan(crop_slug="maize", planting=date.today(), today=date.today())
        for r in pp.task_rows(plan):
            self.check("farm_plan_tasks", r, extra=("farm_id", "farm_crop_id"))
        self.check("farm_crops", {"crop_id": 1, "variety": 1, "planting_date": 1, "water_source": 1, "savanna_zone": 1, "maturity_group": 1, "maturity_days": 1}, extra=("farm_id",))

    def test_market_listing(self):
        # market_payload needs a real decoded image, so compare the listing columns it produces by name instead
        sample = {"kind", "title", "category", "product", "description", "quantity", "quantity_unit", "price_ngn", "price_unit",
                  "negotiable", "state", "lga", "contact_phone", "seller_name"}
        self.assertFalse(sample - columns("market_listings"))

    def test_cron_functions_exist_for_every_rpc_the_api_calls(self):
        api = (Path(__file__).resolve().parent.parent / "kanofarm" / "api" / "main.py").read_text(encoding="utf-8")
        called = set(re.findall(r'rpc\(\s*(?:None|jwt),\s*"([a-z_]+)"', api))
        defined = set(re.findall(r"create or replace function ([a-z_]+)\(", SQL, re.I))
        self.assertFalse(called - defined, f"API calls database functions that no migration defines: {sorted(called - defined)}")


if __name__ == "__main__":
    unittest.main()
