import asyncio
import io
import json
import os
import runpy
import subprocess
import sys
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import test_live as fixtures
from diamond.config import ProviderConfig, load_config
from diamond.delivery import run_stage
from diamond.delivery_contracts import next_role
from diamond.domain import Provider, Role
from diamond.delivery_store import DeliveryStore
from diamond.store import EventStore
from support import ROOT, ProjectCase


CORRECT_SOURCE = fixtures._SOURCE.replace("map(int, match.groups())",
    "(int(part.lstrip('0') or '0') for part in match.groups())")


class DeliveryLiveTests(ProjectCase):
    def run_driver(self, overrides: dict | None = None, *, routes: dict[Role, Provider] | None = None) -> tuple[int, dict, EventStore]:
        script = runpy.run_path(str(ROOT / "scripts/delivery-live.py"))
        main = script["main"]
        fake = self.fake_executable("fake-routes", f"""import sys,json
if '--version' in sys.argv: print('1.0.0'); sys.exit(0)
prompt=next(arg for arg in sys.argv if arg.startswith('Return only a JSON') or arg.startswith('You are a fresh independent'))
data=json.loads(prompt.split('\\n',1)[1])
if 'owned_files' in data:
 file=data['owned_files'][0]; response={{'files':[{{'relative_path':file['relative_path'],'base_sha256':file['base_sha256'],'content':{CORRECT_SOURCE!r}}}],'summary':'fake-only source'}}
 session='author-'+str(file['base_sha256'])
else:
 role=data['role']; session=data['delivery_id']+'-'+role
 if role=='integrator':
  response={{'base_digest':data['base_digest'],'decisions':[{{'relative_path':change['relative_path'],'workflow_id':data['components'][0]['workflow_id'],'reason':'accepted component selection'}} for change in data['components'][0]['changes']],'summary':'bounded integration'}}
 else:
  resolutions=[]
  for finding in data['inherited_findings']:
   file=next(item for item in data['candidate_files'] if item['relative_path']==finding['relative_path'])
   resolutions.append({{'finding_id':finding['finding_id'],'relative_path':file['relative_path'],'sha256':file['sha256'],'description':'New source passes full duration regression'}})
  response={{'candidate_digest':data['candidate_digest'],'evidence_digest':data['evidence_digest'],'verdict':'PASS','checked_requirements':list(data['requirements']),'findings':[],'resolutions':resolutions,'summary':'independent fake-only audit'}}
print(json.dumps({{'result':json.dumps(response),'session_id':session}}))
""")
        config = load_config(ROOT / "config/diamond.toml")
        providers = dict(config.providers)
        for provider in (Provider.CLAUDE, Provider.CODEX, Provider.COPILOT):
            providers[provider] = ProviderConfig(str(fake), True)
        config = replace(config, providers=providers, roles={**config.roles, **(routes or {})})
        output = io.StringIO()
        state = self.base.resolve() / "live-evidence"
        with patch.dict(main.__globals__, {"load_config": lambda path: config, **(overrides or {})}), redirect_stdout(output):
            code = asyncio.run(main(ROOT, state=state))
        summary = json.loads(output.getvalue())
        report = json.loads(Path(summary["report"]).read_text(encoding="utf-8"))
        self.assertEqual(summary["ok"], report["ok"])
        return code, report, EventStore(state / "diamond.sqlite3")

    def test_fake_only_live_driver_rejects_fault_then_accepts_evidenced_revision(self) -> None:
        code, report, ledger = self.run_driver()
        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])
        rejected, accepted = report["outcomes"]
        self.assertEqual(rejected["actual_status"], "REJECTED")
        self.assertEqual(accepted["actual_status"], "ACCEPTED")
        self.assertEqual(accepted["supersedes"], rejected["delivery_id"])
        self.assertEqual(len(accepted["inherited_findings"]), 1)
        self.assertFalse(accepted["certificate"]["production_authorization"])
        backup = report["backup_validation"]
        self.assertEqual(backup["status"], "PASS")
        self.assertEqual(len(backup["row_counts"]), 19)
        self.assertIn("improvement_publications", backup["row_counts"])
        self.assertIn("improvement_dispositions", backup["row_counts"])
        self.assertEqual(backup["row_counts"]["deliveries"], 2)
        self.assertTrue(backup["all_table_rows_match"])
        self.assertTrue(backup["fresh_restored_cli_matches"])
        self.assertTrue(backup["restored_certificate_matches"])
        deliveries = DeliveryStore(ledger)
        self.assertEqual(len(deliveries.list()), 2)
        for outcome in report["outcomes"]:
            self.assertTrue(outcome["fresh_process_matches"])
            self.assertEqual(deliveries.replay(outcome["delivery_id"]).status.value, outcome["actual_status"])
        for role in ("qa", "reviewer", "release"):
            self.assertEqual(accepted["reports"][role]["response"]["resolutions"][0]["finding_id"], accepted["inherited_findings"][0]["finding_id"])
            self.assertEqual(json.loads(accepted["actual_checks"][role]["tests"][0]["stdout"]), {"passed": 24, "status": "PASS"})

    def test_extended_duration_checks_fail_on_old_conversion_and_pass_on_zero_normalized_source(self) -> None:
        script = runpy.run_path(str(ROOT / "scripts/delivery-live.py"))
        root = self.base / "duration-regression"
        (root / "src").mkdir(parents=True)
        check = root / "check.py"
        check.write_text(script["acceptance_checks"](), encoding="utf-8")
        source = root / "src/duration.py"
        environment = dict(os.environ, PYTHONPATH="", PYTHONHOME="", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                           PYTHONINTMAXSTRDIGITS="4300")
        source.write_text(fixtures._SOURCE, encoding="utf-8")
        command = (sys.executable, "-B", str(check))
        failed = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, timeout=10)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("integer string conversion", failed.stderr)
        source.write_text(CORRECT_SOURCE, encoding="utf-8")
        passed = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, timeout=10)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(json.loads(passed.stdout), {"passed": 24, "status": "PASS"})

    def test_failed_fresh_read_stops_before_corrected_workflow_and_marks_scenario_failed(self) -> None:
        code, report, ledger = self.run_driver({"fresh_read": lambda *args: False})
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(len(report["outcomes"]), 1)
        receipt = report["outcomes"][0]
        self.assertEqual(receipt["actual_status"], "REJECTED")
        self.assertEqual(receipt["status"], "FAIL")
        self.assertFalse(receipt["fresh_process_matches"])
        self.assertEqual(len(ledger.list()), 2)

    def test_prelaunch_stale_candidate_retains_failure_receipt_without_attempt(self) -> None:
        async def stale(ledger, identifier, *args):
            deliveries = DeliveryStore(ledger)
            return deliveries.invalidate(deliveries.get(identifier), "fixture prelaunch stale candidate", stale=True)

        code, report, ledger = self.run_driver({"run_stage": stale})
        self.assertEqual(code, 1)
        self.assertNotIn("error", report)
        receipt = report["outcomes"][0]
        self.assertEqual(receipt["actual_status"], "STALE")
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["attempt_receipt"], "NOT_CREATED")
        self.assertIsNone(DeliveryStore(ledger).get(receipt["delivery_id"]).active_attempt_id)
        self.assertEqual(report["current_delivery_id"], receipt["delivery_id"])

    def test_preprovider_qa_block_retains_receipt_for_allocated_but_absent_attempt(self) -> None:
        async def block(ledger, identifier, revision, *args):
            deliveries = DeliveryStore(ledger)
            if next_role(deliveries.get(identifier).status) is Role.QA:
                active = deliveries.start(identifier, revision)
                return deliveries.invalidate(active, "fixture preprovider check failure")
            return await run_stage(ledger, identifier, revision, *args)

        code, report, ledger = self.run_driver({"run_stage": block})
        self.assertEqual(code, 1)
        self.assertNotIn("error", report)
        receipt = report["outcomes"][1]
        self.assertEqual(receipt["actual_status"], "BLOCKED")
        self.assertEqual(receipt["stages"][-1]["role"], "qa")
        self.assertEqual(receipt["attempt_receipt"], "NOT_CREATED")
        self.assertIsNotNone(DeliveryStore(ledger).get(receipt["delivery_id"]).active_attempt_id)
        self.assertEqual(receipt["status"], "FAIL")

    def test_fresh_read_exception_keeps_initial_manifest_and_partial_scenario(self) -> None:
        def timeout(project, ledger, current):
            paths = list(ledger.database.parent.glob("delivery-acceptance-*.json"))
            initial = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(initial["status"], "RUNNING")
            self.assertTrue(initial["source_manifest"])
            self.assertEqual(initial["current_delivery_id"], current.spec.delivery_id)
            self.assertEqual(initial["current_operation"], "receipt_verification")
            raise TimeoutError("fixture fresh-process timeout")

        code, report, ledger = self.run_driver({"fresh_read": timeout})
        self.assertEqual(code, 1)
        self.assertEqual(report["error"]["type"], "TimeoutError")
        self.assertTrue(report["source_manifest"])
        self.assertIsNotNone(report["source_candidate_sha256"])
        self.assertEqual(len(report["outcomes"]), 1)
        self.assertEqual(report["outcomes"][0]["status"], "FAIL")
        self.assertEqual(len(ledger.list()), 2)

    def test_changed_source_candidate_finalizes_failed_report_without_relabeling_old_digest(self) -> None:
        script = runpy.run_path(str(ROOT / "scripts/delivery-live.py"))
        original = script["candidate_manifest"](ROOT)
        code, report, _ = self.run_driver({"candidate_manifest": lambda project: original if not
            (self.base / "live-evidence/workspaces/target").exists() else {}})
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["error"]["type"], "RuntimeError")
        self.assertEqual(report["source_manifest"], original)
        self.assertEqual([item["status"] for item in report["outcomes"]], ["PASS", "PASS"])

    def test_gemini_route_blocks_before_any_probe_or_execution(self) -> None:
        probe = Mock(side_effect=AssertionError("must not probe"))
        code, report, ledger = self.run_driver({"probe_version": probe}, routes={Role.QA: Provider.GEMINI})
        self.assertEqual(code, 1)
        probe.assert_not_called()
        self.assertIn("quota confirmation remains blocked", report["error"]["detail"])
        self.assertEqual(report["outcomes"], [])
        self.assertEqual(ledger.list(), ())
