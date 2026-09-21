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


if __name__ == "__main__":
    unittest.main()