import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from diamond.autonomy import (AutonomyLimits, clear_stop, fallback_plan, parse_plan,
                              plan_and_enqueue, request_stop, run_queue, stop_path)
from diamond.config import load_config
from diamond.errors import ConflictError, ValidationError
from diamond.store import EventStore
from support import ProjectCase


class AutonomyTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.state = self.base / "state"
        self.ledger = EventStore(self.state / "diamond.sqlite3")
        (self.project / "src").mkdir()
        (self.project / "tests").mkdir()
        (self.project / "src" / "value.py").write_text("def value(): return 1\n", encoding="utf-8")
        (self.project / "tests" / "check.py").write_text("assert True\n", encoding="utf-8")

    def test_fallback_goal_is_bounded_and_deterministic(self) -> None:
        self.assertEqual(fallback_plan("first step. second step; third step"),
                         ("first step", "second step", "third step"))
        with self.assertRaises(ValidationError):
            parse_plan({"steps": [{"objective": ""}]})
        with self.assertRaises(ValidationError):
            AutonomyLimits(0, 1)

    def test_plan_enqueues_only_validated_workflows(self) -> None:
        config = load_config(self.project / "config" / "diamond.toml")
        test = json.dumps({"name": "check", "argv": [sys.executable, "-B", "tests/check.py"],
                           "timeout_seconds": 5, "output_limit": 4096})
        with patch("diamond.autonomy.plan_goal", return_value=("write value", "verify value")):
            result = plan_and_enqueue("ship value", config, self.project / "config" / "diamond.toml",
                                      self.project, self.ledger, self.project, ("src/value.py",), (test,), "plan-1")
        self.assertEqual(result["objectives"], ["write value", "verify value"])
        self.assertEqual(len(result["workflows"]), 2)

    def test_stop_is_durable_and_stops_queue_before_provider_launch(self) -> None:
        request_stop(self.state)
        self.assertTrue(stop_path(self.state).is_file())
        report = run_queue(load_config(self.project / "config" / "diamond.toml"),
                           self.project / "config" / "diamond.toml", self.ledger, self.state,
                           AutonomyLimits(2, 0))
        self.assertTrue(report["stopped"])
        self.assertEqual(report["steps"], [])
        clear_stop(self.state)
        self.assertFalse(stop_path(self.state).exists())

    def test_second_autonomy_runner_is_rejected(self) -> None:
        self.state.mkdir()
        (self.state / "autonomy.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")
        with self.assertRaises(ConflictError):
            run_queue(load_config(self.project / "config" / "diamond.toml"),
                      self.project / "config" / "diamond.toml", self.ledger, self.state)
