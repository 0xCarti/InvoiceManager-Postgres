from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from typing import Iterable


_TOKEN_RE = re.compile(r"[a-z]+|\d+(?:\.\d+)?")

_STOP_TOKENS = {
    "a",
    "an",
    "and",
    "bag",
    "bags",
    "bar",
    "bars",
    "bib",
    "bibs",
    "bottle",
    "bottles",
    "box",
    "boxes",
    "can",
    "cans",
    "case",
    "cases",
    "ct",
    "count",
    "crate",
    "each",
    "ea",
    "flat",
    "flats",
    "keg",
    "kegs",
    "of",
    "pack",
    "packs",
    "package",
    "packages",
    "pc",
    "pcs",
    "piece",
    "pieces",
    "pkg",
    "pk",
    "single",
    "sleeve",
    "sleeves",
    "the",
    "unit",
    "units",
}

_UNIT_TOKENS = {
    "cl",
    "g",
    "gal",
    "gallon",
    "gallons",
    "gram",
    "grams",
    "kg",
    "l",
    "lb",
    "lbs",
    "liter",
    "liters",
    "litre",
    "litres",
    "ml",
    "ounce",
    "ounces",
    "oz",
}

_TOKEN_ALIASES = {
    "btl": "bottle",
    "btls": "bottle",
    "cn": "can",
    "cs": "case",
    "liters": "litre",
    "liter": "litre",
    "litres": "litre",
    "ounces": "ounce",
    "grams": "gram",
    "gallons": "gallon",
}


@dataclass(frozen=True)
class DuplicateItemCandidate:
    item_id: int
    name: str
    normalized_name: str
    tokens: tuple[str, ...]
    core_tokens: tuple[str, ...]
    core_signature: str


@dataclass(frozen=True)
class DuplicateItemGroup:
    item_ids: tuple[int, ...]
    score: float
    reasons: tuple[str, ...]


def _normalize_token(token: str) -> str:
    normalized = _TOKEN_ALIASES.get(token, token)
    if len(normalized) > 4 and normalized.endswith("s"):
        normalized = normalized[:-1]
    return normalized


def _tokenize_name(name: str) -> tuple[str, ...]:
    return tuple(_normalize_token(token) for token in _TOKEN_RE.findall(name.lower()))


def _core_tokens(tokens: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        token
        for token in tokens
        if not token.replace(".", "", 1).isdigit()
        and token not in _STOP_TOKENS
        and token not in _UNIT_TOKENS
    )


def _candidate_for_item(item) -> DuplicateItemCandidate | None:
    item_id = getattr(item, "id", None)
    name = (getattr(item, "name", "") or "").strip()
    if item_id is None or not name:
        return None
    tokens = _tokenize_name(name)
    if not tokens:
        return None
    core_tokens = _core_tokens(tokens)
    return DuplicateItemCandidate(
        item_id=int(item_id),
        name=name,
        normalized_name=" ".join(tokens),
        tokens=tokens,
        core_tokens=core_tokens,
        core_signature=" ".join(core_tokens),
    )


def _blocking_keys(candidate: DuplicateItemCandidate) -> set[str]:
    keys = {f"name:{candidate.normalized_name}"}
    core_tokens = candidate.core_tokens
    if len(core_tokens) >= 2:
        keys.add(f"core:{candidate.core_signature}")
        keys.add(f"first:{core_tokens[0]}")
        keys.add(f"first-two:{' '.join(core_tokens[:2])}")
        for left, right in zip(core_tokens, core_tokens[1:]):
            keys.add(f"pair:{left} {right}")
    elif core_tokens:
        keys.add(f"short:{core_tokens[0]}")
    return keys


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _pair_score(
    left: DuplicateItemCandidate, right: DuplicateItemCandidate
) -> tuple[float, str] | None:
    if left.normalized_name == right.normalized_name:
        return 1.0, "Exact normalized name"

    if (
        left.core_signature
        and left.core_signature == right.core_signature
        and len(left.core_tokens) >= 1
    ):
        score = 0.96 if len(left.core_tokens) >= 2 else 0.9
        return score, "Same core item words"

    left_core = set(left.core_tokens)
    right_core = set(right.core_tokens)
    shared_core_count = len(left_core & right_core)

    core_ratio = SequenceMatcher(
        None, left.core_signature, right.core_signature
    ).ratio()
    name_ratio = SequenceMatcher(
        None, left.normalized_name, right.normalized_name
    ).ratio()
    token_ratio = _jaccard(left_core, right_core)

    if shared_core_count >= 2 and (
        left_core.issubset(right_core) or right_core.issubset(left_core)
    ):
        score = max(0.9, min(0.95, (core_ratio + token_ratio) / 2))
        return score, "One name adds pack or size wording"

    combined = max(name_ratio, (core_ratio * 0.65) + (token_ratio * 0.35))
    if shared_core_count >= 2 and combined >= 0.84:
        return combined, "Similar item name"

    shorter, longer = sorted(
        (left.normalized_name, right.normalized_name), key=len
    )
    if len(shorter) >= 8 and longer.startswith(shorter) and name_ratio >= 0.78:
        return name_ratio, "One name extends the other"

    return None


def find_duplicate_item_groups(
    items: Iterable[object], *, max_bucket_size: int = 500
) -> list[DuplicateItemGroup]:
    """Return connected groups of items whose names look like duplicates.

    The matcher intentionally errs on the side of reviewable candidates rather than
    automatic deletion. It ignores common package/unit words and numeric size/count
    suffixes, then combines exact core-word matches with fuzzy name similarity.
    """

    candidates = [
        candidate
        for candidate in (_candidate_for_item(item) for item in items)
        if candidate is not None
    ]
    if len(candidates) < 2:
        return []

    buckets: dict[str, set[int]] = defaultdict(set)
    for index, candidate in enumerate(candidates):
        for key in _blocking_keys(candidate):
            buckets[key].add(index)

    pair_matches: dict[tuple[int, int], tuple[float, str]] = {}
    for bucket in buckets.values():
        if len(bucket) < 2 or len(bucket) > max_bucket_size:
            continue
        for left_index, right_index in combinations(sorted(bucket), 2):
            key = (left_index, right_index)
            if key in pair_matches:
                continue
            score = _pair_score(candidates[left_index], candidates[right_index])
            if score is not None:
                pair_matches[key] = score

    if not pair_matches:
        return []

    parent = list(range(len(candidates)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, right_index in pair_matches:
        union(left_index, right_index)

    grouped_indexes: dict[int, list[int]] = defaultdict(list)
    for index in range(len(candidates)):
        grouped_indexes[find(index)].append(index)

    groups: list[DuplicateItemGroup] = []
    for indexes in grouped_indexes.values():
        if len(indexes) < 2:
            continue
        index_set = set(indexes)
        scores: list[float] = []
        reasons: set[str] = set()
        for (left_index, right_index), (score, reason) in pair_matches.items():
            if left_index in index_set and right_index in index_set:
                scores.append(score)
                reasons.add(reason)
        ordered_candidates = sorted(
            (candidates[index] for index in indexes),
            key=lambda candidate: (candidate.name.casefold(), candidate.item_id),
        )
        groups.append(
            DuplicateItemGroup(
                item_ids=tuple(candidate.item_id for candidate in ordered_candidates),
                score=max(scores) if scores else 0.0,
                reasons=tuple(sorted(reasons)),
            )
        )

    return sorted(
        groups,
        key=lambda group: (
            -group.score,
            len(group.item_ids),
            group.item_ids,
        ),
    )
