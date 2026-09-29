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

from jev_reranker import JevRerankerError


SCHEMA_VERSION = "1.1.0"
INDEX_VERSION = "1.0.0"
DEFAULT_STATE_FILE = "skill-tags.yaml"
FEEDBACK_SCHEMA_VERSION = "1.2.0"
DEFAULT_FEEDBACK_FILE = "feedback-stats.json"


def catalog_digest_from_records(records: Mapping[str, Any]) -> str:
    manifest = [
        [skill_id, str(record.get("metadata_fingerprint", ""))]
        for skill_id, record in sorted(records.items())
    ]
    return "sha256:" + hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def enabled_skill_ids_digest(skill_ids: Iterable[str]) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(sorted(set(skill_ids))).encode("utf-8")
    ).hexdigest()


def build_tag_index(records: Mapping[str, Any]) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = {}
    for skill_id, record in records.items():
        for tag in unique_strings(record.get("effective_tags", [])):
            index.setdefault(tag, []).append(skill_id)
    return {tag: sorted(skill_ids) for tag, skill_ids in sorted(index.items())}


def refresh_skill_in_tag_index(state: MutableMapping[str, Any], skill_id: str) -> None:
    index = state.setdefault("tag_index", {})
    for tag in list(index):
        skill_ids = [value for value in index[tag] if value != skill_id]
        if skill_ids:
            index[tag] = skill_ids
        else:
            del index[tag]
    record = state.get("skills", {}).get(skill_id)
    if record is not None:
        for tag in unique_strings(record.get("effective_tags", [])):
            index[tag] = sorted(set(index.get(tag, [])) | {skill_id})
    state["tag_index"] = dict(sorted(index.items()))


def migrate_tag_state(state: MutableMapping[str, Any]) -> Dict[str, Any]:
    version = state.get("schema_version")
    if version == "1.0.0":
        state["schema_version"] = SCHEMA_VERSION
        state["index_version"] = INDEX_VERSION
        records = state.setdefault("skills", {})
        state["tag_index"] = build_tag_index(records)
        state["catalog_digest"] = catalog_digest_from_records(records)
    elif version != SCHEMA_VERSION:
        raise ValueError("unsupported tag-library schema version")
    records = state.get("skills", {})
    if state.get("index_version") != INDEX_VERSION:
        raise ValueError("tag-index version differs; rebuild required")
    if not isinstance(state.get("tag_index"), Mapping) or not isinstance(
        state.get("catalog_digest"), str
    ):
        raise ValueError("tag index metadata is invalid; rebuild required")
    if state.get("tag_index") != build_tag_index(records):
        raise ValueError("tag index differs from tag records; rebuild required")
    if state.get("catalog_digest") != catalog_digest_from_records(records):
        raise ValueError("catalog digest differs from tag records; rebuild required")
    return dict(state)


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
    return migrate_tag_state(state)


def save_state(state_dir: str, state: Mapping[str, Any]) -> None:
    atomic_write_json_yaml(state_path(state_dir), state)


def feedback_path(state_dir: str) -> Path:
    return Path(state_dir).expanduser().resolve() / DEFAULT_FEEDBACK_FILE


def empty_feedback_state() -> Dict[str, Any]:
    return {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "total_comparisons": 0,
        "agreements": 0,
        "disagreements": 0,
        "skills": {},
        "tag_skill": {},
        "rerank_skills": {},
        "query_tag_corrections": {},
        "annotation_suggestions": {},
        "tag_rejections": {},
        "root_causes": {},
        "processed_comparisons": {},
    }


def load_feedback_state(state_dir: str, required: bool = False) -> Dict[str, Any]:
    path = feedback_path(state_dir)
    if not path.exists():
        if required:
            raise FileNotFoundError(f"feedback state not initialized: {path}")
        return empty_feedback_state()
    state = load_json_yaml(path)
    if state.get("schema_version") in ("1.0.0", "1.1.0"):
        # User-labelled feedback from the previous protocol is deliberately not
        # carried into automatic shadow-routing corrections.
        return empty_feedback_state()
    elif state.get("schema_version") != FEEDBACK_SCHEMA_VERSION:
        raise ValueError("unsupported feedback-state schema version")
    return state


def save_feedback_state(state_dir: str, state: Mapping[str, Any]) -> None:
    atomic_write_json_yaml(feedback_path(state_dir), state)


