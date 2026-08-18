# Skill Library Router — Phase 1 Protocol

## 1. Objective

Manage every user-installed Skill currently present or installed later. For each user request:

1. Retrieve a bounded candidate set using normalized capability tags and request features.
2. Rank only those candidates and select one closest Skill.
3. Return `none` when no candidate is sufficiently relevant or the winner is not decisive.

This protocol defines discovery, indexing, routing, refusal, and incremental-update behavior. It does not assume that a normal Skill can intercept Codex's native Skill resolver; runtime integration is a later phase.

## 2. Managed scope

Scan these source classes in precedence order:

1. `personal`: `${CODEX_HOME}/skills/*/SKILL.md`, falling back to `~/.codex/skills/*/SKILL.md`.
2. `shared`: `~/.agents/skills/*/SKILL.md`.
3. `plugin`: `${CODEX_HOME}/plugins/cache/**/skills/*/SKILL.md`, falling back to `~/.codex/plugins/cache/**/skills/*/SKILL.md`.

Rules:

- Exclude any path segment named `.system`.
- Follow only resolved paths that remain under a configured discovery root.
- Treat a directory containing `SKILL.md` as one Skill.
- Read `agents/openai.yaml` when present, but never require it.
- Do not modify a discovered Skill.
- Deduplicate by normalized `skill_id`; prefer `personal`, then `shared`, then the newest enabled plugin version.
- Preserve shadowed records in the index with `enabled: false` and a `shadowed_by` reference.
- Mark disappeared Skills as deleted during synchronization, then remove them from the active routing view.

## 3. Incremental discovery

Run a synchronization:

- when the manager starts;
- immediately after installation, update, or removal when an installer hook is available;
- before routing when the discovery-root fingerprint differs from the last successful scan;
- periodically as a fallback when no hook or filesystem watcher is available.

Use the canonical path, `SKILL.md` modification time, size, and SHA-256 content hash. Reclassify only new or changed Skills. Rebuild affected tag postings atomically; retain the previous valid index if synchronization fails.

## 4. Indexing contract

Derive indexable content from:

1. YAML `name` and `description` — highest weight.
2. `agents/openai.yaml` display metadata — medium weight.
3. Headings, imperative workflow statements, tools, platform names, file types, and examples in the body — supporting weight.

Assign each Skill:

- one to three controlled `primary_tags`;
- zero to five controlled or normalized `secondary_tags`;
- positive trigger examples;
- negative examples or exclusions;
- typed facets such as platform, artifact, action, tool, and time orientation;
- a compact semantic representation for second-stage ranking.

Generated tags must resolve to `tag-taxonomy.yaml`. Unknown candidate labels are mapped to an existing tag or placed in a review queue; they must not silently expand the controlled taxonomy.

## 5. Stage 1 — candidate retrieval

Normalize the request without discarding the original text. Extract:

- explicit Skill names;
- platforms and products;
- requested actions;
- artifact or file types;
- temporal scope;
- read, write, send, delete, publish, or administrative intent;
- positive and negative constraints;
- recent conversational context required to resolve pronouns or follow-up intent.

Score controlled tags:

```text
tag_score =
    0.35 * semantic_similarity
  + 0.25 * positive_example_similarity
  + 0.20 * entity_and_artifact_match
  + 0.15 * lexical_alias_match
  + 0.05 * context_continuity
  - exclusion_penalty
```

Candidate construction:

1. Keep up to three tags with `tag_score >= 0.48`.
2. Union active Skills posted under those tags.
3. Add an explicitly named installed Skill even when its tag score is below the threshold.
4. Apply hard compatibility filters for explicit platform, artifact, and action exclusions.
5. Keep at most 12 candidates using preliminary score; ensure at least one candidate from each retained tag when possible.
6. If the set is empty, return `none` with `no_candidate_tag`.

Stage 1 retrieves a batch. It never makes the final invocation decision.

## 6. Stage 2 — unique Skill selection

Rank only the Stage 1 candidates:

```text
skill_score =
    0.30 * description_similarity
  + 0.25 * positive_example_similarity
  + 0.20 * platform_artifact_action_fit
  + 0.10 * explicit_name_match
  + 0.10 * context_continuity
  + 0.05 * calibrated_success_prior
  - negative_example_penalty
  - hard_mismatch_penalty
```

Default decision policy:

- Select exactly one Skill when the top score is at least `0.65` and its margin over the runner-up is at least `0.05`.
- Return `none` with `below_skill_threshold` when the top score is below `0.65`.
- Return `none` with `ambiguous_top_candidates` when the score margin is below `0.05`.
- Return `none` with `hard_constraint_mismatch` when the top candidate conflicts with an explicit platform, artifact, or action constraint.
- Never invoke multiple Skills in Phase 1.

Thresholds are configuration defaults, not immutable constants. Change them only through versioned evaluation results.

## 7. Decisions

The router emits one of:

- `invoke`: one selected Skill, candidate trace, matched tags, scores, and reason codes.
- `none`: no selected Skill, candidate trace when available, and a refusal reason.
- `error`: the router could not produce a valid decision. Runtime must fail closed and invoke no Skill.

`ambiguous` is represented as `none` plus the `ambiguous_top_candidates` reason so callers cannot accidentally treat ambiguity as authorization to invoke.

## 8. Safety and observability

- Fail closed on invalid index, stale schema version, classifier error, or missing selected Skill.
- Store routing traces without secrets or full sensitive message bodies; prefer request hashes and redacted features.
- Include model, taxonomy, policy, and index versions in each trace.
- Separate relevance from authorization. A routing match never grants permission to write, send, publish, purchase, delete, or administer external systems.
- Do not use historical popularity as a substitute for relevance; cap success-prior contribution at 0.05.

## 9. Versioning

Version independently:

- `protocol_version`
- `taxonomy_version`
- `index_schema_version`
- `decision_schema_version`
- `ranking_policy_version`
- `embedding_model_version`

Any incompatible schema or taxonomy change requires a full index rebuild.
