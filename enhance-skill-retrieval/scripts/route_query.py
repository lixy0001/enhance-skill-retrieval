#!/usr/bin/env python3
"""Route a query through tag recall and candidate metadata vector ranking."""

from __future__ import annotations

import argparse

from index_store import load_vector_cache
from retrieval_core import (
    load_feedback_state,
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
        router = rerank_candidates if "candidate_skills" in payload else recall_candidates
        result = router(payload, state, taxonomy, policy, provider, feedback, vector_cache)
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
