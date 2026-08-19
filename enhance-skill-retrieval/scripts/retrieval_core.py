#!/usr/bin/env python3
"""Dependency-free tag management and vector retrieval primitives."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple


SCHEMA_VERSION = "1.0.0"
DEFAULT_STATE_FILE = "skill-tags.yaml"


def read_json(path: str | None) -> Dict[str, Any]:
    if path and path != "-":
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    import sys

    return json.load(sys.stdin)


def write_json(data: Mapping[str, Any]) -> None:
    import sys

    json.dump(data, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def load_json_yaml(path: Path) -> Dict[str, Any]:
    """Load a JSON-compatible YAML file without a third-party YAML dependency."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json_yaml(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def state_path(state_dir: str) -> Path:
    return Path(state_dir).expanduser().resolve() / DEFAULT_STATE_FILE


def load_state(state_dir: str, required: bool = True) -> Dict[str, Any]:
    path = state_path(state_dir)
    if not path.exists():
        if required:
            raise FileNotFoundError(f"tag library not initialized: {path}")
        return {
            "schema_version": SCHEMA_VERSION,
            "taxonomy_version": "",
            "registry_version": "",
            "skills": {},
        }
    state = load_json_yaml(path)
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported tag-library schema version")
    return state


def save_state(state_dir: str, state: Mapping[str, Any]) -> None:
    atomic_write_json_yaml(state_path(state_dir), state)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def metadata_fingerprint(skill: Mapping[str, Any]) -> str:
    relevant = {
        "skill_id": skill.get("skill_id"),
        "name": skill.get("name"),
        "description": skill.get("description"),
        "tags": skill.get("tags", []),
        "aliases": skill.get("aliases", []),
        "metadata_version": skill.get("metadata_version", ""),
    }
    return "sha256:" + hashlib.sha256(canonical_json(relevant).encode("utf-8")).hexdigest()


def require_skill(skill: Mapping[str, Any]) -> None:
    for key in ("skill_id", "name", "description"):
        if not isinstance(skill.get(key), str) or not skill[key].strip():
            raise ValueError(f"skill requires non-empty {key}")


