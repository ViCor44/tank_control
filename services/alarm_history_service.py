import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / "config" / "alarm_history.json"
_lock = threading.RLock()
MAX_EVENTS = 500


@contextmanager
def _history_file_lock():
    lock_path = HISTORY_PATH.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0)
    if handle.read(1) == b"":
        handle.write(b"0")
        handle.flush()
    handle.seek(0)

    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _is_loggable_alarm(alarm):
    """A full tank is an operating state, not an error worth auditing."""
    return not str(alarm.get("alarm_id") or alarm.get("id") or "").endswith("_full")


def _deduplicate_transitions(entries):
    latest_event_by_alarm = {}
    deduplicated = []
    for entry in entries:
        alarm_id = entry.get("alarm_id")
        event = entry.get("event")
        if alarm_id and event and latest_event_by_alarm.get(alarm_id) == event:
            continue
        deduplicated.append(entry)
        if alarm_id and event:
            latest_event_by_alarm[alarm_id] = event
    return deduplicated

def load_alarm_history():
    with _lock:
        if not HISTORY_PATH.exists():
            return []
        try:
            with HISTORY_PATH.open("r", encoding="utf-8") as handle:
                entries = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return []
    visible_entries = [entry for entry in entries if _is_loggable_alarm(entry)]
    deduplicated = _deduplicate_transitions(visible_entries)
    return list(reversed(deduplicated[-MAX_EVENTS:]))

def record_alarm_transitions(previous_alarms, current_alarms, state):
    previous = {alarm.get("id"): alarm for alarm in previous_alarms or [] if alarm.get("id") and _is_loggable_alarm(alarm)}
    current = {alarm.get("id"): alarm for alarm in current_alarms or [] if alarm.get("id") and _is_loggable_alarm(alarm)}
    timestamp = datetime.now(timezone.utc).isoformat()
    events = []
    for alarm_id, alarm in current.items():
        if alarm_id in previous:
            continue
        tank_id = alarm.get("tank_id")
        tank_state = state.get("tanks", {}).get(tank_id, {}) if tank_id else {}
        events.append({"timestamp": timestamp, "event": "activated", "alarm_id": alarm_id, "severity": alarm.get("severity", "low"), "message": alarm.get("message", "Alarme"), "tank_id": tank_id, "level_percent": alarm.get("level_percent"), "error": tank_state.get("last_error", "")})
    for alarm_id, alarm in previous.items():
        if alarm_id in current:
            continue
        events.append({"timestamp": timestamp, "event": "resolved", "alarm_id": alarm_id, "severity": alarm.get("severity", "low"), "message": alarm.get("message", "Alarme"), "tank_id": alarm.get("tank_id"), "level_percent": alarm.get("level_percent"), "error": ""})
    if not events:
        return
    with _lock, _history_file_lock():
        existing = []
        if HISTORY_PATH.exists():
            try:
                with HISTORY_PATH.open("r", encoding="utf-8") as handle:
                    existing = json.load(handle)
            except (OSError, json.JSONDecodeError):
                existing = []
        latest_event_by_alarm = {
            entry.get("alarm_id"): entry.get("event")
            for entry in _deduplicate_transitions(existing)
            if entry.get("alarm_id")
        }
        new_events = [
            event
            for event in events
            if latest_event_by_alarm.get(event["alarm_id"]) != event["event"]
        ]
        if not new_events:
            return
        existing.extend(new_events)
        temporary = HISTORY_PATH.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(existing[-MAX_EVENTS:], handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(HISTORY_PATH)
