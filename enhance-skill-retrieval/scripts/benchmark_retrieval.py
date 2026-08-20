#!/usr/bin/env python3
"""Compare tag-enhanced routing with full-catalog metadata matching."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence

from retrieval_core import (
    cosine,
    initialize_state,
    load_resources,
    require_skill,
    route,
    text_features,
    write_json,
)


def metadata_match_score(
    query: str,
    query_vector: Sequence[float],
    skill: Mapping[str, Any],
    provider: Any,
) -> float:
    metadata_text = " ".join(
        [str(skill.get("name", "")), str(skill.get("description", ""))]
        + [str(alias) for alias in skill.get("aliases", [])]
        + [str(tag) for tag in skill.get("tags", [])]
    )
    semantic = cosine(query_vector, provider.embed(metadata_text))
    query_features = set(text_features(query))
    metadata_features = set(text_features(metadata_text))
    lexical = len(query_features & metadata_features) / max(1, len(query_features))
    return min(1.0, 0.72 * min(1.0, semantic * 1.5) + 0.28 * lexical)


def full_metadata_route(
    payload: Mapping[str, Any],
    policy: Mapping[str, Any],
    provider: Any,
) -> Dict[str, Any]:
    """Score the full catalog as a benchmark-only baseline."""
    skills = payload.get("skills")
    query = payload.get("query")
    if not isinstance(skills, list) or not isinstance(query, str) or not query.strip():
        raise ValueError("full metadata route requires query and skills array")
    registry_version = str(payload.get("registry_version", ""))
    query_vector = provider.embed(query)
    ranked: list[tuple[float, str]] = []
    lowered_query = query.casefold()
    for skill in skills:
        require_skill(skill)
        skill_id = skill["skill_id"]
        if skill_id.casefold() in lowered_query or str(skill.get("name", "")).casefold() in lowered_query:
            score = 1.0
        else:
            score = metadata_match_score(query, query_vector, skill, provider)
        ranked.append((score, skill_id))
    ranked.sort(reverse=True)
    if not ranked:
        return {
            "decision": "none",
            "selected_skill_id": None,
            "confidence": 0.0,
            "candidate_skill_ids": [],
            "reason_codes": ["empty_catalog"],
            "registry_version": registry_version,
        }
    winner_score, winner_id = ranked[0]
    runner_score = ranked[1][0] if len(ranked) > 1 else 0.0
    margin = winner_score - runner_score
    if winner_score < float(policy["minimum_skill_score"]):
        decision, selected, reasons = "none", None, ["below_skill_threshold"]
    elif margin < float(policy["minimum_winner_margin"]):
        decision, selected, reasons = "none", None, ["ambiguous_top_candidates"]
    else:
        decision, selected, reasons = "invoke", winner_id, ["full_metadata_match"]
    return {
        "decision": decision,
        "selected_skill_id": selected,
        "confidence": round(winner_score, 6),
        "winner_margin": round(margin, 6),
        "candidate_skill_ids": [skill_id for _, skill_id in ranked],
        "reason_codes": reasons,
        "registry_version": registry_version,
    }


def load_cases(path: str) -> list[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def expand_catalog(skills: list[Dict[str, Any]], target_size: int) -> list[Dict[str, Any]]:
    expanded = [dict(skill) for skill in skills]
    for index in range(max(0, target_size - len(expanded))):
        expanded.append(
            {
                "skill_id": f"benchmark-fixture-{index:05d}",
                "name": f"benchmark-fixture-{index:05d}",
                "description": f"Synthetic generic application utility number {index}.",
                "tags": ["development.apps"],
            }
        )
    return expanded


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


def measure(
    runner: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    payloads: Iterable[Mapping[str, Any]],
    iterations: int,
    warmup: int,
) -> tuple[Dict[str, Any], list[Mapping[str, Any]]]:
    materialized = list(payloads)
    for _ in range(warmup):
        for payload in materialized:
            runner(payload)
    samples_ms: list[float] = []
    results: list[Mapping[str, Any]] = []
    started = time.perf_counter_ns()
    for _ in range(iterations):
        for payload in materialized:
            call_started = time.perf_counter_ns()
            result = runner(payload)
            samples_ms.append((time.perf_counter_ns() - call_started) / 1_000_000.0)
            if len(results) < len(materialized):
                results.append(result)
    elapsed_seconds = (time.perf_counter_ns() - started) / 1_000_000_000.0
    return (
        {
            "calls": len(samples_ms),
            "queries_per_second": round(len(samples_ms) / elapsed_seconds, 3),
            "mean_ms": round(statistics.fmean(samples_ms), 6),
            "p50_ms": round(percentile(samples_ms, 0.50), 6),
            "p95_ms": round(percentile(samples_ms, 0.95), 6),
        },
        results,
    )


def accuracy(cases: list[Mapping[str, Any]], results: list[Mapping[str, Any]]) -> float:
    correct = 0
    checked = 0
    for case, result in zip(cases, results):
        expected_decision = case.get("expected_decision")
        expected_skill = case.get("expected_skill")
        if expected_decision is None:
            continue
        checked += 1
        if result.get("decision") == expected_decision and (
            expected_decision != "invoke" or result.get("selected_skill_id") == expected_skill
        ):
            correct += 1
    return round(correct / checked, 6) if checked else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--synthetic-size", type=int, default=0)
    args = parser.parse_args()
    if args.iterations < 1 or args.warmup < 0:
        raise ValueError("iterations must be positive and warmup must not be negative")

    with open(args.catalog, "r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    cases = load_cases(args.cases)
    skills = expand_catalog(catalog["skills"], args.synthetic_size)
    taxonomy, policy, provider = load_resources()
    state = initialize_state(
        {"registry_version": catalog.get("registry_version", "benchmark"), "skills": skills},
        taxonomy,
        provider,
    )
    payloads = [
        {
            "query": case["prompt"],
            "registry_version": catalog.get("registry_version", "benchmark"),
            "activation_threshold": 0,
            "skills": skills,
        }
        for case in cases
    ]
    enhanced_metrics, enhanced_results = measure(
        lambda payload: route(payload, state, taxonomy, policy, provider),
        payloads,
        args.iterations,
        args.warmup,
    )
    baseline_metrics, baseline_results = measure(
        lambda payload: full_metadata_route(payload, policy, provider),
        payloads,
        args.iterations,
        args.warmup,
    )
    agreement = sum(
        left.get("selected_skill_id") == right.get("selected_skill_id")
        and left.get("decision") == right.get("decision")
        for left, right in zip(enhanced_results, baseline_results)
    )
    report = {
        "skill_count": len(skills),
        "case_count": len(cases),
        "iterations": args.iterations,
        "enhanced": {
            **enhanced_metrics,
            "expected_accuracy": accuracy(cases, enhanced_results),
        },
        "full_metadata_baseline": {
            **baseline_metrics,
            "expected_accuracy": accuracy(cases, baseline_results),
        },
        "selected_result_agreement": round(agreement / len(cases), 6) if cases else 0.0,
        "p95_speedup": round(
            baseline_metrics["p95_ms"] / max(enhanced_metrics["p95_ms"], 0.000001), 3
        ),
    }
    write_json(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
