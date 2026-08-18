# Phase 1 Acceptance Criteria

## Contract completeness

- Discovery covers personal, shared, and installed-plugin Skill sources.
- `.system` Skills are excluded.
- Existing, newly installed, updated, removed, duplicated, and shadowed Skills have defined behavior.
- Stage 1 returns a batch of at most 12 candidates from at most three matched tags.
- Stage 2 can select only one Skill.
- Low-confidence, ambiguous, incompatible, invalid-index, and internal-error cases invoke no Skill.

## Schema validity

- Both JSON Schema documents parse as JSON and use Draft 2020-12.
- An index record cannot omit identity, source, hash, tags, facets, or examples.
- An `invoke` decision requires a non-null selected Skill.
- A `none` or `error` decision requires a null selected Skill.
- Candidate and tag limits match the ranking policy.

## Taxonomy quality

- Every active Skill receives one to three controlled primary tags.
- No generated primary tag bypasses taxonomy review.
- Tags represent reusable capability domains rather than one label per Skill.
- Chinese and English requests resolve to the same stable tag IDs.
- Platform, artifact, action, time, and access concerns remain facets and do not cause uncontrolled tag growth.

## Evaluation gates for the Phase 2 implementation

Evaluate on the versioned cases in `evaluation-cases.jsonl` using the current installed library plus synthetic future-install cases.

Minimum initial gates:

- Stage 1 candidate recall@12: at least 0.97 for `invoke` cases.
- Stage 2 top-1 accuracy: at least 0.90 for `invoke` cases.
- Incorrect invocation rate: at most 0.03 over all cases.
- `none` precision: at least 0.90.
- Explicit Skill-name resolution: 1.00 when the named Skill is installed and enabled.
- Removed or shadowed Skill invocation rate: 0.
- Schema-invalid decisions accepted by the caller: 0.

Threshold changes require a committed evaluation report containing the dataset version, metric deltas, and error examples. Optimize incorrect invocation before missed invocation.

## Phase 1 completion checklist

- [x] Managed scope and precedence defined.
- [x] Incremental synchronization behavior defined.
- [x] Controlled taxonomy defined.
- [x] All 64 currently discovered user-library Skills have a baseline tag mapping.
- [x] Candidate-retrieval and unique-selection policies defined.
- [x] Index and route-decision schemas defined.
- [x] Evaluation cases parse and contain the required coverage groups.
- [x] Cross-file constants and versions pass automated consistency checks.
