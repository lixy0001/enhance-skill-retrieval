#!/usr/bin/env python3
"""Route a query through tag recall and TypeSafe Jev candidate selection."""

from __future__ import annotations

import argparse

from index_store import load_vector_cache
from jev_reranker import JevReranker, read_harness_api_key
from retrieval_core import (
    load_feedback_state,
    enabled_skill_ids_digest,
    load_resources,
    load_state,
    read_json,
    recall_candidates,
    rerank_candidates,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--input", default="-", help="JSON input file; defaults to stdin")
    parser.add_argument(
        "--jev-api-key-file",
        help=(
            "Harness-owned secret file or inherited file descriptor path containing "
            "the TypeSafe API key"
        ),
    )
    args = parser.parse_args()
    try:
        payload = read_json(args.input)
        taxonomy, policy, provider = load_resources()
        allowed = {"query", "registry_version", "enabled_skill_ids"}
        if "candidate_skills" in payload:
            allowed.add("candidate_skills")
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise ValueError(
                "ordinary routing accepts only query, registry_version, "
                "enabled_skill_ids, and second-stage candidate_skills; unexpected: "
                + ", ".join(unexpected)
            )
        state = load_state(args.state_dir)
        vector_cache = load_vector_cache(args.state_dir, state, taxonomy, provider, required=True)
        feedback = load_feedback_state(args.state_dir)
        if "candidate_skills" in payload:
            jev_reranker = None
            if args.jev_api_key_file:
                jev_reranker = JevReranker.from_policy(
                    policy,
                    api_key=read_harness_api_key(args.jev_api_key_file),
                )
            result = rerank_candidates(
                payload,
                state,
                taxonomy,
                policy,
                provider,
                feedback,
                vector_cache,
                jev_reranker=jev_reranker,
            )
        else:
            result = recall_candidates(
                payload, state, taxonomy, policy, provider, feedback, vector_cache
            )
        result["enabled_skill_ids_digest"] = enabled_skill_ids_digest(
            payload["enabled_skill_ids"]
        )
    except Exception as error:
        result = {
            "decision": "error",
            "selected_skill_id": None,
            "confidence": 0.0,
            "matched_tags": [],
            "feedback_tags": [],
            "candidate_skill_ids": [],
            "reason_codes": [type(error).__name__, str(error)],
            "registry_version": "",
        }
    write_json(result)
    return 0 if result["decision"] != "error" else 2


if __name__ == "__main__":
    raise SystemExit(main())
