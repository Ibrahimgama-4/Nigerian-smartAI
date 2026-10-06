"""Delivery channels for alerts sent outside the app.

Free by default: web push (browser/PWA notifications) costs nothing. WhatsApp and SMS are provider-backed and usually cost
money, so they stay OFF unless the site owner configures a provider; the farmer's checkbox then has an effect. Nothing here
touches the database or the weather provider, so it is unit-tested with fakes."""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

LEVEL_RANK = {"info": 0, "watch": 1, "warning": 2}


@dataclass
class SendResult:
    ok: bool
    gone: bool = False      # the device no longer exists (push service said 404/410): forget it
    error: str = ""         # short technical reason for the server log only; never shown to farmers


def level_allowed(level: str, min_level: Optional[str]) -> bool:
    """A farmer picks the lowest level they want to hear about; the default is 'watch'."""
    return LEVEL_RANK.get(level, 0) >= LEVEL_RANK.get(min_level or "watch", 1)


def in_quiet_hours(start: Optional[int], end: Optional[int], hour: int) -> bool:
    """Quiet window is [start, end) in Lagos hours and may wrap past midnight. start == end (or either missing) = never quiet."""
    if start is None or end is None or start == end:
        return False
    return start <= hour < end if start < end else (hour >= start or hour < end)


def build_message(farm_name: str, text: str) -> dict:
    return {"title": f"KanoFarm AI: {farm_name}"[:80], "body": text[:240], "url": "/#/farms"}


# ---------------------------------------------------------------- channels
class WebPushChannel:
    """Free. Needs the pywebpush package (imported only when a real send happens) and a VAPID key pair."""
    name = "push"

    def __init__(self, vapid_private_key: str, vapid_subject: str, sender: Optional[Callable] = None):
        self.key, self.subject, self._sender = vapid_private_key, vapid_subject, sender

    @property
    def configured(self) -> bool:
        return bool(self.key and self.subject)

    def send(self, sub: dict, message: dict, farm_name: str = "") -> SendResult:
        """sub = {'endpoint','p256dh','auth'} exactly as the browser produced it."""
        try:
            sender = self._sender or self._real_sender
            sender(sub, json.dumps(message), self.key, {"sub": self.subject})
            return SendResult(True)
        except Exception as e:                                      # noqa: BLE001 - classify, never leak details to farmers
            status = getattr(getattr(e, "response", None), "status_code", None)
            return SendResult(False, gone=status in (404, 410), error=f"{type(e).__name__} {status or ''}".strip())

    @staticmethod
    def _real_sender(sub, data, key, claims):
        from pywebpush import webpush
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=data, vapid_private_key=key, vapid_claims=dict(claims), ttl=6 * 3600)


class WhatsAppCloudChannel:
    """Meta WhatsApp Cloud API using an approved TEMPLATE whose body has exactly one variable ({{1}}). Meta charges for
    business-initiated messages, so read Meta's current pricing before configuring this."""
    name = "whatsapp"

    def __init__(self, token: str, phone_number_id: str, template: str, template_lang: str = "en", post: Optional[Callable] = None):
        self.token, self.pid, self.template, self.lang = token, phone_number_id, template, template_lang or "en"
        self._post = post or self._real_post

    @property
    def configured(self) -> bool:
        return bool(self.token and self.pid and self.template)

    def send(self, phone: str, message: dict, farm_name: str = "") -> SendResult:
        text = (f"{farm_name}: " if farm_name else "") + message.get("body", "")
        body = {"messaging_product": "whatsapp", "to": phone.lstrip("+"), "type": "template",
                "template": {"name": self.template, "language": {"code": self.lang},
                             "components": [{"type": "body", "parameters": [{"type": "text", "text": text[:600]}]}]}}
        try:
            self._post(f"https://graph.facebook.com/v20.0/{self.pid}/messages", body, self.token)
            return SendResult(True)
        except Exception as e:                                      # noqa: BLE001
            return SendResult(False, error=type(e).__name__)

    @staticmethod
    def _real_post(url, body, token, timeout=15):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:     # nosec - fixed https host; raises HTTPError on non-2xx
            return r.status


class SmsChannel:
    """Deliberately a stub: Nigerian SMS has no dependable free tier. To add one, implement send() against a provider's API
    (after checking its pricing) and set configured to True once its key is present."""
    name = "sms"
    configured = False

    def send(self, phone: str, message: dict, farm_name: str = "") -> SendResult:
        return SendResult(False, error="sms provider not configured")


def available_channels(push, wa, sms) -> dict:
    return {"push": bool(push.configured), "whatsapp": bool(wa.configured), "sms": bool(sms.configured)}


def wanted_channels(target: dict, available: dict) -> list:
    """Channels a farm's owner switched on AND the site can actually deliver on (phone channels also need a stored number)."""
    out = []
    if target.get("push_enabled") and available.get("push"): out.append("push")
    if target.get("whatsapp_enabled") and target.get("phone") and available.get("whatsapp"): out.append("whatsapp")
    if target.get("sms_enabled") and target.get("phone") and available.get("sms"): out.append("sms")
    return out
