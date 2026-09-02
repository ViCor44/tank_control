import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / "config" / "alarm_history.json"
_lock = threading.RLock()
MAX_EVENTS = 500

def load_alarm_history():
    with _lock:
        if not HISTORY_PATH.exists():
            return []
        try:
            with HISTORY_PATH.open("r", encoding="utf-8") as handle:
                entries = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return []
    return list(reversed(entries[-MAX_EVENTS:]))

def record_alarm_transitions(previous_alarms, current_alarms, state):
    previous = {alarm.get("id"): alarm for alarm in previous_alarms or [] if alarm.get("id")}
    current = {alarm.get("id"): alarm for alarm in current_alarms or [] if alarm.get("id")}
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
    with _lock:
        existing = []
        if HISTORY_PATH.exists():
            try:
                with HISTORY_PATH.open("r", encoding="utf-8") as handle:
                    existing = json.load(handle)
            except (OSError, json.JSONDecodeError):
                existing = []
        existing.extend(events)
        temporary = HISTORY_PATH.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(existing[-MAX_EVENTS:], handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(HISTORY_PATH)
