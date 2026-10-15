import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from services import tank_history_service


class TankHistoryRollingWindowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.history_path = Path(directory.name) / "tank_history.json"
        for name, path in (
            ("HISTORY_PATH", self.history_path),
            ("LEGACY_HISTORY_PATH", self.history_path),
            ("PER_USER_HISTORY_PATH", Path(directory.name) / "missing.json"),
        ):
            patcher = patch.object(tank_history_service, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.states = {
            "T1": {
                "sensor_ok": True,
                "sensor_reading_valid": True,
                "volume_liters": 11000,
            }
        }

    def save_samples(self, count):
        samples = [
            {
                "timestamp": (self.start + timedelta(minutes=minute)).isoformat(),
                "volume_m3": 10.0,
            }
            for minute in range(count)
        ]
        self.history_path.write_text(
            json.dumps({"tanks": {"T1": samples}}), encoding="utf-8"
        )

    def load_at(self, now):
        with patch.object(tank_history_service, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            return tank_history_service.load_tank_history("T1")

    def test_window_remains_full_across_multiple_two_day_boundaries(self):
        self.save_samples(7 * 24 * 60)
        for day in (2, 3, 4, 5, 6):
            with self.subTest(day=day):
                now = self.start + timedelta(days=day)
                samples = self.load_at(now)
                self.assertEqual(len(samples), 48 * 60 + 1)
                self.assertEqual(
                    samples[0]["timestamp"], (now - timedelta(hours=48)).isoformat()
                )
                self.assertEqual(samples[-1]["timestamp"], now.isoformat())

    def test_new_minute_moves_window_without_resetting_saved_history(self):
        self.save_samples(7 * 24 * 60)
        for minute in range(3):
            now = self.start + timedelta(days=7, minutes=minute)
            tank_history_service.record_tank_volumes(self.states, timestamp=now)
            samples = self.load_at(now)
            self.assertEqual(len(samples), 48 * 60 + 1)
            self.assertEqual(
                samples[0]["timestamp"], (now - timedelta(hours=48)).isoformat()
            )
            self.assertEqual(samples[-1]["timestamp"], now.isoformat())
            saved = json.loads(self.history_path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["tanks"]["T1"]), 7 * 24 * 60)

    def test_unreadable_history_is_not_overwritten_with_a_new_history(self):
        self.save_samples(10)
        original = self.history_path.read_bytes()
        with patch.object(Path, "open", side_effect=PermissionError("access denied")):
            with self.assertRaises(PermissionError):
                tank_history_service.record_tank_volumes(self.states, self.start)
        self.assertEqual(self.history_path.read_bytes(), original)

    def test_invalid_history_is_not_overwritten_with_a_new_history(self):
        for content in (
            '{"tanks":', '[]', '{}', '{"tanks":[]}', '{"tanks":{"T1":{}}}',
            '{"tanks":{"T1":[{"timestamp":"invalid"}]}}',
        ):
            with self.subTest(content=content):
                self.history_path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    tank_history_service.record_tank_volumes(self.states, self.start)
                self.assertEqual(self.history_path.read_text(encoding="utf-8"), content)


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