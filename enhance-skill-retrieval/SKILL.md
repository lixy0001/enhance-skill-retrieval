---
name: enhance-skill-retrieval
description: Maintain user and agent-assigned tags for a harness-provided Skill catalog and improve Skill selection when the active catalog exceeds a configured threshold. Use when a harness initializes tags, reports a Skill installation/update/removal, a user edits or inspects Skill tags, or a large Skill catalog needs tag-first candidate retrieval followed by metadata vector ranking. Do not use for installing, deleting, or storing Skills, and bypass tag retrieval for small catalogs.
---

# Enhance Skill Retrieval

Keep the harness registry as the source of truth for Skill metadata. Persist only tag assignments and derived vectors in the harness-provided state directory.

## Choose an operation

- Initialize a tag library from the harness's complete current catalog: run `scripts/manage_tags.py initialize`.
- Apply a normal harness install, update, or removal event: run `scripts/manage_tags.py sync`.
- Let users inspect and edit tags through the stable `enhance-skill` command in `scripts/enhance-skill`.
- Route a user request when the harness catalog exceeds its activation threshold: run `scripts/route_query.py`.
- Measure retrieval behavior against JSONL cases: run `scripts/evaluate_retrieval.py`.

Read [references/protocol.md](references/protocol.md) before integrating a new harness. Read [references/tag-taxonomy.yaml](references/tag-taxonomy.yaml) only when changing controlled tags or reviewing classification.

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

Add `--json` before the subcommand for structured output. Keep `manage_tags.py` as the harness integration interface for initialization and lifecycle synchronization; do not expose its shorter internal mutation names as additional user commands.

## Preserve ownership boundaries

1. Accept Skill IDs and metadata from the harness; do not discover or mutate installed Skills.
2. Create the complete initial tag library once from the harness snapshot.
3. After initialization, process only normal harness `skill_added`, `skill_updated`, and `skill_removed` events in v1.
4. Preserve manual and locked tags when metadata changes.
5. Return `bypass` for routing when the catalog does not exceed the configured threshold. Continue allowing tag maintenance below the threshold.
6. Return one Skill only when it clears the score and winner-margin rules; otherwise return `none`.
7. Fail closed with `error`; let the harness decide whether to use its native fallback.

## State directory

Require a writable `state_dir` supplied by the harness. Store:

- `skill-tags.yaml`: persistent tag assignments in JSON-compatible YAML.
- `vector-cache.json`: optional, disposable derived vectors.

Never place dynamic state in this installed Skill directory. Never store full `SKILL.md` bodies.

## Default policy

- Activate tag retrieval above 80 enabled Skills.
- Retrieve up to 5 tags and 32 candidate Skills.
- Prefer manual locked tags, then native tags, then agent-generated controlled tags.
- Keep the controlled taxonomy stable. Treat unknown model suggestions as auxiliary tags until a user approves them.

Override defaults through the request payload or [references/routing-policy.yaml](references/routing-policy.yaml), not by editing scripts.
