#!/usr/bin/env python3
"""Route a query through tag recall and candidate metadata vector ranking."""

from __future__ import annotations

import argparse

from retrieval_core import load_resources, load_state, read_json, route, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--input", default="-", help="JSON input file; defaults to stdin")
    args = parser.parse_args()
    try:
        payload = read_json(args.input)
        taxonomy, policy, provider = load_resources()
        threshold = int(payload.get("activation_threshold", policy["activation_threshold"]))
        if isinstance(payload.get("skills"), list) and len(payload["skills"]) <= threshold:
            state = {"skills": {}}
        else:
            state = load_state(args.state_dir)
        result = route(payload, state, taxonomy, policy, provider)
    except Exception as error:
        result = {
            "decision": "error",
            "selected_skill_id": None,
            "confidence": 0.0,
            "matched_tags": [],
            "candidate_skill_ids": [],
            "reason_codes": [type(error).__name__, str(error)],
            "registry_version": "",
        }
    write_json(result)
    return 0 if result["decision"] != "error" else 2


if __name__ == "__main__":
    raise SystemExit(main())
