import hashlib
from pathlib import Path

from diamond.domain import Role
from diamond.errors import ValidationError
from diamond.skill_catalog import load_skill_catalog
from support import ProjectCase


class SkillCatalogTests(ProjectCase):
    def _write_catalog(self, entries: str) -> None:
        directory = self.project / "config" / "skills"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "catalog.toml").write_text(
            "schema_version = 1\n\n" + entries, encoding="utf-8")

    def _entry(self, name: str, source: str, roles: str, dependencies: str = "[]") -> str:
        digest = hashlib.sha256(source.encode()).hexdigest()
        directory = self.project / "config" / "skills"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.md").write_text(source, encoding="utf-8")
        return f'''[[skills]]
name = "{name}"
source_path = "config/skills/{name}.md"
source_url = "https://example.test/{name}.md"
source_commit = "{"a" * 40}"
git_blob_sha1 = "{"b" * 40}"
source_sha256 = "{digest}"
license = "MIT"
supplied_version = "test-1"
scope = "test guidance"
applicable_roles = {roles}
dependencies = {dependencies}
required_capabilities = []
'''

    def test_vendored_catalog_has_verified_provenance_and_role_order(self) -> None:
        catalog = load_skill_catalog(Path(__file__).parents[1])
        debugger = catalog.for_role(Role.DEBUGGER)
        self.assertEqual(tuple(skill.name for skill in debugger),
                         ("test-driven-development", "verification-before-completion", "systematic-debugging"))
        builder = catalog.for_role(Role.BUILDER)
        self.assertEqual(tuple(skill.name for skill in builder),
                         ("test-driven-development", "verification-before-completion"))
        guidance = catalog.guidance_for(Role.REVIEWER)
        self.assertEqual({item["name"] for item in guidance},
                         {"code-review-excellence", "verification-before-completion"})
        self.assertTrue(all(item["digest_sha256"] for item in guidance))
        self.assertTrue(all(item["content"].startswith("---") for item in guidance))

    def test_catalog_without_file_is_an_empty_safe_registry(self) -> None:
        catalog = load_skill_catalog(self.project)
        self.assertEqual(catalog.registry.records, ())
        self.assertEqual(catalog.for_role(Role.BUILDER), ())
        self.assertEqual(catalog.guidance_for(Role.REVIEWER), ())

    def test_catalog_resolves_dependencies_and_rejects_digest_drift(self) -> None:
        source = "Follow the controller contract.\n"
        base = self._entry("base", source, '["builder"]')
        dependent = self._entry("dependent", source, '["builder"]', '["base"]')
        self._write_catalog(base + dependent)
        catalog = load_skill_catalog(self.project)
        self.assertEqual(tuple(item.name for item in catalog.for_role(Role.BUILDER)), ("base", "dependent"))
        source_path = self.project / "config" / "skills" / "base.md"
        source_path.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "digest changed"):
            catalog.for_role(Role.BUILDER)

    def test_catalog_rejects_invalid_source_url_and_traversal(self) -> None:
        source = "safe\n"
        entry = self._entry("safe", source, '["builder"]').replace(
            'source_url = "https://example.test/safe.md"', 'source_url = "http://example.test/safe.md"')
        self._write_catalog(entry)
        with self.assertRaisesRegex(ValidationError, "HTTPS"):
            load_skill_catalog(self.project)
        traversal = entry.replace('source_url = "http://example.test/safe.md"',
                                  'source_url = "https://example.test/safe.md"').replace(
                                      'source_path = "config/skills/safe.md"',
                                      'source_path = "../diamond.toml"')
        self._write_catalog(traversal)
        with self.assertRaisesRegex(ValidationError, "relative and cannot traverse"):
            load_skill_catalog(self.project)

    def test_catalog_rejects_catalog_symlink_escape(self) -> None:
        directory = self.project / "config" / "skills"
        directory.mkdir(parents=True, exist_ok=True)
        outside = self.base / "outside-catalog.toml"
        outside.write_text("schema_version = 1\nskills = []\n", encoding="utf-8")
        (directory / "catalog.toml").symlink_to(outside)
        with self.assertRaisesRegex(ValidationError, "escapes"):
            load_skill_catalog(self.project)

    def test_catalog_rejects_unknown_role_and_malformed_digest(self) -> None:
        source = "safe\n"
        entry = self._entry("safe", source, '["not-a-role"]')
        self._write_catalog(entry)
        with self.assertRaisesRegex(ValidationError, "unknown role"):
            load_skill_catalog(self.project)
        malformed = self._entry("safe", source, '["builder"]').replace(
            f'source_sha256 = "{hashlib.sha256(source.encode()).hexdigest()}"',
            'source_sha256 = "not-a-digest"')
        self._write_catalog(malformed)
        with self.assertRaisesRegex(ValidationError, "does not match the catalog"):
            load_skill_catalog(self.project)
