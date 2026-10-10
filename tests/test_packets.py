import hashlib
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from diamond.adapters import Invocation, OutputMode, PermissionMode
from diamond.domain import Provider, Role, TaskSpec
from diamond.errors import ValidationError
from diamond.packets import ArtifactRef, OutputContract, TaskPacket
from diamond.skills import SkillRecord, SkillRegistry
from support import ProjectCase, TASK_ID


class PacketTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.skill_path = self.project / "builder-skill.md"
        self.skill_path.write_text("Build only the owned files.\n", encoding="utf-8")
        self.skill = SkillRecord.from_file(
            "builder-skill", self.skill_path, "1.0", "project",
            (Role.BUILDER,), required_capabilities=("python",))
        self.registry = SkillRegistry((self.skill,))
        (self.project / "src").mkdir()

    def packet(self) -> TaskPacket:
        spec = TaskSpec(
            TASK_ID, "Build one bounded change", Role.BUILDER, Provider.CLAUDE,
            acceptance_conditions=("checks pass",), owned_paths=("src/main.py",),
            allowed_tools=("python",), allowed_skills=("builder-skill",),
            required_tests=("python -m unittest",), timeout_seconds=30,
            correction_round_limit=2)
        return TaskPacket.from_task_spec(
            spec, "b" * 32, self.project, 4, "1" * 64, "2" * 64, "3" * 64,
            self.registry, ("python",), OutputContract("json", ("status",), 4096))

    def test_packet_is_immutable_and_contains_verified_skill_digest(self) -> None:
        packet = self.packet()
        packet.verify(self.registry, ("python",))
        self.assertEqual(packet.skills[0].digest_sha256,
                         hashlib.sha256(self.skill_path.read_bytes()).hexdigest())
        self.assertEqual(packet.to_dict()["role"], "builder")
        self.assertTrue(packet.canonical_json())
        with self.assertRaises(FrozenInstanceError):
            packet.objective = "changed"  # type: ignore[misc]

    def test_stale_plan_and_skill_change_block_validation(self) -> None:
        packet = self.packet()
        with self.assertRaisesRegex(ValidationError, "stale"):
            packet.verify_current(5, "1" * 64, "2" * 64, "3" * 64)
        self.skill_path.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "digest changed"):
            packet.verify(self.registry, ("python",))

    def test_missing_prerequisite_and_dependency_are_blocking(self) -> None:
        with self.assertRaisesRegex(ValidationError, "prerequisites unavailable"):
            self.registry.resolve(("builder-skill",), Role.BUILDER, ())
        with self.assertRaisesRegex(ValidationError, "not registered"):
            SkillRegistry((self.skill,)).resolve(("missing",), Role.BUILDER, ("python",))

    def test_skill_role_mismatch_and_dependency_cycle_are_blocking(self) -> None:
        with self.assertRaisesRegex(ValidationError, "not applicable"):
            self.registry.resolve(("builder-skill",), Role.QA, ("python",))
        first = SkillRecord.from_file("first", self.skill_path, "1", "project",
                                      (Role.BUILDER,), dependencies=("second",))
        second = SkillRecord.from_file("second", self.skill_path, "1", "project",
                                       (Role.BUILDER,), dependencies=("first",))
        with self.assertRaisesRegex(ValidationError, "cycle"):
            SkillRegistry((first, second)).resolve(("first",), Role.BUILDER, ())

    def test_owned_path_symlink_escape_is_rejected(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.project / "src" / "escape.py").symlink_to(outside)
        packet = self.packet()
        escaped = TaskPacket(
            **{**packet.__dict__, "owned_paths": ("src/escape.py",)})
        with self.assertRaisesRegex(ValidationError, "escapes workspace"):
            escaped.verify_workspace()

    def test_packet_rejects_invalid_digest_and_traversal(self) -> None:
        packet = self.packet()
        with self.assertRaises(ValidationError):
            TaskPacket(**{**packet.__dict__, "plan_digest": "bad"})
        with self.assertRaises(ValidationError):
            TaskPacket(**{**packet.__dict__, "owned_paths": ("../outside",)})

    def test_packet_authorizes_only_declared_provider_tool_and_workspace(self) -> None:
        packet = replace(self.packet(), allowed_tools=(sys.executable,))
        allowed = Invocation(Provider.CLAUDE, (sys.executable, "-c", "pass"), OutputMode.TEXT, None,
                             False, PermissionMode.DEFAULT)
        packet.authorize_invocation(allowed, self.project)
        with self.assertRaisesRegex(ValidationError, "not allowed"):
            packet.authorize_invocation(
                Invocation(Provider.CLAUDE, ("shell", "-c", "pass"), OutputMode.TEXT, None,
                           False, PermissionMode.DEFAULT), self.project)
        with self.assertRaisesRegex(ValidationError, "workspace root"):
            packet.authorize_invocation(allowed, self.base)

    def test_output_artifact_must_match_owned_path_size_and_digest(self) -> None:
        packet = self.packet()
        target = self.project / "src" / "main.py"
        target.write_text("accepted\n", encoding="utf-8")
        artifact = ArtifactRef("main", "src/main.py", hashlib.sha256(target.read_bytes()).hexdigest(),
                               target.stat().st_size, "source")
        self.assertEqual(packet.verify_output_artifacts((artifact,)), (artifact,))
        target.write_text("rejected\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "digest changed"):
            packet.verify_output_artifacts((artifact,))