def unique_strings(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def text_features(text: str) -> List[str]:
    lowered = text.casefold()
    words = re.findall(r"[a-z0-9][a-z0-9_.+-]*", lowered)
    cjk_runs = re.findall(r"[\u3400-\u9fff]+", lowered)
    cjk: List[str] = []
    for run in cjk_runs:
        cjk.extend(list(run))
        cjk.extend(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    return unique_strings(words + cjk)


class HashEmbeddingProvider:
    """Stable local feature-hashing vectors; replaceable by a harness adapter."""

    def __init__(self, dimensions: int = 512):
        if dimensions < 64:
            raise ValueError("embedding dimensions must be at least 64")
        self.dimensions = dimensions
        self.name = "local-hash-v1"

    def embed(self, text: str) -> List[float]:
        vector = [0.0] * self.dimensions
        features = text_features(text)
        for feature in features:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions differ")
    return max(0.0, sum(a * b for a, b in zip(left, right)))


def taxonomy_by_id(taxonomy: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {item["id"]: dict(item) for item in taxonomy.get("tags", [])}


def tag_text(tag: Mapping[str, Any]) -> str:
    return " ".join(
        [str(tag.get("id", "")), str(tag.get("name_zh", "")), str(tag.get("description", ""))]
        + [str(value) for value in tag.get("aliases", [])]
    )


def lexical_alias_score(query: str, aliases: Iterable[str]) -> float:
    lowered = query.casefold()
    query_features = set(text_features(query))
    score = 0.0
    for alias in aliases:
        normalized = alias.casefold().strip()
        if not normalized:
            continue
        if normalized in lowered:
            score = max(score, 1.0)
        else:
            alias_features = set(text_features(normalized))
            if alias_features:
                score = max(score, len(query_features & alias_features) / len(alias_features))
    return score


def classify_skill(
    skill: Mapping[str, Any], taxonomy: Mapping[str, Any], provider: HashEmbeddingProvider
) -> Tuple[List[str], List[str], str]:
    require_skill(skill)
    controlled = taxonomy_by_id(taxonomy)
    native_raw = unique_strings(str(tag) for tag in skill.get("tags", []))
    native = [tag for tag in native_raw if tag in controlled]
    auxiliary = [tag for tag in native_raw if tag not in controlled]
    source_text = " ".join(
        [str(skill["name"]), str(skill["description"])]
        + [str(alias) for alias in skill.get("aliases", [])]
    )
    source_vector = provider.embed(source_text)
    scores: List[Tuple[float, str]] = []
    for tag_id, tag in controlled.items():
        semantic = cosine(source_vector, provider.embed(tag_text(tag)))
        lexical = lexical_alias_score(source_text, tag.get("aliases", []))
        score = 0.65 * semantic + 0.35 * lexical
        scores.append((score, tag_id))
    scores.sort(reverse=True)
    generated: List[str] = []
    generated_threshold = 0.65 if native else 0.28
    for score, tag_id in scores:
        if tag_id in native:
            continue
        if score >= generated_threshold or (not native and not generated):
            generated.append(tag_id)
        if len(generated) >= max(1, 3 - len(native)):
            break
    reason = "; ".join(f"{tag_id}:{score:.3f}" for score, tag_id in scores[:3])
    return unique_strings(native + auxiliary), generated, reason


def effective_tags(record: Mapping[str, Any]) -> List[str]:
    return unique_strings(
        list(record.get("locked_tags", []))
        + list(record.get("manual_tags", []))
        + list(record.get("native_tags", []))
        + list(record.get("agent_tags", []))
    )


def make_record(
    skill: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
    previous: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    native, generated, reason = classify_skill(skill, taxonomy, provider)
    record: Dict[str, Any] = {
        "metadata_fingerprint": metadata_fingerprint(skill),
        "manual_tags": list((previous or {}).get("manual_tags", [])),
        "native_tags": native,
        "agent_tags": generated,
        "locked_tags": list((previous or {}).get("locked_tags", [])),
        "classification_reason": reason,
    }
    record["effective_tags"] = effective_tags(record)
    return record


def initialize_state(
    payload: Mapping[str, Any], taxonomy: Mapping[str, Any], provider: HashEmbeddingProvider
) -> Dict[str, Any]:
    skills = payload.get("skills")
    if not isinstance(skills, list):
        raise ValueError("initialize payload requires skills array")
    records: Dict[str, Any] = {}
    for skill in skills:
        require_skill(skill)
        skill_id = skill["skill_id"]
        if skill_id in records:
            raise ValueError(f"duplicate skill_id: {skill_id}")
        records[skill_id] = make_record(skill, taxonomy, provider)
    return {
        "schema_version": SCHEMA_VERSION,
        "taxonomy_version": taxonomy.get("version", ""),
        "registry_version": str(payload.get("registry_version", "")),
        "skills": records,
    }


def sync_state(
    state: MutableMapping[str, Any],
    event: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
) -> str:
    event_type = event.get("event")
    records = state.setdefault("skills", {})
    if event_type in ("skill_added", "skill_updated"):
        skill = event.get("skill")
        if not isinstance(skill, Mapping):
            raise ValueError(f"{event_type} requires skill")
        require_skill(skill)
        skill_id = skill["skill_id"]
        previous = records.get(skill_id)
        if event_type == "skill_added" and previous is not None:
            raise ValueError(f"skill already exists: {skill_id}")
        records[skill_id] = make_record(skill, taxonomy, provider, previous)
    elif event_type == "skill_removed":
        skill_id = event.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id:
            raise ValueError("skill_removed requires skill_id")
        records.pop(skill_id, None)
    else:
        raise ValueError("unsupported harness event")
    state["registry_version"] = str(event.get("registry_version", state.get("registry_version", "")))
    state["taxonomy_version"] = taxonomy.get("version", "")
    return str(event_type)


def mutate_tags(
    state: MutableMapping[str, Any], skill_id: str, action: str, tags: Iterable[str]
) -> Dict[str, Any]:
    records = state.get("skills", {})
    if skill_id not in records:
        raise KeyError(f"unknown skill_id: {skill_id}")
    record = records[skill_id]
    requested = unique_strings(tags)
    manual = list(record.get("manual_tags", []))
    locked = list(record.get("locked_tags", []))
    if action == "add":
        manual = unique_strings(manual + requested)
    elif action == "remove":
        manual = [tag for tag in manual if tag not in requested]
        locked = [tag for tag in locked if tag not in requested]
    elif action == "lock":
        manual = unique_strings(manual + requested)
        locked = unique_strings(locked + requested)
    elif action == "unlock":
        locked = [tag for tag in locked if tag not in requested]
    else:
        raise ValueError("unsupported tag mutation")
    record["manual_tags"] = manual
    record["locked_tags"] = locked
    record["effective_tags"] = effective_tags(record)
    return record


def reclassify_record(
    state: MutableMapping[str, Any],
    skill: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
) -> Dict[str, Any]:
    require_skill(skill)
    previous = state.get("skills", {}).get(skill["skill_id"])
    if previous is None:
        raise KeyError(f"unknown skill_id: {skill['skill_id']}")
    record = make_record(skill, taxonomy, provider, previous)
    state["skills"][skill["skill_id"]] = record
    return record


def route(
    payload: Mapping[str, Any],
    state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    policy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
) -> Dict[str, Any]:
    skills = payload.get("skills")
    query = payload.get("query")
    if not isinstance(skills, list) or not isinstance(query, str) or not query.strip():
        raise ValueError("route requires query and skills array")
    threshold = int(payload.get("activation_threshold", policy["activation_threshold"]))
    registry_version = str(payload.get("registry_version", ""))
    if len(skills) <= threshold:
        return {
            "decision": "bypass",
            "selected_skill_id": None,
            "confidence": 0.0,
            "matched_tags": [],
            "candidate_skill_ids": [],
            "reason_codes": ["below_activation_threshold"],
            "registry_version": registry_version,
        }
    records = state.get("skills", {})
    catalog: Dict[str, Mapping[str, Any]] = {}
    for skill in skills:
        require_skill(skill)
        catalog[skill["skill_id"]] = skill
    missing = sorted(set(catalog) - set(records))
    if missing:
        raise ValueError("tag library out of sync: " + ", ".join(missing[:5]))

    lowered_query = query.casefold()
    for skill_id, skill in catalog.items():
        names = [skill_id, str(skill.get("name", ""))]
        if any(name and name.casefold() in lowered_query for name in names):
            return {
                "decision": "invoke",
                "selected_skill_id": skill_id,
                "confidence": 1.0,
                "matched_tags": list(records[skill_id].get("effective_tags", [])),
                "candidate_skill_ids": [skill_id],
                "reason_codes": ["explicit_skill_name"],
                "registry_version": registry_version,
            }

    controlled = taxonomy_by_id(taxonomy)
    query_vector = provider.embed(query)
    tag_scores: List[Tuple[float, str]] = []
    for tag_id, tag in controlled.items():
        semantic = cosine(query_vector, provider.embed(tag_text(tag)))
        lexical = lexical_alias_score(query, tag.get("aliases", []))
        score = 0.72 * semantic + 0.28 * lexical
        if score >= float(policy["minimum_tag_score"]):
            tag_scores.append((score, tag_id))
    tag_scores.sort(reverse=True)
    retained_tags = tag_scores[: int(policy["tag_top_k"])]
    if not retained_tags:
        return {
            "decision": "none",
            "selected_skill_id": None,
            "confidence": 0.0,
            "matched_tags": [],
            "candidate_skill_ids": [],
            "reason_codes": ["no_candidate_tag"],
            "registry_version": registry_version,
        }

    tag_score_map = dict((tag_id, score) for score, tag_id in retained_tags)
    preliminary: List[Tuple[float, str]] = []
    for skill_id in catalog:
        matched = set(records[skill_id].get("effective_tags", [])) & set(tag_score_map)
        if matched:
            preliminary.append((max(tag_score_map[tag] for tag in matched), skill_id))
    preliminary.sort(reverse=True)
    candidate_ids = [skill_id for _, skill_id in preliminary[: int(policy["candidate_skill_limit"])]]
    if not candidate_ids:
        return {
            "decision": "none",
            "selected_skill_id": None,
            "confidence": 0.0,
            "matched_tags": [tag_id for _, tag_id in retained_tags],
            "candidate_skill_ids": [],
            "reason_codes": ["empty_candidate_set"],
            "registry_version": registry_version,
        }

    query_features = set(text_features(query))
    ranked: List[Tuple[float, str]] = []
    for skill_id in candidate_ids:
        skill = catalog[skill_id]
        record = records[skill_id]
        metadata_text = " ".join(
            [str(skill.get("name", "")), str(skill.get("description", ""))]
            + [str(alias) for alias in skill.get("aliases", [])]
            + list(record.get("effective_tags", []))
        )
        semantic = cosine(query_vector, provider.embed(metadata_text))
        metadata_features = set(text_features(metadata_text))
        lexical = len(query_features & metadata_features) / max(1, len(query_features))
        matched = set(record.get("effective_tags", [])) & set(tag_score_map)
        affinity = max((tag_score_map[tag] for tag in matched), default=0.0)
        calibrated_semantic = min(1.0, semantic * 1.5)
        calibrated_affinity = min(1.0, affinity * 1.4)
        score = min(1.0, 0.58 * calibrated_semantic + 0.22 * lexical + 0.20 * calibrated_affinity)
        ranked.append((score, skill_id))
    ranked.sort(reverse=True)
    winner_score, winner_id = ranked[0]
    runner_score = ranked[1][0] if len(ranked) > 1 else 0.0
    margin = winner_score - runner_score
    reason_codes: List[str] = []
    decision = "invoke"
    selected: str | None = winner_id
    if winner_score < float(policy["minimum_skill_score"]):
        decision, selected, reason_codes = "none", None, ["below_skill_threshold"]
    elif margin < float(policy["minimum_winner_margin"]):
        decision, selected, reason_codes = "none", None, ["ambiguous_top_candidates"]
    else:
        reason_codes = ["decisive_metadata_match"]
    return {
        "decision": decision,
        "selected_skill_id": selected,
        "confidence": round(winner_score, 6),
        "winner_margin": round(margin, 6),
        "matched_tags": [tag_id for _, tag_id in retained_tags],
        "candidate_skill_ids": [skill_id for _, skill_id in ranked],
        "reason_codes": reason_codes,
        "registry_version": registry_version,
    }


def resource_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "references"


def load_resources() -> Tuple[Dict[str, Any], Dict[str, Any], HashEmbeddingProvider]:
    references = resource_dir()
    taxonomy = load_json_yaml(references / "tag-taxonomy.yaml")
    policy = load_json_yaml(references / "routing-policy.yaml")
    provider = HashEmbeddingProvider(int(policy.get("embedding_dimensions", 512)))
    return taxonomy, policy, provider
