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
FEEDBACK_SCHEMA_VERSION = "1.1.0"
DEFAULT_FEEDBACK_FILE = "feedback-stats.json"


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


def feedback_path(state_dir: str) -> Path:
    return Path(state_dir).expanduser().resolve() / DEFAULT_FEEDBACK_FILE


def empty_feedback_state() -> Dict[str, Any]:
    return {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "total_responses": 0,
        "skills": {},
        "tag_skill": {},
        "rerank_skills": {},
        "query_tag_corrections": {},
        "annotation_suggestions": {},
        "tag_rejections": {},
        "root_causes": {},
        "pending": {},
    }


def load_feedback_state(state_dir: str, required: bool = False) -> Dict[str, Any]:
    path = feedback_path(state_dir)
    if not path.exists():
        if required:
            raise FileNotFoundError(f"feedback state not initialized: {path}")
        return empty_feedback_state()
    state = load_json_yaml(path)
    if state.get("schema_version") == "1.0.0":
        state["schema_version"] = FEEDBACK_SCHEMA_VERSION
        for key, value in empty_feedback_state().items():
            state.setdefault(key, value)
    elif state.get("schema_version") != FEEDBACK_SCHEMA_VERSION:
        raise ValueError("unsupported feedback-state schema version")
    return state


def save_feedback_state(state_dir: str, state: Mapping[str, Any]) -> None:
    atomic_write_json_yaml(feedback_path(state_dir), state)


