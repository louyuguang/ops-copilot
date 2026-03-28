from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MAIN = BASE_DIR / "src" / "main.py"
SAMPLE = BASE_DIR / "samples" / "incidents" / "high_cpu.json"


class CliBehaviorTest(unittest.TestCase):
    def _run(self, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        env.pop("OPENAI_API_KEY", None)
        if extra_env:
            env.update(extra_env)

        return subprocess.run(
            [sys.executable, str(MAIN), "--event", str(SAMPLE)],
            cwd=BASE_DIR,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_invalid_chroma_top_k_exit_2(self) -> None:
        proc = self._run({"CHROMA_TOP_K": "abc"})

        self.assertEqual(2, proc.returncode)
        self.assertIn("[config_error]", proc.stderr)
        self.assertIn("CHROMA_TOP_K", proc.stderr)

    def test_llm_missing_key_fallback_rule_and_metadata(self) -> None:
        proc = self._run({"ANALYSIS_MODE": "llm", "OPSCOPILOT_DEBUG": "1"})

        self.assertEqual(0, proc.returncode)
        data = json.loads(proc.stdout)
        self.assertIn("high_cpu", data["summary"])

        meta = json.loads(proc.stderr.strip().splitlines()[-1])
        self.assertEqual("degraded_success", meta.get("run_status"))
        gen = meta["pipeline"]["generator"]
        self.assertTrue(gen.get("fallback"))
        self.assertEqual("llm_api_key_missing", gen.get("fallback_reason"))
        self.assertEqual("config_error", gen.get("error_type"))

    def test_chroma_unavailable_fallback_with_external_error_type(self) -> None:
        proc = self._run(
            {
                "RETRIEVER_MODE": "chroma",
                "CHROMA_HOST": "127.0.0.1",
                "CHROMA_PORT": "1",
                "OPSCOPILOT_DEBUG": "1",
            }
        )

        self.assertEqual(0, proc.returncode)
        data = json.loads(proc.stdout)
        self.assertTrue(data["summary"])

        meta = json.loads(proc.stderr.strip().splitlines()[-1])
        self.assertEqual("degraded_success", meta.get("run_status"))
        retr = meta["pipeline"]["retriever"]
        self.assertTrue(retr.get("fallback"))
        self.assertEqual("chroma_unavailable", retr.get("fallback_reason"))
        self.assertEqual("external_dependency_error", retr.get("error_type"))


if __name__ == "__main__":
    unittest.main()
