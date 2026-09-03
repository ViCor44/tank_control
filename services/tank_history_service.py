import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
HISTORY_PATH = BASE_DIR / "config" / "tank_history.json"
_history_lock = threading.RLock()
_retention = timedelta(hours=48)


def _parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _load_unlocked():
    if not HISTORY_PATH.exists():
        return {"tanks": {}}
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {"tanks": {}}
    except (OSError, json.JSONDecodeError):
        return {"tanks": {}}


def _save_unlocked(data):
    temporary_path = HISTORY_PATH.with_suffix(".json.tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, HISTORY_PATH)


def record_tank_volumes(tank_states, timestamp=None):
    """Store at most one valid volume sample per tank and minute."""
    now = timestamp or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - _retention
    minute_key = now.strftime("%Y-%m-%dT%H:%M")

    with _history_lock:
        data = _load_unlocked()
        histories = data.setdefault("tanks", {})
        active_ids = set(tank_states)

        for tank_id, tank_state in tank_states.items():
            samples = histories.setdefault(tank_id, [])
            samples[:] = [
                item for item in samples
                if (_parse_timestamp(item.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
            ]
            volume = tank_state.get("volume_liters")
            if not tank_state.get("sensor_ok") or not tank_state.get("sensor_reading_valid") or volume is None:
                continue
            sample = {"timestamp": now.isoformat(), "volume_m3": round(float(volume) / 1000, 2)}
            if samples and str(samples[-1].get("timestamp", ""))[:16] == minute_key:
                samples[-1] = sample
            else:
                samples.append(sample)

        for tank_id in list(histories):
            if tank_id not in active_ids:
                histories.pop(tank_id, None)
        _save_unlocked(data)


def load_tank_history(tank_id, hours=48):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, min(int(hours), 48)))
    with _history_lock:
        samples = _load_unlocked().get("tanks", {}).get(tank_id, [])
    return [item for item in samples if (_parse_timestamp(item.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
