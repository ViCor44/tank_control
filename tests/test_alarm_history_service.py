import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import alarm_history_service


class AlarmHistoryServiceTests(unittest.TestCase):
    def test_repeated_transition_is_recorded_once(self):
        alarm = {
            "id": "tank_T5_sensor_offline",
            "severity": "high",
            "message": "Tanque 5: sensor offline",
            "tank_id": "T5",
        }

        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "alarm_history.json"
            with patch.object(alarm_history_service, "HISTORY_PATH", history_path):
                alarm_history_service.record_alarm_transitions([], [alarm], {})
                alarm_history_service.record_alarm_transitions([], [alarm], {})
                alarm_history_service.record_alarm_transitions([alarm], [], {})
                alarm_history_service.record_alarm_transitions([], [alarm], {})

            entries = json.loads(history_path.read_text(encoding="utf-8"))

        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["event"], "activated")
        self.assertEqual(entries[1]["event"], "resolved")
        self.assertEqual(entries[2]["event"], "activated")
        self.assertEqual(entries[0]["alarm_id"], alarm["id"])

    def test_load_hides_duplicate_stored_transitions(self):
        entries = [
            {"alarm_id": "tank_T5_sensor_offline", "event": "activated"},
            {"alarm_id": "tank_T5_sensor_offline", "event": "activated"},
            {"alarm_id": "tank_T5_sensor_offline", "event": "resolved"},
            {"alarm_id": "tank_T5_sensor_offline", "event": "resolved"},
        ]

        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "alarm_history.json"
            history_path.write_text(json.dumps(entries), encoding="utf-8")
            with patch.object(alarm_history_service, "HISTORY_PATH", history_path):
                loaded = alarm_history_service.load_alarm_history()

        self.assertEqual(
            [entry["event"] for entry in loaded],
            ["resolved", "activated"],
        )


if __name__ == "__main__":
    unittest.main()