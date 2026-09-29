#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "enhance-skill-retrieval" / "scripts"
CLI = SCRIPTS / "enhance-skill"
sys.path.insert(0, str(SCRIPTS))

from retrieval_core import (  # noqa: E402
    catalog_digest,
    candidate_effective_tags,
    corrected_tag_scores,
    empty_feedback_state,
    enabled_skill_ids_digest,
    feedback_adjustment,
    initialize_state,
    load_feedback_state,
    load_resources,
    load_state,
    metadata_match_score,
    mutate_tags,
    record_shadow_comparison,
    recall_candidates,
    rerank_candidates,
    route,
    save_feedback_state,
    save_state,
    sync_state,
)
from index_store import (  # noqa: E402
    build_vector_cache,
    ensure_vector_cache,
    load_vector_cache,
    save_vector_cache,
    update_vector_cache,
    vector_cache_path,
)
from jev_reranker import (  # noqa: E402
    build_jev_request,
    parse_jev_response,
    read_harness_api_key,
    JevReranker,
    JevRerankerError,
)
from fake_jev import FakeJevReranker  # noqa: E402


def catalog(size: int = 85):
    skills = [
        {
            "skill_id": "lark-calendar",
            "name": "lark-calendar",
            "description": "Manage calendars, future events, availability, and meeting rooms. 管理日历日程会议室。",
            "tags": ["productivity.calendar"],
            "aliases": ["日历", "日程", "会议室"],
        },
        {
            "skill_id": "lark-vc",
            "name": "lark-vc",
            "description": "Search historical video meetings, minutes, participants, and transcripts. 历史会议纪要逐字稿。",
            "tags": ["collaboration.meetings"],
        },
        {
            "skill_id": "pdf",
            "name": "pdf",
            "description": "Read, create, inspect, render, and fill PDF files.",
            "tags": ["content.pdf"],
        },
        {
            "skill_id": "git-submit-push",
            "name": "git-submit-push",
            "description": "Inspect Git changes, create a commit, and push a branch. 提交并推送代码。",
            "tags": ["engineering.version-control"],
        },
    ]
    for index in range(max(0, size - len(skills))):
        skills.append(
            {
                "skill_id": f"fixture-{index:03d}",
                "name": f"fixture-{index:03d}",
                "description": f"Synthetic fixture utility number {index} for retrieval scale tests.",
                "tags": ["development.apps"],
            }
        )
    return skills


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.taxonomy, self.policy, self.provider = load_resources()
        self.skills = catalog()
        self.enabled_digest = enabled_skill_ids_digest(skill["skill_id"] for skill in self.skills)
        self.payload = {"registry_version": "v1", "skills": self.skills}
        self.state = initialize_state(self.payload, self.taxonomy, self.provider)

    def test_initialize_and_round_trip_state(self):
        self.assertEqual(85, len(self.state["skills"]))
        self.assertEqual(catalog_digest(self.skills), self.state["catalog_digest"])
        self.assertIn("pdf", self.state["tag_index"]["content.pdf"])
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, self.state)
            loaded = load_state(directory)
            self.assertEqual(self.state, loaded)
            with open(Path(directory) / "skill-tags.yaml", "r", encoding="utf-8") as handle:
                self.assertEqual("1.1.0", json.load(handle)["schema_version"])

    def test_version_one_tag_state_migrates_derived_index(self):
        legacy = json.loads(json.dumps(self.state))
        legacy["schema_version"] = "1.0.0"
        legacy.pop("index_version")
        legacy.pop("catalog_digest")
        legacy.pop("tag_index")
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "skill-tags.yaml").write_text(json.dumps(legacy))
            migrated = load_state(directory)
            self.assertEqual("1.1.0", migrated["schema_version"])
            self.assertEqual(catalog_digest(self.skills), migrated["catalog_digest"])
            self.assertIn("pdf", migrated["tag_index"]["content.pdf"])

    def test_current_state_rejects_stale_derived_index(self):
        stale = json.loads(json.dumps(self.state))
        stale["tag_index"]["content.pdf"] = []
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, stale)
            with self.assertRaisesRegex(ValueError, "tag index differs"):
                load_state(directory)

    def test_initialize_command_is_idempotent_and_requires_explicit_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "catalog.json"
            input_path.write_text(json.dumps(self.payload))
            first = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "manage_tags.py"),
                    "--state-dir",
                    directory,
                    "--input",
                    str(input_path),
                    "initialize",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            second = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "manage_tags.py"),
                    "--state-dir",
                    directory,
                    "--input",
                    str(input_path),
                    "initialize",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual("initialized", json.loads(first.stdout)["status"])
            self.assertEqual("already_initialized", json.loads(second.stdout)["status"])

            changed = {"registry_version": "v2", "skills": self.skills[:-1]}
            input_path.write_text(json.dumps(changed))
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "manage_tags.py"),
                    "--state-dir",
                    directory,
                    "--input",
                    str(input_path),
                    "initialize",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, rejected.returncode)
            self.assertIn("rebuild required", rejected.stderr)

    def test_manual_locked_tag_survives_update(self):
        mutate_tags(self.state, "pdf", "lock", ["content.documents"])
        updated = dict(self.skills[2])
        updated["description"] = "Updated PDF metadata"
        sync_state(
            self.state,
            {"event": "skill_updated", "registry_version": "v2", "skill": updated},
            self.taxonomy,
            self.provider,
        )
        record = self.state["skills"]["pdf"]
        self.assertIn("content.documents", record["manual_tags"])
        self.assertIn("content.documents", record["locked_tags"])
        self.assertIn("content.documents", record["effective_tags"])
        self.assertIn("pdf", self.state["tag_index"]["content.documents"])

    def test_rebuild_preserves_manual_and_locked_tags(self):
        mutate_tags(self.state, "pdf", "lock", ["content.documents"])
        rebuilt = initialize_state(
            self.payload,
            self.taxonomy,
            self.provider,
            previous_state=self.state,
        )
        self.assertIn("content.documents", rebuilt["skills"]["pdf"]["manual_tags"])
        self.assertIn("content.documents", rebuilt["skills"]["pdf"]["locked_tags"])
        self.assertIn("pdf", rebuilt["tag_index"]["content.documents"])

    def test_rebuild_command_preserves_manual_and_locked_tags(self):
        mutate_tags(self.state, "pdf", "lock", ["content.documents"])
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, self.state)
            input_path = Path(directory) / "catalog.json"
            input_path.write_text(json.dumps(self.payload))
            rebuilt = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "manage_tags.py"),
                    "--state-dir",
                    directory,
                    "--input",
                    str(input_path),
                    "rebuild",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual("rebuilt", json.loads(rebuilt.stdout)["status"])
            state = load_state(directory)
            self.assertIn("content.documents", state["skills"]["pdf"]["manual_tags"])
            self.assertIn("content.documents", state["skills"]["pdf"]["locked_tags"])
            self.assertTrue(vector_cache_path(directory).exists())

    def test_rebuild_prunes_feedback_for_removed_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, self.state)
            save_feedback_state(
                directory,
                {
                    "schema_version": "1.1.0",
                    "total_responses": 1,
                    "skills": {"pdf": {"positive": 1, "negative": 0}},
                    "tag_skill": {
                        "content.pdf": {"pdf": {"positive": 1, "negative": 0}}
                    },
                    "rerank_skills": {"pdf": {"positive": 1, "negative": 0}},
                    "annotation_suggestions": {"pdf": {"content.documents": 5}},
                    "pending": {},
                },
            )
            input_path = Path(directory) / "catalog.json"
            remaining = [skill for skill in self.skills if skill["skill_id"] != "pdf"]
            input_path.write_text(json.dumps({"registry_version": "v2", "skills": remaining}))

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "manage_tags.py"),
                    "--state-dir",
                    directory,
                    "--input",
                    str(input_path),
                    "rebuild",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            feedback = load_feedback_state(directory)
            self.assertNotIn("pdf", feedback["skills"])
            self.assertNotIn("content.pdf", feedback["tag_skill"])
            self.assertNotIn("pdf", feedback["rerank_skills"])
            self.assertNotIn("pdf", feedback["annotation_suggestions"])

            save_feedback_state(
                directory,
                {
                    "schema_version": "1.1.0",
                    "total_responses": 1,
                    "skills": {"pdf": {"positive": 1, "negative": 0}},
                    "tag_skill": {},
                    "rerank_skills": {},
                    "annotation_suggestions": {},
                    "pending": {},
                },
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "manage_tags.py"),
                    "--state-dir",
                    directory,
                    "--input",
                    str(input_path),
                    "rebuild",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertNotIn("pdf", load_feedback_state(directory)["skills"])

    def test_added_and_removed_events(self):
        original_digest = self.state["catalog_digest"]
        skill = {
            "skill_id": "new-audio",
            "name": "new-audio",
            "description": "Clean background noise from audio recordings.",
            "tags": ["media.audio"],
        }
        sync_state(
            self.state,
            {"event": "skill_added", "registry_version": "v2", "skill": skill},
            self.taxonomy,
            self.provider,
        )
        self.assertIn("new-audio", self.state["skills"])
        self.assertIn("new-audio", self.state["tag_index"]["media.audio"])
        self.assertNotEqual(original_digest, self.state["catalog_digest"])
        sync_state(
            self.state,
            {"event": "skill_removed", "registry_version": "v3", "skill_id": "new-audio"},
            self.taxonomy,
            self.provider,
        )
        self.assertNotIn("new-audio", self.state["skills"])
        self.assertNotIn("new-audio", self.state.get("tag_index", {}).get("media.audio", []))
        self.assertEqual(original_digest, self.state["catalog_digest"])

    def test_vector_cache_can_be_deleted_and_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = build_vector_cache(self.skills, self.state, self.taxonomy, self.provider)
            save_vector_cache(directory, cache)
            loaded = load_vector_cache(directory, self.state, self.taxonomy, self.provider)
            self.assertEqual(self.state["catalog_digest"], loaded["catalog_digest"])
            self.assertIn("content.pdf", loaded["tag_vectors"])
            self.assertIn("pdf", loaded["skill_vectors"])

            vector_cache_path(directory).unlink()
            rebuilt, changed = ensure_vector_cache(
                directory,
                self.skills,
                self.state,
                self.taxonomy,
                self.provider,
            )
            self.assertTrue(changed)
            self.assertTrue(vector_cache_path(directory).exists())
            self.assertEqual(
                self.state["skills"]["pdf"]["metadata_fingerprint"],
                rebuilt["skill_vectors"]["pdf"]["metadata_fingerprint"],
            )

            vector_cache_path(directory).write_text("not json", encoding="utf-8")
            rebuilt, changed = ensure_vector_cache(
                directory,
                self.skills,
                self.state,
                self.taxonomy,
                self.provider,
            )
            self.assertTrue(changed)
            self.assertIn("pdf", rebuilt["skill_vectors"])

    def test_vector_cache_rejects_missing_skill_vector(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = build_vector_cache(self.skills, self.state, self.taxonomy, self.provider)
            del cache["skill_vectors"]["pdf"]
            save_vector_cache(directory, cache)
            with self.assertRaisesRegex(ValueError, "stale"):
                load_vector_cache(
                    directory,
                    self.state,
                    self.taxonomy,
                    self.provider,
                    required=True,
                )

    def test_vector_cache_rejects_non_numeric_and_non_finite_vectors(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = build_vector_cache(self.skills, self.state, self.taxonomy, self.provider)
            cache["tag_vectors"]["content.pdf"] = ["bad"] * self.provider.dimensions
            save_vector_cache(directory, cache)
            with self.assertRaisesRegex(ValueError, "stale"):
                load_vector_cache(
                    directory,
                    self.state,
                    self.taxonomy,
                    self.provider,
                    required=True,
                )

            cache = build_vector_cache(self.skills, self.state, self.taxonomy, self.provider)
            cache["skill_vectors"]["pdf"]["vector"][0] = float("nan")
            save_vector_cache(directory, cache)
            with self.assertRaisesRegex(ValueError, "stale"):
                load_vector_cache(
                    directory,
                    self.state,
                    self.taxonomy,
                    self.provider,
                    required=True,
                )

    def test_vector_cache_updates_incrementally_for_lifecycle_events(self):
        cache = build_vector_cache(self.skills, self.state, self.taxonomy, self.provider)
        added = {
            "skill_id": "new-audio",
            "name": "new-audio",
            "description": "Clean background noise from audio recordings.",
            "tags": ["media.audio"],
        }
        event = {"event": "skill_added", "registry_version": "v2", "skill": added}
        sync_state(self.state, event, self.taxonomy, self.provider)
        cache = update_vector_cache(cache, event, self.state, self.taxonomy, self.provider)
        self.assertIn("new-audio", cache["skill_vectors"])
        self.assertEqual(self.state["catalog_digest"], cache["catalog_digest"])

        event = {"event": "skill_removed", "registry_version": "v3", "skill_id": "new-audio"}
        sync_state(self.state, event, self.taxonomy, self.provider)
        cache = update_vector_cache(cache, event, self.state, self.taxonomy, self.provider)
        self.assertNotIn("new-audio", cache["skill_vectors"])
        self.assertEqual(self.state["catalog_digest"], cache["catalog_digest"])

    def test_cached_and_uncached_metadata_scoring_match(self):
        skill = self.skills[0]
        query = "Book a meeting room and update the calendar"
        query_vector = self.provider.embed(query)
        cache = build_vector_cache(self.skills, self.state, self.taxonomy, self.provider)
        uncached = metadata_match_score(
            query,
            query_vector,
            skill,
            self.provider,
            self.state["skills"][skill["skill_id"]]["effective_tags"],
            0.75,
        )
        cached = metadata_match_score(
            query,
            query_vector,
            skill,
            self.provider,
            self.state["skills"][skill["skill_id"]]["effective_tags"],
            0.75,
            cache["skill_vectors"][skill["skill_id"]]["vector"],
        )
        self.assertEqual(uncached, cached)

    def test_bypass_below_threshold(self):
        result = route(
            {"query": "read a PDF", "registry_version": "v1", "activation_threshold": 100, "skills": self.skills},
            self.state,
            self.taxonomy,
            self.policy,
            self.provider,
        )
        self.assertEqual("bypass", result["decision"])

    def test_small_catalog_checks_enabled_ids_before_bypass(self):
        with self.assertRaisesRegex(ValueError, "out of sync"):
            recall_candidates(
                {
                    "query": "general request",
                    "registry_version": "v1",
                    "enabled_skill_ids": ["lark-calendar", "missing"],
                },
                self.state,
                self.taxonomy,
                self.policy,
                self.provider,
            )

    def test_explicit_skill_name(self):
        result = route(
            {"query": "Use git-submit-push now", "registry_version": "v1", "activation_threshold": 80, "skills": self.skills},
            self.state,
            self.taxonomy,
            self.policy,
            self.provider,
        )
        self.assertEqual("invoke", result["decision"])
        self.assertEqual("git-submit-push", result["selected_skill_id"])

    def test_explicit_skill_id_does_not_match_prefix(self):
        skills = self.skills + [
            {
                "skill_id": "skill-1",
                "name": "skill-1",
                "description": "First numbered Skill.",
                "tags": ["development.apps"],
            },
            {
                "skill_id": "skill-10",
                "name": "skill-10",
                "description": "Tenth numbered Skill.",
                "tags": ["development.apps"],
            },
        ]
        state = initialize_state(
            {"registry_version": "prefix", "skills": skills},
            self.taxonomy,
            self.provider,
        )
        result = recall_candidates(
            {
                "query": "Use skill-10 for this request",
                "registry_version": "prefix",
                "enabled_skill_ids": [skill["skill_id"] for skill in skills],
            },
            state,
            self.taxonomy,
            self.policy,
            self.provider,
        )
        self.assertEqual(["skill-10"], result["candidate_skill_ids"])

    def test_tag_recall_then_jev_route(self):
        jev = FakeJevReranker("lark-calendar")
        result = route(
            {"query": "预订明天下午的会议室并更新日历日程", "registry_version": "v1", "activation_threshold": 80, "skills": self.skills},
            self.state,
            self.taxonomy,
            self.policy,
            self.provider,
            jev_reranker=jev,
        )
        self.assertEqual("invoke", result["decision"], msg=result)
        self.assertEqual("lark-calendar", result["selected_skill_id"])
        self.assertEqual(["productivity.calendar"], result["feedback_tags"])
        self.assertEqual("jev-test", result["reranker_model"])
        self.assertEqual(1, len(jev.calls))

    def test_route_command_uses_id_only_recall_then_candidate_metadata_rerank(self):
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, self.state)
            save_vector_cache(
                directory,
                build_vector_cache(self.skills, self.state, self.taxonomy, self.provider),
            )
            route_input = Path(directory) / "route.json"
            recall_payload = {
                "query": "预订明天下午的会议室并更新日历日程",
                "registry_version": "v1",
                "enabled_skill_ids": [skill["skill_id"] for skill in self.skills],
            }
            route_input.write_text(json.dumps(recall_payload))
            recalled = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route_query.py"),
                    "--state-dir",
                    directory,
                    "--input",
                    str(route_input),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            recall_result = json.loads(recalled.stdout)
            self.assertEqual("candidates", recall_result["decision"])
            self.assertIn("lark-calendar", recall_result["candidate_skill_ids"])

            wanted = set(recall_result["candidate_skill_ids"])
            route_input.write_text(
                json.dumps(
                    {
                        **recall_payload,
                        "candidate_skills": [
                            skill for skill in self.skills if skill["skill_id"] in wanted
                        ],
                    }
                )
            )
            reranked = rerank_candidates(
                json.loads(route_input.read_text()),
                self.state,
                self.taxonomy,
                self.policy,
                self.provider,
                vector_cache=load_vector_cache(
                    directory,
                    self.state,
                    self.taxonomy,
                    self.provider,
                    required=True,
                ),
                jev_reranker=FakeJevReranker("lark-calendar"),
            )
            self.assertEqual("lark-calendar", reranked["selected_skill_id"])

            environment = dict(os.environ)
            environment["TYPESAFE_API_KEY"] = "legacy-environment-secret"
            missing_injection = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route_query.py"),
                    "--state-dir",
                    directory,
                    "--input",
                    str(route_input),
                ],
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(2, missing_injection.returncode)
            self.assertIn("Harness must inject a Jev reranker", missing_injection.stdout)
            self.assertNotIn("legacy-environment-secret", missing_injection.stdout)

            route_input.write_text(json.dumps({**recall_payload, "skills": self.skills}))
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "route_query.py"),
                    "--state-dir",
                    directory,
                    "--input",
                    str(route_input),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, rejected.returncode)
            self.assertIn("ordinary routing accepts only", rejected.stdout)

    def test_rerank_requires_exact_recalled_candidate_metadata_set(self):
        with self.assertRaisesRegex(ValueError, "exactly match"):
            rerank_candidates(
                {
                    "query": "预订明天下午的会议室并更新日历日程",
                    "registry_version": "v1",
                    "enabled_skill_ids": [skill["skill_id"] for skill in self.skills],
                    "candidate_skills": [],
                },
                self.state,
                self.taxonomy,
                self.policy,
                self.provider,
            )

    def test_out_of_sync_catalog_fails(self):
        with self.assertRaisesRegex(ValueError, "out of sync"):
            recall_candidates(
                {
                    "query": "missing",
                    "registry_version": "v1",
                    "activation_threshold": 80,
                    "enabled_skill_ids": [skill["skill_id"] for skill in self.skills] + ["missing"],
                },
                self.state,
                self.taxonomy,
                self.policy,
                self.provider,
            )

    def test_changed_candidate_metadata_requires_sync(self):
        changed_pdf = dict(self.skills[2])
        changed_pdf["description"] = "Changed without a harness update event"
        with self.assertRaisesRegex(ValueError, "candidate metadata differs"):
            rerank_candidates(
                {
                    "query": "Use pdf now",
                    "registry_version": "v1",
                    "activation_threshold": 80,
                    "enabled_skill_ids": [skill["skill_id"] for skill in self.skills],
                    "candidate_skills": [changed_pdf],
                },
                self.state,
                self.taxonomy,
                self.policy,
                self.provider,
            )

    def run_cli(self, directory, *arguments):
        environment = dict(os.environ)
        environment["ENHANCE_SKILL_STATE_DIR"] = str(directory)
        result = subprocess.run(
            [sys.executable, str(CLI), "--json", *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        return json.loads(result.stdout)

    def test_stable_cli_lists_and_mutates_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, self.state)

            tags = self.run_cli(directory, "list-tags", "--assigned-only")
            assigned = {row["tag"]: row["assigned_skill_count"] for row in tags["tags"]}
            self.assertEqual(1, assigned["productivity.calendar"])

            updated = self.run_cli(
                directory,
                "lock-tags",
                "pdf",
                "content.documents",
            )
            self.assertIn("content.documents", updated["record"]["locked_tags"])

            shown = self.run_cli(directory, "show-tags", "pdf")
            self.assertIn("content.documents", shown["record"]["effective_tags"])

            matches = self.run_cli(directory, "skills-by-tag", "content.documents")
            self.assertEqual(["pdf"], matches["skills"])

    def test_stable_cli_reclassify_updates_vector_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, self.state)
            save_vector_cache(
                directory,
                build_vector_cache(self.skills, self.state, self.taxonomy, self.provider),
            )
            changed = dict(self.skills[2])
            changed["description"] = "Read, render, edit, and compare PDF documents."
            input_path = Path(directory) / "pdf.json"
            input_path.write_text(json.dumps(changed))

            result = self.run_cli(
                directory,
                "reclassify-tags",
                "pdf",
                "--input",
                str(input_path),
            )
            self.assertEqual("reclassified", result["status"])
            state = load_state(directory)
            cache = load_vector_cache(
                directory, state, self.taxonomy, self.provider, required=True
            )
            self.assertEqual(
                state["skills"]["pdf"]["metadata_fingerprint"],
                cache["skill_vectors"]["pdf"]["metadata_fingerprint"],
            )

    def test_stable_cli_reports_uninitialized_status(self):
        environment = dict(os.environ)
        environment.pop("ENHANCE_SKILL_STATE_DIR", None)
        result = subprocess.run(
            [sys.executable, str(CLI), "--json", "status"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        status = json.loads(result.stdout)
        self.assertFalse(status["initialized"])

    def test_harness_selects_highest_jev_fit(self):
        skills = catalog()
        skills[0] = {
            "skill_id": "calendar-a",
            "name": "calendar-a",
            "description": "Manage calendars, future events, availability, and meeting rooms.",
            "tags": ["productivity.calendar"],
        }
        skills[1] = {
            "skill_id": "calendar-b",
            "name": "calendar-b",
            "description": "Manage calendars, future events, availability, and meeting rooms.",
            "tags": ["productivity.calendar"],
        }
        state = initialize_state({"registry_version": "feedback", "skills": skills}, self.taxonomy, self.provider)
        payload = {
            "query": "Book a future meeting room and update the calendar",
            "registry_version": "feedback",
            "activation_threshold": 80,
            "skills": skills,
        }
        feedback = {
            "schema_version": "1.2.0",
            "total_comparisons": 16,
            "agreements": 0,
            "disagreements": 16,
            "skills": {},
            "tag_skill": {},
            "rerank_skills": {
                "calendar-a": {"positive": 0, "negative": 8},
                "calendar-b": {"positive": 8, "negative": 0},
            },
        }

        class FitsOnlyJevReranker:
            def score(self, query, candidates):
                return {
                    "fits": {"calendar-a": 0.2, "calendar-b": 0.9},
                    "model": "jev-test",
                }

        result = route(
            payload,
            state,
            self.taxonomy,
            self.policy,
            self.provider,
            feedback,
            jev_reranker=FitsOnlyJevReranker(),
        )
        self.assertEqual("invoke", result["decision"])
        self.assertEqual("calendar-b", result["selected_skill_id"])
        self.assertEqual(["calendar-b", "calendar-a"], result["candidate_skill_ids"])
        self.assertEqual({"calendar-b": 0.9, "calendar-a": 0.2}, result["candidate_fit_scores"])
        self.assertEqual(0.9, result["highest_fit_probability"])
        self.assertNotIn("candidate_choice_scores", result)
        self.assertNotIn("confidence", result)
        self.assertEqual(["jev_fit"], result["reason_codes"])

    def test_jev_fit_gate_can_reject_all_candidates(self):
        result = route(
            {
                "query": "预订明天下午的会议室并更新日历日程",
                "registry_version": "v1",
                "activation_threshold": 80,
                "skills": self.skills,
            },
            self.state,
            self.taxonomy,
            self.policy,
            self.provider,
            jev_reranker=FakeJevReranker("lark-calendar", best_fit=0.1),
        )
        self.assertEqual("none", result["decision"])
        self.assertIsNone(result["selected_skill_id"])
        self.assertEqual(["jev_no_candidate_fit"], result["reason_codes"])

    def test_harness_breaks_equal_jev_fits_by_skill_id(self):
        class EqualFitsJevReranker:
            def score(self, query, candidates):
                return {
                    "fits": {skill["skill_id"]: 0.8 for skill in candidates},
                    "model": "jev-test",
                }

        result = route(
            {
                "query": "预订明天下午的会议室并更新日历日程",
                "registry_version": "v1",
                "activation_threshold": 80,
                "skills": self.skills,
            },
            self.state,
            self.taxonomy,
            self.policy,
            self.provider,
            jev_reranker=EqualFitsJevReranker(),
        )
        self.assertEqual("invoke", result["decision"])
        self.assertEqual(["lark-calendar", "lark-vc"], result["candidate_skill_ids"])
        self.assertEqual("lark-calendar", result["selected_skill_id"])

    def test_jev_request_hides_recall_order_and_scores(self):
        request = build_jev_request(
            "read this document",
            [self.skills[2], self.skills[0]],
            "jev-test",
        )
        self.assertEqual(
            ["fits::lark-calendar", "fits::pdf"],
            list(request["questions"]),
        )
        self.assertEqual(
            {"noul"},
            {question["type"] for question in request["questions"].values()},
        )
        self.assertNotIn("score", json.dumps(request).casefold())

    def test_jev_response_rejects_fit_outside_candidate_set(self):
        response = {
            "model": "jev-test",
            "answers": {
                "fits::pdf": {"type": "noul", "noul": 1.0},
                "fits::outside": {"type": "noul", "noul": 0.5},
            },
        }
        with self.assertRaisesRegex(JevRerankerError, "do not match"):
            parse_jev_response(response, ["pdf"])

    def test_jev_http_client_uses_only_typed_fit_answers(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, limit):
                return json.dumps(
                    {
                        "model": "jev-1.test",
                        "answers": {
                            "fits::lark-calendar": {"type": "noul", "noul": 0.1},
                            "fits::pdf": {"type": "noul", "noul": 0.95},
                        },
                        "usage": {"input_tokens": 10, "output_tokens": 4},
                    }
                ).encode()

        client = JevReranker(
            "secret",
            endpoint="https://typesafe.invalid",
            model="jev-test",
            max_attempts=1,
        )
        with patch("jev_reranker.urllib.request.urlopen", return_value=Response()) as mocked:
            result = client.score("read a PDF", [self.skills[2], self.skills[0]])
        self.assertEqual({"lark-calendar": 0.1, "pdf": 0.95}, result["fits"])
        request = mocked.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual("jev-test", payload["model"])
        self.assertEqual(
            ["fits::lark-calendar", "fits::pdf"],
            list(payload["questions"]),
        )
        self.assertEqual("Bearer secret", request.get_header("Authorization"))

    def test_jev_requires_harness_injected_api_key(self):
        with self.assertRaisesRegex(JevRerankerError, "Harness must inject"):
            JevReranker.from_policy(self.policy, api_key="  ")

    def test_jev_policy_uses_harness_injected_api_key(self):
        client = JevReranker.from_policy(self.policy, api_key=" harness-secret\n")
        self.assertEqual("harness-secret", client.api_key)
        self.assertEqual("jev-latest", client.model)

    def test_harness_api_key_file_is_bounded_and_trimmed(self):
        with tempfile.TemporaryDirectory() as directory:
            secret_path = Path(directory) / "jev-key"
            secret_path.write_text(" injected-secret\n", encoding="utf-8")
            self.assertEqual("injected-secret", read_harness_api_key(str(secret_path)))

            secret_path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(JevRerankerError, "Harness must inject"):
                read_harness_api_key(str(secret_path))

    def test_shadow_comparison_cli_records_agreement_without_user_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, self.state)
            save_vector_cache(
                directory,
                build_vector_cache(self.skills, self.state, self.taxonomy, self.provider),
            )
            enhanced_path = Path(directory) / "enhanced-result.json"
            baseline_path = Path(directory) / "baseline-result.json"
            enhanced_path.write_text(
                json.dumps(
                    {
                        "decision": "invoke",
                        "selected_skill_id": "pdf",
                        "matched_tags": ["content.pdf"],
                        "feedback_tags": ["content.pdf"],
                        "candidate_skill_ids": ["pdf"],
                        "registry_version": "v1",
                        "enabled_skill_ids_digest": self.enabled_digest,
                    }
                )
            )
            baseline_path.write_text(
                json.dumps(
                    {
                        "decision": "invoke",
                        "selected_skill_id": "pdf",
                        "registry_version": "v1",
                        "enabled_skill_ids_digest": self.enabled_digest,
                    }
                )
            )
            recorded = self.run_cli(
                directory,
                "compare-routing",
                "--comparison-id",
                "completed-task-1",
                "--enhanced-result",
                str(enhanced_path),
                "--baseline-result",
                str(baseline_path),
                "--query",
                "Read and render a PDF",
            )
            self.assertTrue(recorded["agreement"])
            self.assertEqual(["baseline_agreement"], recorded["root_causes"])
            persisted_text = (Path(directory) / "feedback-stats.json").read_text()
            self.assertNotIn("completed-task-1", persisted_text)
            self.assertNotIn("Read and render a PDF", persisted_text)
            self.assertIn("sha256:", persisted_text)
            persisted = load_feedback_state(directory)
            self.assertEqual(1, persisted["skills"]["pdf"]["positive"])

            report = self.run_cli(directory, "comparison-status", "pdf")
            self.assertEqual(1, report["total_comparisons"])
            self.assertEqual(1, report["agreements"])

            duplicate = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "--state-dir",
                    directory,
                    "compare-routing",
                    "--comparison-id",
                    "completed-task-1",
                    "--enhanced-result",
                    str(enhanced_path),
                    "--baseline-result",
                    str(baseline_path),
                    "--query",
                    "Read and render a PDF",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, duplicate.returncode)
            self.assertIn("already recorded", duplicate.stderr)

    def test_shadow_comparison_cli_uses_native_hit_as_pseudo_correct_result(self):
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, self.state)
            save_vector_cache(
                directory,
                build_vector_cache(self.skills, self.state, self.taxonomy, self.provider),
            )
            enhanced_path = Path(directory) / "enhanced-result.json"
            baseline_path = Path(directory) / "baseline-result.json"
            enhanced_path.write_text(
                json.dumps(
                    {
                        "decision": "invoke",
                        "selected_skill_id": "lark-calendar",
                        "matched_tags": ["productivity.calendar"],
                        "feedback_tags": ["productivity.calendar"],
                        "candidate_skill_ids": ["lark-calendar"],
                        "registry_version": "v1",
                        "enabled_skill_ids_digest": self.enabled_digest,
                    }
                )
            )
            baseline_path.write_text(
                json.dumps(
                    {
                        "decision": "invoke",
                        "selected_skill_id": "lark-vc",
                        "registry_version": "v1",
                        "enabled_skill_ids_digest": self.enabled_digest,
                    }
                )
            )
            recorded = None
            for index in range(5):
                recorded = self.run_cli(
                    directory,
                    "compare-routing",
                    "--comparison-id",
                    f"native-comparison-{index}",
                    "--enhanced-result",
                    str(enhanced_path),
                    "--baseline-result",
                    str(baseline_path),
                    "--query",
                    "Find the historical meeting transcript",
                )
            self.assertIn("query_tag_mismatch", recorded["root_causes"])
            self.assertIn("candidate_recall_error", recorded["root_causes"])
            self.assertEqual(["collaboration.meetings"], recorded["inferred_intent_tags"])
            feedback = load_feedback_state(directory)
            self.assertEqual(5, feedback["disagreements"])
            self.assertEqual(
                5,
                feedback["query_tag_corrections"]["productivity.calendar"]
                ["collaboration.meetings"]["samples"],
            )

    def test_shadow_comparison_classifies_rerank_error(self):
        feedback = empty_feedback_state()
        enhanced_result = {
            "decision": "invoke",
            "selected_skill_id": "lark-vc",
            "matched_tags": ["collaboration.meetings", "productivity.calendar"],
            "feedback_tags": ["collaboration.meetings"],
            "candidate_skill_ids": ["lark-vc", "lark-calendar"],
            "registry_version": "v1",
            "enabled_skill_ids_digest": self.enabled_digest,
        }
        baseline_result = {
            "decision": "invoke",
            "selected_skill_id": "lark-calendar",
            "registry_version": "v1",
            "enabled_skill_ids_digest": self.enabled_digest,
        }
        result = None
        for index in range(5):
            result = record_shadow_comparison(
                feedback,
                f"rerank-comparison-{index}",
                "Schedule a future calendar meeting",
                enhanced_result,
                baseline_result,
                self.state,
                self.taxonomy,
                self.provider,
            )
        self.assertEqual(["skill_rerank_error"], result["root_causes"])
        self.assertEqual(5, feedback["rerank_skills"]["lark-calendar"]["positive"])
        self.assertEqual(5, feedback["rerank_skills"]["lark-vc"]["negative"])

    def test_shadow_comparison_classifies_over_recall(self):
        feedback = empty_feedback_state()
        enhanced_result = {
            "decision": "invoke",
            "selected_skill_id": "lark-calendar",
            "matched_tags": ["productivity.calendar"],
            "feedback_tags": ["productivity.calendar"],
            "candidate_skill_ids": ["lark-calendar"],
            "registry_version": "v1",
            "enabled_skill_ids_digest": self.enabled_digest,
        }
        baseline_result = {
            "decision": "none",
            "selected_skill_id": None,
            "registry_version": "v1",
            "enabled_skill_ids_digest": self.enabled_digest,
        }
        result = None
        for index in range(5):
            result = record_shadow_comparison(
                feedback,
                f"over-recall-{index}",
                "Just answer this general question",
                enhanced_result,
                baseline_result,
                self.state,
                self.taxonomy,
                self.provider,
            )
        self.assertEqual(["over_recall"], result["root_causes"])
        corrected, applied = corrected_tag_scores(
            self.provider.embed("Just answer this general question"),
            {"productivity.calendar": 0.5},
            feedback,
            self.policy,
        )
        self.assertTrue(applied)
        self.assertLess(corrected["productivity.calendar"], 0.5)

    def test_shadow_comparison_rejects_different_enabled_sets_without_mutation(self):
        feedback = empty_feedback_state()
        enhanced_result = {
            "decision": "invoke",
            "selected_skill_id": "pdf",
            "matched_tags": ["content.pdf"],
            "feedback_tags": ["content.pdf"],
            "candidate_skill_ids": ["pdf"],
            "registry_version": "v1",
            "enabled_skill_ids_digest": self.enabled_digest,
        }
        baseline_result = {
            "decision": "invoke",
            "selected_skill_id": "pdf",
            "registry_version": "v1",
            "enabled_skill_ids_digest": enabled_skill_ids_digest(["pdf"]),
        }
        with self.assertRaisesRegex(ValueError, "enabled Skill sets differ"):
            record_shadow_comparison(
                feedback,
                "different-enabled-set",
                "Read a PDF",
                enhanced_result,
                baseline_result,
                self.state,
                self.taxonomy,
                self.provider,
            )
        self.assertEqual(0, feedback["total_comparisons"])
        self.assertEqual({}, feedback["processed_comparisons"])

    def test_legacy_user_feedback_is_not_carried_into_shadow_comparisons(self):
        with tempfile.TemporaryDirectory() as directory:
            save_feedback_state(
                directory,
                {
                    "schema_version": "1.1.0",
                    "total_responses": 9,
                    "skills": {"pdf": {"positive": 9, "negative": 0}},
                    "rerank_skills": {"pdf": {"positive": 9, "negative": 0}},
                    "pending": {"sha256:legacy": {}},
                },
            )
            migrated = load_feedback_state(directory)
            self.assertEqual("1.2.0", migrated["schema_version"])
            self.assertEqual(0, migrated["total_comparisons"])
            self.assertEqual({}, migrated["skills"])
            self.assertNotIn("pending", migrated)

    def test_harness_removal_prunes_skill_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, self.state)
            save_vector_cache(
                directory,
                build_vector_cache(self.skills, self.state, self.taxonomy, self.provider),
            )
            feedback = {
                "schema_version": "1.0.0",
                "total_responses": 1,
                "skills": {"pdf": {"positive": 1, "negative": 0}},
                "tag_skill": {
                    "content.pdf": {"pdf": {"positive": 1, "negative": 0}}
                },
            }
            save_feedback_state(directory, feedback)
            event = {
                "event": "skill_removed",
                "registry_version": "v2",
                "skill_id": "pdf",
            }
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "manage_tags.py"),
                    "--state-dir",
                    directory,
                    "sync",
                ],
                input=json.dumps(event),
                check=True,
                capture_output=True,
                text=True,
            )
            persisted = load_feedback_state(directory)
            self.assertNotIn("pdf", persisted["skills"])
            self.assertNotIn("content.pdf", persisted["tag_skill"])

if __name__ == "__main__":
    unittest.main()