def increment_counts(container: MutableMapping[str, Any], key: str, positive: bool) -> Dict[str, int]:
    counts = container.setdefault(key, {"positive": 0, "negative": 0})
    field = "positive" if positive else "negative"
    counts[field] = int(counts.get(field, 0)) + 1
    return counts


def comparison_key(comparison_id: str, salt: str = "") -> str:
    if not comparison_id:
        raise ValueError("comparison_id must not be empty")
    return "sha256:" + hashlib.sha256(f"{salt}\0{comparison_id}".encode("utf-8")).hexdigest()


def inferred_intent_tags(
    query: str,
    query_vector: Sequence[float],
    skill_id: str,
    tag_state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: "HashEmbeddingProvider",
    vector_cache: Mapping[str, Any] | None = None,
) -> List[str]:
    """Infer one controlled intent tag for a baseline-selected Skill."""
    record = tag_state.get("skills", {}).get(skill_id)
    if not isinstance(record, Mapping):
        raise ValueError(f"baseline selected an unknown Skill: {skill_id}")
    controlled = taxonomy_by_id(taxonomy)
    eligible = [tag for tag in unique_strings(record.get("effective_tags", [])) if tag in controlled]
    if not eligible:
        raise ValueError(f"baseline-selected Skill has no controlled tags: {skill_id}")
    cached_tags = (vector_cache or {}).get("tag_vectors", {})
    scores: List[Tuple[float, str]] = []
    for tag_id in eligible:
        tag = controlled[tag_id]
        tag_vector = cached_tags.get(tag_id)
        if tag_vector is None:
            tag_vector = provider.embed(tag_text(tag))
        semantic = cosine(query_vector, tag_vector)
        lexical = lexical_alias_score(query, tag.get("aliases", []))
        scores.append((0.65 * semantic + 0.35 * lexical, tag_id))
    scores.sort(reverse=True)
    return [scores[0][1]]


def route_result_selection(
    result: Mapping[str, Any], label: str, known_skill_ids: Iterable[str]
) -> str | None:
    decision = result.get("decision")
    if decision not in ("invoke", "none"):
        raise ValueError(f"{label} result must have decision invoke or none")
    selected = result.get("selected_skill_id")
    if decision == "none":
        if selected is not None:
            raise ValueError(f"{label} none result must not select a Skill")
        return None
    if not isinstance(selected, str) or selected not in set(known_skill_ids):
        raise ValueError(f"{label} selected an unknown Skill")
    return selected


