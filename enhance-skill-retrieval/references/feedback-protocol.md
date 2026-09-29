# Automatic Shadow-Comparison Protocol

## Run two independent selectors

For one query, the harness runs:

1. the enhanced two-stage route;
2. the harness native Skill selector with enhancement disabled.

Both selectors must use the same `registry_version` and enabled Skill set. Each result carries the SHA-256 digest of the sorted enabled Skill IDs, and `compare-routing` rejects unequal digests. The native selector may return `invoke` with one Skill ID or `none`. Treat that result as a pseudo-label; do not ask the user to judge accuracy.

The native selector stays outside the enhancer. It may use the harness registry internally, but the ordinary enhanced route still receives only `query + registry_version + enabled_skill_ids`, followed by candidate metadata for reranking.

## Record one comparison

Save the two final results and call:

```text
enhance-skill --json compare-routing \
  --comparison-id task-2026-0001 \
  --enhanced-result enhanced-result.json \
  --baseline-result native-result.json \
  --query "查看昨天会议的逐字稿"
```

Each result must contain `decision`, `selected_skill_id`, `registry_version`, and `enabled_skill_ids_digest`. Enhanced results also carry `matched_tags`, `feedback_tags`, and `candidate_skill_ids` when available.

The comparison ID is stored only as a bounded hash for idempotency. The raw query is vectorized for correction learning and is not persisted. A repeated comparison ID is rejected.

## Attribute differences automatically

When the native selector chooses a Skill, infer the most relevant controlled intent tag from that Skill's current `effective_tags`. Then classify:

| Condition | Root cause | Correction channel |
|---|---|---|
| Native intent tag absent from enhanced matched tags | `query_tag_mismatch` | Similar-query tag correction prototype |
| Native Skill absent from enhanced candidates | `candidate_recall_error` | First-stage query/tag-to-Skill adjustment |
| Native Skill present but enhanced route selects another Skill or `none` | `skill_rerank_error` | Metadata rerank adjustment |
| Native selector returns `none` while enhanced route invokes | `over_recall` | Matched-tag rejection adjustment |
| Both selectors choose the same Skill or both return `none` | `baseline_agreement` | Aggregate agreement; reinforce matching Skill/tag only |

The baseline comparison alone cannot prove that a Skill needs a new persistent tag, so it does not rewrite manual/native/agent tags or create virtual annotation tags.

## Apply corrections conservatively

- Treat the native result as a pseudo-label, not ground truth guaranteed by a user.
- Require `feedback_min_samples` matching observations before any correction affects routing.
- Apply query-tag corrections only to similar queries above `feedback_query_similarity`.
- Cap query-tag and final score movements with the configured maximum adjustments.
- Explicit Skill IDs override learned corrections.
- Reject comparisons with stale registry versions, unequal enabled-set digests, unknown Skill IDs, uncontrolled matched tags, invalid decisions, or duplicate comparison IDs.

Removing a Skill through the harness removes its Skill-level and rerank comparison statistics. Rebuild discards statistics for Skills no longer present.
