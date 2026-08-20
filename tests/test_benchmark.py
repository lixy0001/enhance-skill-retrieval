#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "enhance-skill-retrieval" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from benchmark_retrieval import full_metadata_route  # noqa: E402
from retrieval_core import load_resources  # noqa: E402


class BenchmarkTest(unittest.TestCase):
    def test_full_metadata_baseline_returns_expected_skill(self):
        with (ROOT / "tests" / "fixtures" / "catalog.json").open(encoding="utf-8") as handle:
            catalog = json.load(handle)
        _, policy, provider = load_resources()
        result = full_metadata_route(
            {
                "query": "Read and render a PDF",
                "registry_version": catalog["registry_version"],
                "skills": catalog["skills"],
            },
            policy,
            provider,
        )
        self.assertEqual("invoke", result["decision"])
        self.assertEqual("pdf", result["selected_skill_id"])

    def test_benchmark_reports_both_query_rates(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "benchmark_retrieval.py"),
                "--catalog",
                str(ROOT / "tests" / "fixtures" / "catalog.json"),
                "--cases",
                str(ROOT / "tests" / "performance-cases.jsonl"),
                "--synthetic-size",
                "120",
                "--iterations",
                "2",
                "--warmup",
                "1",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(result.stdout)
        self.assertEqual(120, report["skill_count"])
        self.assertGreater(report["enhanced"]["queries_per_second"], 0)
        self.assertGreater(report["full_metadata_baseline"]["queries_per_second"], 0)
        self.assertGreater(report["enhanced"]["p95_ms"], 0)
        self.assertGreater(report["full_metadata_baseline"]["p95_ms"], 0)


if __name__ == "__main__":
    unittest.main()
