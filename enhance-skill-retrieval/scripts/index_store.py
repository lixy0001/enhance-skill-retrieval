#!/usr/bin/env python3
"""Persist and incrementally maintain derived retrieval vectors."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from retrieval_core import (
    HashEmbeddingProvider,
    atomic_write_json_yaml,
    catalog_digest,
    is_finite_vector,
    load_json_yaml,
    metadata_fingerprint,
    require_skill,
    skill_metadata_text,
    tag_text,
)


VECTOR_CACHE_SCHEMA_VERSION = "1.0.0"
DEFAULT_VECTOR_CACHE_FILE = "vector-cache.json"


def vector_cache_path(state_dir: str) -> Path:
    return Path(state_dir).expanduser().resolve() / DEFAULT_VECTOR_CACHE_FILE


def provider_manifest(provider: HashEmbeddingProvider) -> Dict[str, Any]:
    return {"name": provider.name, "dimensions": provider.dimensions}


def build_vector_cache(
    skills: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
) -> Dict[str, Any]:
    if catalog_digest(skills) != state.get("catalog_digest"):
        raise ValueError("catalog snapshot differs; synchronization or rebuild required")
    vectors: Dict[str, Any] = {}
    for skill in skills:
        require_skill(skill)
        skill_id = str(skill["skill_id"])
        vectors[skill_id] = {
            "metadata_fingerprint": metadata_fingerprint(skill),
            "vector": provider.embed(skill_metadata_text(skill)),
        }
    tag_vectors = {
        str(tag["id"]): provider.embed(tag_text(tag))
        for tag in taxonomy.get("tags", [])
        if isinstance(tag, Mapping) and tag.get("id")
    }
    return {
        "schema_version": VECTOR_CACHE_SCHEMA_VERSION,
        "taxonomy_version": str(taxonomy.get("version", "")),
        "provider": provider_manifest(provider),
        "catalog_digest": str(state.get("catalog_digest", "")),
        "tag_vectors": dict(sorted(tag_vectors.items())),
        "skill_vectors": dict(sorted(vectors.items())),
    }


def cache_is_compatible(
    cache: Mapping[str, Any],
    state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
) -> bool:
    if not (
        cache.get("schema_version") == VECTOR_CACHE_SCHEMA_VERSION
        and cache.get("taxonomy_version") == str(taxonomy.get("version", ""))
        and cache.get("provider") == provider_manifest(provider)
        and cache.get("catalog_digest") == state.get("catalog_digest")
    ):
        return False
    tag_vectors = cache.get("tag_vectors")
    skill_vectors = cache.get("skill_vectors")
    if not isinstance(tag_vectors, Mapping) or not isinstance(skill_vectors, Mapping):
        return False
    expected_tags = {
        str(tag["id"])
        for tag in taxonomy.get("tags", [])
        if isinstance(tag, Mapping) and tag.get("id")
    }
    records = state.get("skills", {})
    if set(tag_vectors) != expected_tags or set(skill_vectors) != set(records):
        return False
    if any(not is_finite_vector(vector, provider.dimensions) for vector in tag_vectors.values()):
        return False
    for skill_id, record in records.items():
        entry = skill_vectors.get(skill_id)
        if not isinstance(entry, Mapping):
            return False
        if entry.get("metadata_fingerprint") != record.get("metadata_fingerprint"):
            return False
        if not is_finite_vector(entry.get("vector"), provider.dimensions):
            return False
    return True


def load_vector_cache(
    state_dir: str,
    state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
    required: bool = False,
) -> Dict[str, Any] | None:
    path = vector_cache_path(state_dir)
    if not path.exists():
        if required:
            raise FileNotFoundError(f"vector cache not initialized: {path}")
        return None
    try:
        cache = load_json_yaml(path)
    except (OSError, ValueError):
        if required:
            raise
        return None
    if not isinstance(cache, Mapping):
        if required:
            raise ValueError("vector cache must be an object")
        return None
    if not cache_is_compatible(cache, state, taxonomy, provider):
        if required:
            raise ValueError("vector cache is stale; rebuild required")
        return None
    return cache


def save_vector_cache(state_dir: str, cache: Mapping[str, Any]) -> None:
    atomic_write_json_yaml(vector_cache_path(state_dir), cache)


def ensure_vector_cache(
    state_dir: str,
    skills: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
) -> Tuple[Dict[str, Any], bool]:
    cache = load_vector_cache(state_dir, state, taxonomy, provider)
    if cache is not None:
        return cache, False
    cache = build_vector_cache(skills, state, taxonomy, provider)
    save_vector_cache(state_dir, cache)
    return cache, True


def update_vector_cache(
    cache: Mapping[str, Any],
    event: Mapping[str, Any],
    state: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    provider: HashEmbeddingProvider,
) -> Dict[str, Any]:
    """Update an already-compatible cache for one harness lifecycle event."""
    updated = dict(cache)
    skill_vectors = dict(cache.get("skill_vectors", {}))
    event_type = event.get("event")
    if event_type in ("skill_added", "skill_updated"):
        skill = event.get("skill")
        if not isinstance(skill, Mapping):
            raise ValueError(f"{event_type} requires skill")
        require_skill(skill)
        skill_vectors[str(skill["skill_id"])] = {
            "metadata_fingerprint": metadata_fingerprint(skill),
            "vector": provider.embed(skill_metadata_text(skill)),
        }
    elif event_type == "skill_removed":
        skill_vectors.pop(str(event.get("skill_id", "")), None)
    else:
        raise ValueError("unsupported harness event")
    updated.update(
        {
            "schema_version": VECTOR_CACHE_SCHEMA_VERSION,
            "taxonomy_version": str(taxonomy.get("version", "")),
            "provider": provider_manifest(provider),
            "catalog_digest": str(state.get("catalog_digest", "")),
            "skill_vectors": dict(sorted(skill_vectors.items())),
        }
    )
    return updated
