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
enhance-skill compare-routing --comparison-id <id> --enhanced-result <result.json> --baseline-result <result.json> --query <query>
enhance-skill comparison-status [skill-id]
```

Pass `--json` before the subcommand for structured output. Manual tags override generated tags. Locked tags survive metadata updates. `unlock-tags` retains the tag as manual; use `remove-tags` to delete the manual assignment.

### Benchmark retrieval

Compare tag-enhanced retrieval with full-catalog metadata matching:

```text
python3 scripts/benchmark_retrieval.py \
  --catalog <catalog.json> \
  --cases <cases.jsonl> \
  --jev-api-key-file <harness-secret-path-or-/dev/fd/N> \
  --synthetic-size 500 \
  --iterations 20
```

The report includes query throughput, mean/P50/P95 latency, expected accuracy, result agreement, and P95 speedup. Treat timing as a benchmark report, not a unit-test pass condition.
The enhanced arm makes live Jev requests. The harness must supply its credential through `--jev-api-key-file`; the value may be a protected secret-file path or an inherited descriptor such as `/dev/fd/N`. Its latency includes the external service and the run consumes API usage.

### Compare enhanced and native routing

For the same query, registry version, and enabled Skill set, let the harness run its native Skill selector independently of the enhanced route. Both results must include the same SHA-256 digest of the sorted enabled Skill IDs. Pass both final route results to `compare-routing`. The native selected Skill, or native `none`, is the pseudo-correct result; no user rating is requested. Follow [feedback-protocol.md](feedback-protocol.md). Ordinary enhanced routing must still receive no complete catalog.

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
match the recalled IDs, validates metadata fingerprints, and sends all candidates
to TypeSafe Jev. One `Noul` answer per candidate supplies an absolute fit probability.
The harness sorts those probabilities, breaks equal fits by Skill ID, and returns
`none` when the highest fit is below `jev_fit_threshold`; otherwise it selects the
highest-fit Skill. Candidates carry no recall score or previous ordering. The result
includes `highest_fit_probability`, `candidate_fit_scores`, and `reranker_model`
for diagnostics.

For an in-process integration, construct `JevReranker.from_policy(policy,
api_key=secret)` and pass it as `jev_reranker`. For a subprocess integration, add
`--jev-api-key-file <harness-secret-path-or-/dev/fd/N>` only to the second call.
Keep the key out of routing JSON, environment variables, state, output, and logs.
The first-stage call needs no credential. A small enabled set returns `bypass` on
the first call. Version drift, unknown enabled IDs, full-catalog `skills` input,
stale candidate metadata, missing vector cache, missing Jev injection, service
failure, and missing, extra, or malformed fit probabilities fail closed with `error`;
the harness owns fallback behavior.

## State ownership

The harness owns Skill metadata, enabled state, and the native selector used as the shadow baseline. The enhancer owns manual/native/generated tag assignments, its derived tag-to-Skill index, and aggregate comparison corrections. Derived vectors are persisted for reuse and maintained by lifecycle events; repeat `initialize` with the identical full snapshot or use explicit `rebuild` to recover a missing or incompatible cache. Routing below the activation threshold still returns `bypass`; initialization and lifecycle maintenance continue regardless of catalog size.
