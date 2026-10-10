import math
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

from diamond.config import MAX_CONFIG_BYTES, ControllerConfig, DiamondConfig, ProviderConfig, parse_config
from diamond.domain import LedgerEvent, Provider, Role, TaskSnapshot, TaskSpec, TaskStatus
from diamond.errors import ConfigError, ExecutionGuardError, ValidationError
from diamond.validation import canonical_json, parse_canonical_json, utc_now
from support import CONFIG_TEXT, TASK_ID, ProjectCase


class ContractTests(ProjectCase):
    def test_deep_toml_is_config_error_without_state_creation(self) -> None:
        source = "x=" + "{x=" * 1000 + "1" + "}" * 1000
        self.assertLess(len(source.encode("utf-8")), 4096)
        with self.assertRaises(ConfigError):
            parse_config(source)
        with patch("diamond.config.tomllib.loads", side_effect=RecursionError("injected parser recursion")):
            with self.assertRaisesRegex(ConfigError, "nesting exceeds supported parser depth"):
                parse_config(CONFIG_TEXT)
        self.assertFalse((self.project / ".diamond").exists())

    def test_config_source_type_and_utf8_byte_limit_checked_before_parser(self) -> None:
        invalid = [None, b"schema_version=1", 1, "x" * (MAX_CONFIG_BYTES + 1),
                   "é" * (MAX_CONFIG_BYTES // 2 + 1), "\ud800"]
        with patch("diamond.config.tomllib.loads", side_effect=AssertionError("invalid source must not reach parser")):
            for source in invalid:
                with self.subTest(source_type=type(source)), self.assertRaises(ConfigError):
                    parse_config(source)

    def test_config_valid_and_frozen_routing(self) -> None:
        config = parse_config(CONFIG_TEXT)
        self.assertEqual(config.controller, ControllerConfig(2, 900, 2))
        self.assertEqual(config.resolve_provider(Role.BUILDER), Provider.CLAUDE)
        self.assertEqual(config.resolve_provider(Role.BUILDER, Provider.CODEX), Provider.CODEX)
        providers, roles = dict(config.providers), dict(config.roles)
        copied = DiamondConfig(1, "test", config.controller, providers, roles)
        roles[Role.BUILDER] = Provider.CODEX
        self.assertEqual(copied.roles[Role.BUILDER], Provider.CLAUDE)
        with self.assertRaises(TypeError):
            copied.providers[Provider.GEMINI] = ProviderConfig("different", True)

    def test_config_rejects_malformed_missing_extra_and_invalid_values(self) -> None:
        mutations = [
            "[unclosed", CONFIG_TEXT.replace("schema_version = 1", "schema_version = 2"),
            CONFIG_TEXT.replace("schema_version = 1", "schema_version = true"),
            CONFIG_TEXT.replace('project_name = "Flawless Diamond Piece"', 'project_name = " "'),
            CONFIG_TEXT.replace("max_workers = 2", "max_workers = true"),
            CONFIG_TEXT.replace("max_workers = 2", "max_workers = 0"),
            CONFIG_TEXT.replace("max_workers = 2", "max_workers = 65"),
            CONFIG_TEXT.replace("task_timeout_seconds = 900", "task_timeout_seconds = 86401"),
            CONFIG_TEXT.replace("task_timeout_seconds = 900", "task_timeout_seconds = 0"),
            CONFIG_TEXT.replace("correction_round_limit = 2", "correction_round_limit = -1"),
            CONFIG_TEXT.replace("correction_round_limit = 2", "correction_round_limit = 101"),
            CONFIG_TEXT.replace("correction_round_limit = 2", "correction_round_limit = false"),
            CONFIG_TEXT.replace("max_workers = 2\n", ""),
            CONFIG_TEXT.replace("max_workers = 2", "max_workers = 2\nextra = 1"),
            'credential = "synthetic-test-value"\n' + CONFIG_TEXT,
            CONFIG_TEXT.replace('planner = "antigravity"', 'planner = "unknown"'),
            CONFIG_TEXT.replace('planner = "antigravity"', 'controller = "antigravity"'),
            CONFIG_TEXT.replace('[providers.gemini]', '[providers.unknown]'),
            CONFIG_TEXT.replace('executable = "gemini"', 'executable = ""'),
            CONFIG_TEXT.replace('executable = "gemini"', 'executable = "./gemini"'),
            CONFIG_TEXT.replace('enabled = true', 'enabled = "true"', 1),
            CONFIG_TEXT.replace('[providers.antigravity]\nexecutable = "agy"\nenabled = true',
                                '[providers.antigravity]\nexecutable = "agy"\nenabled = false'),
            CONFIG_TEXT.replace('executable = "gemini"\n', ''),
        ]
        for source in mutations:
            with self.subTest(source=source[:80]), self.assertRaises(ConfigError):
                parse_config(source)

    def test_disabled_explicit_provider_rejected_without_probe(self) -> None:
        source = CONFIG_TEXT.replace('enabled = true', 'enabled = false', 1)
        source = source.replace('planner = "gemini"', 'planner = "claude"').replace('researcher = "gemini"', 'researcher = "claude"')
        config = parse_config(source)
        with patch("subprocess.Popen", side_effect=AssertionError("provider execution forbidden")):
            with self.assertRaises(ValidationError):
                config.resolve_provider(Role.BUILDER, Provider.GEMINI)

    def test_direct_dataclass_validation(self) -> None:
        invalid_specs = [
            {"task_id": "../escape"}, {"objective": " "}, {"objective": "a" * 16_385},
            {"role": "builder"}, {"provider": "claude"}, {"schema_version": True},
            {"acceptance_conditions": ["mutable"]}, {"dependencies": ("bad",)},
            {"timeout_seconds": True}, {"timeout_seconds": 0}, {"correction_round_limit": -1},
        ]
        spec = TaskSpec(TASK_ID, "Build foundation", Role.BUILDER, Provider.CLAUDE)
        for changes in invalid_specs:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                replace(spec, **changes)
        with self.assertRaises(FrozenInstanceError):
            spec.objective = "changed"
        with self.assertRaises(ValidationError):
            ControllerConfig(True, 900, 2)
        with self.assertRaises(ValidationError):
            ProviderConfig("tool", "true")
        config = parse_config(CONFIG_TEXT)
        with self.assertRaises(ValidationError):
            replace(config, providers=None)
        timestamp = utc_now()
        with self.assertRaises(ExecutionGuardError):
            TaskSnapshot(spec, TaskStatus.SUCCEEDED, 1, timestamp, timestamp)
        with self.assertRaises(ValidationError):
            TaskSnapshot(spec, TaskStatus.BLOCKED, 1, timestamp, timestamp)

    def test_canonical_json_is_strict_and_payload_is_immutable(self) -> None:
        source = {"nested": ["original"], "z": 1}
        encoded = canonical_json(source)
        event = LedgerEvent("c" * 32, TASK_ID, 1, "TASK_CREATED", utc_now(), "create:1", encoded)
        source["nested"].append("changed")
        returned = event.to_dict()
        returned["payload"]["nested"].append("changed again")
        self.assertEqual(event.payload_json, '{"nested":["original"],"z":1}')
        for value in (math.nan, math.inf, {1: "not a string key"}, {"x": object()}, {"x": {1, 2}}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                canonical_json(value)
        for value in ('{"x":NaN}', '{"x":1,"x":1}', '{ "x":1}', '"unterminated'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_canonical_json(value)
        with self.assertRaises(ValidationError):
            replace(event, command_id="../bad")
        with self.assertRaises(ValidationError):
            replace(event, timestamp="2026-10-07T00:00:00")
        with self.assertRaises(ValidationError):
            replace(event, event_type=[])

    def test_contract_round_trip_rejects_unknown_fields(self) -> None:
        spec = TaskSpec(TASK_ID, "Preserve exact inputs", Role.QA, Provider.COPILOT, required_tests=("unittest",))
        self.assertEqual(TaskSpec.from_dict(spec.to_dict()), spec)
        timestamp = utc_now()
        snapshot = TaskSnapshot(spec, TaskStatus.CREATED, 1, timestamp, timestamp)
        self.assertEqual(TaskSnapshot.from_dict(snapshot.to_dict()), snapshot)
        with self.assertRaises(ValidationError):
            TaskSpec.from_dict(dict(spec.to_dict(), extra="rejected"))
        with self.assertRaises(ValidationError):
            TaskSnapshot.from_dict(dict(snapshot.to_dict(), revision=True))
