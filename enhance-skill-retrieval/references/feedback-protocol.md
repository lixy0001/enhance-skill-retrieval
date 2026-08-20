# Feedback Protocol

## Request feedback after completion

Run feedback sampling only after a successful task whose route decision was `invoke`. Save the route result to JSON, then pass it with the completed-task query:

```text
enhance-skill --json request-feedback \
  --interaction-id task-2026-0001 \
  --route-result route-result.json \
  --query "查看昨天会议的逐字稿"
```

The command validates the selected Skill and its `feedback_tags`, hashes the interaction ID, converts the query to a local derived vector, and applies the default 10% stable pseudorandom sample. Never ask after `none`, `bypass`, `error`, cancellation, or failed execution.

Do not persist the raw interaction ID or query. A sampled pending request contains only the interaction hash, selected Skill, matched tags, feedback tags, candidate IDs, and derived query vector. Cap pending requests with `feedback_pending_limit`.

## Record accurate feedback

```text
enhance-skill record-feedback \
  --interaction-id task-2026-0001 \
  --rating accurate
```

Reinforce only the selected Skill and its final `feedback_tags`.

## Record inaccurate feedback

Ask which Skill should have been used and which controlled tags represent the user's intent:

```text
enhance-skill record-feedback \
  --interaction-id task-2026-0001 \
  --rating inaccurate \
  --expected-skill-id lark-vc \
  --expected-tags collaboration.meetings
```

If no Skill should have been invoked:

```text
enhance-skill record-feedback \
  --interaction-id task-2026-0001 \
  --rating inaccurate \
  --expected-none
```

Reject negative feedback that omits both the expected Skill and intent tags. Consume each pending request once so repeated feedback fails.

## Attribute the root cause

Compare:

- `matched_tags`: tags retrieved for the query;
- `expected_tags`: user-confirmed intent tags;
- the expected Skill's `effective_tags`;
- `candidate_skill_ids`: Skills admitted to metadata ranking.

Classify independently:

| Condition | Root cause | Correction channel |
|---|---|---|
| Expected tag absent from `matched_tags` | `query_tag_mismatch` | Similar-query tag correction prototype |
| Expected tag absent from expected Skill's tags | `skill_tag_annotation_error` | Controlled virtual-tag suggestion |
| Expected Skill absent from candidates | `candidate_recall_error` | First-stage tag-to-Skill adjustment |
| Expected Skill present but not selected | `skill_rerank_error` | Metadata rerank adjustment |
| No Skill should have run | `over_recall` | Matched-tag rejection adjustment |

Multiple causes may apply to one response. Do not force a single cause when both query matching and Skill annotation are wrong.

## Apply corrections conservatively

- Query-tag mismatch: aggregate derived-vector centroids by wrong-tag to expected-tag pair. After `feedback_min_samples`, adjust only similar queries above `feedback_query_similarity` and cap tag-score movement with `feedback_tag_max_adjustment`.
- Skill annotation error: count a controlled tag suggestion for the expected Skill. After `feedback_min_samples`, use it as a virtual candidate tag without rewriting the Skill's persisted manual/native/agent tags.
- Rerank error: adjust final metadata ranking only when the expected Skill was already in the candidate set.
- Over-recall: penalize repeatedly rejected matched tags after the minimum sample count.
- Explicit Skill names always override learned feedback.

Removing a Skill through the harness removes its Skill-level, rerank, annotation, and pending feedback state.
