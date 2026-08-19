#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "enhance-skill-retrieval" / "scripts"
CLI = SCRIPTS / "enhance-skill"
sys.path.insert(0, str(SCRIPTS))

from retrieval_core import (  # noqa: E402
    initialize_state,
    load_resources,
    load_state,
    mutate_tags,
    route,
    save_state,
    sync_state,
)


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
        self.payload = {"registry_version": "v1", "skills": self.skills}
        self.state = initialize_state(self.payload, self.taxonomy, self.provider)

    def test_initialize_and_round_trip_state(self):
        self.assertEqual(85, len(self.state["skills"]))
        with tempfile.TemporaryDirectory() as directory:
            save_state(directory, self.state)
            loaded = load_state(directory)
            self.assertEqual(self.state, loaded)
            with open(Path(directory) / "skill-tags.yaml", "r", encoding="utf-8") as handle:
                self.assertEqual("1.0.0", json.load(handle)["schema_version"])

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

    def test_added_and_removed_events(self):
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
        sync_state(
            self.state,
            {"event": "skill_removed", "registry_version": "v3", "skill_id": "new-audio"},
            self.taxonomy,
            self.provider,
        )
        self.assertNotIn("new-audio", self.state["skills"])

    def test_bypass_below_threshold(self):
        result = route(
            {"query": "read a PDF", "registry_version": "v1", "activation_threshold": 100, "skills": self.skills},
            self.state,
            self.taxonomy,
            self.policy,
            self.provider,
        )
        self.assertEqual("bypass", result["decision"])

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

    def test_tag_then_metadata_route(self):
        result = route(
            {"query": "预订明天下午的会议室并更新日历日程", "registry_version": "v1", "activation_threshold": 80, "skills": self.skills},
            self.state,
            self.taxonomy,
            self.policy,
            self.provider,
        )
        self.assertEqual("invoke", result["decision"], msg=result)
        self.assertEqual("lark-calendar", result["selected_skill_id"])

    def test_out_of_sync_catalog_fails(self):
        changed = list(self.skills) + [{"skill_id": "missing", "name": "missing", "description": "Missing tag record"}]
        with self.assertRaisesRegex(ValueError, "out of sync"):
            route(
                {"query": "missing", "registry_version": "v2", "activation_threshold": 80, "skills": changed},
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


if __name__ == "__main__":
    unittest.main()