def should_request_feedback(interaction_id: str, sample_rate: float, salt: str = "") -> bool:
    if not interaction_id:
        raise ValueError("interaction_id must not be empty")
    if sample_rate < 0.0 or sample_rate > 1.0:
        raise ValueError("feedback sample rate must be between 0 and 1")
    digest = hashlib.sha256(f"{salt}\0{interaction_id}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return bucket < sample_rate


def record_feedback(
    state: MutableMapping[str, Any], skill_id: str, tags: Iterable[str], accurate: bool
) -> Dict[str, Any]:
    if not skill_id:
        raise ValueError("skill_id must not be empty")
    field = "positive" if accurate else "negative"
    state["total_responses"] = int(state.get("total_responses", 0)) + 1
    skill_counts = state.setdefault("skills", {}).setdefault(
        skill_id, {"positive": 0, "negative": 0}
    )
    skill_counts[field] = int(skill_counts.get(field, 0)) + 1
    for tag in unique_strings(tags):
        pair_counts = state.setdefault("tag_skill", {}).setdefault(tag, {}).setdefault(
            skill_id, {"positive": 0, "negative": 0}
        )
        pair_counts[field] = int(pair_counts.get(field, 0)) + 1
    return {
        "skill_id": skill_id,
        "rating": "accurate" if accurate else "inaccurate",
        "skill_counts": dict(skill_counts),
    }


def increment_counts(container: MutableMapping[str, Any], key: str, positive: bool) -> Dict[str, int]:
    counts = container.setdefault(key, {"positive": 0, "negative": 0})
    field = "positive" if positive else "negative"
    counts[field] = int(counts.get(field, 0)) + 1
    return counts


def feedback_interaction_key(interaction_id: str, salt: str = "") -> str:
    if not interaction_id:
        raise ValueError("interaction_id must not be empty")
    return "sha256:" + hashlib.sha256(f"{salt}\0{interaction_id}".encode("utf-8")).hexdigest()


def register_feedback_request(
    state: MutableMapping[str, Any],
    interaction_id: str,
    route_result: Mapping[str, Any],
    query_vector: Sequence[float],
    sample_rate: float,
    salt: str = "",
    pending_limit: int = 1000,
) -> bool:
    sampled = should_request_feedback(interaction_id, sample_rate, salt)
    if not sampled:
        return False
    pending = state.setdefault("pending", {})
    key = feedback_interaction_key(interaction_id, salt)
    pending[key] = {
        "selected_skill_id": route_result["selected_skill_id"],
        "matched_tags": unique_strings(route_result.get("matched_tags", [])),
        "feedback_tags": unique_strings(route_result.get("feedback_tags", [])),
        "candidate_skill_ids": unique_strings(route_result.get("candidate_skill_ids", [])),
        "query_vector": [round(float(value), 8) for value in query_vector],
    }
    while len(pending) > max(1, pending_limit):
        del pending[next(iter(pending))]
    return True


def record_pending_feedback(
    state: MutableMapping[str, Any],
    interaction_id: str,
    accurate: bool,
    tag_state: Mapping[str, Any],
    expected_skill_id: str | None = None,
    expected_tags: Iterable[str] = (),
    expected_none: bool = False,
    salt: str = "",
) -> Dict[str, Any]:
    key = feedback_interaction_key(interaction_id, salt)
    pending = state.setdefault("pending", {})
    request = pending.get(key)
    if request is None:
        raise ValueError("feedback request is missing, expired, or already answered")
    if not accurate and not expected_none:
        if not expected_skill_id or expected_skill_id not in tag_state.get("skills", {}):
            raise ValueError("inaccurate feedback requires a known expected_skill_id or expected_none")
        if not unique_strings(expected_tags):
            raise ValueError("inaccurate feedback requires expected_tags")
    del pending[key]
    selected_skill_id = str(request["selected_skill_id"])
    selected_tags = unique_strings(request.get("feedback_tags", []))
    state["total_responses"] = int(state.get("total_responses", 0)) + 1
    increment_counts(state.setdefault("skills", {}), selected_skill_id, accurate)
    if accurate:
        for tag in selected_tags:
            increment_counts(
                state.setdefault("tag_skill", {}).setdefault(tag, {}),
                selected_skill_id,
                True,
            )
        return {
            "skill_id": selected_skill_id,
            "rating": "accurate",
            "root_causes": ["accurate_selection"],
            "skill_counts": dict(state["skills"][selected_skill_id]),
        }

    if expected_none:
        for tag in unique_strings(request.get("matched_tags", [])):
            state.setdefault("tag_rejections", {})[tag] = int(
                state.setdefault("tag_rejections", {}).get(tag, 0)
            ) + 1
        root_causes = ["over_recall"]
    else:
        intent_tags = set(unique_strings(expected_tags))
        matched_tags = set(request.get("matched_tags", []))
        correct_skill_tags = set(
            tag_state["skills"][expected_skill_id].get("effective_tags", [])
        )
        candidates = set(request.get("candidate_skill_ids", []))
        missed_query_tags = intent_tags - matched_tags
        missing_skill_tags = intent_tags - correct_skill_tags
        root_causes: List[str] = []

        if missed_query_tags:
            root_causes.append("query_tag_mismatch")
            wrong_tags = (matched_tags - intent_tags) or {"__none__"}
            for wrong_tag in wrong_tags:
                for expected_tag in missed_query_tags:
                    update_query_tag_correction(
                        state,
                        wrong_tag,
                        expected_tag,
                        request.get("query_vector", []),
                    )

        if missing_skill_tags:
            root_causes.append("skill_tag_annotation_error")
            suggestions = state.setdefault("annotation_suggestions", {}).setdefault(
                expected_skill_id, {}
            )
            for tag in missing_skill_tags:
                suggestions[tag] = int(suggestions.get(tag, 0)) + 1

        if expected_skill_id in candidates and expected_skill_id != selected_skill_id:
            root_causes.append("skill_rerank_error")
            increment_counts(state.setdefault("rerank_skills", {}), selected_skill_id, False)
            increment_counts(state.setdefault("rerank_skills", {}), expected_skill_id, True)
        elif expected_skill_id not in candidates:
            root_causes.append("candidate_recall_error")
            for tag in selected_tags:
                increment_counts(
                    state.setdefault("tag_skill", {}).setdefault(tag, {}),
                    selected_skill_id,
                    False,
                )

        if not root_causes:
            root_causes.append("unclassified_negative")

    for cause in root_causes:
        state.setdefault("root_causes", {})[cause] = int(
            state.setdefault("root_causes", {}).get(cause, 0)
        ) + 1
    return {
        "skill_id": selected_skill_id,
        "expected_skill_id": expected_skill_id,
        "rating": "inaccurate",
        "root_causes": root_causes,
        "skill_counts": dict(state["skills"][selected_skill_id]),
    }


def update_query_tag_correction(
    state: MutableMapping[str, Any], wrong_tag: str, expected_tag: str, vector: Sequence[float]
) -> None:
    if not vector:
        return
    correction = state.setdefault("query_tag_corrections", {}).setdefault(
        wrong_tag, {}
    ).setdefault(expected_tag, {"samples": 0, "centroid": [0.0] * len(vector)})
    samples = int(correction.get("samples", 0))
    centroid = correction.get("centroid", [])
    if len(centroid) != len(vector):
        centroid = [0.0] * len(vector)
        samples = 0
    combined = [centroid[index] * samples + float(value) for index, value in enumerate(vector)]
    norm = math.sqrt(sum(value * value for value in combined))
    correction["centroid"] = [round(value / norm, 8) for value in combined] if norm else combined
    correction["samples"] = samples + 1


def remove_skill_feedback(state: MutableMapping[str, Any], skill_id: str) -> None:
    state.setdefault("skills", {}).pop(skill_id, None)
    for tag in list(state.setdefault("tag_skill", {})):
        state["tag_skill"][tag].pop(skill_id, None)
        if not state["tag_skill"][tag]:
            del state["tag_skill"][tag]
    for key in list(state.setdefault("pending", {})):
        request = state["pending"][key]
        if request.get("selected_skill_id") == skill_id or skill_id in request.get(
            "candidate_skill_ids", []
        ):
            del state["pending"][key]
    state.setdefault("rerank_skills", {}).pop(skill_id, None)
    state.setdefault("annotation_suggestions", {}).pop(skill_id, None)


def feedback_count_adjustment(counts: Mapping[str, Any], policy: Mapping[str, Any]) -> float:
    positive = int(counts.get("positive", 0))
    negative = int(counts.get("negative", 0))
    total = positive + negative
    minimum = int(policy.get("feedback_min_samples", 5))
    if total < minimum:
        return 0.0
    prior = float(policy.get("feedback_prior_strength", 6.0))
    posterior = (positive + 0.5 * prior) / (total + prior)
    confidence = min(1.0, total / max(1.0, minimum * 2.0))
    maximum = float(policy.get("feedback_max_adjustment", 0.08))
    return max(-maximum, min(maximum, (posterior - 0.5) * 2.0 * maximum * confidence))


def feedback_adjustment(
    feedback: Mapping[str, Any] | None,
    skill_id: str,
    matched_tags: Iterable[str],
    policy: Mapping[str, Any],
) -> float:
    if not feedback:
        return 0.0
    pair_adjustments = [
        feedback_count_adjustment(
            feedback.get("tag_skill", {}).get(tag, {}).get(skill_id, {}), policy
        )
        for tag in unique_strings(matched_tags)
    ]
    active_pairs = [value for value in pair_adjustments if value]
    if not active_pairs:
        return 0.0
    return sum(active_pairs) / len(active_pairs)


def rerank_feedback_adjustment(
    feedback: Mapping[str, Any] | None, skill_id: str, policy: Mapping[str, Any]
) -> float:
    if not feedback:
        return 0.0
    return feedback_count_adjustment(
        feedback.get("rerank_skills", {}).get(skill_id, {}), policy
    )


def candidate_effective_tags(
    record: Mapping[str, Any],
    feedback: Mapping[str, Any] | None,
    skill_id: str,
    policy: Mapping[str, Any],
) -> List[str]:
    tags = list(record.get("effective_tags", []))
    if not feedback:
        return unique_strings(tags)
    minimum = int(policy.get("feedback_min_samples", 5))
    suggestions = feedback.get("annotation_suggestions", {}).get(skill_id, {})
    tags.extend(tag for tag, count in suggestions.items() if int(count) >= minimum)
    return unique_strings(tags)


def corrected_tag_scores(
    query_vector: Sequence[float],
    scores: Mapping[str, float],
    feedback: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
) -> Tuple[Dict[str, float], bool]:
    corrected = dict(scores)
    if not feedback:
        return corrected, False
    minimum = int(policy.get("feedback_min_samples", 5))
    similarity_threshold = float(policy.get("feedback_query_similarity", 0.55))
    maximum = float(policy.get("feedback_tag_max_adjustment", 0.12))
    applied = False
    for wrong_tag, expected_map in feedback.get("query_tag_corrections", {}).items():
        if wrong_tag != "__none__" and corrected.get(wrong_tag, 0.0) <= 0.0:
            continue
        for expected_tag, correction in expected_map.items():
            samples = int(correction.get("samples", 0))
            centroid = correction.get("centroid", [])
            if samples < minimum or len(centroid) != len(query_vector):
                continue
            similarity = cosine(query_vector, centroid)
            if similarity < similarity_threshold:
                continue
            confidence = min(1.0, samples / max(1.0, minimum * 2.0))
            adjustment = maximum * confidence * similarity
            corrected[expected_tag] = min(1.0, corrected.get(expected_tag, 0.0) + adjustment)
            if wrong_tag != "__none__":
                corrected[wrong_tag] = max(0.0, corrected.get(wrong_tag, 0.0) - adjustment * 0.5)
            applied = True
    for tag, count in feedback.get("tag_rejections", {}).items():
        if int(count) < minimum or tag not in corrected:
            continue
        confidence = min(1.0, int(count) / max(1.0, minimum * 2.0))
        corrected[tag] = max(0.0, corrected[tag] - maximum * confidence)
        applied = True
    return corrected, applied


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


def metadata_match_score(
    query: str,
    query_vector: Sequence[float],
    skill: Mapping[str, Any],
    provider: HashEmbeddingProvider,
    extra_tags: Iterable[str] = (),
    affinity: float = 0.0,
) -> float:
    metadata_text = " ".join(
        [str(skill.get("name", "")), str(skill.get("description", ""))]
        + [str(alias) for alias in skill.get("aliases", [])]
        + [str(tag) for tag in skill.get("tags", [])]
        + list(extra_tags)
    )
    semantic = cosine(query_vector, provider.embed(metadata_text))
    query_features = set(text_features(query))
    metadata_features = set(text_features(metadata_text))
    lexical = len(query_features & metadata_features) / max(1, len(query_features))
    calibrated_semantic = min(1.0, semantic * 1.5)
    calibrated_affinity = min(1.0, affinity * 1.4)
    return min(1.0, 0.58 * calibrated_semantic + 0.22 * lexical + 0.20 * calibrated_affinity)


def route(
    payload: Mapping[str, Any],
    state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    policy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
    feedback: Mapping[str, Any] | None = None,
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
            "feedback_tags": [],
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
                "feedback_tags": [],
                "candidate_skill_ids": [skill_id],
                "reason_codes": ["explicit_skill_name"],
                "registry_version": registry_version,
            }

    controlled = taxonomy_by_id(taxonomy)
    query_vector = provider.embed(query)
    base_tag_scores: Dict[str, float] = {}
    for tag_id, tag in controlled.items():
        semantic = cosine(query_vector, provider.embed(tag_text(tag)))
        lexical = lexical_alias_score(query, tag.get("aliases", []))
        base_tag_scores[tag_id] = 0.72 * semantic + 0.28 * lexical
    adjusted_tag_scores, query_tag_feedback_applied = corrected_tag_scores(
        query_vector, base_tag_scores, feedback, policy
    )
    tag_scores: List[Tuple[float, str]] = []
    for tag_id, score in adjusted_tag_scores.items():
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
            "feedback_tags": [],
            "candidate_skill_ids": [],
            "reason_codes": ["no_candidate_tag"],
            "registry_version": registry_version,
        }

    tag_score_map = dict((tag_id, score) for score, tag_id in retained_tags)
    preliminary: List[Tuple[float, str]] = []
    candidate_tags_by_skill: Dict[str, List[str]] = {}
    annotation_feedback_applied = False
    for skill_id in catalog:
        candidate_tags = candidate_effective_tags(records[skill_id], feedback, skill_id, policy)
        candidate_tags_by_skill[skill_id] = candidate_tags
        added_tags = set(candidate_tags) - set(records[skill_id].get("effective_tags", []))
        if added_tags & set(tag_score_map):
            annotation_feedback_applied = True
        matched = set(candidate_tags) & set(tag_score_map)
        if matched:
            base_affinity = max(tag_score_map[tag] for tag in matched)
            adjustment = feedback_adjustment(feedback, skill_id, matched, policy)
            preliminary.append((max(0.0, min(1.0, base_affinity + adjustment)), skill_id))
    preliminary.sort(reverse=True)
    candidate_ids = [skill_id for _, skill_id in preliminary[: int(policy["candidate_skill_limit"])]]
    if not candidate_ids:
        return {
            "decision": "none",
            "selected_skill_id": None,
            "confidence": 0.0,
            "matched_tags": [tag_id for _, tag_id in retained_tags],
            "feedback_tags": [],
            "candidate_skill_ids": [],
            "reason_codes": ["empty_candidate_set"],
            "registry_version": registry_version,
        }

    ranked: List[Tuple[float, str]] = []
    rerank_feedback_applied = False
    for skill_id in candidate_ids:
        skill = catalog[skill_id]
        record = records[skill_id]
        candidate_tags = candidate_tags_by_skill[skill_id]
        matched = set(candidate_tags) & set(tag_score_map)
        affinity = max((tag_score_map[tag] for tag in matched), default=0.0)
        score = metadata_match_score(
            query,
            query_vector,
            skill,
            provider,
            candidate_tags,
            affinity,
        )
        adjustment = rerank_feedback_adjustment(feedback, skill_id, policy)
        if adjustment:
            rerank_feedback_applied = True
            score = max(0.0, min(1.0, score + adjustment))
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
    if query_tag_feedback_applied:
        reason_codes.append("query_tag_feedback_applied")
    if annotation_feedback_applied:
        reason_codes.append("annotation_feedback_applied")
    if rerank_feedback_applied:
        reason_codes.append("rerank_feedback_applied")
    return {
        "decision": decision,
        "selected_skill_id": selected,
        "confidence": round(winner_score, 6),
        "winner_margin": round(margin, 6),
        "matched_tags": [tag_id for _, tag_id in retained_tags],
        "feedback_tags": (
            sorted(set(candidate_tags_by_skill[selected]) & set(tag_score_map))
            if selected
            else []
        ),
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
