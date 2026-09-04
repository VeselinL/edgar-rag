"""Exact citation parsing, visibility filtering, and evidence resolution."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


CITATION_GROUP_PATTERN = re.compile(r"\[([^\[\]]+)\]")
CITATION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
INTERNAL_CITATION_ID_PATTERN = re.compile(
    r"(?:[A-Z][A-Z0-9.]{0,9}-\d{4}-(?:(?:CHUNK|TABLE|HTMLTABLE)-)?[A-Z0-9:-]+|"
    r"upload:[A-Z0-9._:-]+|web-\d+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CitationResolution:
    """Exact used-evidence resolution within the supplied generation context."""

    evidence: tuple[dict[str, Any], ...]
    parsed_ids: tuple[str, ...]
    resolved_ids: tuple[str, ...]
    rejected_ids: tuple[str, ...]
    diagnostic_reason: str


def citation_ids(answer: str) -> list[str]:
    """Return unique single or grouped citation identifiers in answer order."""
    identifiers = []
    for group in CITATION_GROUP_PATTERN.findall(answer):
        candidates = re.split(r"\s*[,;]\s*", group.strip())
        if candidates and all(CITATION_ID_PATTERN.fullmatch(value) for value in candidates):
            identifiers.extend(candidates)
    return list(dict.fromkeys(identifiers))


def _is_resolved_citation_group(group: str, allowed_ids: set[str]) -> bool:
    candidates = re.split(r"\s*[,;]\s*", group.strip())
    return bool(
        candidates
        and all(CITATION_ID_PATTERN.fullmatch(value) for value in candidates)
        and all(value in allowed_ids for value in candidates)
    )


def _is_internal_citation_group(group: str) -> bool:
    candidates = re.split(r"\s*[,;]\s*", group.strip())
    return bool(
        candidates
        and all(INTERNAL_CITATION_ID_PATTERN.fullmatch(value) for value in candidates)
    )


class CitationVisibilityFilter:
    """Hide internal source IDs while retaining ordinary bracketed user text."""

    def __init__(self, allowed_ids: Iterable[str], *, maximum_group_length: int = 512):
        self.allowed_ids = set(allowed_ids)
        self.maximum_group_length = maximum_group_length
        self.buffer = ""
        self.pending_whitespace = ""

    def _plain(self, value: str) -> str:
        combined = self.pending_whitespace + value
        trailing = re.search(r"[ \t]+$", combined)
        if trailing:
            self.pending_whitespace = trailing.group(0)
            return combined[: trailing.start()]
        self.pending_whitespace = ""
        return combined

    def feed(self, fragment: str) -> str:
        self.buffer += fragment
        visible: list[str] = []
        while self.buffer:
            opening = self.buffer.find("[")
            if opening < 0:
                visible.append(self._plain(self.buffer))
                self.buffer = ""
                break
            if opening:
                visible.append(self._plain(self.buffer[:opening]))
                self.buffer = self.buffer[opening:]
            closing = self.buffer.find("]", 1)
            if closing < 0:
                if len(self.buffer) <= self.maximum_group_length:
                    break
                visible.append(self._plain(self.buffer[0]))
                self.buffer = self.buffer[1:]
                continue
            group = self.buffer[1:closing]
            marker = self.buffer[: closing + 1]
            self.buffer = self.buffer[closing + 1 :]
            if _is_resolved_citation_group(
                group, self.allowed_ids
            ) or _is_internal_citation_group(group):
                self.pending_whitespace = ""
            else:
                visible.append(self._plain(marker))
        return "".join(visible)

    def finish(self) -> str:
        visible = self._plain(self.buffer)
        self.buffer = ""
        result = visible + self.pending_whitespace
        self.pending_whitespace = ""
        return result


def visible_answer_text(answer: str, allowed_ids: Iterable[str]) -> str:
    citation_filter = CitationVisibilityFilter(allowed_ids)
    return citation_filter.feed(answer) + citation_filter.finish()


def resolve_cited_evidence(
    answer: str,
    final_evidence: Sequence[dict[str, Any]],
) -> CitationResolution:
    """Resolve cited IDs in answer order without any evidence fallback."""
    by_id = {
        result.get("chunk", result)["chunk_id"]: result for result in final_evidence
    }
    parsed = citation_ids(answer)
    resolved_ids = [chunk_id for chunk_id in parsed if chunk_id in by_id]
    rejected_ids = [chunk_id for chunk_id in parsed if chunk_id not in by_id]
    return CitationResolution(
        evidence=tuple(by_id[chunk_id] for chunk_id in resolved_ids),
        parsed_ids=tuple(parsed),
        resolved_ids=tuple(resolved_ids),
        rejected_ids=tuple(rejected_ids),
        diagnostic_reason=(
            "resolved_citations" if resolved_ids else "no_resolved_citations"
        ),
    )
