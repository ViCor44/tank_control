import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from services import tank_history_service


class TankHistoryMigrationTests(unittest.TestCase):
    def test_merges_per_user_history_into_canonical_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "config" / "tank_history.json"
            per_user_path = root / "home" / "tank_history.json"
            per_user_path.parent.mkdir(parents=True)
            per_user_path.write_text(
                json.dumps({
                    "tanks": {
                        "T1": [{
                            "timestamp": "2026-09-21T08:00:00+00:00",
                            "volume_m3": 10.0,
                        }]
                    }
                }),
                encoding="utf-8",
            )

            with (
                patch.object(tank_history_service, "HISTORY_PATH", canonical_path),
                patch.object(tank_history_service, "LEGACY_HISTORY_PATH", canonical_path),
                patch.object(tank_history_service, "PER_USER_HISTORY_PATH", per_user_path),
            ):
                tank_history_service.record_tank_volumes(
                    {
                        "T1": {
                            "sensor_ok": True,
                            "sensor_reading_valid": True,
                            "volume_liters": 11000,
                        }
                    },
                    timestamp=datetime.fromisoformat("2026-09-21T08:01:00+00:00"),
                )

            saved = json.loads(canonical_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [sample["volume_m3"] for sample in saved["tanks"]["T1"]],
                [10.0, 11.0],
            )

    def test_future_clock_during_reboot_does_not_prune_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "config" / "tank_history.json"
            canonical_path.parent.mkdir(parents=True)
            existing_samples = [
                {
                    "timestamp": f"2026-09-21T{hour:02d}:00:00+00:00",
                    "volume_m3": float(hour),
                }
                for hour in range(1, 9)
            ]
            canonical_path.write_text(
                json.dumps({"tanks": {"T1": existing_samples}}),
                encoding="utf-8",
            )

            with (
                patch.object(tank_history_service, "HISTORY_PATH", canonical_path),
                patch.object(tank_history_service, "LEGACY_HISTORY_PATH", canonical_path),
                patch.object(
                    tank_history_service,
                    "PER_USER_HISTORY_PATH",
                    root / "missing" / "tank_history.json",
                ),
            ):
                tank_history_service.record_tank_volumes(
                    {
                        "T1": {
                            "sensor_ok": True,
                            "sensor_reading_valid": True,
                            "volume_liters": 9000,
                        }
                    },
                    timestamp=datetime.fromisoformat("2027-01-01T00:00:00+00:00"),
                )

            saved = json.loads(canonical_path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["tanks"]["T1"]), 9)
            self.assertEqual(saved["tanks"]["T1"][0], existing_samples[0])


if __name__ == "__main__":
    unittest.main()