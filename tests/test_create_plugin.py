from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import create_plugin as subject

SOURCE_ROOT = Path(__file__).resolve().parents[1]


class PluginRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        shutil.copytree(SOURCE_ROOT / "plugins" / "_template", self.root / "plugins" / "_template")
        for relative in (
            Path(".claude-plugin/marketplace.json"),
            Path(".agents/plugins/marketplace.json"),
        ):
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE_ROOT / relative, destination)
            payload = json.loads(destination.read_text(encoding="utf-8"))
            payload["plugins"] = []
            destination.write_text(
                json.dumps(payload, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )

    def create(self, name: str = "sample-plugin") -> str:
        return subject.create_plugin(
            self.root,
            name,
            description="Handle a focused sample workflow.",
        )

    def read_json(self, relative: str) -> dict[str, object]:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def write_json(self, relative: str, payload: dict[str, object]) -> None:
        (self.root / relative).write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )


class CreatePluginTests(PluginRepositoryTestCase):
    def test_contained_path_rejects_template_path_escape(self) -> None:
        target = self.root / "plugins" / "sample"

        with self.assertRaisesRegex(subject.PluginError, "escapes"):
            subject._contained_path(target, "..", "escaped.txt")

    def test_create_normalizes_name_and_renders_both_manifests(self) -> None:
        name = subject.create_plugin(
            self.root,
            "My_Plugin",
            description="Review a user's focused request.",
            display_name="My Plugin",
        )

        self.assertEqual(name, "my-plugin")
        claude, codex = subject.validate_plugin(self.root, name)
        self.assertEqual(claude["name"], name)
        self.assertEqual(codex["interface"]["displayName"], "My Plugin")
        skill = self.root / "plugins/my-plugin/skills/my-plugin/SKILL.md"
        self.assertIn(
            "description: 'Review a user''s focused request.'", skill.read_text(encoding="utf-8")
        )
        metadata = self.root / "plugins/my-plugin/skills/my-plugin/agents/openai.yaml"
        self.assertIn('display_name: "My Plugin"', metadata.read_text(encoding="utf-8"))

    def test_create_refuses_collisions(self) -> None:
        self.create()

        with self.assertRaisesRegex(subject.PluginError, "already exists"):
            self.create()

    def test_create_rejects_reserved_name(self) -> None:
        with self.assertRaisesRegex(subject.PluginError, "reserved"):
            self.create("_template")

    def test_description_must_be_short_and_single_line(self) -> None:
        with self.assertRaisesRegex(subject.PluginError, "single line"):
            subject.create_plugin(self.root, "sample", description="first\nsecond")
        with self.assertRaisesRegex(subject.PluginError, "120"):
            subject.create_plugin(self.root, "sample", description="x" * 121)

    def test_validation_allows_binary_plugin_assets(self) -> None:
        name = self.create()
        asset = self.root / "plugins" / name / "assets" / "icon.png"
        asset.parent.mkdir()
        asset.write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")

        subject.validate_plugin(self.root, name)

    def test_validation_accepts_multiple_named_skills(self) -> None:
        name = self.create()
        source = self.root / "plugins" / name / "skills" / name
        extra = self.root / "plugins" / name / "skills" / "extra-workflow"
        shutil.copytree(source, extra)
        skill = extra / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace(f"name: {name}", "name: extra-workflow", 1),
            encoding="utf-8",
            newline="\n",
        )

        subject.validate_plugin(self.root, name)

    def test_validation_rejects_a_skill_name_mismatch(self) -> None:
        name = self.create()
        skill = self.root / "plugins" / name / "skills" / name / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace(f"name: {name}", "name: wrong-name", 1),
            encoding="utf-8",
            newline="\n",
        )

        with self.assertRaisesRegex(subject.PluginError, "skill name must match"):
            subject.validate_plugin(self.root, name)

    def test_validation_requires_skill_ui_metadata(self) -> None:
        name = self.create()
        metadata = self.root / "plugins" / name / "skills" / name / "agents" / "openai.yaml"
        metadata.unlink()

        with self.assertRaisesRegex(subject.PluginError, "UI metadata is missing"):
            subject.validate_plugin(self.root, name)


