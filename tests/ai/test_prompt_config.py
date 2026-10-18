from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from backend.app.prompting.config import load_prompt_template_config


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config.example" / "prompt_templates.json"


class PromptConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "prompt_templates.json"
        shutil.copyfile(DEFAULT_CONFIG, self.path)
        shutil.copytree(DEFAULT_CONFIG.parent / "prompts", self.root / "prompts")
        self.payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.system_path = self.root / self.payload["system_lines"][0]["file"]

    def save_config(self):
        self.path.write_text(json.dumps(self.payload), encoding="utf-8")

    def test_markdown_paths_are_relative_to_config_file(self):
        self.system_path.write_text(
            "First paragraph.\nContinued on the next line.\n\nLast paragraph.\n",
            encoding="utf-8",
        )

        config = load_prompt_template_config(self.path)

        self.assertEqual(
            config.system_lines[0],
            "First paragraph.\nContinued on the next line.\n\nLast paragraph.",
        )
        self.assertEqual(config.source_path, self.path)

    def test_role_files_are_loaded_independently(self):
        for role in ("merlin", "percival"):
            path = self.root / self.payload["role_gameplay"][role]["file"]
            path.write_text(f"{role} gameplay with {{players}}.\n", encoding="utf-8")

        config = load_prompt_template_config(self.path)

        self.assertEqual(config.role_gameplay["merlin"], "merlin gameplay with {players}.")
        self.assertEqual(config.role_gameplay["percival"], "percival gameplay with {players}.")

    def test_actions_read_files_in_configured_order_and_keep_json_contract(self):
        (self.root / "last.md").write_text("First rule.\n", encoding="utf-8")
        (self.root / "first.md").write_text("Second rule.\n", encoding="utf-8")
        self.payload["action_prompts"]["speak"]["lines"] = [
            {"file": "last.md"},
            {"file": "first.md"},
        ]
        self.save_config()

        config = load_prompt_template_config(self.path)

        self.assertEqual(config.action_prompts["speak"]["lines"], ["First rule.", "Second rule."])
        self.assertEqual(
            config.action_prompts["speak"]["json"],
            self.payload["action_prompts"]["speak"]["json"],
        )

    def test_each_default_text_has_its_own_markdown_file(self):
        references = list(self.payload["system_lines"])
        for key in ("role_descriptions", "role_gameplay", "role_strategy_tips"):
            references.extend(self.payload[key].values())
        for prompt in self.payload["action_prompts"].values():
            references.extend(prompt["lines"])
        paths = [self.root / reference["file"] for reference in references]

        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(set(paths), set((self.root / "prompts").rglob("*.md")))

    def test_inline_json_config_still_loads_the_same_values(self):
        markdown_config = load_prompt_template_config(self.path)
        self.payload = asdict(markdown_config)
        self.payload.pop("source_path")
        self.save_config()

        self.assertEqual(load_prompt_template_config(self.path), markdown_config)

    def test_inline_text_and_file_references_can_be_mixed(self):
        self.payload["system_lines"][1] = "Inline system rule."
        self.payload["role_gameplay"]["merlin"] = "Inline role gameplay."
        self.payload["action_prompts"]["speak"]["lines"][1] = "Inline action rule."
        self.save_config()

        config = load_prompt_template_config(self.path)

        self.assertEqual(config.system_lines[1], "Inline system rule.")
        self.assertEqual(config.role_gameplay["merlin"], "Inline role gameplay.")
        self.assertEqual(config.action_prompts["speak"]["lines"][1], "Inline action rule.")
        self.assertEqual(config.system_lines[0], self.system_path.read_text(encoding="utf-8").strip())

    def test_environment_override_loads_config_and_its_markdown(self):
        self.system_path.write_text("Custom system prompt.\n", encoding="utf-8")

        with patch.dict(os.environ, {"SOLOAVALON_PROMPT_CONFIG": str(self.path)}):
            config = load_prompt_template_config()

        self.assertEqual(config.system_lines[0], "Custom system prompt.")
        self.assertEqual(config.source_path, self.path)

    def test_missing_markdown_file_reports_path_and_field(self):
        self.payload["system_lines"][0] = {"file": "missing.md"}
        self.save_config()

        with self.assertRaises(ValueError) as caught:
            load_prompt_template_config(self.path)

        self.assertIn(str(self.root / "missing.md"), str(caught.exception))
        self.assertIn("system_lines[0]", str(caught.exception))

    def test_invalid_markdown_references_are_rejected(self):
        for reference in (
            {"file": ""},
            {"file": 1},
            {"section": "speak"},
            {"file": "prompt.md", "section": "speak"},
            {"file": "prompt.md", "typo": "speak"},
            None,
            1,
        ):
            with self.subTest(reference=reference):
                self.payload["system_lines"][0] = reference
                self.save_config()
                with self.assertRaisesRegex(ValueError, "markdown reference: system_lines"):
                    load_prompt_template_config(self.path)

    def test_empty_markdown_is_rejected(self):
        self.system_path.write_text("\n \n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "empty prompt template markdown"):
            load_prompt_template_config(self.path)

    def test_missing_role_file_reports_role(self):
        self.payload["role_gameplay"]["merlin"] = {"file": "missing.md"}
        self.save_config()

        with self.assertRaises(ValueError) as caught:
            load_prompt_template_config(self.path)

        self.assertIn(str(self.root / "missing.md"), str(caught.exception))
        self.assertIn("role_gameplay.merlin", str(caught.exception))

    def test_missing_action_file_reports_action_and_index(self):
        self.payload["action_prompts"]["speak"]["lines"][1] = {"file": "missing.md"}
        self.save_config()

        with self.assertRaises(ValueError) as caught:
            load_prompt_template_config(self.path)

        self.assertIn(str(self.root / "missing.md"), str(caught.exception))
        self.assertIn("action_prompts.speak.lines[1]", str(caught.exception))

    def test_text_lists_must_remain_lists(self):
        self.payload["system_lines"] = {"file": "prompt.md"}
        self.save_config()

        with self.assertRaisesRegex(ValueError, "config field: system_lines"):
            load_prompt_template_config(self.path)