def record_shadow_comparison(
    state: MutableMapping[str, Any],
    comparison_id: str,
    query: str,
    enhanced_result: Mapping[str, Any],
    baseline_result: Mapping[str, Any],
    tag_state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: "HashEmbeddingProvider",
    vector_cache: Mapping[str, Any] | None = None,
    history_limit: int = 1000,
    salt: str = "",
) -> Dict[str, Any]:
    """Treat the native harness route as a pseudo-label and learn from a shadow comparison."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("shadow comparison requires a non-empty query")
    known = set(tag_state.get("skills", {}))
    enhanced_skill_id = route_result_selection(enhanced_result, "enhanced", known)
    baseline_skill_id = route_result_selection(baseline_result, "baseline", known)
    expected_version = str(tag_state.get("registry_version", ""))
    for label, result in (("enhanced", enhanced_result), ("baseline", baseline_result)):
        if str(result.get("registry_version", "")) != expected_version:
            raise ValueError(f"{label} registry version differs; comparison rejected")
    enhanced_enabled_digest = str(enhanced_result.get("enabled_skill_ids_digest", ""))
    baseline_enabled_digest = str(baseline_result.get("enabled_skill_ids_digest", ""))
    if not enhanced_enabled_digest or enhanced_enabled_digest != baseline_enabled_digest:
        raise ValueError("enhanced and baseline enabled Skill sets differ; comparison rejected")

    enhanced_tags = unique_strings(enhanced_result.get("feedback_tags", []))
    matched_tags = set(unique_strings(enhanced_result.get("matched_tags", [])))
    candidates = set(unique_strings(enhanced_result.get("candidate_skill_ids", [])))
    unknown_candidates = candidates - known
    if unknown_candidates:
        raise ValueError("enhanced candidates contain unknown Skill IDs")
    controlled_tags = set(taxonomy_by_id(taxonomy))
    if matched_tags - controlled_tags:
        raise ValueError("enhanced matched_tags contain uncontrolled tags")
    if enhanced_skill_id:
        effective = set(tag_state["skills"][enhanced_skill_id].get("effective_tags", []))
        if set(enhanced_tags) - effective:
            raise ValueError("enhanced feedback_tags are not assigned to the selected Skill")
    needs_intent = baseline_skill_id is not None and baseline_skill_id != enhanced_skill_id
    query_vector = provider.embed(query) if needs_intent else []
    inferred_tags = (
        inferred_intent_tags(
            query,
            query_vector,
            baseline_skill_id,
            tag_state,
            taxonomy,
            provider,
            vector_cache,
        )
        if needs_intent
        else []
    )

    key = comparison_key(comparison_id, salt)
    processed = state.setdefault("processed_comparisons", {})
    if key in processed:
        raise ValueError("routing comparison is already recorded")
    processed[key] = True
    while len(processed) > max(1, history_limit):
        del processed[next(iter(processed))]

    state["total_comparisons"] = int(state.get("total_comparisons", 0)) + 1
    agreement = enhanced_skill_id == baseline_skill_id
    counter = "agreements" if agreement else "disagreements"
    state[counter] = int(state.get(counter, 0)) + 1

    if agreement:
        if enhanced_skill_id:
            increment_counts(state.setdefault("skills", {}), enhanced_skill_id, True)
            for tag in enhanced_tags:
                increment_counts(
                    state.setdefault("tag_skill", {}).setdefault(tag, {}),
                    enhanced_skill_id,
                    True,
                )
        root_causes = ["baseline_agreement"]
        inferred_tags = []
    elif baseline_skill_id is None:
        if enhanced_skill_id:
            increment_counts(state.setdefault("skills", {}), enhanced_skill_id, False)
        for tag in matched_tags:
            state.setdefault("tag_rejections", {})[tag] = int(
                state.setdefault("tag_rejections", {}).get(tag, 0)
            ) + 1
        root_causes = ["over_recall"]
        inferred_tags = []
    else:
        intent_tags = set(inferred_tags)
        missed_query_tags = intent_tags - matched_tags
        root_causes = []
        if enhanced_skill_id:
            increment_counts(state.setdefault("skills", {}), enhanced_skill_id, False)
        increment_counts(state.setdefault("skills", {}), baseline_skill_id, True)

        if missed_query_tags:
            root_causes.append("query_tag_mismatch")
            for wrong_tag in (matched_tags - intent_tags) or {"__none__"}:
                for expected_tag in missed_query_tags:
                    update_query_tag_correction(
                        state, wrong_tag, expected_tag, query_vector
                    )

        if baseline_skill_id in candidates:
            root_causes.append("skill_rerank_error")
            if enhanced_skill_id:
                increment_counts(
                    state.setdefault("rerank_skills", {}), enhanced_skill_id, False
                )
            increment_counts(
                state.setdefault("rerank_skills", {}), baseline_skill_id, True
            )
        else:
            root_causes.append("candidate_recall_error")
            for tag in enhanced_tags:
                if enhanced_skill_id:
                    increment_counts(
                        state.setdefault("tag_skill", {}).setdefault(tag, {}),
                        enhanced_skill_id,
                        False,
                    )
        if not root_causes:
            root_causes.append("unclassified_difference")

    for cause in root_causes:
        state.setdefault("root_causes", {})[cause] = int(
            state.setdefault("root_causes", {}).get(cause, 0)
        ) + 1
    return {
        "agreement": agreement,
        "enhanced_skill_id": enhanced_skill_id,
        "baseline_skill_id": baseline_skill_id,
        "inferred_intent_tags": inferred_tags,
        "root_causes": root_causes,
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
    state.setdefault("rerank_skills", {}).pop(skill_id, None)
    state.setdefault("annotation_suggestions", {}).pop(skill_id, None)


def prune_feedback_to_skill_ids(
    state: MutableMapping[str, Any], allowed_skill_ids: Iterable[str]
) -> None:
    allowed = set(allowed_skill_ids)
    referenced = set(state.get("skills", {}))
    referenced.update(state.get("rerank_skills", {}))
    referenced.update(state.get("annotation_suggestions", {}))
    for skill_map in state.get("tag_skill", {}).values():
        referenced.update(skill_map)
    for skill_id in referenced - allowed:
        remove_skill_feedback(state, skill_id)


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


def skill_metadata_text(skill: Mapping[str, Any]) -> str:
    return " ".join(
        [str(skill.get("name", "")), str(skill.get("description", ""))]
        + [str(alias) for alias in skill.get("aliases", [])]
        + [str(tag) for tag in skill.get("tags", [])]
    )


def catalog_digest(skills: Iterable[Mapping[str, Any]]) -> str:
    manifest: List[List[str]] = []
    seen = set()
    for skill in skills:
        require_skill(skill)
        skill_id = str(skill["skill_id"])
        if skill_id in seen:
            raise ValueError(f"duplicate skill_id: {skill_id}")
        seen.add(skill_id)
        manifest.append([skill_id, metadata_fingerprint(skill)])
    manifest.sort()
    return "sha256:" + hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


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
    payload: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
    previous_state: Mapping[str, Any] | None = None,
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
        previous = (previous_state or {}).get("skills", {}).get(skill_id)
        records[skill_id] = make_record(skill, taxonomy, provider, previous)
    return {
        "schema_version": SCHEMA_VERSION,
        "index_version": INDEX_VERSION,
        "taxonomy_version": taxonomy.get("version", ""),
        "registry_version": str(payload.get("registry_version", "")),
        "catalog_digest": catalog_digest(skills),
        "skills": records,
        "tag_index": build_tag_index(records),
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
    state["schema_version"] = SCHEMA_VERSION
    state["index_version"] = INDEX_VERSION
    refresh_skill_in_tag_index(state, skill_id)
    state["catalog_digest"] = catalog_digest_from_records(records)
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
    refresh_skill_in_tag_index(state, skill_id)
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
    refresh_skill_in_tag_index(state, skill["skill_id"])
    state["catalog_digest"] = catalog_digest_from_records(state.get("skills", {}))
    return record


def metadata_match_score(
    query: str,
    query_vector: Sequence[float],
    skill: Mapping[str, Any],
    provider: HashEmbeddingProvider,
    extra_tags: Iterable[str] = (),
    affinity: float = 0.0,
    metadata_vector: Sequence[float] | None = None,
) -> float:
    metadata_text = skill_metadata_text(skill)
    lexical_text = " ".join([metadata_text] + list(extra_tags))
    semantic = cosine(query_vector, metadata_vector or provider.embed(metadata_text))
    query_features = set(text_features(query))
    metadata_features = set(text_features(lexical_text))
    lexical = len(query_features & metadata_features) / max(1, len(query_features))
    calibrated_semantic = min(1.0, semantic * 1.5)
    calibrated_affinity = min(1.0, affinity * 1.4)
    return min(1.0, 0.58 * calibrated_semantic + 0.22 * lexical + 0.20 * calibrated_affinity)


def is_finite_vector(vector: Any, dimensions: int) -> bool:
    return (
        isinstance(vector, list)
        and len(vector) == dimensions
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            for value in vector
        )
    )


def query_tag_scores(
    query: str,
    query_vector: Sequence[float],
    taxonomy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
    feedback: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
    vector_cache: Mapping[str, Any] | None,
) -> Tuple[Dict[str, float], bool]:
    base_scores: Dict[str, float] = {}
    for tag_id, tag in taxonomy_by_id(taxonomy).items():
        cached_tag_vector = (vector_cache or {}).get("tag_vectors", {}).get(tag_id)
        if vector_cache is not None and not is_finite_vector(
            cached_tag_vector, provider.dimensions
        ):
            raise ValueError(f"tag vector is missing or invalid: {tag_id}")
        semantic = cosine(query_vector, cached_tag_vector or provider.embed(tag_text(tag)))
        lexical = lexical_alias_score(query, tag.get("aliases", []))
        base_scores[tag_id] = 0.72 * semantic + 0.28 * lexical
    return corrected_tag_scores(query_vector, base_scores, feedback, policy)


def query_mentions_skill_id(query: str, skill_id: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(skill_id)}(?![A-Za-z0-9_-])"
    return re.search(pattern, query, flags=re.IGNORECASE) is not None


def cached_skill_vector(
    vector_cache: Mapping[str, Any] | None,
    skill_id: str,
    metadata_fingerprint_value: str,
    dimensions: int,
) -> Sequence[float] | None:
    if vector_cache is None:
        return None
    entry = vector_cache.get("skill_vectors", {}).get(skill_id)
    if not isinstance(entry, Mapping):
        raise ValueError(f"Skill vector is missing or invalid: {skill_id}")
    if entry.get("metadata_fingerprint") != metadata_fingerprint_value:
        raise ValueError(f"Skill vector fingerprint differs: {skill_id}")
    vector = entry.get("vector")
    if not is_finite_vector(vector, dimensions):
        raise ValueError(f"Skill vector is missing or invalid: {skill_id}")
    return vector


def recall_candidates(
    payload: Mapping[str, Any],
    state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    policy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
    feedback: Mapping[str, Any] | None = None,
    vector_cache: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    enabled_skill_ids = payload.get("enabled_skill_ids")
    query = payload.get("query")
    if not isinstance(enabled_skill_ids, list) or not isinstance(query, str) or not query.strip():
        raise ValueError("recall requires query and enabled_skill_ids array")
    if any(
        not isinstance(skill_id, str)
        or not skill_id.strip()
        or skill_id != skill_id.strip()
        for skill_id in enabled_skill_ids
    ):
        raise ValueError("enabled_skill_ids must contain exact non-empty strings")
    enabled = list(enabled_skill_ids)
    if len(set(enabled)) != len(enabled):
        raise ValueError("enabled_skill_ids must contain unique non-empty strings")
    threshold = int(payload.get("activation_threshold", policy["activation_threshold"]))
    registry_version_value = payload.get("registry_version")
    if not isinstance(registry_version_value, str) or not registry_version_value:
        raise ValueError("registry_version must not be empty")
    registry_version = registry_version_value
    if registry_version != str(state.get("registry_version", "")):
        raise ValueError("registry version differs; synchronization required")
    records = state.get("skills", {})
    missing = sorted(set(enabled) - set(records))
    if missing:
        raise ValueError("tag library out of sync: " + ", ".join(missing[:5]))
    if state.get("taxonomy_version") != taxonomy.get("version"):
        raise ValueError("taxonomy version differs; rebuild required")
    if len(enabled) <= threshold:
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

    for skill_id in sorted(enabled, key=len, reverse=True):
        if query_mentions_skill_id(query, skill_id):
            return {
                "decision": "candidates",
                "selected_skill_id": None,
                "confidence": 0.0,
                "matched_tags": list(records[skill_id].get("effective_tags", [])),
                "feedback_tags": [],
                "candidate_skill_ids": [skill_id],
                "reason_codes": ["explicit_skill_id"],
                "registry_version": registry_version,
            }

    query_vector = provider.embed(query)
    adjusted_tag_scores, query_tag_feedback_applied = query_tag_scores(
        query, query_vector, taxonomy, provider, feedback, policy, vector_cache
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
    tag_index = state.get("tag_index", {})
    candidate_pool = {
        skill_id
        for tag_id in tag_score_map
        for skill_id in tag_index.get(tag_id, [])
        if skill_id in enabled
    }
    suggestions = (feedback or {}).get("annotation_suggestions", {})
    minimum_annotation_samples = int(policy.get("feedback_min_samples", 5))
    for skill_id, tags in suggestions.items():
        if skill_id not in enabled:
            continue
        if any(
            tag_id in tag_score_map and int(count) >= minimum_annotation_samples
            for tag_id, count in tags.items()
        ):
            candidate_pool.add(skill_id)
    for skill_id in sorted(candidate_pool):
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

    recall_reasons = ["candidate_recall"]
    if query_tag_feedback_applied:
        recall_reasons.append("query_tag_feedback_applied")
    if annotation_feedback_applied:
        recall_reasons.append("annotation_feedback_applied")
    return {
        "decision": "candidates",
        "selected_skill_id": None,
        "confidence": 0.0,
        "matched_tags": [tag_id for _, tag_id in retained_tags],
        "feedback_tags": [],
        "candidate_skill_ids": candidate_ids,
        "reason_codes": recall_reasons,
        "registry_version": registry_version,
    }


def rerank_candidates(
    payload: Mapping[str, Any],
    state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    policy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
    feedback: Mapping[str, Any] | None = None,
    vector_cache: Mapping[str, Any] | None = None,
    jev_reranker: Any | None = None,
) -> Dict[str, Any]:
    candidate_skills = payload.get("candidate_skills")
    if not isinstance(candidate_skills, list):
        raise ValueError("rerank requires candidate_skills array")
    recall_result = recall_candidates(
        payload, state, taxonomy, policy, provider, feedback, vector_cache
    )
    if recall_result["decision"] != "candidates":
        return recall_result

    catalog: Dict[str, Mapping[str, Any]] = {}
    for skill in candidate_skills:
        require_skill(skill)
        skill_id = skill["skill_id"]
        if skill_id in catalog:
            raise ValueError(f"duplicate candidate skill_id: {skill_id}")
        catalog[skill_id] = skill
    expected_ids = recall_result["candidate_skill_ids"]
    if set(catalog) != set(expected_ids):
        raise ValueError("candidate metadata must exactly match recalled candidate_skill_ids")

    records = state.get("skills", {})
    for skill_id, skill in catalog.items():
        if metadata_fingerprint(skill) != records[skill_id].get("metadata_fingerprint"):
            raise ValueError(f"candidate metadata differs for {skill_id}; synchronization required")

    if recall_result["reason_codes"] == ["explicit_skill_id"]:
        skill_id = expected_ids[0]
        return {
            "decision": "invoke",
            "selected_skill_id": skill_id,
            "confidence": 1.0,
            "winner_margin": 1.0,
            "matched_tags": recall_result["matched_tags"],
            "feedback_tags": [],
            "candidate_skill_ids": [skill_id],
            "reason_codes": ["explicit_skill_id"],
            "registry_version": str(payload["registry_version"]),
        }

    query = str(payload["query"])
    registry_version = str(payload["registry_version"])
    retained_tags = recall_result["matched_tags"]
    candidates = [catalog[skill_id] for skill_id in expected_ids]
    if jev_reranker is None:
        raise JevRerankerError(
            "Harness must inject a Jev reranker for second-stage routing"
        )
    selector = jev_reranker
    jev_result = selector.score(query, candidates)
    fits = {
        skill_id: float(jev_result["fits"][skill_id]) for skill_id in expected_ids
    }
    ranked_ids = sorted(expected_ids, key=lambda skill_id: (-fits[skill_id], skill_id))
    winner_id = ranked_ids[0]
    winner_fit = fits[winner_id]
    runner_fit = fits[ranked_ids[1]] if len(ranked_ids) > 1 else 0.0
    margin = winner_fit - runner_fit
    minimum_fit = float(policy.get("jev_fit_threshold", 0.30))
    if winner_fit < minimum_fit:
        decision, selected, reason_codes = "none", None, ["jev_no_candidate_fit"]
    else:
        decision, selected, reason_codes = "invoke", winner_id, ["jev_fit"]
    reason_codes.extend(
        reason
        for reason in recall_result["reason_codes"]
        if reason.endswith("_feedback_applied")
    )
    return {
        "decision": decision,
        "selected_skill_id": selected,
        "highest_fit_probability": round(winner_fit, 6),
        "winner_margin": round(margin, 6),
        "matched_tags": retained_tags,
        "feedback_tags": (
            sorted(
                set(candidate_effective_tags(records[selected], feedback, selected, policy))
                & set(retained_tags)
            )
            if selected
            else []
        ),
        "candidate_skill_ids": ranked_ids,
        "candidate_fit_scores": {
            skill_id: round(fits[skill_id], 6) for skill_id in ranked_ids
        },
        "reranker_model": str(jev_result["model"]),
        "reason_codes": reason_codes,
        "registry_version": registry_version,
    }


def route(
    payload: Mapping[str, Any],
    state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    policy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
    feedback: Mapping[str, Any] | None = None,
    vector_cache: Mapping[str, Any] | None = None,
    jev_reranker: Any | None = None,
) -> Dict[str, Any]:
    """Compatibility wrapper for benchmarks; public routing uses recall then rerank."""
    skills = payload.get("skills")
    if not isinstance(skills, list):
        raise ValueError("route compatibility wrapper requires skills array")
    adapted = dict(payload)
    adapted["enabled_skill_ids"] = [skill.get("skill_id") for skill in skills]
    recall_result = recall_candidates(
        adapted, state, taxonomy, policy, provider, feedback, vector_cache
    )
    if recall_result["decision"] != "candidates":
        return recall_result
    wanted = set(recall_result["candidate_skill_ids"])
    adapted["candidate_skills"] = [skill for skill in skills if skill.get("skill_id") in wanted]
    return rerank_candidates(
        adapted,
        state,
        taxonomy,
        policy,
        provider,
        feedback,
        vector_cache,
        jev_reranker,
    )


def resource_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "references"


def load_resources() -> Tuple[Dict[str, Any], Dict[str, Any], HashEmbeddingProvider]:
    references = resource_dir()
    taxonomy = load_json_yaml(references / "tag-taxonomy.yaml")
    policy = load_json_yaml(references / "routing-policy.yaml")
    provider = HashEmbeddingProvider(int(policy.get("embedding_dimensions", 512)))
    return taxonomy, policy, provider
