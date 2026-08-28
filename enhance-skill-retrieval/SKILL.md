---
name: enhance-skill-retrieval
description: Maintain user and agent-assigned tags for a harness-provided Skill catalog, benchmark large-catalog retrieval, and improve Skill selection using sampled post-task accuracy feedback. Use when a harness initializes or synchronizes tags, a user edits or inspects Skill tags, retrieval speed needs comparison, completed Skill use may request feedback, or a large Skill catalog needs tag-first candidate retrieval followed by metadata vector ranking. Do not use for installing, deleting, or storing Skills, and bypass tag retrieval for small catalogs.
---

# Enhance Skill Retrieval

Keep the harness registry as the source of truth for Skill metadata. Persist only tag assignments and derived vectors in the harness-provided state directory.

## Choose an operation

- Initialize the tag library and derived vector cache once from the harness's complete current catalog: run `scripts/manage_tags.py initialize`. Repeating the same snapshot is a no-op.
- Explicitly rebuild from a changed complete snapshot only for recovery, migration, or taxonomy changes: run `scripts/manage_tags.py rebuild`.
- Apply a normal harness install, update, or removal event: run `scripts/manage_tags.py sync`.
- Let users inspect and edit tags through the stable `enhance-skill` command in `scripts/enhance-skill`.
- Route a user request when the harness catalog exceeds its activation threshold: first call `scripts/route_query.py` with only `query`, `registry_version`, and `enabled_skill_ids`; after it returns candidate IDs, have the harness load only those Skill metadata records and call it again with `candidate_skills`.
- Measure retrieval behavior against JSONL cases: run `scripts/evaluate_retrieval.py`.
- Compare enhanced and full-metadata query rates: run `scripts/benchmark_retrieval.py`.

Read [references/protocol.md](references/protocol.md) before integrating a new harness. Read [references/feedback-protocol.md](references/feedback-protocol.md) before collecting feedback. Read [references/tag-taxonomy.yaml](references/tag-taxonomy.yaml) only when changing controlled tags or reviewing classification.

## Use the stable tag commands

Expose `scripts/enhance-skill` on `PATH` as `enhance-skill` and set `ENHANCE_SKILL_STATE_DIR` to the harness-owned state directory. Use only these public tag-management subcommands:

- `enhance-skill status`
- `enhance-skill list-tags [--assigned-only]`
- `enhance-skill list-skills`
- `enhance-skill show-tags <skill-id>`
- `enhance-skill skills-by-tag <tag>`
- `enhance-skill add-tags <skill-id> <tag>...`
- `enhance-skill remove-tags <skill-id> <tag>...`
- `enhance-skill lock-tags <skill-id> <tag>...`
- `enhance-skill unlock-tags <skill-id> <tag>...`
- `enhance-skill reclassify-tags <skill-id> --input <skill.json>`
- `enhance-skill request-feedback --interaction-id <id> --route-result <result.json> --query <query>`
- `enhance-skill record-feedback --interaction-id <id> --rating accurate`
- `enhance-skill record-feedback --interaction-id <id> --rating inaccurate --expected-skill-id <id> --expected-tags <tag>...`
- `enhance-skill record-feedback --interaction-id <id> --rating inaccurate --expected-none`
- `enhance-skill feedback-status [skill-id]`

Add `--json` before the subcommand for structured output. Keep `manage_tags.py` as the harness integration interface for initialization and lifecycle synchronization; do not expose its shorter internal mutation names as additional user commands.

## Collect feedback after completion

1. Ask only after a task completed with an `invoke` decision; never ask for `none`, `bypass`, or `error`.
2. Let `request-feedback` apply the configured 10% deterministic-random sample to the harness interaction ID.
3. Present one optional accurate/inaccurate question only when `sampled` is true.
4. For negative feedback, require the correct Skill plus user-intent tags, or `expected-none`.
5. Compare intent tags with matched tags and the correct Skill's effective tags. Classify query-tag mismatch, Skill-tag annotation error, candidate recall error, and rerank error independently.
6. Consume the pending request so a second answer is rejected. Store no raw interaction ID or query; persist only bounded hashes, derived vectors, and aggregate corrections.
7. Apply each correction only after 5 matching samples. Correct query-to-tag scores, virtual candidate tags, and reranking through separate channels.

## Preserve ownership boundaries

1. Accept Skill IDs and metadata from the harness; do not discover or mutate installed Skills.
2. Create the complete initial tag library, tag-to-Skill index, and derived metadata/tag vectors once from the harness snapshot.
3. Treat a second identical initialization as success without reclassification. Require explicit `rebuild` for a different full snapshot, preserving manual and locked tags for surviving Skill IDs.
4. After initialization, process only normal harness `skill_added`, `skill_updated`, and `skill_removed` events in v1; update the affected tag postings and metadata vector incrementally.
5. Preserve manual and locked tags when metadata changes.
6. Return `bypass` for routing when the catalog does not exceed the configured threshold. Continue maintaining indexes below the threshold so later activation needs no full rebuild.
7. Return one Skill only when it clears the score and winner-margin rules; otherwise return `none`.
8. Fail closed with `error`; let the harness decide whether to use its native fallback.
9. Never pass the complete Skill catalog during ordinary routing. Require the rerank metadata set to exactly match the freshly recalled candidate IDs and validate each metadata fingerprint.

## State directory

Require a writable `state_dir` supplied by the harness. Store:

- `skill-tags.yaml`: persistent tag assignments plus the tag-to-Skill inverted index in JSON-compatible YAML.
- `feedback-stats.json`: aggregate feedback, bounded pending hashes, derived correction vectors, and root-cause counters.
- `vector-cache.json`: persistent tag and Skill-metadata vectors maintained by initialization and lifecycle events; recover it by repeating `initialize` with the identical full snapshot or by explicit `rebuild`.

Never place dynamic state in this installed Skill directory. Never store full `SKILL.md` bodies.

## Default policy

- Activate tag retrieval above 80 enabled Skills.
- Retrieve up to 5 tags and 32 candidate Skills.
- Prefer manual locked tags, then native tags, then agent-generated controlled tags.
- Keep the controlled taxonomy stable. Treat unknown model suggestions as auxiliary tags until a user approves them.

Override defaults through the request payload or [references/routing-policy.yaml](references/routing-policy.yaml), not by editing scripts.
