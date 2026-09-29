#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "enhance-skill-retrieval" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from benchmark_retrieval import expand_catalog, full_metadata_route, measure  # noqa: E402
from index_store import build_vector_cache  # noqa: E402
from retrieval_core import initialize_state, load_resources, route  # noqa: E402
from fake_jev import FakeJevReranker  # noqa: E402


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
        with (ROOT / "tests" / "fixtures" / "catalog.json").open(encoding="utf-8") as handle:
            catalog = json.load(handle)
        with (ROOT / "tests" / "performance-cases.jsonl").open(encoding="utf-8") as handle:
            cases = [json.loads(line) for line in handle if line.strip()]
        taxonomy, policy, provider = load_resources()
        skills = expand_catalog(catalog["skills"], 120)
        state = initialize_state(
            {"registry_version": catalog["registry_version"], "skills": skills},
            taxonomy,
            provider,
        )
        cache = build_vector_cache(skills, state, taxonomy, provider)
        payloads = [
            {
                "query": case["prompt"],
                "registry_version": catalog["registry_version"],
                "activation_threshold": 0,
                "skills": skills,
            }
            for case in cases
        ]
        enhanced, _ = measure(
            lambda payload: route(
                payload,
                state,
                taxonomy,
                policy,
                provider,
                vector_cache=cache,
                jev_reranker=FakeJevReranker(),
            ),
            payloads,
            iterations=2,
            warmup=1,
        )
        baseline, _ = measure(
            lambda payload: full_metadata_route(payload, policy, provider),
            payloads,
            iterations=2,
            warmup=1,
        )
        self.assertGreater(enhanced["queries_per_second"], 0)
        self.assertGreater(baseline["queries_per_second"], 0)
        self.assertGreater(enhanced["p95_ms"], 0)
        self.assertGreater(baseline["p95_ms"], 0)


if __name__ == "__main__":
    unittest.main()
