import json

from diamond.daily_improve import EvidenceInput, analyze_evidence
from support import ProjectCase


class DailyImproveTests(ProjectCase):
    def evidence(self, evidence_id: str, kind: str, attempt_id: str, payload: dict,
                 provider: str = "claude", role: str = "builder") -> EvidenceInput:
        return EvidenceInput(evidence_id, attempt_id, kind, "2026-10-12T01:00:00.000000Z",
                             provider, role, payload)

    def test_empty_analysis_is_safe_and_has_no_execution_side_effects(self) -> None:
        report = analyze_evidence(())
        self.assertEqual(report.candidates, ())
        self.assertEqual(report.evidence_records, 0)
        self.assertFalse(report.to_dict()["execution"]["provider_launch"])

    def test_duplicate_evidence_is_one_incident_and_recurrence_increases_score(self) -> None:
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
        self.assertEqual(candidate.evidence_count, 2)
        self.assertEqual(candidate.confidence, "high")
        self.assertIn("controller suite", candidate.tests)
        self.assertGreaterEqual(candidate.score, 50)

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

    def test_cli_analyze_without_a_ledger_returns_empty_report(self) -> None:
        result = self.cli_process("--json", "daily-improve", "analyze")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"]["candidates"], [])