class MarketplaceLifecycleTests(PluginRepositoryTestCase):
    def test_publish_adds_matching_entries(self) -> None:
        name = self.create()

        subject.publish_plugin(self.root, name)

        claude_entries, codex_entries = subject.validate_catalogs(self.root)
        self.assertEqual(list(claude_entries), [name])
        self.assertEqual(list(codex_entries), [name])

    def test_publish_refuses_a_one_sided_existing_entry(self) -> None:
        name = self.create()
        claude = self.read_json(".claude-plugin/marketplace.json")
        claude["plugins"].append(
            {
                "name": name,
                "source": f"./plugins/{name}",
                "description": "Handle a focused sample workflow.",
                "version": "0.1.0",
                "author": {"name": subject.AUTHOR_NAME},
            }
        )
        self.write_json(".claude-plugin/marketplace.json", claude)
        before = (self.root / ".claude-plugin/marketplace.json").read_bytes()

        with self.assertRaisesRegex(subject.PluginError, "already present"):
            subject.publish_plugin(self.root, name)

        self.assertEqual((self.root / ".claude-plugin/marketplace.json").read_bytes(), before)

    def test_sync_updates_mutable_marketplace_metadata(self) -> None:
        name = self.create()
        subject.publish_plugin(self.root, name)
        for relative in (
            f"plugins/{name}/.claude-plugin/plugin.json",
            f"plugins/{name}/.codex-plugin/plugin.json",
        ):
            manifest = self.read_json(relative)
            manifest["version"] = "0.2.0"
            manifest["description"] = "Handle an updated focused workflow."
            self.write_json(relative, manifest)

        subject.sync_plugin(self.root, name)

        claude_entries, _ = subject.validate_catalogs(self.root)
        self.assertEqual(claude_entries[name]["version"], "0.2.0")
        self.assertEqual(claude_entries[name]["description"], "Handle an updated focused workflow.")

    def test_catalog_write_restores_both_files_after_failure(self) -> None:
        name = self.create()
        paths = subject._catalog_paths(self.root)
        originals = {path: path.read_bytes() for path in paths}
        original_write = subject._atomic_write
        call_count = 0

        def fail_second_write(path: Path, data: bytes) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("simulated write failure")
            original_write(path, data)

        with (
            mock.patch.object(subject, "_atomic_write", side_effect=fail_second_write),
            self.assertRaisesRegex(subject.PluginError, "original files restored"),
        ):
            subject.publish_plugin(self.root, name)

        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_check_all_detects_catalog_drift(self) -> None:
        name = self.create()
        subject.publish_plugin(self.root, name)
        codex = self.read_json(".agents/plugins/marketplace.json")
        codex["plugins"] = []
        self.write_json(".agents/plugins/marketplace.json", codex)

        with self.assertRaisesRegex(subject.PluginError, "same plugins"):
            subject.check_all(self.root)

    def test_check_all_accepts_empty_marketplaces_and_template(self) -> None:
        subject.check_all(self.root)


class CommandLineTests(PluginRepositoryTestCase):
    def test_create_publish_and_check_commands(self) -> None:
        result = subject.main(
            [
                "create",
                "Command Sample",
                "--description",
                "Exercise the command-line workflow.",
                "--publish",
            ],
            root=self.root,
        )

        self.assertEqual(result, 0)
        self.assertEqual(subject.main(["check", "command-sample"], root=self.root), 0)
        self.assertEqual(subject.main(["check", "--all"], root=self.root), 0)

    def test_check_requires_a_target(self) -> None:
        self.assertEqual(subject.main(["check"], root=self.root), 1)


if __name__ == "__main__":
    unittest.main()
