# Harness Protocol

## Operations

### Initialize

Pass a complete catalog snapshot to `manage_tags.py initialize`:

```json
{
  "registry_version": "v1",
  "skills": [
    {
      "skill_id": "lark-calendar",
      "name": "lark-calendar",
      "description": "Manage calendars, events, availability, and rooms.",
      "tags": ["productivity.calendar"],
      "aliases": ["日历", "日程"]
    }
  ]
}
```

### Synchronize

Pass one normal harness event to `manage_tags.py sync`:

```json
{"event":"skill_added","registry_version":"v2","skill":{"skill_id":"pdf","name":"pdf","description":"Read and edit PDF files."}}
```

Supported events are `skill_added`, `skill_updated`, and `skill_removed`. A removal event requires `skill_id`. V1 does not watch files or reconcile out-of-band changes.

### Edit tags

Expose `scripts/enhance-skill` on `PATH` and set `ENHANCE_SKILL_STATE_DIR` to the harness-owned state directory. The stable user commands are:

```text
enhance-skill status
enhance-skill list-tags [--assigned-only]
enhance-skill list-skills
enhance-skill show-tags <skill-id>
enhance-skill skills-by-tag <tag>
enhance-skill add-tags <skill-id> <tag>...
enhance-skill remove-tags <skill-id> <tag>...
enhance-skill lock-tags <skill-id> <tag>...
enhance-skill unlock-tags <skill-id> <tag>...
enhance-skill reclassify-tags <skill-id> --input <skill.json>
```

Pass `--json` before the subcommand for structured output. Manual tags override generated tags. Locked tags survive metadata updates. `unlock-tags` retains the tag as manual; use `remove-tags` to delete the manual assignment.

### Route

Pass the query and current harness catalog to `route_query.py`:

```json
{
  "query": "Move tomorrow's meeting to 3 PM",
  "registry_version": "v2",
  "activation_threshold": 80,
  "skills": []
}
```

The router returns `invoke`, `none`, `bypass`, or `error`. `invoke` contains exactly one `selected_skill_id`.

## State ownership

The harness owns Skill metadata and enabled state. The enhancer owns only manual/native/generated tag assignments. Derived vectors may be deleted and rebuilt at any time.
