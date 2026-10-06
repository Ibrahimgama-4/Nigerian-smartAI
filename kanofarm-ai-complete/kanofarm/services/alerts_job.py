"""The scheduled alert run. Orchestration only: weather, advisories, the database and the channels are injected, so the whole
flow is unit-tested with fakes. Vercel Cron (Hobby plan) can run once a day; see vercel.json.

Rules this enforces:
 * one send per farm, per message, per day, per channel (the database claim is what makes that true even if two runs overlap);
 * if every attempt fails, the claim is handed back so the next run can retry;
 * a device the push service says is gone is forgotten;
 * quiet hours skip a farm WITHOUT claiming anything;
 * weather alerts respect the farmer's own thresholds, on/off switches and minimum level;
 * growth-stage reminders are separate from weather levels and have their own switch."""
import logging
from datetime import date
from typing import Callable, Optional

from .alerts import filter_advisories, thresholds_from_rules
from .notify import build_message, in_quiet_hours, level_allowed, wanted_channels
from .weather_rules import build_advisories

log = logging.getLogger("kanofarm")


def _deliver(ch: str, t: dict, msg: dict, channels: dict, subs_by_user: dict, drop_sub: Callable, stats: dict) -> bool:
    """Send one message on one channel. True if at least one device/number accepted it."""
    channel = channels[ch]
    if ch == "push":
        results = [(s, channel.send(s, msg, t.get("farm_name", ""))) for s in subs_by_user.get(t["user_id"], [])]
    else:
        results = [(None, channel.send(t["phone"], msg, t.get("farm_name", "")))]
    for sub, r in results:
        if r.ok:
            continue
        if r.gone and sub is not None:
            drop_sub(sub["endpoint"]); stats["dropped_devices"] += 1
        else:
            log.warning("alert send failed channel=%s reason=%s", ch, r.error)
    return any(r.ok for _, r in results)


def run_alert_job(*, targets: list, subs_by_user: dict, get_forecast: Callable, translate: Callable, channels: dict,
                  available: dict, claim: Callable, release: Callable, drop_sub: Callable, today: date, lagos_hour: int,
                  extra_events: Optional[Callable] = None) -> dict:
    """claim(farm_id, key, channel) -> True for the first caller only. extra_events(target, today) -> [{'key','text',...}].
    Returns counts only, never farmer data."""
    stats = {"farms": 0, "sent": 0, "failed": 0, "duplicates": 0, "skipped_quiet": 0, "weather_errors": 0, "dropped_devices": 0}
    for t in targets:
        use = wanted_channels(t, available)
        if not use:
            continue
        stats["farms"] += 1
        if in_quiet_hours(t.get("quiet_start"), t.get("quiet_end"), lagos_hour):
            stats["skipped_quiet"] += 1
            continue

        messages = []                                                   # (key, text)
        try:
            fc = get_forecast(float(t["latitude"]), float(t["longitude"]))
            rules = t.get("rules") or []
            for a in filter_advisories(build_advisories(fc, thresholds_from_rules(rules)), rules):
                if level_allowed(a.level, t.get("min_level")):
                    tr = translate(a.message_key, t.get("language") or "en")
                    messages.append((a.message_key, tr["text"] if isinstance(tr, dict) else str(tr)))
        except Exception as e:                                          # noqa: BLE001 - weather trouble must not stop other farms
            log.warning("alert job weather error: %s", type(e).__name__)
            stats["weather_errors"] += 1
        if extra_events and t.get("stage_reminders", True) is not False:
            messages += [(ev["key"], ev["text"]) for ev in extra_events(t, today)]

        for key, text in messages:
            msg = build_message(t["farm_name"], text)
            for ch in use:
                if ch == "push" and not subs_by_user.get(t["user_id"]):
                    continue                                            # push is on but no device is registered: nothing to send
                if not claim(t["farm_id"], key, ch):
                    stats["duplicates"] += 1
                    continue
                if _deliver(ch, t, msg, channels, subs_by_user, drop_sub, stats):
                    stats["sent"] += 1
                else:
                    stats["failed"] += 1
                    release(t["farm_id"], key, ch)
    return stats
