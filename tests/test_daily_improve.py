import json
from dataclasses import replace

from diamond.daily_improve import EvidenceInput, analyze_evidence
from support import ProjectCase


class DailyImproveTests(ProjectCase):
    def evidence(self, evidence_id: str, kind: str, attempt_id: str, payload: dict,
                 provider: str = "claude", role: str = "builder") -> EvidenceInput:
        return EvidenceInput(evidence_id, attempt_id, kind, "2026-10-12T01:00:00.000000Z",
                             provider, role, payload, workspace=str(self.project), lineage="one-workflow")

    def test_empty_analysis_is_safe_and_has_no_execution_side_effects(self) -> None:
        report = analyze_evidence(())
        self.assertEqual(report.candidates, ())
        self.assertEqual(report.evidence_records, 0)
        self.assertFalse(report.to_dict()["execution"]["provider_launch"])

    def test_duplicate_evidence_keeps_citations_without_inflating_task_support(self) -> None:
        payload = {"candidate_digest": "a" * 64, "passed": False, "tests": [{
            "name": "controller suite", "passed": False, "stderr": "AssertionError: expected 1, got 0"}]}
        records = (
            self.evidence("1" * 32, "required_tests", "2" * 32, payload),
            self.evidence("3" * 32, "workflow_verification", "2" * 32, {
                "candidate_digest": "a" * 64, "passed": False,
                "failure_packet": {"report_json": json.dumps(payload)}}),
            self.evidence("4" * 32, "required_tests", "5" * 32, {
                **payload, "candidate_digest": "b" * 64}),
        )
        report = analyze_evidence(records)
        self.assertEqual(len(report.candidates), 1)
        candidate = report.candidates[0]
        self.assertEqual(candidate.occurrences, 2)
        self.assertEqual(candidate.evidence_count, 3)
        self.assertEqual(candidate.known_task_lineages, 1)
        self.assertEqual(candidate.evidence_support, "single_lineage")
        self.assertNotIn("confidence", candidate.to_dict())
        self.assertIn("controller suite", candidate.tests)
        self.assertEqual(candidate.score, 35)

    def test_blocking_delivery_finding_identifies_path_and_requires_reconciliation(self) -> None:
        record = self.evidence("6" * 32, "delivery_gate", "7" * 32, {
            "candidate_digest": "c" * 64, "passed": False,
            "findings": [{"severity": "blocking", "relative_path": "src/diamond/workflow.py",
                           "description": "candidate digest does not match the source manifest"}]},
            provider="copilot", role="qa")
        candidate = analyze_evidence((record,)).candidates[0]
        self.assertEqual(candidate.category, "delivery_finding")
        self.assertEqual(candidate.paths, ("src/diamond/workflow.py",))
        self.assertIn("Reconcile", candidate.recommendation)
        self.assertEqual(candidate.adjudication, "unadjudicated_claim")
        self.assertEqual(candidate.severity, "medium")

    def test_cli_analyze_without_a_ledger_returns_empty_report(self) -> None:
        result = self.cli_process("--json", "daily-improve", "analyze")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"]["candidates"], [])
        self.assertFalse((self.project / ".diamond").exists())

    def test_later_pass_is_historical_without_dropping_failure_citations(self):
        failed = self.evidence("1" * 32, "required_tests", "2" * 32, {
            "passed": False, "tests": [{"name": "parser", "passed": False, "stderr": "assertion failed"}]})
        passed = replace(failed, evidence_id="3" * 32, attempt_id="4" * 32, created_at="2026-10-12T02:00:00.000000Z",
                         payload={"passed": True, "tests": [{"name": "parser", "passed": True}]})
        report = analyze_evidence((passed, failed))
        self.assertEqual(report.candidates, ())
        historical, = report.historical_candidates
        self.assertEqual(historical.evidence_ids, (failed.evidence_id,))
        self.assertEqual(historical.resolution_evidence_ids, (passed.evidence_id,))
        self.assertEqual(historical.adjudication, "later_pass_observed")
        unrelated = replace(passed, lineage="another-task")
        self.assertEqual(len(analyze_evidence((unrelated, failed)).candidates), 1)

    def test_different_sources_and_semantic_paths_are_not_merged(self):
        first = self.evidence("1" * 32, "provider_result", "2" * 32, {"execution_status": "nonzero", "stderr": "Error in /src/a.py"})
        other = replace(first, evidence_id="3" * 32, payload={"execution_status": "nonzero", "stderr": "Error in /src/b.py"})
        foreign = replace(first, workspace="/unrelated/project", evidence_id="4" * 32)
        self.assertEqual(len(analyze_evidence((first, other, foreign)).candidates), 3)
