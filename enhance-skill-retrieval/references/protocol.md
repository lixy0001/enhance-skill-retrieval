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

Use `manage_tags.py add`, `remove`, `lock`, or `unlock`. Manual tags override generated tags. Locked tags survive metadata updates.

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
