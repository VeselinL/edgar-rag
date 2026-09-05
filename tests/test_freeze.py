import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.config.settings import ApplicationSettings
from src.evaluation.freeze import (
    _input_hashes,
    _is_permitted_post_freeze_path,
    _prompt_hashes,
    _safe_settings,
    _sha256_json,
    _qdrant_server_version,
    _validate_runtime,
    validate_manifest,
)
from src.tools.web_search import TRUSTED_WEB_SOURCES


class FreezeTests(unittest.TestCase):
    def test_permits_saved_evaluation_outputs_but_not_code(self):
        manifest = Path("data/evaluation/finalization/v1/freeze_manifest.json")

        self.assertTrue(_is_permitted_post_freeze_path("data/evaluation/finalization/v1/runs/run/raw.jsonl", manifest))
        self.assertTrue(_is_permitted_post_freeze_path("data/evaluation/finalization/v1/reports/baseline.md", manifest))
        v2_manifest = Path("data/evaluation/finalization/v2/freeze_manifest.json")
        self.assertTrue(_is_permitted_post_freeze_path("data/evaluation/finalization/v2/runs/run/raw.jsonl", v2_manifest))
        self.assertFalse(_is_permitted_post_freeze_path("data/evaluation/finalization/v1/runs/run/raw.jsonl", v2_manifest))
        self.assertFalse(_is_permitted_post_freeze_path("src/generation/rag.py", manifest))

    def test_input_hashes_cover_finalization_evaluation_manifests(self):
        hashes = _input_hashes(Path(__file__).resolve().parents[1])

        self.assertEqual(
            set(hashes["evaluation_manifests"]),
            {
                "data/evaluation/finalization/v1/qa_gold.jsonl",
                "data/evaluation/finalization/v1/agent_routes.jsonl",
                "data/evaluation/finalization/v1/conversations.jsonl",
                "data/evaluation/finalization/v1/memory.jsonl",
                "data/evaluation/finalization/v1/security.jsonl",
                "data/evaluation/finalization/v1/ui_language.jsonl",
                "data/evaluation/finalization/v1/splits.json",
            },
        )

    def test_safe_settings_exclude_credentials_and_connection_secrets(self):
        settings = ApplicationSettings.for_tests(
            OPENAI_API_KEY="provider-secret",
            OPENAI_API_URL="https://gateway.example.test",
            QDRANT_API_KEY="qdrant-secret",
            TAVILY_API_KEY="tavily-secret",
        )

        value = json.dumps(_safe_settings(settings))

        self.assertNotIn("provider-secret", value)
        self.assertNotIn("qdrant-secret", value)
        self.assertNotIn("tavily-secret", value)
        self.assertNotIn("gateway.example.test", value)
        self.assertEqual(_safe_settings(settings), json.loads(value))

    def test_prompt_hashes_include_router_instruction(self):
        hashes = _prompt_hashes()

        self.assertIn("src.orchestration.routing.ROUTER_INSTRUCTION", hashes)
        self.assertIn("src.generation.prompts.SYSTEM_PROMPT", hashes)

    def test_trusted_source_registry_hashes_typed_records(self):
        self.assertRegex(_sha256_json(TRUSTED_WEB_SOURCES), r"^sha256:[0-9a-f]{64}$")

    def test_qdrant_server_version_requires_a_public_version_field(self):
        with patch("src.evaluation.freeze.httpx.get") as request:
            request.return_value.json.return_value = {"version": "1.18.2"}
            request.return_value.raise_for_status.return_value = None
            self.assertEqual(_qdrant_server_version("http://qdrant:6333", 5), "1.18.2")

    def test_runtime_validation_rejects_effective_configuration_change(self):
        settings = ApplicationSettings.for_tests()
        manifest = {"effective_configuration": {"changed": True}, "qdrant": {}}
        with patch("src.evaluation.freeze.ApplicationSettings.from_environment", return_value=settings):
            with self.assertRaisesRegex(ValueError, "effective configuration"):
                _validate_runtime(manifest, Path("."))

    def test_validate_rejects_post_freeze_code_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "data/evaluation/finalization/v1/freeze_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "source_commit": "source",
                    "corpus": {"input_hashes": {}, "artifact_version": "artifact", "point_count": 1},
                    "prompts": {"hashes": {}},
                    "trusted_source_registry_sha256": "registry",
                }),
                encoding="utf-8",
            )
            bundle = SimpleNamespace(artifact_version="artifact", point_count=1)
            with (
                patch("src.evaluation.freeze._assert_clean"),
                patch("src.evaluation.freeze._git", return_value="src/evaluation/freeze.py"),
                patch("src.evaluation.freeze._is_ancestor", return_value=True),
                patch("src.evaluation.freeze._input_hashes", return_value={}),
                patch("src.evaluation.freeze.load_artifact_bundle", return_value=bundle),
                patch("src.evaluation.freeze._prompt_hashes", return_value={}),
                patch("src.evaluation.freeze._sha256_json", return_value="registry"),
            ):
                with self.assertRaisesRegex(ValueError, "does not match current code"):
                    validate_manifest(manifest_path, project_root=root)


if __name__ == "__main__":
    unittest.main()
