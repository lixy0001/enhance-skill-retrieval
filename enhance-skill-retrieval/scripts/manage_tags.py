#!/usr/bin/env python3
"""Initialize, synchronize, edit, inspect, and reclassify Skill tags."""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict

from index_store import (
    build_vector_cache,
    ensure_vector_cache,
    load_vector_cache,
    save_vector_cache,
    update_vector_cache,
)
from retrieval_core import (
    catalog_digest,
    feedback_path,
    initialize_state,
    load_feedback_state,
    load_resources,
    load_state,
    mutate_tags,
    prune_feedback_to_skill_ids,
    read_json,
    reclassify_record,
    remove_skill_feedback,
    save_feedback_state,
    save_state,
    state_path,
    sync_state,
    write_json,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state-dir", required=True, help="Harness-owned writable state directory")
    result.add_argument("--input", default="-", help="JSON input file; defaults to stdin")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("initialize")
    sub.add_parser("rebuild")
    sub.add_parser("sync")
    for command in ("add", "remove", "lock", "unlock"):
        item = sub.add_parser(command)
        item.add_argument("skill_id")
        item.add_argument("tags", nargs="+")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("skill_id", nargs="?")
    reclassify = sub.add_parser("reclassify")
    reclassify.add_argument("skill_id")
    return result


def main() -> int:
    args = parser().parse_args()
    taxonomy, _, provider = load_resources()
    output: Dict[str, Any]
    if args.command in ("initialize", "rebuild"):
        payload = read_json(args.input)
        skills = payload.get("skills")
        if not isinstance(skills, list):
            raise ValueError(f"{args.command} payload requires skills array")
        existing = (
            load_state(args.state_dir, required=False)
            if state_path(args.state_dir).exists()
            else None
        )
        if args.command == "initialize" and existing is not None:
            if (
                existing.get("catalog_digest") != catalog_digest(skills)
                or existing.get("taxonomy_version") != taxonomy.get("version")
            ):
                raise ValueError("tag library differs from catalog or classifier; rebuild required")
            existing["registry_version"] = str(
                payload.get("registry_version", existing.get("registry_version", ""))
            )
            save_state(args.state_dir, existing)
            _, cache_rebuilt = ensure_vector_cache(
                args.state_dir, skills, existing, taxonomy, provider
            )
            output = {
                "status": "already_initialized",
                "skill_count": len(existing["skills"]),
                "vector_cache_rebuilt": cache_rebuilt,
            }
        else:
            state = initialize_state(
                payload,
                taxonomy,
                provider,
                previous_state=existing if args.command == "rebuild" else None,
            )
            save_state(args.state_dir, state)
            save_vector_cache(
                args.state_dir, build_vector_cache(skills, state, taxonomy, provider)
            )
            if (
                args.command == "rebuild"
                and existing is not None
                and feedback_path(args.state_dir).exists()
            ):
                feedback = load_feedback_state(args.state_dir)
                prune_feedback_to_skill_ids(feedback, state["skills"])
                save_feedback_state(args.state_dir, feedback)
            output = {
                "status": "rebuilt" if args.command == "rebuild" else "initialized",
                "skill_count": len(state["skills"]),
            }
    elif args.command == "sync":
        payload = read_json(args.input)
        state = load_state(args.state_dir)
        cache = load_vector_cache(args.state_dir, state, taxonomy, provider, required=True)
        event = sync_state(state, payload, taxonomy, provider)
        save_state(args.state_dir, state)
        save_vector_cache(
            args.state_dir,
            update_vector_cache(cache, payload, state, taxonomy, provider),
        )
        if event == "skill_removed" and feedback_path(args.state_dir).exists():
            feedback = load_feedback_state(args.state_dir)
            remove_skill_feedback(feedback, payload["skill_id"])
            save_feedback_state(args.state_dir, feedback)
        output = {"status": "synchronized", "event": event, "skill_count": len(state["skills"])}
    elif args.command in ("add", "remove", "lock", "unlock"):
        state = load_state(args.state_dir)
        record = mutate_tags(state, args.skill_id, args.command, args.tags)
        save_state(args.state_dir, state)
        output = {"status": "updated", "skill_id": args.skill_id, "record": record}
    elif args.command == "inspect":
        state = load_state(args.state_dir)
        if args.skill_id:
            output = {"skill_id": args.skill_id, "record": state["skills"].get(args.skill_id)}
        else:
            output = {"skill_count": len(state["skills"]), "skills": state["skills"]}
    else:
        payload = read_json(args.input)
        skill = payload.get("skill")
        if not isinstance(skill, dict) or skill.get("skill_id") != args.skill_id:
            raise ValueError("reclassify input requires matching skill object")
        state = load_state(args.state_dir)
        cache = load_vector_cache(args.state_dir, state, taxonomy, provider, required=True)
        record = reclassify_record(state, skill, taxonomy, provider)
        save_state(args.state_dir, state)
        event = {"event": "skill_updated", "skill": skill}
        save_vector_cache(
            args.state_dir,
            update_vector_cache(cache, event, state, taxonomy, provider),
        )
        output = {"status": "reclassified", "skill_id": args.skill_id, "record": record}
    write_json(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        sys.stderr.write(f"manage_tags: {error}\n")
        raise SystemExit(2)
