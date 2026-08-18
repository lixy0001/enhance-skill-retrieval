#!/bin/sh
set -eu

phase_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

jq empty "$phase_dir/index.schema.json"
jq empty "$phase_dir/route-decision.schema.json"

case_count=$(wc -l < "$phase_dir/evaluation-cases.jsonl" | tr -d ' ')
test "$case_count" -eq 100

jq -s '
  length == 100 and
  ([.[].id] | unique | length) == 100 and
  all(.[];
    (.expected_decision == "invoke" and (.expected_skill | type) == "string" and (.required_candidate | type) == "string") or
    ((.expected_decision == "none" or .expected_decision == "error") and .expected_skill == null and (.expected_reason | type) == "string")
  )
' "$phase_dir/evaluation-cases.jsonl" | grep -qx true

ruby -e '
  require "yaml"
  require "json"
  dir = ARGV.fetch(0)
  taxonomy = YAML.safe_load(File.read(File.join(dir, "tag-taxonomy.yaml")), aliases: true)
  discovery = YAML.safe_load(File.read(File.join(dir, "discovery-policy.yaml")), aliases: true)
  ranking = YAML.safe_load(File.read(File.join(dir, "ranking-policy.yaml")), aliases: true)
  baseline = YAML.safe_load(File.read(File.join(dir, "baseline-skill-map.yaml")), aliases: true)
  cases = File.readlines(File.join(dir, "evaluation-cases.jsonl"), chomp: true).map { |line| JSON.parse(line) }
  tags = taxonomy.fetch("tags").map { |tag| tag.fetch("id") }
  raise "duplicate taxonomy tag" unless tags.uniq.length == tags.length
  expected_tags = cases.map { |item| item["expected_tag"] }.compact.uniq
  missing = expected_tags - tags
  raise "unknown expected tags: #{missing.join(", ")}" unless missing.empty?
  baseline_skills = baseline.fetch("skills")
  raise "baseline count mismatch" unless baseline_skills.length == baseline.fetch("managed_skill_count")
  baseline_ids = baseline_skills.map { |item| item.fetch("skill_id") }
  raise "duplicate baseline skill" unless baseline_ids.uniq.length == baseline_ids.length
  baseline_tags = baseline_skills.flat_map { |item| item.fetch("primary_tags") }.uniq
  missing_baseline_tags = baseline_tags - tags
  raise "unknown baseline tags: #{missing_baseline_tags.join(", ")}" unless missing_baseline_tags.empty?
  invalid_tag_counts = baseline_skills.select { |item| !(1..3).include?(item.fetch("primary_tags").length) }
  raise "invalid baseline primary tag count" unless invalid_tag_counts.empty?
  raise "candidate limit mismatch" unless ranking.dig("candidate_retrieval", "candidate_limit") == 12
  raise "managed scope mismatch" unless discovery["managed_scope"] == "user_installed"
' "$phase_dir"

echo "phase1 validation passed: 100 cases"
