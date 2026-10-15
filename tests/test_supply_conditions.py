import copy
import importlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, call, patch

from services import control_service


def make_config():
    return {
        "tanks": [
            {"id": tank_id, "name": tank_id, "enabled": True}
            for tank_id in ("T5", "T6", "T7")
        ],
        "sources": [{
            "id": "S1", "enabled": True, "enable_relay": 1,
            "sequence": [{
                "tank_id": "T6", "enabled": True,
                "supply_condition": {"tank_id": "T5", "above_percent": 80},
            }],
        }],
        "routes": [
            {"source_id": "S1", "tank_id": "T6", "valve_relay": 2},
            {"source_id": "S1", "tank_id": "T7", "valve_relay": 3},
        ],
    }


def make_state():
    return {
        "tanks": {
            tank_id: {
                "sensor_ok": True, "sensor_reading_valid": True,
                "level_percent": level, "status": "normal",
            }
            for tank_id, level in (("T5", 81), ("T6", 20), ("T7", 30))
        },
        "sources": {},
    }


class SupplyConditionControlTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.state = make_state()
        self.step = self.config["sources"][0]["sequence"][0]
        self.relay = Mock()
        self.relay.relay_on.return_value = {"ok": True}
        self.relay.relay_off.return_value = {"ok": True}
        patcher = patch.object(
            control_service, "build_relay_board_service", return_value=self.relay
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_condition_is_optional_and_existing_thresholds_still_apply(self):
        self.step.pop("supply_condition")
        self.state["tanks"]["T5"]["sensor_ok"] = False
        control_service.compute_source_targets(self.config, self.state)
        self.assertTrue(self.state["sources"]["S1"]["desired_active"])
        self.step["start_below_percent"] = 10
        self.state["sources"] = {}
        control_service.compute_source_targets(self.config, self.state)
        self.assertFalse(self.state["sources"]["S1"]["desired_active"])

    def test_strict_percentage_boundary_and_recovery(self):
        for level, expected in ((81, True), (80, False), (79, False), (80.01, True)):
            with self.subTest(level=level):
                self.state["tanks"]["T5"]["level_percent"] = level
                control_service.compute_source_targets(self.config, self.state)
                source = self.state["sources"]["S1"]
                self.assertEqual(source["desired_active"], expected)
                self.assertEqual(
                    source["supply_blocked_step_indexes"], [] if expected else [0]
                )
                if not expected:
                    self.assertEqual(source["target_reason"], "supply_condition")

    def test_missing_disabled_offline_or_invalid_origin_blocks_filling(self):
        cases = (
            ("missing", None), ("disabled", None), ("sensor_ok", False),
            ("sensor_reading_valid", False), ("level_percent", None),
            ("level_percent", float("nan")), ("level_percent", float("inf")),
            ("level_percent", -1), ("level_percent", 101),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                config, state = make_config(), make_state()
                if key == "missing":
                    config["tanks"].pop(0)
                elif key == "disabled":
                    config["tanks"][0]["enabled"] = False
                else:
                    state["tanks"]["T5"][key] = value
                control_service.compute_source_targets(config, state)
                self.assertFalse(state["sources"]["S1"]["desired_active"])

    def test_bad_condition_fails_closed_and_is_logged(self):
        for condition in ({}, {"tank_id": "T5", "above_percent": "NaN"}, "invalid"):
            with self.subTest(condition=condition):
                self.step["supply_condition"] = condition
                with self.assertLogs(control_service.logger, level="ERROR"):
                    control_service.compute_source_targets(self.config, self.state)
                self.assertFalse(self.state["sources"]["S1"]["desired_active"])

    def test_condition_is_per_destination_and_per_source(self):
        self.state["tanks"]["T5"]["level_percent"] = 80
        self.config["sources"][0]["sequence"].append({"tank_id": "T7"})
        self.config["sources"].append({
            "id": "S2", "enabled": True, "enable_relay": 4,
            "sequence": [{"tank_id": "T6"}],
        })
        self.config["routes"].append({
            "source_id": "S2", "tank_id": "T6", "valve_relay": 5,
        })
        control_service.compute_source_targets(self.config, self.state)
        self.assertEqual(self.state["sources"]["S1"]["current_tank_id"], "T7")
        self.assertEqual(self.state["sources"]["S2"]["current_tank_id"], "T6")

    def test_destination_full_and_stop_threshold_are_still_respected(self):
        for status, threshold in (("full", None), ("normal", 20)):
            with self.subTest(status=status):
                self.state["sources"]["S1"] = {"current_tank_id": "T6"}
                self.state["tanks"]["T6"]["status"] = status
                self.step["stop_at_percent"] = threshold
                control_service.compute_source_targets(self.config, self.state)
                self.assertFalse(self.state["sources"]["S1"]["desired_active"])

    def test_condition_loss_stops_pump_before_valve_even_with_stop_delay(self):
        self.config["sources"][0]["stop_delay_seconds"] = 60
        self.config["sources"][0]["valve_close_delay_seconds"] = 10
        control_service.apply_source_relays(self.config, self.state)
        self.assertTrue(self.state["sources"]["S1"]["active"])
        self.relay.reset_mock()
        self.state["tanks"]["T5"]["level_percent"] = 80
        control_service.apply_source_relays(self.config, self.state)
        source = self.state["sources"]["S1"]
        self.assertFalse(source["active"])
        self.assertEqual(source["status"], "supply_condition")
        self.assertEqual(self.relay.mock_calls[0], call.relay_off(1))
        self.relay.relay_on.assert_called_once_with(2)
        self.assertIsNone(self.state["tanks"]["T6"]["filling_by"])

    def test_supply_loss_during_route_transition_cancels_transition(self):
        self.state["tanks"]["T5"]["level_percent"] = 80
        self.state["sources"]["S1"] = {
            "active": True, "current_tank_id": "T7",
            "physical_tank_id": "T6", "physical_route_relay": 2,
            "route_transition": {
                "phase": "stopping", "phase_since": datetime.now(timezone.utc).isoformat(),
                "target_tank_id": "T7", "target_route_relay": 3,
            },
        }
        self.config["sources"][0]["sequence"].append({"tank_id": "T7"})
        self.config["sources"][0]["stop_delay_seconds"] = 60
        control_service.apply_source_relays(self.config, self.state)
        self.assertFalse(self.state["sources"]["S1"]["active"])
        self.assertIsNone(self.state["sources"]["S1"]["route_transition"])
        self.relay.relay_on.assert_not_called()

    def test_alternative_waits_for_safe_valve_close_then_can_start(self):
        self.config["sources"][0]["sequence"].append({"tank_id": "T7"})
        self.config["sources"][0]["valve_close_delay_seconds"] = 10
        control_service.apply_source_relays(self.config, self.state)
        self.state["tanks"]["T5"]["level_percent"] = 80
        control_service.apply_source_relays(self.config, self.state)
        self.assertFalse(self.state["sources"]["S1"]["active"])
        control_service.apply_source_relays(self.config, self.state)
        self.assertFalse(self.state["sources"]["S1"]["active"])
        future = datetime.now(timezone.utc) + timedelta(seconds=11)
        with patch.object(control_service, "datetime", wraps=datetime) as clock:
            clock.now.return_value = future
            control_service.apply_source_relays(self.config, self.state)
            control_service.apply_source_relays(self.config, self.state)
        self.assertTrue(self.state["sources"]["S1"]["active"])
        self.assertEqual(self.state["sources"]["S1"]["current_tank_id"], "T7")


class SupplyConditionRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (
            patch("sensor_poller.start_background_poller"),
            patch("services.security_service.get_session_secret", return_value="test-secret"),
        ):
            cls.app_module = importlib.import_module("app")

    def setUp(self):
        self.config = make_config()
        self.state = make_state()
        for name, kwargs in (
            ("load_config", {"side_effect": lambda: copy.deepcopy(self.config)}),
            ("load_state", {"side_effect": lambda: copy.deepcopy(self.state)}),
            ("save_config", {}),
            ("append_audit", {}),
            ("find_active_user", {"return_value": {"id": "test", "name": "Test"}}),
            ("get_session_secret", {"return_value": "test-secret"}),
        ):
            patcher = patch.object(self.app_module, name, **kwargs)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            if name == "save_config":
                self.save_config = mocked
        self.app = self.app_module.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "test"

    def post_condition(self, tank_id="T5", percent="80", index="0"):
        return self.client.post("/rules/sources/S1", data={
            "action": "set_step_supply_condition", "index": index,
            "supply_tank_id": tank_id, "supply_above_percent": percent,
        })

    def test_save_and_remove_optional_condition(self):
        response = self.post_condition(percent="82.5")
        self.assertEqual(response.status_code, 302)
        saved = self.save_config.call_args.args[0]["sources"][0]["sequence"][0]
        self.assertEqual(saved["supply_condition"], {"tank_id": "T5", "above_percent": 82.5})
        self.config = self.save_config.call_args.args[0]
        response = self.client.get("/rules")
        self.assertIn(b'value="82.5"', response.data)
        self.save_config.reset_mock()
        response = self.post_condition(tank_id="", percent="")
        self.assertEqual(response.status_code, 302)
        saved = self.save_config.call_args.args[0]["sources"][0]["sequence"][0]
        self.assertNotIn("supply_condition", saved)

    def test_unrelated_threshold_updates_preserve_supply_condition(self):
        response = self.client.post("/rules/sources/S1", data={
            "action": "set_step_threshold", "index": "0",
            "start_below_percent": "10", "stop_at_percent": "30",
        })
        self.assertEqual(response.status_code, 302)
        step = self.save_config.call_args.args[0]["sources"][0]["sequence"][0]
        self.assertEqual(step["supply_condition"], {"tank_id": "T5", "above_percent": 80})
        self.assertEqual(step["start_below_percent"], 10)
        self.assertEqual(step["stop_at_percent"], 30)

    def test_renaming_origin_keeps_condition_linked_to_that_tank(self):
        response = self.client.post("/tanks/T5/edit", data={
            "id": "T5-renamed", "name": "Origin", "enabled": "on",
        })
        self.assertEqual(response.status_code, 302)
        step = self.save_config.call_args.args[0]["sources"][0]["sequence"][0]
        self.assertEqual(step["supply_condition"]["tank_id"], "T5-renamed")

    def test_reject_invalid_condition_without_saving(self):
        cases = (
            ("T5", "NaN", "0"), ("T5", "inf", "0"), ("T5", "-1", "0"),
            ("T5", "101", "0"), ("T5", "", "0"), ("", "80", "0"),
            ("missing", "80", "0"), ("T6", "80", "0"), ("T5", "80", "99"),
        )
        for tank_id, percent, index in cases:
            with self.subTest(tank=tank_id, percent=percent, index=index):
                response = self.post_condition(tank_id, percent, index)
                self.assertEqual(response.status_code, 400)
                self.save_config.assert_not_called()

    def test_rules_page_renders_optional_condition_and_missing_origin(self):
        for tank_id in ("T5", "missing"):
            with self.subTest(tank_id=tank_id):
                self.config["sources"][0]["sequence"][0]["supply_condition"]["tank_id"] = tank_id
                response = self.client.get("/rules")
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'name="supply_tank_id"', response.data)
                self.assertIn(b"Sem condi", response.data)
        self.config["sources"][0]["sequence"][0].pop("supply_condition")
        self.assertEqual(self.client.get("/rules").status_code, 200)

    def test_api_exposes_blocked_steps_and_source_page_displays_reason(self):
        self.state["tanks"]["T5"]["level_percent"] = 80
        control_service.compute_source_targets(self.config, self.state)
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        source = response.get_json()["sources"][0]
        self.assertEqual(source["supply_blocked_step_indexes"], [0])
        self.assertEqual(source["target_reason"], "supply_condition")
        self.assertEqual(self.client.get("/sources").status_code, 200)


if __name__ == "__main__":
    unittest.main()
