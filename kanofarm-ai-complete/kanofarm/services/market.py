"""Farm Market: pure helpers (no framework, unit-tested). Nothing here touches the network."""
import re
import urllib.parse

CATEGORIES = {
    "produce": "Fresh produce & grains", "livestock": "Livestock & poultry", "seeds_inputs": "Seeds & farm inputs",
    "equipment": "Equipment & tools", "processed": "Processed foods", "other": "Other",
}
KINDS = {"for_sale": "For sale", "wanted": "Wanted"}
UNITS = ("kg", "bag", "tonne", "crate", "basket", "bunch", "piece", "litre", "tray", "other")
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 600_000        # per photo after the phone compresses it (Vercel request bodies are limited to a few MB)
MAX_ACTIVE_PER_USER = 20
LISTING_DAYS = 30
REPORTS_TO_HIDE = 3              # enforced in SQL (report_listing); kept here for docs/tests
REPORTS_TO_HIDE_VERIFIED = 5     # a verified seller's listing needs more reports (also in SQL)
VERIFY_MAX_MONTHS = 12           # a badge lasts at most this long, then expires

PROOF_KINDS = {
    "cooperative": "Member of a farmers' cooperative",
    "extension_officer": "An extension officer can confirm me",
    "farm_visit": "An administrator can visit my farm or stall",
    "market_association": "Member of a market or traders' association",
    "other": "Other evidence (describe it)",
}
BADGE_MEANING = ("Verified seller means an administrator phoned this seller on the number shown, the seller confirmed it is theirs, "
                 "and the administrator saw one piece of supporting evidence. It lasts at most 12 months. It does NOT guarantee the "
                 "goods, the price or a safe deal: always inspect before you pay.")

PUBLIC_COLUMNS = ("id,kind,title,category,product,description,quantity,quantity_unit,price_ngn,price_unit,negotiable,"
                  "state,lga,seller_name,seller_verified,contact_phone,image_paths,status,created_at,expires_at")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

def strip_control(text: str) -> str:
    return _CONTROL.sub("", text)

def normalize_phone(raw):
    """Nigerian mobile numbers only -> '+234XXXXXXXXXX', or None. Accepts 0803 123 4567, +234 803..., 234803..."""
    if not isinstance(raw, str): return None
    digits = re.sub(r"[\s\-().]", "", raw)
    if digits.startswith("+234"): rest = digits[4:]
    elif digits.startswith("234"): rest = digits[3:]
    elif digits.startswith("0"): rest = digits[1:]
    else: return None
    return "+234" + rest if re.fullmatch(r"[789]\d{9}", rest) else None

def display_phone(e164: str) -> str:
    rest = e164[4:]
    return f"0{rest[:3]} {rest[3:6]} {rest[6:]}"

def whatsapp_url(e164: str) -> str:
    return "https://wa.me/234" + e164[4:]

def public_image_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/storage/v1/object/public/market/{urllib.parse.quote(path)}"

def clean_search_term(q) -> str:
    """Keeps letters, digits, spaces and hyphens only, so a search can never inject PostgREST filter syntax."""
    return re.sub(r"[^\w\s-]", "", q or "").strip()[:40]

def search_filter(term: str):
    if not term: return None
    return f"(title.ilike.*{term}*,product.ilike.*{term}*,description.ilike.*{term}*)"

def listing_view(row: dict, base_url: str) -> dict:
    """What the browser gets. Deliberately never includes owner_id or any GPS: the seller's public identity is the
    display name and the phone number they chose to publish for this listing."""
    phone = row.get("contact_phone") or ""
    ok = bool(re.fullmatch(r"\+234\d{10}", phone))
    return {
        "id": row.get("id"), "kind": row.get("kind"), "title": row.get("title"), "category": row.get("category"),
        "category_label": CATEGORIES.get(row.get("category"), "Other"), "product": row.get("product"),
        "description": row.get("description"), "quantity": row.get("quantity"), "quantity_unit": row.get("quantity_unit"),
        "price_ngn": row.get("price_ngn"), "price_unit": row.get("price_unit"), "negotiable": bool(row.get("negotiable")),
        "state": row.get("state"), "lga": row.get("lga"), "seller_name": row.get("seller_name"),
        "seller_verified": bool(row.get("seller_verified")),
        "status": row.get("status"), "created_at": row.get("created_at"), "expires_at": row.get("expires_at"),
        "images": [public_image_url(base_url, p) for p in (row.get("image_paths") or [])],
        "contact": ({"display": display_phone(phone), "call": "tel:" + phone, "whatsapp": whatsapp_url(phone)} if ok else None),
    }

def storage_paths(row: dict) -> list:
    return [p for p in (row.get("image_paths") or []) if isinstance(p, str)]
