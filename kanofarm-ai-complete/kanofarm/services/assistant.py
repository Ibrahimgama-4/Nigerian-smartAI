"""AI assistant grounded in the farmer's context. Supports two interchangeable providers, chosen by
ASSISTANT_PROVIDER: "anthropic" (paid) or "groq" (has a genuine free tier, no card required, as of
writing roughly 30 requests/minute and ~1,000/day on free Llama models -- verify current limits at
console.groq.com/settings/limits before relying on them). Both are billed/rate-limited to the site
owner's key, never the farmer's.
Stateless: the client resends recent history each turn; nothing is stored server-side."""
import json, socket, urllib.error, urllib.request
from typing import Callable, Optional

ASSISTANT_MODULE_VERSION = "model-lister-v1"   # bump this whenever this file changes meaningfully

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_TOKENS = 700
MAX_HISTORY_MESSAGES = 16     # ~8 exchanges; keeps requests small and predictable to bill/rate-limit

DEFAULT_MODEL = {"anthropic": "claude-haiku-4-5-20251001", "groq": "openai/gpt-oss-120b"}

SYSTEM = """You are the KanoFarm AI assistant for smallholder farmers across Nigeria.
Rules:
- Use simple, practical language and short answers (a few sentences, or a short numbered list for steps).
- Farming conditions, seasons and common crops differ a lot between northern and southern Nigeria (and between zones such as North West, North East, North Central, South West, South East and South South). Use the farmer's FARM CONTEXT (state, LGA, ward) to give area-appropriate answers, and say plainly when you are unsure how something applies to their specific area.
- If the farmer writes in Hausa, Yoruba, Igbo or Nigerian Pidgin, answer in that language only if you are confident; otherwise answer simply in English and say that full support for that language is still limited.
- Use the FARM CONTEXT provided (weather advisories, crop, days after planting). If information you would need is missing from the context, say so plainly instead of guessing.
- Never invent weather figures, statistics, prevalence rates, or specific local agronomic numbers you were not given.
- Never recommend a specific pesticide product, brand, dose, or registration number. Suggest integrated pest management first: prevention, cultural, mechanical, then biological control. Say chemical control needs a registered product, verified with an extension officer, following the product label.
- You cannot diagnose a plant from a text description alone. Give the 2-3 most likely possible causes, how to start telling them apart, and recommend using the app's Scan Plant feature or an extension officer for a confirmed diagnosis.
- Be honest about uncertainty. You do not replace qualified agricultural extension professionals.
- Keep replies focused on farming in Nigeria. If asked something unrelated and harmless, answer briefly and steer back."""

class AssistantError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind   # auth | forbidden | rate_limit | overloaded | bad_request | network | timeout | empty | server


def default_model(provider: str) -> str:
    return DEFAULT_MODEL.get(provider, DEFAULT_MODEL["anthropic"])


def build_context(profile: dict, farm: Optional[dict], crops: list, advisories: list) -> str:
    lines = [f"Language preference: {profile.get('language', 'en')}"]
    if farm:
        lines.append(f"Farm state: {farm.get('state') or 'unknown'}, LGA={farm.get('lga') or 'unknown'}, ward={farm.get('ward') or 'unknown'}")
    else:
        lines.append("No farm selected: weather and crop context unavailable.")
    for c in crops:
        lines.append(f"Crop: {c['name']}, planted {c['planting_date']}, {c['days_after_planting']} days after planting")
    for a in advisories:
        lines.append(f"Weather advisory: {a['message_key']} evidence={json.dumps(a['evidence'])}")
    return "FARM CONTEXT\n" + "\n".join(lines)


def build_request(provider: str, model: str, context: str, history: list, message: str) -> dict:
    """history: [{'role': 'user'|'assistant', 'content': str}, ...] already validated and trimmed."""
    messages = [{"role": h["role"], "content": h["content"]} for h in history] + [{"role": "user", "content": message}]
    if provider == "groq":
        # OpenAI-compatible shape: the system prompt is just the first message in the array.
        return {"model": model, "max_tokens": MAX_TOKENS, "temperature": 0.3,
                "messages": [{"role": "system", "content": SYSTEM + "\n\n" + context}] + messages}
    return {"model": model, "max_tokens": MAX_TOKENS, "system": SYSTEM + "\n\n" + context, "messages": messages}


