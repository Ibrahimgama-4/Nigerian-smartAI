"""Minimal Supabase client over REST using the FARMER'S OWN JWT, so Postgres Row Level Security
applies to every query. The backend never holds a service-role key."""
import json, re, urllib.parse, urllib.request, urllib.error
from typing import Callable, Optional

class SupabaseError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"supabase {status}: {message}")
        self.status = status

def default_http(method, url, headers, body=None, timeout=15):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # nosec - URL from server config
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

_TABLE = re.compile(r"^[a-z_]+$")

class Supabase:
    def __init__(self, url: str, anon_key: str, http: Callable = default_http):
        if not url or not anon_key: raise SupabaseError(503, "not configured")
        self.url, self.key, self.http = url.rstrip("/"), anon_key, http

    def _h(self, jwt, extra=None):
        h = {"apikey": self.key, "Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
        h.update(extra or {}); return h

    def _json(self, status, body):
        if status >= 400: raise SupabaseError(status, body.decode("utf-8", "replace")[:200])
        return json.loads(body) if body else None

    def user(self, jwt: str) -> Optional[dict]:
        s, b = self.http("GET", f"{self.url}/auth/v1/user", self._h(jwt))
        if s != 200: return None
        u = json.loads(b)
        return u if isinstance(u, dict) and u.get("id") else None

    def _tbl(self, table):
        if not _TABLE.match(table): raise ValueError("bad table")
        return f"{self.url}/rest/v1/{table}"

    def select(self, jwt, table, params=None):
        q = urllib.parse.urlencode(params or {}, safe="*,().:")
        return self._json(*self.http("GET", f"{self._tbl(table)}?{q}", self._h(jwt))) or []

    def insert(self, jwt, table, rows, on_conflict: Optional[str] = None, ignore_duplicates=False):
        prefer = "return=representation" + (",resolution=ignore-duplicates" if ignore_duplicates else "")
        url = self._tbl(table) + (f"?on_conflict={urllib.parse.quote(on_conflict, safe=',')}" if on_conflict else "")
        return self._json(*self.http("POST", url, self._h(jwt, {"Prefer": prefer}), json.dumps(rows).encode())) or []

    def update(self, jwt, table, params, patch):
        q = urllib.parse.urlencode(params, safe="*,().:")
        return self._json(*self.http("PATCH", f"{self._tbl(table)}?{q}",
                          self._h(jwt, {"Prefer": "return=representation"}), json.dumps(patch).encode())) or []

    def delete(self, jwt, table, params):
        q = urllib.parse.urlencode(params, safe="*,().:")
        self._json(*self.http("DELETE", f"{self._tbl(table)}?{q}", self._h(jwt)))

    def upload(self, jwt, bucket, path, data: bytes, content_type: str):
        h = self._h(jwt, {"Content-Type": content_type, "x-upsert": "false"})
        s, b = self.http("POST", f"{self.url}/storage/v1/object/{bucket}/{urllib.parse.quote(path)}", h, data)
        if s >= 400: raise SupabaseError(s, b.decode("utf-8", "replace")[:200])

    def sign_url(self, jwt, bucket, path, seconds=300):
        s, b = self.http("POST", f"{self.url}/storage/v1/object/sign/{bucket}/{urllib.parse.quote(path)}",
                         self._h(jwt), json.dumps({"expiresIn": seconds}).encode())
        if s >= 400: return None
        u = json.loads(b).get("signedURL")
        return f"{self.url}/storage/v1{u}" if u else None
