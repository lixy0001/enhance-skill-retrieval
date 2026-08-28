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

Call `initialize` when this enhancer is first attached to a harness state directory. It creates tag assignments, the tag-to-Skill inverted index, and tag/metadata vectors. Repeating it with the same catalog and taxonomy returns `already_initialized`; it does not reclassify every Skill. A different snapshot is rejected with `rebuild required` so a missed lifecycle event cannot silently overwrite state.

For explicit recovery or a taxonomy migration, pass the same complete snapshot shape to `manage_tags.py rebuild`. Rebuild preserves manual and locked tags for Skill IDs that still exist, recreates derived indexes, and replaces the vector cache.

### Synchronize

Pass one normal harness event to `manage_tags.py sync`:

```json
{"event":"skill_added","registry_version":"v2","skill":{"skill_id":"pdf","name":"pdf","description":"Read and edit PDF files."}}
```

Supported events are `skill_added`, `skill_updated`, and `skill_removed`. A removal event requires `skill_id`. Each event updates only the affected tag postings and Skill metadata vector. V1 does not watch files or reconcile out-of-band changes.

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
enhance-skill request-feedback --interaction-id <id> --route-result <result.json> --query <query>
enhance-skill record-feedback --interaction-id <id> --rating accurate
enhance-skill record-feedback --interaction-id <id> --rating inaccurate --expected-skill-id <id> --expected-tags <tag>...
enhance-skill record-feedback --interaction-id <id> --rating inaccurate --expected-none
enhance-skill feedback-status [skill-id]
```

Pass `--json` before the subcommand for structured output. Manual tags override generated tags. Locked tags survive metadata updates. `unlock-tags` retains the tag as manual; use `remove-tags` to delete the manual assignment.

### Benchmark retrieval

Compare tag-enhanced retrieval with full-catalog metadata matching:

```text
python3 scripts/benchmark_retrieval.py \
  --catalog <catalog.json> \
  --cases <cases.jsonl> \
  --synthetic-size 500 \
  --iterations 20
```

The report includes query throughput, mean/P50/P95 latency, expected accuracy, result agreement, and P95 speedup. Treat timing as a benchmark report, not a unit-test pass condition.

### Collect feedback

After an invoked Skill completes successfully, call `request-feedback` with the route result and query. Ask the returned question only when sampled. A negative answer must identify the correct Skill and intended controlled tags, or state that no Skill was needed. Follow [feedback-protocol.md](feedback-protocol.md); never collect feedback before task completion or persist the raw conversation.

### Route

Ordinary conversation routing is a two-stage handshake. First pass only the query,
the current registry version, and enabled Skill IDs to `route_query.py`:

```json
{
  "query": "Move tomorrow's meeting to 3 PM",
  "registry_version": "v2",
  "enabled_skill_ids": ["lark-calendar", "lark-vc", "pdf"]
}
```

When enhanced routing is active, the router returns `decision: candidates` plus up
to the configured candidate limit of Skill IDs. The harness loads metadata only for
those IDs and calls the same script again:

```json
{
  "query": "Move tomorrow's meeting to 3 PM",
  "registry_version": "v2",
  "enabled_skill_ids": ["lark-calendar", "lark-vc", "pdf"],
  "candidate_skills": [
    {
      "skill_id": "lark-calendar",
      "name": "lark-calendar",
      "description": "Manage calendars, events, availability, and rooms.",
      "tags": ["productivity.calendar"]
    }
  ]
}
```

The second call recomputes recall, requires the candidate metadata IDs to exactly
match the recalled IDs, validates metadata fingerprints, and then returns `invoke`
or `none`. A small enabled set returns `bypass` on the first call. Version drift,
unknown enabled IDs, full-catalog `skills` input, stale candidate metadata, and a
missing vector cache fail closed with `error`.

## State ownership

The harness owns Skill metadata and enabled state. The enhancer owns manual/native/generated tag assignments, its derived tag-to-Skill index, and aggregate accuracy counters. Derived vectors are persisted for reuse and maintained by lifecycle events; repeat `initialize` with the identical full snapshot or use explicit `rebuild` to recover a missing or incompatible cache. Routing below the activation threshold still returns `bypass`; initialization and lifecycle maintenance continue regardless of catalog size.
