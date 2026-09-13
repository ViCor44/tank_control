import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
LEGACY_HISTORY_PATH = BASE_DIR / "config" / "tank_history.json"
HISTORY_PATH = Path(
    os.environ.get(
        "TANK_CONTROL_HISTORY_PATH",
        Path.home() / ".local" / "share" / "tank_control" / "tank_history.json",
    )
).expanduser()
_history_lock = threading.RLock()
_retention = timedelta(hours=48)


def _parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _load_unlocked():
    source_path = HISTORY_PATH if HISTORY_PATH.exists() else LEGACY_HISTORY_PATH
    if not source_path.exists():
        return {"tanks": {}}
    try:
        with source_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {"tanks": {}}
    except (OSError, json.JSONDecodeError):
        return {"tanks": {}}


def _save_unlocked(data):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = HISTORY_PATH.with_suffix(".json.tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, HISTORY_PATH)


def _samples_with_boundary(samples, cutoff):
    previous = None
    recent = []
    for item in samples:
        timestamp = _parse_timestamp(item.get("timestamp"))
        if timestamp is None:
            continue
        if timestamp < cutoff:
            if previous is None or timestamp > previous[0]:
                previous = (timestamp, item)
        else:
            recent.append((timestamp, item))

    recent.sort(key=lambda entry: entry[0])
    retained = [item for _, item in recent]
    if previous is not None:
        retained.insert(0, previous[1])
    return retained


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

        for tank_id in list(histories):
            samples = histories[tank_id]
            samples[:] = _samples_with_boundary(samples, cutoff)
            if not samples and tank_id not in tank_states:
                histories.pop(tank_id, None)

        for tank_id, tank_state in tank_states.items():
            samples = histories.setdefault(tank_id, [])
            volume = tank_state.get("volume_liters")
            if not tank_state.get("sensor_ok") or not tank_state.get("sensor_reading_valid") or volume is None:
                continue
            sample = {"timestamp": now.isoformat(), "volume_m3": round(float(volume) / 1000, 2)}
            if samples and str(samples[-1].get("timestamp", ""))[:16] == minute_key:
                samples[-1] = sample
            else:
                samples.append(sample)
        _save_unlocked(data)


def load_tank_history(tank_id, hours=48):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, min(int(hours), 48)))
    with _history_lock:
        samples = _load_unlocked().get("tanks", {}).get(tank_id, [])
    window = _samples_with_boundary(samples, cutoff)
    if window and (_parse_timestamp(window[0].get("timestamp")) or now) < cutoff:
        if len(window) == 1:
            return []
        window[0] = {**window[0], "timestamp": cutoff.isoformat(), "is_boundary": True}
    return window
