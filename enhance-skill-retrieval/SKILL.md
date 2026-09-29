---
name: enhance-skill-retrieval
description: Maintain tags for a harness-provided Skill catalog and route large catalogs with tag-first recall plus TypeSafe Jev semantic selection. Use when a harness initializes or synchronizes tags, a user edits or inspects Skill tags, retrieval behavior needs comparison, or candidate Skills need Jev reranking. Do not use for installing, deleting, or storing Skills, and bypass tag retrieval for small catalogs.
---

# Enhance Skill Retrieval

Keep the harness registry as the source of truth for Skill metadata. Persist only tag assignments and derived vectors in the harness-provided state directory.

## Choose an operation

- Initialize the tag library and derived vector cache once from the harness's complete current catalog: run `scripts/manage_tags.py initialize`. Repeating the same snapshot is a no-op.
- Explicitly rebuild from a changed complete snapshot only for recovery, migration, or taxonomy changes: run `scripts/manage_tags.py rebuild`.
- Apply a normal harness install, update, or removal event: run `scripts/manage_tags.py sync`.
- Let users inspect and edit tags through the stable `enhance-skill` command in `scripts/enhance-skill`.
- Route a user request when the harness catalog exceeds its activation threshold: first call `scripts/route_query.py` with only `query`, `registry_version`, and `enabled_skill_ids`; after it returns candidate IDs, have the harness load only those Skill metadata records and call it again with `candidate_skills`. Inject a configured `JevReranker` for the second stage; subprocess harnesses pass a secret file or inherited descriptor through `--jev-api-key-file`.
- Measure retrieval behavior against JSONL cases with a harness-owned Jev secret file: run `scripts/evaluate_retrieval.py --jev-api-key-file <path>`.
- Compare enhanced and full-metadata query rates with a harness-owned Jev secret file: run `scripts/benchmark_retrieval.py --jev-api-key-file <path>`.

Read [references/protocol.md](references/protocol.md) before integrating a new harness. Read [references/feedback-protocol.md](references/feedback-protocol.md) before enabling automatic shadow comparisons. Read [references/tag-taxonomy.yaml](references/tag-taxonomy.yaml) only when changing controlled tags or reviewing classification.

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
- `enhance-skill compare-routing --comparison-id <id> --enhanced-result <result.json> --baseline-result <result.json> --query <query>`
- `enhance-skill comparison-status [skill-id]`

Add `--json` before the subcommand for structured output. Keep `manage_tags.py` as the harness integration interface for initialization and lifecycle synchronization; do not expose its shorter internal mutation names as additional user commands.

## Learn from native-route comparisons

1. Run the enhanced route and the harness native route independently for the same query, registry version, and enabled Skill set; require both results to carry the same enabled-ID digest.
2. Treat the native route's selected Skill, or native `none`, as the pseudo-correct result. Do not ask the user to rate the selection.
3. Let the harness pass only the two route results and query to `compare-routing`; ordinary enhanced routing still receives no complete Skill catalog.
4. Infer the query's intended controlled tag from the native-selected Skill's effective tags, then classify query-tag mismatch, candidate recall error, rerank error, or over-recall.
5. Hash and bound comparison IDs for idempotency. Store no raw comparison ID or query.
6. Apply a correction only after 5 matching comparisons and keep all score movements capped. A single native-route error must not immediately change routing.

## Use Jev for the final decision

1. Keep the first stage deterministic and high-recall. Its tag and hash-vector scores produce candidate IDs only.
2. Send every recalled candidate's name, description, aliases, and tags to Jev in one request. Sort options by Skill ID and omit recall scores and prior ordering.
3. Use one `Choice` answer to select among candidates and one `Noul` answer per candidate to decide whether any candidate truly fits.
4. Return `none` when every fit probability is below `jev_fit_threshold`. Otherwise return the `Choice` winner; expose Choice and fit probabilities as diagnostics, not accuracy claims.
5. Validate that every returned probability is finite and bounded and that the winner belongs to the freshly recalled enabled set.
6. Require the harness to construct and inject the Jev client with its TypeSafe API key. For subprocess integration, read the key only from the harness-owned path supplied by `--jev-api-key-file`; keep credentials out of routing JSON, environment variables, state, output, and logs.
7. Fail closed with `error` on missing injection, timeout, service failure, malformed output, or an out-of-set winner. Let the harness choose its native fallback.

## Preserve ownership boundaries

1. Accept Skill IDs and metadata from the harness; do not discover or mutate installed Skills.
2. Create the complete initial tag library, tag-to-Skill index, and derived metadata/tag vectors once from the harness snapshot.
3. Treat a second identical initialization as success without reclassification. Require explicit `rebuild` for a different full snapshot, preserving manual and locked tags for surviving Skill IDs.
4. After initialization, process only normal harness `skill_added`, `skill_updated`, and `skill_removed` events in v1; update the affected tag postings and metadata vector incrementally.
5. Preserve manual and locked tags when metadata changes.
6. Return `bypass` for routing when the catalog does not exceed the configured threshold. Continue maintaining indexes below the threshold so later activation needs no full rebuild.
7. Return one Skill only when Jev selects a recalled candidate and at least one candidate clears the calibrated fit gate; otherwise return `none`.
8. Fail closed with `error`; let the harness decide whether to use its native fallback.
9. Never pass the complete Skill catalog during ordinary routing. Require the rerank metadata set to exactly match the freshly recalled candidate IDs and validate each metadata fingerprint.

## State directory

Require a writable `state_dir` supplied by the harness. Store:

- `skill-tags.yaml`: persistent tag assignments plus the tag-to-Skill inverted index in JSON-compatible YAML.
- `feedback-stats.json`: aggregate shadow-comparison counts, bounded comparison hashes, derived correction vectors, and root-cause counters.
- `vector-cache.json`: persistent tag and Skill-metadata vectors maintained by initialization and lifecycle events; recover it by repeating `initialize` with the identical full snapshot or by explicit `rebuild`.

Never place dynamic state in this installed Skill directory. Never store full `SKILL.md` bodies.

## Default policy

- Activate tag retrieval above 80 enabled Skills.
- Retrieve up to 5 tags and 32 candidate Skills.
- Use `jev-latest` with a 5-second timeout, two attempts, and a 0.30 fit gate. Calibrate the gate against harness golden cases before production use.
- Prefer manual locked tags, then native tags, then agent-generated controlled tags.
- Keep the controlled taxonomy stable. Treat unknown model suggestions as auxiliary tags until a user approves them.

Override defaults through the request payload or [references/routing-policy.yaml](references/routing-policy.yaml), not by editing scripts.
