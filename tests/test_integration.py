import asyncio
import copy
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from diamond.domain import Provider, Role, TaskSpec
from diamond.errors import ConflictError, ExecutionGuardError, StorageError, ValidationError
from diamond.evidence import AttemptStatus, digest_text
from diamond.integration import capture_components, integration_response
from diamond.store import EventStore
from diamond.validation import canonical_json
from diamond.workflow import record, run_step
from diamond.workflow_contracts import TestCommand, WorkflowSpec, WorkflowStatus
from diamond.workflow_store import WorkflowStore
from diamond.workspace import apply_response, workspace_manifest
from support import ProjectCase


class IntegrationTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.ledger = EventStore(self.base.resolve() / "state" / "diamond.sqlite3")
        self.workflows = WorkflowStore(self.ledger)
        self.target = self.base.resolve() / "target with spaces"
        (self.target / "src").mkdir(parents=True)
        (self.target / "src/value.py").write_bytes(b"# human base\r\ndef value(): return 0\r\n")
        self.base_manifest = workspace_manifest(self.target)
        self.runs = {}

    def build(self, versions=None, *, owned=("src/value.py",), session="fixture-session"):
        versions = versions or ({owned[0]: "def value(): return 42\n"},)
        identifier = uuid4().hex
        root = self.base.resolve() / f"fork-{identifier}"
        shutil.copytree(self.target, root)
        tests = tuple(TestCommand(f"regression {relative}", (sys.executable, "-B", "-c",
            f"from pathlib import Path; scope={{}}; exec(Path({relative!r}).read_text(), scope); assert scope['value']() in (42,43)"))
            for relative in owned)
        spec = WorkflowSpec(identifier, "implement and verify bounded source", str(root), owned, tests,
            Provider.CLAUDE, Provider.CODEX, "f" * 64, workspace_manifest(root), timeout_seconds=10)
        body = f"""import json,sys
data=json.loads(sys.argv[-1].split('\\n',1)[1])
debug='failure_packet' in data
index=data['failure_packet']['round_number']+1 if debug else 0
versions=json.loads({json.dumps(versions)!r})
current={{item['relative_path']:item for item in data['owned_files']}}
response={{'files':[{{'relative_path':path,'base_sha256':current[path]['base_sha256'],'content':content}} for path,content in versions[index].items()], 'summary':'real fake workflow fixture'}}
if debug:
 response.update(root_cause='incorrect return value',failure_digest=data['failure_digest'],reproduction_tests=[item['name'] for item in data['reproduction']['tests'] if not item['passed']])
session={session!r}
if session is not None: session+=('-debugger' if debug else '-builder')+'-{identifier}'
print(json.dumps({{'result':json.dumps(response),'session_id':session}}))
"""
        executable = self.fake_executable(f"provider-{identifier}", body)
        snapshot = self.workflows.create(spec, f"create-{identifier}")
        steps = []
        for index in range(len(versions)):
            snapshot = asyncio.run(run_step(self.ledger, identifier, snapshot.revision, spec.config_digest, str(executable), "fake-only"))
            expected = WorkflowStatus.VERIFIED if index == len(versions) - 1 else WorkflowStatus.NEEDS_CORRECTION
            self.assertEqual(snapshot.status, expected, snapshot.detail)
            steps.append(snapshot)
        self.runs[identifier] = steps
        return snapshot

    def capture(self, *snapshots):
        return capture_components(self.ledger, tuple((item.spec.workflow_id, item.revision) for item in snapshots),
                                  self.target, self.base_manifest)

    def response(self, components, *, choices=None):
        entries = json.loads(components)
        paths = sorted({change["relative_path"] for item in entries for change in item["changes"]})
        choices = choices or {path: next(item["workflow_id"] for item in entries if any(
            change["relative_path"] == path for change in item["changes"])) for path in paths}
        return {"base_digest": digest_text(self.base_manifest), "decisions": [
            {"relative_path": path, "workflow_id": choices[path], "reason": "select the verified source version"} for path in paths],
            "summary": "integrate explicitly selected source versions"}

    def test_real_builder_capture_retains_exact_source_citations_and_author(self) -> None:
        source = "# café\r\ndef value(): return 42\r\n"
        snapshot = self.build(({"src/value.py": source},))
        captured = self.capture(snapshot)
        component = json.loads(captured)[0]
        self.assertEqual(captured, canonical_json([component]))
        self.assertEqual(set(component), {"workflow_id", "revision", "candidate_digest", "verification_evidence_id", "changes", "authors"})
        self.assertEqual(component["candidate_digest"], digest_text(snapshot.manifest_json))
        verification = next(item for item in self.ledger.evidence(snapshot.active_task_id) if item.kind == "workflow_verification")
        self.assertEqual(component["verification_evidence_id"], verification.evidence_id)
        self.assertEqual(component["changes"], [{"relative_path": "src/value.py", "base_sha256": json.loads(self.base_manifest)["src/value.py"]["sha256"],
            "content": source, "sha256": digest_text(source), "size_bytes": len(source.encode("utf-8"))}])
        attempt = self.ledger.replay_attempt(snapshot.active_attempt_id)
        self.assertEqual(component["authors"], [{"attempt_id": attempt.attempt_id, "task_id": attempt.task_id,
            "provider": "claude", "role": "builder", "session_id": attempt.provider_session_id}])
        self.assertEqual((self.target / "src/value.py").read_bytes(), b"# human base\r\ndef value(): return 0\r\n")

    def test_two_forked_versions_require_and_apply_explicit_conflict_selection(self) -> None:
        first = self.build()
        second = self.build(({"src/value.py": "def value(): return 43\n"},))
        components = self.capture(first, second)
        response = self.response(components, choices={"src/value.py": second.spec.workflow_id})
        accepted = integration_response(components, self.base_manifest, response)
        self.assertEqual(accepted["files"], [{"relative_path": "src/value.py",
            "base_sha256": json.loads(self.base_manifest)["src/value.py"]["sha256"], "content": "def value(): return 43\n"}])
        manifest, artifacts = apply_response(replace(first.spec, workspace_root=str(self.target)), self.base_manifest, accepted)
        self.assertEqual((self.target / "src/value.py").read_text(), "def value(): return 43\n")
        self.assertEqual(artifacts[0].digest_sha256, json.loads(manifest)["src/value.py"]["sha256"])
        self.assertEqual(response["decisions"][0]["workflow_id"], second.spec.workflow_id)
        self.assertEqual(response["decisions"][0]["reason"], "select the verified source version")

    def test_nonconflicting_paths_still_require_choices_and_allow_creation(self) -> None:
        first, second = self.build(), self.build(owned=("src/other.py",))
        components = self.capture(first, second)
        response = self.response(components)
        output = integration_response(components, self.base_manifest, response)
        self.assertEqual([item["relative_path"] for item in output["files"]], ["src/other.py", "src/value.py"])
        self.assertIsNone(output["files"][0]["base_sha256"])
        with self.assertRaises(ValidationError):
            integration_response(components, self.base_manifest, {**response, "decisions": response["decisions"][:1]})

    def test_builder_and_debugger_lineage_includes_prior_successful_publication(self) -> None:
        snapshot = self.build(({"src/value.py": "def value(): return 41\n", "src/other.py": "def value(): return 42\n"},
                               {"src/value.py": "def value(): return 42\n"}), owned=("src/value.py", "src/other.py"))
        component = json.loads(self.capture(snapshot))[0]
        self.assertEqual([item["role"] for item in component["authors"]], ["builder", "debugger"])
        self.assertEqual([item["provider"] for item in component["authors"]], ["claude", "codex"])
        steps = self.runs[snapshot.spec.workflow_id]
        self.assertEqual([item["attempt_id"] for item in component["authors"]], [item.active_attempt_id for item in steps])
        for author, step in zip(component["authors"], steps):
            attempt = self.ledger.replay_attempt(step.active_attempt_id)
            evidence = next(item for item in self.ledger.evidence(step.active_task_id) if item.kind == "provider_result")
            self.assertEqual(attempt.status, AttemptStatus.SUCCEEDED)
            self.assertEqual(author["session_id"], evidence.to_dict()["payload"]["session_id"])
        self.assertNotEqual(component["authors"][0]["session_id"], component["authors"][1]["session_id"])
        verification = next(item for item in self.ledger.evidence(snapshot.active_task_id) if item.kind == "workflow_verification")
        self.assertEqual(len(verification.to_dict()["payload"]["artifacts"]), 1)
        self.assertEqual({change["relative_path"] for change in component["changes"]}, {"src/value.py", "src/other.py"})

    def test_nullable_session_is_captured_without_fabricating_independence(self) -> None:
        component = json.loads(self.capture(self.build(session=None)))[0]
        self.assertIsNone(component["authors"][0]["session_id"])
        integration_response(canonical_json([component]), self.base_manifest, self.response(canonical_json([component])))

    def test_stale_workflow_revision_and_changed_target_base_are_rejected(self) -> None:
        snapshot = self.build()
        with self.assertRaises(ConflictError):
            self.capture(replace(snapshot, revision=snapshot.revision - 1))
        (self.target / "src/value.py").write_text("human edit\n")
        with self.assertRaises(ConflictError):
            self.capture(snapshot)
        current = workspace_manifest(self.target)
        with self.assertRaises(ConflictError):
            capture_components(self.ledger, ((snapshot.spec.workflow_id, snapshot.revision),), self.target, current)
        self.assertEqual((self.target / "src/value.py").read_text(), "human edit\n")

    def test_source_edits_including_unowned_files_invalidate_verification(self) -> None:
        snapshot = self.build()
        source = Path(snapshot.spec.workspace_root) / "src/value.py"
        original = source.read_bytes()
        source.write_text("def value(): return 43\n")
        with self.assertRaises(ConflictError):
            self.capture(snapshot)
        source.write_bytes(original)
        (source.parent / "unowned.txt").write_text("late human edit\n")
        with self.assertRaises(ConflictError):
            self.capture(snapshot)

    def test_target_manifest_is_revalidated_after_source_capture(self) -> None:
        from diamond.integration import _workspace
        snapshot = self.build()
        calls = []

        def race(root, expected):
            _workspace(root, expected)
            calls.append(root)
            if len(calls) == 2:
                (self.target / "human.txt").write_text("concurrent edit\n")

        with patch("diamond.integration._workspace", side_effect=race), self.assertRaises(ConflictError):
            self.capture(snapshot)
        self.assertEqual(calls[0], self.target)
        self.assertEqual((self.target / "human.txt").read_text(), "concurrent edit\n")

    def test_source_manifest_is_revalidated_after_reading_owned_contents(self) -> None:
        from diamond.integration import _workspace
        snapshot = self.build()
        root = Path(snapshot.spec.workspace_root)
        changed = []

        def race(path, expected):
            _workspace(path, expected)
            if path == root and not changed:
                (root / "unowned.txt").write_text("concurrent source edit")
                changed.append(True)

        with patch("diamond.integration._workspace", side_effect=race), self.assertRaises(ConflictError):
            self.capture(snapshot)

    def test_provider_success_and_verified_projection_without_controller_verification_fail(self) -> None:
        def omit(ledger, attempt_id, kind, payload, suffix):
            if kind != "workflow_verification":
                return record(ledger, attempt_id, kind, payload, suffix)

        with patch("diamond.workflow.record", side_effect=omit):
            snapshot = self.build()
        self.assertEqual(self.ledger.get_attempt(snapshot.active_attempt_id).status, AttemptStatus.SUCCEEDED)
        with self.assertRaisesRegex(ExecutionGuardError, "workflow_verification"):
            self.capture(snapshot)

    def test_required_tests_aggregate_cannot_hide_failed_or_unproven_outcomes(self) -> None:
        for defect in ({"returncode": 7}, {"returncode": False}, {"execution_status": "nonzero"},
                       {"cleanup_complete": 1}, {"passed": 1}):
            with self.subTest(defect=defect):
                def forge(ledger, attempt_id, kind, payload, suffix):
                    if kind == "required_tests":
                        payload["tests"][0].update(defect)
                    return record(ledger, attempt_id, kind, payload, suffix)

                with patch("diamond.workflow.record", side_effect=forge):
                    snapshot = self.build()
                with self.assertRaises(ExecutionGuardError):
                    self.capture(snapshot)

    def test_test_digest_artifact_digest_and_individual_test_evidence_are_bound(self) -> None:
        for defect in ("tests_digest", "artifact", "individual"):
            with self.subTest(defect=defect):
                def forge(ledger, attempt_id, kind, payload, suffix):
                    if kind == "workflow_verification" and defect == "tests_digest":
                        payload["tests_digest"] = "0" * 64
                    if kind == "workflow_verification" and defect == "artifact":
                        payload = copy.deepcopy(payload)
                        payload["artifacts"][0]["digest_sha256"] = "0" * 64
                    if kind == "required_tests" and defect == "individual":
                        payload["tests"][0]["stdout"] = "invented output"
                    return record(ledger, attempt_id, kind, payload, suffix)

                with patch("diamond.workflow.record", side_effect=forge):
                    snapshot = self.build()
                with self.assertRaises((ExecutionGuardError, ValidationError, ConflictError)):
                    self.capture(snapshot)

    def test_actual_created_task_role_and_spec_cannot_be_substituted(self) -> None:
        for changes in ({"role": Role.QA}, {"objective": "unrelated task"}):
            with self.subTest(changes=changes):
                def substitute(*args, **kwargs):
                    return replace(TaskSpec(*args, **kwargs), **changes)

                with patch("diamond.workflow.TaskSpec", side_effect=substitute):
                    snapshot = self.build()
                with self.assertRaises(ValidationError):
                    self.capture(snapshot)

    def test_mutable_author_projection_cannot_forge_a_provider_session(self) -> None:
        snapshot = self.build()
        with self.ledger._connection(write=True) as connection:
            connection.execute("UPDATE attempts SET provider_session_id=? WHERE attempt_id=?", ("forged-session", snapshot.active_attempt_id))
        with self.assertRaises(StorageError):
            self.capture(snapshot)

    def test_unchanged_owned_file_is_omitted_and_no_change_component_is_rejected(self) -> None:
        (self.target / "src/other.py").write_text("def value(): return 42\n")
        self.base_manifest = workspace_manifest(self.target)
        snapshot = self.build(({"src/value.py": "def value(): return 42\n", "src/other.py": "def value(): return 42\n"},),
                              owned=("src/value.py", "src/other.py"))
        self.assertEqual([item["relative_path"] for item in json.loads(self.capture(snapshot))[0]["changes"]], ["src/value.py"])
        unchanged = self.build(owned=("src/other.py",))
        with self.assertRaisesRegex(ValidationError, "at least one changed"):
            self.capture(unchanged)

    def test_old_target_directory_cannot_be_overwritten(self) -> None:
        snapshot = self.build()
        (self.target / "src/value.py").unlink()
        (self.target / "src/value.py").mkdir()
        with self.assertRaisesRegex(ValidationError, "directory"):
            capture_components(self.ledger, ((snapshot.spec.workflow_id, snapshot.revision),), self.target, workspace_manifest(self.target))
        self.assertTrue((self.target / "src/value.py").is_dir())

    def test_combined_source_budget_is_checked_before_source_files_are_read(self) -> None:
        content = "#" + "x" * 16300 + "\ndef value(): return 42\n"
        first = self.build(({"src/value.py": content},))
        second = self.build(({"src/other.py": content},), owned=("src/other.py",))
        with patch("diamond.integration.workspace_manifest", wraps=workspace_manifest) as manifests:
            with self.assertRaisesRegex(ValidationError, "budget"):
                self.capture(first, second)
        self.assertEqual(manifests.call_args_list, [((self.target,),)])

    def test_serialized_escape_heavy_source_budget_includes_json_overhead(self) -> None:
        content = "#" + "\\" * 16200 + "\ndef value(): return 42\n"
        snapshot = self.build(({"src/value.py": content},))
        with self.assertRaisesRegex(ValidationError, "components JSON|budget"):
            self.capture(snapshot)
        self.assertEqual(workspace_manifest(self.target), self.base_manifest)

    def test_capture_reference_and_target_boundary_shapes(self) -> None:
        snapshot = self.build()
        reference = (snapshot.spec.workflow_id, snapshot.revision)
        for references in ([], (), (list(reference),), ((reference[0], True),), (reference, reference), (reference,) * 9):
            with self.subTest(references=references), self.assertRaises(ValidationError):
                capture_components(self.ledger, references, self.target, self.base_manifest)
        for ledger, target in ((None, self.target), (self.ledger, str(self.target)), (self.ledger, Path("relative"))):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                capture_components(ledger, (reference,), target, self.base_manifest)

    def test_missing_duplicate_unowned_and_unknown_choices_are_rejected(self) -> None:
        first, second = self.build(), self.build(owned=("src/other.py",))
        components = self.capture(first, second)
        valid = self.response(components)
        for choices in ([], valid["decisions"][:1], [valid["decisions"][0]] * 2,
                        [*valid["decisions"], valid["decisions"][0]],
                        [{**valid["decisions"][0], "workflow_id": first.spec.workflow_id}, valid["decisions"][1]],
                        [{**valid["decisions"][0], "relative_path": "src/unowned.py"}, valid["decisions"][1]],
                        [{**valid["decisions"][0], "workflow_id": "0" * 32}, valid["decisions"][1]]):
            with self.subTest(choices=choices), self.assertRaises(ValidationError):
                integration_response(components, self.base_manifest, {**valid, "decisions": choices})

    def test_response_never_accepts_provider_code_or_human_base_retention(self) -> None:
        components = self.capture(self.build())
        valid = self.response(components)
        for response in ({**valid, "files": [{"relative_path": "src/value.py", "content": "provider-invented code"}]},
                         {**valid, "decisions": [{**valid["decisions"][0], "content": "provider-invented code"}]},
                         {**valid, "decisions": [{**valid["decisions"][0], "workflow_id": "base"}]}):
            with self.subTest(response=response), self.assertRaises(ValidationError):
                integration_response(components, self.base_manifest, response)
        output = integration_response(components, self.base_manifest, valid)
        self.assertEqual(output["files"][0]["content"], "def value(): return 42\n")
        self.assertNotIn("reason", output["files"][0])
        self.assertEqual(valid["decisions"][0]["reason"], "select the verified source version")

    def test_stale_base_noncanonical_json_and_forged_manifest_fields_are_rejected(self) -> None:
        components = self.capture(self.build())
        valid = self.response(components)
        with self.assertRaises(ConflictError):
            integration_response(components, self.base_manifest, {**valid, "base_digest": "0" * 64})
        with self.assertRaises(ValidationError):
            integration_response(json.dumps(json.loads(components)), self.base_manifest, valid)
        for change in ({"sha256": "1" * 64}, {"size_bytes": True}, {"kind": "directory"}, {"kind": []}, {"extra": "forged"}):
            manifest = json.loads(self.base_manifest)
            manifest["src/value.py"].update(change)
            base = canonical_json(manifest)
            with self.subTest(change=change), self.assertRaises((ValidationError, ConflictError)):
                integration_response(components, base, {**valid, "base_digest": digest_text(base)})

    def test_forged_component_content_metadata_paths_and_author_fields_are_rejected(self) -> None:
        components = self.capture(self.build())
        valid = self.response(components)
        mutations = [lambda item: item.update(extra=True), lambda item: item.update(revision=True),
            lambda item: item.update(candidate_digest="invalid"), lambda item: item.update(verification_evidence_id="invalid"),
            lambda item: item.update(authors=[]), lambda item: item.update(changes=[])]
        for changes in ({"content": "unverified replacement"}, {"sha256": "0" * 64}, {"size_bytes": True},
                        {"base_sha256": None}, {"relative_path": "../escape.py"}, {"relative_path": ".hidden.py"},
                        {"content": "\x00"}, {"content": "\ud800"}, {"extra": "forged"}):
            mutations.append(lambda item, changes=changes: item["changes"][0].update(changes))
        for changes in ({"role": "qa"}, {"provider": "unknown"}, {"session_id": ""}, {"session_id": []},
                        {"attempt_id": "invalid"}, {"task_id": "invalid"}, {"extra": "forged"}):
            mutations.append(lambda item, changes=changes: item["authors"][0].update(changes))
        for index, mutation in enumerate(mutations):
            entries = json.loads(components)
            mutation(entries[0])
            with self.subTest(index=index), self.assertRaises((ValidationError, ConflictError)):
                integration_response(canonical_json(entries), self.base_manifest, valid)

    def test_summary_reason_and_decision_boundary_shapes(self) -> None:
        components = self.capture(self.build())
        valid = self.response(components)
        for summary in ("", " ", "x" * 4097, None, "bad\x00", "\ud800"):
            with self.subTest(summary=summary), self.assertRaises(ValidationError):
                integration_response(components, self.base_manifest, {**valid, "summary": summary})
        for reason in ("", " ", "x" * 2049, None, "bad\x00", "\ud800"):
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                integration_response(components, self.base_manifest, {**valid, "decisions": [{**valid["decisions"][0], "reason": reason}]})
        for decisions in (tuple(valid["decisions"]), None, [{}], [None]):
            with self.subTest(decisions=decisions), self.assertRaises(ValidationError):
                integration_response(components, self.base_manifest, {**valid, "decisions": decisions})

    def test_captured_json_remains_immutable_after_source_changes(self) -> None:
        snapshot = self.build()
        components = self.capture(snapshot)
        (Path(snapshot.spec.workspace_root) / "src/value.py").write_text("late source edit")
        self.assertEqual(integration_response(components, self.base_manifest, self.response(components))["files"][0]["content"],
                         "def value(): return 42\n")
        with self.assertRaises(ConflictError):
            self.capture(snapshot)

    def test_unverified_workflow_and_noncanonical_target_are_rejected(self) -> None:
        snapshot = self.build()
        created = self.workflows.create(replace(snapshot.spec, workflow_id=uuid4().hex), "unverified-create")
        with self.assertRaises(ExecutionGuardError):
            self.capture(created)
        alias = self.base.resolve() / "target-alias"
        alias.symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(ValidationError):
            capture_components(self.ledger, ((snapshot.spec.workflow_id, snapshot.revision),), alias, self.base_manifest)
        with self.assertRaises(ValidationError):
            capture_components(self.ledger, ((snapshot.spec.workflow_id, snapshot.revision),), self.target / "missing", self.base_manifest)

    def test_publication_command_artifacts_and_event_fields_must_be_actual(self) -> None:
        commit = WorkflowStore._commit
        for defect in ("command", "artifact", "extra"):
            with self.subTest(defect=defect):
                def forge(store, connection, current, workflow_id, event_type, payload, command_id, request):
                    if event_type == "FILES_APPLIED":
                        payload = copy.deepcopy(payload)
                        if defect == "command":
                            request = canonical_json({"operation": "forged"})
                        if defect == "artifact":
                            payload["artifacts"][0]["digest_sha256"] = "0" * 64
                        if defect == "extra":
                            payload["unauthorized"] = True
                    return commit(store, connection, current, workflow_id, event_type, payload, command_id, request)

                with patch.object(WorkflowStore, "_commit", new=forge):
                    snapshot = self.build()
                with self.assertRaises((ValidationError, ConflictError)):
                    self.capture(snapshot)

    def test_provider_evidence_session_digest_status_and_boundary_shapes_are_checked(self) -> None:
        evidence = EventStore._insert_evidence
        for changes in ({"session_id": "invented-session"}, {"stdout_sha256": "0" * 64}, {"execution_status": "nonzero"},
                        {"cleanup_complete": 1}, {"argv": {}}, {"stdout": "\ud800"}, {"redacted_fields": [0]}):
            with self.subTest(changes=changes):
                def forge(connection, item, command_id, request):
                    if item.kind == "provider_result":
                        payload = json.loads(item.payload_json)
                        payload.update(changes)
                        value = canonical_json(payload)
                        item = replace(item, payload_json=value, digest_sha256=digest_text(value))
                    evidence(connection, item, command_id, request)

                with patch.object(EventStore, "_insert_evidence", new=staticmethod(forge)):
                    snapshot = self.build()
                with self.assertRaises((ValidationError, ExecutionGuardError)):
                    self.capture(snapshot)
