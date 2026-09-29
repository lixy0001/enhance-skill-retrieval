#!/usr/bin/env python3
"""Evaluate routing cases against a supplied catalog and initialized tag state."""

from __future__ import annotations

import argparse
import json

from jev_reranker import JevReranker, read_harness_api_key
from retrieval_core import load_resources, load_state, route, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--threshold", type=int, default=0, help="Use 0 to force enhanced retrieval")
    parser.add_argument(
        "--jev-api-key-file",
        required=True,
        help="Harness-owned file containing the TypeSafe API key",
    )
    args = parser.parse_args()
    with open(args.catalog, "r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    with open(args.cases, "r", encoding="utf-8") as handle:
        cases = [json.loads(line) for line in handle if line.strip()]
    taxonomy, policy, provider = load_resources()
    state = load_state(args.state_dir)
    jev_reranker = JevReranker.from_policy(
        policy,
        api_key=read_harness_api_key(args.jev_api_key_file),
    )
    checked = correct = 0
    failures = []
    for case in cases:
        if case.get("setup") or case.get("group", "").startswith(("future-", "deduplication", "error-")):
            continue
        expected = case.get("expected_decision")
        expected_skill = case.get("expected_skill")
        payload = {
            "query": case["prompt"],
            "registry_version": catalog.get("registry_version", "eval"),
            "activation_threshold": args.threshold,
            "skills": catalog["skills"],
        }
        result = route(
            payload,
            state,
            taxonomy,
            policy,
            provider,
            jev_reranker=jev_reranker,
        )
        checked += 1
        passed = result["decision"] == expected and (
            expected != "invoke" or result.get("selected_skill_id") == expected_skill
        )
        if passed:
            correct += 1
        else:
            failures.append({"id": case["id"], "expected": expected_skill or expected, "actual": result})
    report = {
        "checked": checked,
        "correct": correct,
        "accuracy": round(correct / checked, 6) if checked else 0.0,
        "failures": failures[:20],
    }
    write_json(report)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
