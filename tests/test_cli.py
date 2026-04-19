import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from go2web.cli import app
from go2web.search import SearchResult


class CliTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_json_search_output_schema(self):
        fake_results = [SearchResult(title="A", url="https://a.test", snippet="sa", rank=1)]
        with patch("go2web.cli.search", return_value=fake_results):
            result = self.runner.invoke(app, ["-s", "cats", "--json"])

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["query"], "cats")
        self.assertEqual(payload["engine"], "ddg")
        self.assertIn("generated_at", payload)
        self.assertEqual(payload["results"][0]["rank"], 1)
        self.assertEqual(payload["results"][0]["title"], "A")

    def test_json_rejected_for_url_mode(self):
        result = self.runner.invoke(app, ["-u", "https://example.com", "--json"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("--json can only be used", result.stderr)

    def test_engine_rejected_for_url_mode(self):
        result = self.runner.invoke(app, ["-u", "https://example.com", "--engine", "wikipedia"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("--engine is only valid", result.stderr)

    def test_clear_cache_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            (cache_dir / "a.json").write_text("{}", encoding="utf-8")
            result = self.runner.invoke(app, ["--cache-dir", str(cache_dir), "--clear-cache"])
            self.assertEqual(result.exit_code, 0)
            self.assertIn("Cleared", result.stderr)


if __name__ == "__main__":
    unittest.main()
