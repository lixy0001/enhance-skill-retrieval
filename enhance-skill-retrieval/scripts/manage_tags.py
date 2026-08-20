#!/usr/bin/env python3
"""Initialize, synchronize, edit, inspect, and reclassify Skill tags."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from retrieval_core import (
    feedback_path,
    initialize_state,
    load_feedback_state,
    load_resources,
    load_state,
    mutate_tags,
    read_json,
    reclassify_record,
    remove_skill_feedback,
    save_feedback_state,
    save_state,
    sync_state,
    write_json,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state-dir", required=True, help="Harness-owned writable state directory")
    result.add_argument("--input", default="-", help="JSON input file; defaults to stdin")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("initialize")
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
    if args.command == "initialize":
        payload = read_json(args.input)
        state = initialize_state(payload, taxonomy, provider)
        save_state(args.state_dir, state)
        output = {"status": "initialized", "skill_count": len(state["skills"])}
    elif args.command == "sync":
        payload = read_json(args.input)
        state = load_state(args.state_dir)
        event = sync_state(state, payload, taxonomy, provider)
        save_state(args.state_dir, state)
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
        record = reclassify_record(state, skill, taxonomy, provider)
        save_state(args.state_dir, state)
        output = {"status": "reclassified", "skill_id": args.skill_id, "record": record}
    write_json(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