def _auth_headers(provider: str, key: str) -> dict:
    # A descriptive User-Agent matters here: Python's default (Python-urllib/3.x) is a well-known
    # signature that Cloudflare's bot-management (sitting in front of api.groq.com) blocks outright
    # with its own "error code: 1010", before the request ever reaches Groq's actual API.
    base = {"User-Agent": "KanoFarmAI/1.0 (+farming assistant backend)"}
    if provider == "groq":
        return {**base, "Authorization": f"Bearer {key}", "content-type": "application/json"}
    return {**base, "x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}


def _map_http_error(provider: str, e: "urllib.error.HTTPError") -> AssistantError:
    body = e.read().decode("utf-8", "replace")
    etype, emsg = "", ""
    try:
        err = json.loads(body).get("error", {})
        etype = err.get("type") or err.get("code") or ""
        emsg = (err.get("message") or "")[:200]
    except (json.JSONDecodeError, AttributeError):
        pass
    kind = {401: "auth", 403: "forbidden", 429: "rate_limit", 503: "overloaded", 529: "overloaded"}.get(e.code)
    if kind is None:
        kind = "bad_request" if e.code == 400 else "server"
    detail = f"{provider} http {e.code} {etype}" + (f" -- {emsg}" if emsg else "")
    if not etype and not emsg:
        # Body didn't match the provider's known JSON error shape -- show the raw response so an admin can
        # tell a real provider error apart from a network/proxy/firewall block (e.g. Cloudflare "error code: 1010").
        raw = body.strip().replace("\n", " ")[:180]
        detail += f" | raw response: {raw!r}" if raw else " | (empty response body)"
    return AssistantError(kind, detail)


def _post(provider: str, key: str, payload: dict, timeout: float = 30) -> dict:
    url = GROQ_URL if provider == "groq" else ANTHROPIC_URL
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers=_auth_headers(provider, key))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # nosec - fixed https URL per provider, no user input
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise _map_http_error(provider, e) from e
    except socket.timeout as e:
        raise AssistantError("timeout") from e
    except urllib.error.URLError as e:
        raise AssistantError("network", str(e.reason)) from e
    except (TimeoutError, OSError) as e:
        raise AssistantError("network", type(e).__name__) from e



GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"

def list_groq_models(key: str, timeout: float = 15) -> list:
    """Asks Groq which model IDs THIS key may use (metadata call: uses no tokens). Removes all guesswork
    when a model name returns 404 'does not exist or you do not have access to it'."""
    req = urllib.request.Request(GROQ_MODELS_URL, method="GET", headers=_auth_headers("groq", key))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # nosec - fixed https URL
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise _map_http_error("groq", e) from e
    except socket.timeout as e:
        raise AssistantError("timeout") from e
    except urllib.error.URLError as e:
        raise AssistantError("network", str(e.reason)) from e
    except (TimeoutError, OSError) as e:
        raise AssistantError("network", type(e).__name__) from e
    return sorted(m.get("id", "") for m in data.get("data", []) if isinstance(m, dict) and m.get("id"))


def _extract_text(provider: str, resp: dict) -> str:
    if provider == "groq":
        try:
            return (resp["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            return ""
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text").strip()


def ask(provider: str, api_key: str, payload: dict, post: Optional[Callable] = None) -> str:
    if not api_key:
        raise AssistantError("auth", "no key configured")
    # Resolve _post at call time (not as a default argument) so it can be swapped in tests.
    resp = (post or _post)(provider, api_key, payload)
    text = _extract_text(provider, resp)
    if not text:
        raise AssistantError("empty")
    return text


FRIENDLY = {
    "auth": "The assistant is not set up correctly on this server.",
    "forbidden": "The assistant's key was rejected for this account or model (not because the key itself is wrong). "
                 "Check console.groq.com/settings/limits for phone/account verification, and "
                 "console.groq.com/settings/billing for account status.",
    "rate_limit": "Too many people are using the assistant right now. Please try again in a minute.",
    "overloaded": "The assistant is busy right now. Please try again shortly.",
    "timeout": "The assistant took too long to reply. Please try again.",
    "network": "No connection to the assistant right now. Please check your connection and try again.",
    "bad_request": "We couldn't send that question to the assistant. Please rephrase and try again.",
    "empty": "The assistant did not return an answer. Please try again.",
    "server": "The assistant had a temporary problem. Please try again shortly.",
}
STATUS = {"auth": 503, "forbidden": 503, "rate_limit": 429, "overloaded": 503, "timeout": 504,
          "network": 503, "bad_request": 422, "empty": 502, "server": 502}
