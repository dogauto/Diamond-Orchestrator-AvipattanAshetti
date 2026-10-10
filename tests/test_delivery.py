import asyncio
import hashlib
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from diamond.config import load_config
from diamond.delivery import prompt, run_stage
from diamond.delivery_contracts import DeliverySpec, DeliveryStatus, ROLES
from diamond.delivery_store import DeliveryStore
from diamond.domain import Provider, Role, TaskSpec, TaskStatus
from diamond.errors import ConflictError, ExecutionGuardError, StorageError, ValidationError
from diamond.integration import capture_components
from diamond.evidence import AttemptStatus, ExecutionAttempt
from diamond.packets import OutputContract, TaskPacket
from diamond.schema import SCHEMA_STATEMENTS, SCHEMA_VERSION
from diamond.store import EventStore
from diamond.skills import SkillRegistry
from diamond.supervisor import ExecutionResult, ExecutionStatus
from diamond.validation import canonical_json, utc_now
from diamond.workflow import run_step
from diamond.workflow_contracts import TestCommand, WorkflowSpec, WorkflowStatus
from diamond.workflow_store import WorkflowStore
from diamond.workspace import workspace_manifest
from support import ProjectCase


class DeliveryTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.base.resolve() / "target"
        (self.root / "src").mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "src" / "value.py").write_text("def value(): return 0\n")
        (self.root / "tests" / "acceptance.py").write_text("import sys\nsys.path.insert(0,'.')\nfrom src.value import value\nassert value()==42\nprint('accepted 42')\n")
        self.ledger = EventStore(self.base.resolve() / "state" / "diamond.sqlite3")
        self.deliveries = DeliveryStore(self.ledger)
        self.config_path = self.project / "config" / "diamond.toml"
        self.config = load_config(self.config_path)
        self.config_digest = hashlib.sha256(self.config_path.read_bytes()).hexdigest()
        self.tests = (TestCommand("value acceptance", (sys.executable, "-B", "tests/acceptance.py")),)
        self.requirements = canonical_json({"value-contract": {"description": "value() must return 42", "tests": ["value acceptance"]}})

    def component(self, value=42, *, session=True):
        identifier = uuid4().hex
        root = self.base.resolve() / f"source-{identifier}"
        shutil.copytree(self.root, root)
        source = f"def value(): return {value}\n"
        fake = self.fake_executable(f"author-{identifier}", f"""import sys,json
data=json.loads(sys.argv[-1].split('\\n',1)[1])
file=data['owned_files'][0]
response={{'files':[{{'relative_path':file['relative_path'],'base_sha256':file['base_sha256'],'content':{source!r}}}],'summary':'verified component'}}
print(json.dumps({{'result':json.dumps(response),'session_id':{('author-' + identifier) if session else None!r}}}))
""")
        test = TestCommand("component executable", (sys.executable, "-B", "-c", "from src.value import value; assert value() in (41,42,43)"))
        spec = WorkflowSpec(identifier, "Implement value()", str(root), ("src/value.py",), (test,), Provider.CLAUDE,
                            Provider.CODEX, self.config_digest, workspace_manifest(root), timeout_seconds=10)
        created = WorkflowStore(self.ledger).create(spec, f"source:{identifier}")
        verified = asyncio.run(run_step(self.ledger, identifier, created.revision, self.config_digest, str(fake), "fake-only"))
        self.assertEqual(verified.status, WorkflowStatus.VERIFIED, verified.detail)
        return verified

    def create(self, *components, supersedes=None):
        base = workspace_manifest(self.root)
        refs = tuple((item.spec.workflow_id, item.revision) for item in components)
        captured = capture_components(self.ledger, refs, self.root, base)
        spec = DeliverySpec(uuid4().hex, "Deliver value() returning 42", str(self.root), base, captured, self.tests,
            self.requirements, tuple(self.config.resolve_provider(role) for role in ROLES), self.config_digest,
            supersedes=supersedes, timeout_seconds=10)
        return self.deliveries.create(spec, f"create:{spec.delivery_id}")

    def provider(self, *, verdict="PASS", finding_role=None, wrong_digest=False, session=None, missing_requirement=False,
                  bad_resolution=False, choice_index=0, check_prompt_contract=False, schema_identifiers=False):
        return self.fake_executable("delivery-provider", f"""import sys,json
if '--version' in sys.argv: print('1.0.0'); sys.exit(0)
prompt=next(arg for arg in sys.argv if arg.startswith('You are a fresh independent delivery stage.'))
instructions,serialized=prompt.split('\\n',1); data=json.loads(serialized); role=data['role']
if {check_prompt_contract!r} and any(field in instructions and field not in data['response_schema']['required'] for field in ('candidate_digest','evidence_digest')):
 print(json.dumps({{'result':'Instructions require fields absent from the response contract','session_id':data['delivery_id']+'-'+role}})); sys.exit(0)
if role=='integrator':
 components=data['components']; paths=sorted({{change['relative_path'] for item in components for change in item['changes']}})
 choices=[{{'relative_path':path,'workflow_id':next(item['workflow_id'] for item in components[{choice_index}:]+components[:{choice_index}] if any(c['relative_path']==path for c in item['changes'])),'reason':'explicit verified selection'}} for path in paths]
 response={{'base_digest':data['base_digest'],'decisions':choices,'summary':'integrated candidate'}}
else:
 resolutions=[]
 for finding in data['inherited_findings']:
  file=next(item for item in data['candidate_files'] if item['relative_path']==finding['relative_path'])
  resolutions.append({{'finding_id':finding['finding_id'],'relative_path':file['relative_path'],'sha256':'0'*64 if {bad_resolution!r} else file['sha256'],'description':'Changed source now satisfies the captured regression'}})
 findings=[]
 if role=={finding_role!r}: findings=[{{'finding_id':'blocking-value','requirement_id':'value-contract','severity':'blocking','relative_path':'src/value.py','line':1,'description':'Unresolved candidate defect'}}]
 checked=list(data['requirements'])
 if {schema_identifiers!r}: checked=data['response_schema']['properties']['checked_requirements']['items'].get('enum') or [item['description'] for item in data['requirements'].values()]
 response={{'candidate_digest':'0'*64 if {wrong_digest!r} else data['candidate_digest'],'evidence_digest':data['evidence_digest'],
 'verdict':{verdict!r},'checked_requirements':[] if {missing_requirement!r} else checked,
 'findings':findings,'resolutions':resolutions,'summary':'independent scoped inspection'}}
session={session!r} or data['delivery_id']+'-'+role
if session=='UNKNOWN': session=None
print(json.dumps({{'result':json.dumps(response),'session_id':session}}))
""")

    def step(self, current, executable=None):
        return asyncio.run(run_stage(self.ledger, current.spec.delivery_id, current.revision, self.config_digest,
                                    str(executable or self.provider()), "fake-only"))

    def pipeline(self, created, executable=None):
        states = []
        current = created
        for expected in (DeliveryStatus.INTEGRATED, DeliveryStatus.QA_PASSED, DeliveryStatus.REVIEW_PASSED, DeliveryStatus.ACCEPTED):
            current = self.step(current, executable)
            self.assertEqual(current.status, expected, current.detail)
            states.append(current)
        return states

    def test_exact_candidate_runs_serial_independent_stages_and_actual_tests(self) -> None:
        states = self.pipeline(self.create(self.component()))
        accepted = states[-1]
        proof = self.deliveries.certificate(accepted.spec.delivery_id, self.config_digest)
        self.assertFalse(proof["production_authorization"])
        self.assertEqual(set(proof["gate_reports"]), {role.value for role in ROLES})
        self.assertEqual([item["passed"] for item in proof["gate_reports"].values()], [True] * 4)
        self.assertEqual(self.deliveries.replay(accepted.spec.delivery_id), accepted)
        self.assertEqual(len({item["session_id"] for item in proof["gate_reports"].values()}), 4)
        for state, role in zip(states, ROLES):
            reports = json.loads(state.reports_json)
            record_id = reports[role.value]["tests_evidence_id"]
            records = [item for task in self.ledger.list() for item in self.ledger.evidence(task.spec.task_id)]
            report = next(item.to_dict()["payload"] for item in records if item.evidence_id == record_id)
            self.assertEqual(report["tests"][0]["returncode"], 0)
            self.assertEqual(report["tests"][0]["stdout"], "accepted 42\n")
        self.assertEqual((self.root / "src/value.py").read_text(), "def value(): return 42\n")

    def test_conflicting_forks_require_retained_explicit_choice(self) -> None:
        first, second = self.component(43), self.component(42)
        created = self.create(first, second)
        integrated = self.step(created, self.provider(choice_index=1))
        self.assertEqual(integrated.status, DeliveryStatus.INTEGRATED, integrated.detail)
        self.assertEqual(json.loads(integrated.decisions_json)[0]["workflow_id"], second.spec.workflow_id)
        self.assertEqual((self.root / "src/value.py").read_text(), "def value(): return 42\n")

    def test_actual_failed_tests_override_provider_pass_and_block_next_stage(self) -> None:
        rejected = self.step(self.create(self.component(41)))
        self.assertEqual(rejected.status, DeliveryStatus.REJECTED)
        report = json.loads(rejected.reports_json)["integrator"]
        self.assertFalse(report["passed"])
        self.assertEqual(report["findings"][0]["severity"], "blocking")
        count = len(self.ledger.list())
        with self.assertRaises(ExecutionGuardError):
            self.step(rejected)
        self.assertEqual(len(self.ledger.list()), count)
        with self.assertRaises(ExecutionGuardError):
            self.create(self.component(42))

    def test_findings_survive_revision_and_require_changed_source_plus_fresh_gates(self) -> None:
        rejected = self.step(self.create(self.component(41)))
        revised = self.create(self.component(42), supersedes=rejected.spec.delivery_id)
        inherited = json.loads(revised.inherited_findings_json)
        self.assertEqual(inherited[0]["finding_id"], json.loads(rejected.reports_json)["integrator"]["findings"][0]["finding_id"])
        accepted = self.pipeline(revised)[-1]
        for role in ROLES[1:]:
            resolutions = json.loads(accepted.reports_json)[role.value]["response"]["resolutions"]
            self.assertEqual(resolutions[0]["finding_id"], inherited[0]["finding_id"])
        self.assertTrue(self.deliveries.certificate(accepted.spec.delivery_id, self.config_digest))

    def test_role_instructions_and_response_schema_allow_actual_rejection_then_independent_acceptance(self) -> None:
        provider = self.provider(check_prompt_contract=True)
        rejected = self.step(self.create(self.component(41)), provider)
        self.assertEqual(rejected.status, DeliveryStatus.REJECTED, rejected.detail)
        revised = self.create(self.component(42), supersedes=rejected.spec.delivery_id)
        accepted = self.pipeline(revised, provider)[-1]
        self.assertTrue(self.deliveries.certificate(accepted.spec.delivery_id, self.config_digest))

    def test_delivery_prompt_forbids_tool_claims_and_requires_direct_json(self) -> None:
        created = self.create(self.component())
        value = prompt(created, Role.INTEGRATOR, None, None)
        self.assertIn("exactly one JSON object", value)
        self.assertIn("Do not call, mention, or claim", value)

    def test_integrator_schema_binds_component_identifiers(self) -> None:
        created = self.create(self.component())
        value = prompt(created, Role.INTEGRATOR, None, None)
        serialized = json.loads(value.split("\n", 1)[1])
        schema = serialized["response_schema"]["properties"]["decisions"]["items"]["properties"]
        component = json.loads(created.spec.components_json)[0]
        self.assertEqual(schema["relative_path"]["enum"], ["src/value.py"])
        self.assertEqual(schema["workflow_id"]["enum"], [component["workflow_id"]])

    def test_codex_schema_cleanup_failure_blocks_before_reviewer_finish(self) -> None:
        qa = self.step(self.step(self.create(self.component())))
        with patch("diamond.delivery.Path.unlink", side_effect=OSError("fixture schema cleanup failure")):
            blocked = self.step(qa)
        self.assertEqual(blocked.status, DeliveryStatus.BLOCKED)
        self.assertIn("output schema", blocked.detail)
        self.assertNotIn("reviewer", json.loads(blocked.reports_json))

    def test_schema_driven_audits_report_exact_requirement_ids_instead_of_descriptions(self) -> None:
        criteria = json.loads(self.requirements)
        criteria["value-call"] = {"description": "Calling value() without arguments returns 42", "tests": ["value acceptance"]}
        self.requirements = canonical_json(criteria)
        accepted = self.pipeline(self.create(self.component()), self.provider(schema_identifiers=True))[-1]
        for role in ROLES[1:]:
            checked = json.loads(accepted.reports_json)[role.value]["response"]["checked_requirements"]
            self.assertEqual(checked, sorted(criteria))

    def test_unsupported_resolution_and_requirement_weakening_do_not_close_findings(self) -> None:
        rejected = self.step(self.create(self.component(41)))
        source = self.component(42)
        base = workspace_manifest(self.root)
        components = capture_components(self.ledger, ((source.spec.workflow_id, source.revision),), self.root, base)
        with self.assertRaises(ValidationError):
            self.deliveries.create(DeliverySpec(uuid4().hex, "new scope", str(self.root), base, components, self.tests,
                canonical_json({"weakened": {"description": "always pass", "tests": ["value acceptance"]}}),
                tuple(self.config.resolve_provider(role) for role in ROLES), self.config_digest, supersedes=rejected.spec.delivery_id), "weaken")
        revised = self.create(source, supersedes=rejected.spec.delivery_id)
        integrated = self.step(revised)
        blocked = self.step(integrated, self.provider(bad_resolution=True))
        self.assertEqual(blocked.status, DeliveryStatus.BLOCKED)
        self.assertIn("resolution", blocked.detail)
        self.assertEqual(json.loads(blocked.inherited_findings_json), json.loads(revised.inherited_findings_json))

    def test_blocking_reviewer_finding_cannot_be_overridden_by_pass_text(self) -> None:
        created = self.create(self.component())
        qa = self.step(self.step(created))
        rejected = self.step(qa, self.provider(finding_role="reviewer"))
        self.assertEqual(rejected.status, DeliveryStatus.REJECTED, rejected.detail)
        self.assertEqual(json.loads(rejected.reports_json)["reviewer"]["findings"][0]["finding_id"], "blocking-value")
        with self.assertRaises(ExecutionGuardError):
            self.deliveries.certificate(rejected.spec.delivery_id, self.config_digest)

    def test_candidate_edit_after_review_and_after_acceptance_invalidates_release(self) -> None:
        created = self.create(self.component())
        reviewed = self.step(self.step(self.step(created)))
        (self.root / "src/value.py").write_text("human revision\n")
        stale = self.step(reviewed)
        self.assertEqual(stale.status, DeliveryStatus.STALE)
        self.assertEqual((self.root / "src/value.py").read_text(), "human revision\n")
        with self.assertRaises(ExecutionGuardError):
            self.deliveries.certificate(stale.spec.delivery_id, self.config_digest)

    def test_unknown_author_identity_cannot_be_presented_as_independent_acceptance(self) -> None:
        created = self.create(self.component(session=False))
        blocked = self.step(created)
        self.assertEqual(blocked.status, DeliveryStatus.BLOCKED)
        self.assertIn("author session identity is unknown", blocked.detail)
        self.assertEqual((self.root / "src/value.py").read_text(), "def value(): return 0\n")

    def test_reusing_a_stage_session_or_author_session_blocks_independence(self) -> None:
        source = self.component()
        created = self.create(source)
        author = json.loads(created.spec.components_json)[0]["authors"][0]["session_id"]
        blocked = self.step(created, self.provider(session=author))
        self.assertEqual(blocked.status, DeliveryStatus.BLOCKED)
        self.assertIn("reused", blocked.detail)

    def test_release_cannot_reuse_qa_session_even_with_same_provider(self) -> None:
        integrated = self.step(self.create(self.component()))
        qa = self.step(integrated)
        reviewed = self.step(qa)
        qa_session = json.loads(qa.reports_json)["qa"]["session_id"]
        blocked = self.step(reviewed, self.provider(session=qa_session))
        self.assertEqual(blocked.status, DeliveryStatus.BLOCKED)
        self.assertIn("reused", blocked.detail)

    def test_forged_test_digest_missing_requirement_and_unknown_session_are_blocked(self) -> None:
        for mode in ("digest", "requirements", "unknown"):
            with self.subTest(mode=mode):
                self.ledger = EventStore(self.base.resolve() / f"state-{mode}" / "diamond.sqlite3")
                self.deliveries = DeliveryStore(self.ledger)
                root = self.root
                self.root = self.base.resolve() / f"target-{mode}"
                shutil.copytree(root, self.root)
                integrated = self.step(self.create(self.component()))
                fake = self.provider(wrong_digest=mode == "digest", missing_requirement=mode == "requirements",
                                     session="UNKNOWN" if mode == "unknown" else None)
                blocked = self.step(integrated, fake)
                self.assertEqual(blocked.status, DeliveryStatus.BLOCKED, blocked.detail)
                self.root = root

    def test_one_shot_start_revision_cas_and_cross_workflow_ownership(self) -> None:
        created = self.create(self.component())
        active = self.deliveries.start(created.spec.delivery_id, created.revision)
        with self.assertRaises(ConflictError):
            self.deliveries.start(created.spec.delivery_id, created.revision)
        source = self.component()
        other = replace(source.spec, workflow_id=uuid4().hex, workspace_root=str(self.root), initial_manifest_json=workspace_manifest(self.root))
        workflow = WorkflowStore(self.ledger).create(other, "other-workflow")
        with self.assertRaises(ConflictError):
            WorkflowStore(self.ledger).start(workflow.spec.workflow_id, workflow.revision, "other-start")
        quarantined = self.deliveries.invalidate(active, "interrupted stage")
        with self.assertRaises(ConflictError):
            self.create(source)
        self.assertEqual(quarantined.status, DeliveryStatus.BLOCKED)

    def test_late_provider_result_cannot_publish_after_quarantine(self) -> None:
        from diamond.delivery import run_persisted
        created = self.create(self.component())

        async def late(*args, **kwargs):
            result = await run_persisted(*args, **kwargs)
            current = self.deliveries.get(created.spec.delivery_id)
            self.deliveries.invalidate(current, "operator reconciliation")
            return result

        with patch("diamond.delivery.run_persisted", new=late):
            blocked = self.step(created)
        self.assertEqual(blocked.status, DeliveryStatus.BLOCKED)
        self.assertEqual((self.root / "src/value.py").read_text(), "def value(): return 0\n")

    def test_cleanup_failure_retains_actual_test_facts_and_quarantines(self) -> None:
        created = self.create(self.component())

        async def uncertain(*args, **kwargs):
            return ExecutionResult(ExecutionStatus.CLEANUP_FAILED, None, "partial", "unknown process", cleanup_complete=False)

        with patch("diamond.workflow.run", new=uncertain):
            blocked = self.step(created)
        self.assertEqual(blocked.status, DeliveryStatus.BLOCKED, blocked.detail)
        self.assertTrue(any(item.kind == "test_result" and not item.to_dict()["payload"]["outcome"]["cleanup_complete"]
                            for item in self.ledger.evidence(blocked.active_task_id)))

    def test_transaction_failure_rolls_back_start_projection_and_command(self) -> None:
        created = self.create(self.component())
        with closing(sqlite3.connect(self.ledger.database)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_delivery_event BEFORE INSERT ON delivery_events WHEN NEW.revision>1 BEGIN SELECT RAISE(ABORT,'fault'); END")
        with self.assertRaises(StorageError):
            self.deliveries.start(created.spec.delivery_id, created.revision)
        self.assertEqual(self.deliveries.replay(created.spec.delivery_id), created)

    def test_backup_and_explicit_schema_five_migration_preserve_history(self) -> None:
        accepted = self.pipeline(self.create(self.component()))[-1]
        backup = self.base.resolve() / "verified.sqlite3"
        self.ledger.backup(backup)
        restored = DeliveryStore(EventStore(backup))
        self.assertEqual(restored.replay(accepted.spec.delivery_id), accepted)
        self.assertEqual(restored.certificate(accepted.spec.delivery_id, self.config_digest),
                         self.deliveries.certificate(accepted.spec.delivery_id, self.config_digest))
        old = self.base.resolve() / "legacy.sqlite3"
        with closing(sqlite3.connect(old)) as connection, connection:
            for statement in SCHEMA_STATEMENTS[:31]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version=5")
        ledger = EventStore(old)
        before = old.read_bytes()
        with self.assertRaises(StorageError):
            ledger.list()
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(ledger.migrate(), SCHEMA_VERSION)
        self.assertEqual(DeliveryStore(ledger).list(), ())

    def test_cli_create_show_and_certificate_use_retained_state(self) -> None:
        source = self.component()
        args = ("--state-dir", str(self.ledger.database.parent), "--json", "delivery", "create",
            "--objective", "Deliver value() returning 42", "--workspace", str(self.root),
            "--component", f"{source.spec.workflow_id}:{source.revision}", "--test", canonical_json(self.tests[0].to_dict()),
            "--requirements", self.requirements, "--command-id", "cli-delivery")
        result = self.cli_process(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        created = json.loads(result.stdout)["result"]["delivery"]
        read = self.cli_process("--state-dir", str(self.ledger.database.parent), "--json", "delivery", "show", created["spec"]["delivery_id"])
        self.assertEqual(json.loads(read.stdout)["result"]["delivery"], created)
        accepted = self.pipeline(self.deliveries.get(created["spec"]["delivery_id"]))[-1]
        proof = self.cli_process("--state-dir", str(self.ledger.database.parent), "--json", "delivery", "certificate", accepted.spec.delivery_id)
        self.assertEqual(proof.returncode, 0, proof.stdout + proof.stderr)
        self.assertEqual(json.loads(proof.stdout)["result"]["certificate"]["candidate_digest"], json.loads(accepted.reports_json)["release"]["candidate_digest"])

    def writer(self, identifier, *, root=None, owned=()):
        root = root or self.project.resolve()
        task = TaskSpec(identifier, "occupied worker", Role.QA, Provider.CLAUDE, owned_paths=owned, allowed_tools=(sys.executable,))
        self.ledger.create(task, f"writer:{identifier}")
        packet = TaskPacket.from_task_spec(task, uuid4().hex, root, 1, "1" * 64, "2" * 64, self.config_digest,
                                          SkillRegistry(()), (), OutputContract("text", (), 4096))
        attempt = ExecutionAttempt(packet.attempt_id, identifier, 1, task.role, task.provider, sys.executable,
                                   str(root), AttemptStatus.INTENDED, utc_now())
        return self.ledger.reserve_dispatch(packet, attempt, "writer", f"claim:{identifier}")[0]

    def test_external_test_changes_before_execution_and_after_acceptance_invalidate_evidence(self) -> None:
        harness = self.base.resolve() / "external-acceptance.py"
        shutil.copyfile(self.root / "tests/acceptance.py", harness)
        self.tests = (TestCommand("value acceptance", (sys.executable, "-B", str(harness))),)
        created = self.create(self.component(41))
        harness.write_text("print('forged success')\n")
        count = len(self.ledger.list())
        stale = self.step(created)
        self.assertEqual(stale.status, DeliveryStatus.STALE)
        self.assertEqual(len(self.ledger.list()), count)
        self.assertEqual((self.root / "src/value.py").read_text(), "def value(): return 0\n")
        harness.write_bytes((self.root / "tests/acceptance.py").read_bytes())
        accepted = self.pipeline(self.create(self.component(), supersedes=stale.spec.delivery_id))[-1]
        harness.write_text("print('changed after acceptance')\n")
        with self.assertRaises(ExecutionGuardError):
            self.deliveries.certificate(accepted.spec.delivery_id, self.config_digest)
        self.assertEqual(self.deliveries.get(accepted.spec.delivery_id).status, DeliveryStatus.STALE)

    def test_invalidating_an_ancestor_cannot_drop_newer_blocking_findings(self) -> None:
        first = self.step(self.create(self.component(43)))
        second = self.step(self.create(self.component(41), supersedes=first.spec.delivery_id))
        newer_id = json.loads(second.reports_json)["integrator"]["findings"][0]["finding_id"]
        self.deliveries.validate(first.spec.delivery_id, self.config_digest)
        source = self.component(42)
        with self.assertRaises(ExecutionGuardError):
            self.create(source, supersedes=first.spec.delivery_id)
        third = self.create(source, supersedes=second.spec.delivery_id)
        self.assertIn(newer_id, {f["finding_id"] for f in json.loads(third.inherited_findings_json)})
        accepted = self.pipeline(third)[-1]
        for role in ROLES[1:]:
            self.assertIn(newer_id, {item["finding_id"] for item in json.loads(accepted.reports_json)[role.value]["response"]["resolutions"]})

    def test_revoked_integrator_task_cannot_publish_or_advance(self) -> None:
        from diamond.delivery import run_persisted
        created = self.create(self.component())

        async def revoked(*args, **kwargs):
            result = await run_persisted(*args, **kwargs)
            attempt = result[0]
            self.ledger.transition(attempt.task_id, TaskStatus.CANCELLED, attempt.task_revision, "revoke")
            return result

        with patch("diamond.delivery.run_persisted", new=revoked):
            blocked = self.step(created)
        self.assertEqual(blocked.status, DeliveryStatus.BLOCKED)
        self.assertIn("authorization changed", blocked.detail)
        self.assertEqual((self.root / "src/value.py").read_text(), "def value(): return 0\n")

    def test_existing_claim_or_workflow_writer_prevents_freezing_target(self) -> None:
        source = self.component()
        claim = self.writer(uuid4().hex, root=self.root, owned=("src/value.py",))
        with self.assertRaises(ConflictError):
            self.create(source)
        self.assertEqual(self.deliveries.list(), ())
        # Launches are still authorized for the existing writer because no delivery was frozen.
        self.assertEqual(self.ledger.acquire_start(claim, "launch-writer").status, AttemptStatus.STARTED)
        self.ledger = EventStore(self.base.resolve() / "workflow-race-state" / "diamond.sqlite3")
        self.deliveries = DeliveryStore(self.ledger)
        source = self.component()
        spec = replace(source.spec, workflow_id=uuid4().hex, workspace_root=str(self.root), initial_manifest_json=workspace_manifest(self.root))
        created = WorkflowStore(self.ledger).create(spec, "writer-workflow")
        WorkflowStore(self.ledger).start(spec.workflow_id, created.revision, "writer-workflow-start")
        with self.assertRaises(ConflictError):
            self.create(source)
        self.assertEqual(self.deliveries.list(), ())

    def test_dispatch_capacity_blocks_stage_before_any_executable_check(self) -> None:
        integrated = self.step(self.create(self.component()))
        for _ in range(2):
            claim = self.writer(uuid4().hex)
            self.ledger.acquire_start(claim, f"launch:{claim.attempt_id}")
        with patch("diamond.delivery.required_tests", new=AsyncMock()) as checks:
            with self.assertRaisesRegex(ConflictError, "capacity"):
                self.step(integrated)
            checks.assert_not_awaited()
        self.assertEqual(self.deliveries.get(integrated.spec.delivery_id), integrated)

    def test_active_stage_reserves_capacity_before_provider_dispatch(self) -> None:
        integrated = self.step(self.create(self.component()))
        active = self.deliveries.start(integrated.spec.delivery_id, integrated.revision)
        self.writer(uuid4().hex)
        with self.assertRaisesRegex(ConflictError, "capacity"):
            self.writer(uuid4().hex)
        self.assertEqual(self.deliveries.get(active.spec.delivery_id), active)

    def test_resolved_historical_findings_do_not_block_a_subsequent_candidate(self) -> None:
        rejected = self.step(self.create(self.component(41)))
        accepted = self.pipeline(self.create(self.component(), supersedes=rejected.spec.delivery_id))[-1]
        self.assertTrue(json.loads(accepted.inherited_findings_json))
        later = self.create(self.component(43))
        self.assertEqual(json.loads(later.inherited_findings_json), [])
        self.assertIsNone(later.spec.supersedes)
