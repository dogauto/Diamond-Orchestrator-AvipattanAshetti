import asyncio
import hashlib
import io
import json
import runpy
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from diamond.config import ProviderConfig, load_config
from diamond.domain import Provider
from diamond.errors import ValidationError
from diamond.live import _CODE_BRIEF, _CODE_CHECKS, smoke, validate_code
from diamond.store import EventStore
from diamond.workflow_store import WorkflowStore
from support import ROOT, ProjectCase


_SOURCE = '''import re
def parse_duration(value: str) -> int:
    if not isinstance(value, str):
        raise TypeError('must be text')
    match = re.fullmatch(r'([0-9]+)m([0-9]+)s', value)
    if not match:
        raise ValueError('invalid duration')
    minutes, seconds = map(int, match.groups())
    if minutes > 1440 or seconds > 59:
        raise ValueError('out of range')
    return minutes * 60 + seconds
'''


class LiveTests(ProjectCase):
    def test_smoke_real_path_with_fake_only_runs_checks_restarts_and_rejects_duplicate(self) -> None:
        body = ("import json,sys\n"
                "if '--version' in sys.argv:\n print('1.0.0'); sys.exit(0)\n"
                f"source = {_SOURCE!r}\n"
                "prompt = sys.argv[-1]\n"
                "content = json.dumps({'code':source}) if 'parse_duration' in prompt else 'DIAMOND_PERSISTED_CLAUDE_OK'\n"
                "print(json.dumps({'result':content, 'session_id':'fake-only-session'}))")
        fake = self.fake_executable("fake claude", body)
        config = load_config(self.project / "config" / "diamond.toml")
        providers = dict(config.providers)
        providers[Provider.CLAUDE] = ProviderConfig(str(fake), True)
        config = replace(config, providers=providers)
        store = EventStore(self.project / ".diamond" / "diamond.sqlite3")
        result = asyncio.run(smoke(ROOT, config, self.project / "config" / "diamond.toml", store,
                                   (Provider.CLAUDE,), code_test=True))
        self.assertTrue(result["ok"], result["outcomes"])
        self.assertEqual([item["operation"] for item in result["outcomes"]], ["marker", "code-response"])
        self.assertEqual(result["outcomes"][1]["checks"], {"passed": 22, "status": "PASS"})
        for outcome in result["outcomes"]:
            self.assertTrue(outcome["restart_verified"])
            self.assertTrue(outcome["duplicate_rejected"])
            self.assertEqual(outcome["session_id"], "fake-only-session")

    def test_gemini_quota_gate_prevents_any_probe_or_model_launch(self) -> None:
        config = load_config(self.project / "config" / "diamond.toml")
        store = EventStore(self.project / ".diamond" / "diamond.sqlite3")
        with patch("diamond.live.probe_version") as probe:
            with self.assertRaisesRegex(ValidationError, "quota"):
                asyncio.run(smoke(ROOT, config, self.project / "config" / "diamond.toml", store, (Provider.GEMINI,)))
            probe.assert_not_called()
        self.assertFalse(store.database.exists())

    def test_fixture_source_validation_rejects_extra_actions_and_imports(self) -> None:
        self.assertEqual(validate_code(_SOURCE), _SOURCE)
        for source in (_SOURCE + "print('extra')", "import os\n" + _SOURCE, "def other(): pass",
                       "@print('decorator')\ndef parse_duration(value): return 0"):
            with self.assertRaises(ValidationError):
                validate_code(source)

    def test_workflow_live_fixture_injects_recorded_fault_and_corrects_real_failures(self) -> None:
        source = _SOURCE.replace("[0-9]+", r"\d+")
        body = ("import json,sys\n"
                "if '--version' in sys.argv: print('1.0.0'); sys.exit(0)\n"
                "data=json.loads(sys.argv[-1].split('\\n',1)[1])\n"
                f"content={source!r} if 'failure_packet' not in data else {_SOURCE!r}\n"
                "file=data['owned_files'][0]\n"
                "response={'files':[{'relative_path':file['relative_path'],'base_sha256':file['base_sha256'],'content':content}],"
                "'summary':'fake-only fixture'}\n"
                "if 'failure_packet' in data:\n"
                " response.update(root_cause='digits or return value violate the contract', failure_digest=data['failure_digest'],"
                "reproduction_tests=[data['required_tests'][0]])\n"
                "print(json.dumps({'result':json.dumps(response),'session_id':'fake-only'}))")
        fake = self.fake_executable("fake workflow routes", body)
        config = load_config(ROOT / "config" / "diamond.toml")
        providers = dict(config.providers)
        for provider in (Provider.CLAUDE, Provider.CODEX):
            providers[provider] = ProviderConfig(str(fake), True)
        config = replace(config, providers=providers)
        script = runpy.run_path(str(ROOT / "scripts" / "workflow-live.py"))
        main = script["main"]
        output = io.StringIO()
        state = self.base / "workflow-evidence"
        with patch.dict(main.__globals__, {"load_config": lambda path: config}), redirect_stdout(output):
            code = asyncio.run(main(ROOT, state=state))
        self.assertEqual(code, 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertTrue(result["ok"])
        ledger = EventStore(state / "diamond.sqlite3")
        self.assertEqual(len(ledger.list()), 4)
        for outcome in result["outcomes"]:
            self.assertEqual(outcome["first_status"], "NEEDS_CORRECTION")
            self.assertEqual(outcome["final_status"], "VERIFIED")
            self.assertEqual(outcome["corrections_used"], 1)
            self.assertTrue(outcome["fresh_process_restart_matches"])
            self.assertEqual(outcome["verified_checks"], {"passed": 22, "status": "PASS"})
            self.assertEqual(WorkflowStore(ledger).replay(outcome["workflow_id"]).status.value, "VERIFIED")
            retained = Path(outcome["retained_artifact"])
            self.assertEqual(hashlib.sha256(retained.read_bytes()).hexdigest(), outcome["artifact_sha256"])
        imported = result["outcomes"][1]
        self.assertEqual(imported["fault_injection"]["origin"], "controller_acceptance_fixture")
        self.assertEqual(imported["initial_artifact_sha256"], hashlib.sha256(script["_BUGGY"].encode()).hexdigest())
        for steps in (imported["steps"], list(reversed(imported["steps"]))):
            attempt = next(item for item in steps if item["role"] == "builder")["attempts"][0]
            evidence = ledger.evidence(attempt["task_id"])
            injection = next(item.to_dict()["payload"] for item in evidence if item.kind == "fixture_fault_injection")
            self.assertEqual(injection["provider_source_sha256"], hashlib.sha256(source.encode()).hexdigest())
            self.assertEqual(injection["injected_source_sha256"], imported["initial_artifact_sha256"])
