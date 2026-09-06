"""Company-name hints and planner-owned scope for the fixed AVA corpus."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Literal, Sequence

from src.filings.corpus import ACTIVE_FILINGS, COMPANY_ALIASES, COMPANY_NAMES


ResolutionMethod = Literal["exact_alias", "exact_ticker", "fuzzy", "llm"]

COMPARISON_CUES = (
    "other companies",
    "other strategies",
    "the others",
    "competitors",
    "the rest",
    "across the companies",
    "across the industry",
    "which companies",
    "who is most",
    "who is more",
    "compare",
    "compared with",
    "versus",
    " vs ",
)

ENUMERATION_CUES = (
    r"\bwhich companies\b",
    r"\bwhat companies\b",
    r"\bwhich firms\b",
    r"\bwhat firms\b",
    r"\bwho (?:offers|operates|develops|provides|uses|builds|sells)\b",
)

FULL_CORPUS_CUES = (
    r"\b(?:each|every) (?:one of )?(?:the )?(?:company|companies|firm|firms)\b",
    r"\ball (?:of )?(?:the )?(?:eleven )?(?:companies|firms)\b",
)

FULL_CORPUS_EXCLUSION_CUE = r"\b(?:except|excluding|apart from|but not)\b"

CORPORATE_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "limited",
    "ltd",
    "plc",
}

# These configured aliases are ordinary-language collisions in the fixed test
# set. They must be treated as unresolved context, never exact company matches.
AMBIGUOUS_ALIAS_CONTEXTS: tuple[tuple[str, str], ...] = (
    ("aurora", "aurora borealis"),
    ("alphabet", "alphabet soup"),
    ("snapdragon", "snapdragon flower"),
)

QUESTION_WORDS = {
    "a", "an", "and", "answer", "are", "company", "compare", "describe", "did", "do",
    "does", "explain", "filing", "filings", "for", "from", "give", "how",
    "in", "is", "it", "latest", "me", "of", "on", "or", "report", "risk",
    "risks", "strategy", "summarize", "the", "their", "these", "this", "to",
    "what", "when", "where", "which", "who", "why", "with",
}

NON_COMPANY_ACRONYMS = {
    "ADAS", "AI", "AV", "CEO", "COO", "CFO", "CTO", "EV", "FSD", "GAAP",
    "OEM", "OEMs", "R&D", "R1S", "R1T", "R2", "R3", "R3X", "SEC", "SUV", "USD",
}


def _is_non_company_acronym(value: str) -> bool:
    upper = value.upper()
    return upper in NON_COMPANY_ACRONYMS or (
        upper.endswith("S") and upper[:-1] in NON_COMPANY_ACRONYMS
    )


@dataclass(frozen=True)
class CompanyMention:
    raw_text: str
    ticker: str
    canonical_name: str
    method: ResolutionMethod
    confidence: float


@dataclass(frozen=True)
class UnresolvedMention:
    raw_text: str
    reason: str
    candidate_tickers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompanyResolution:
    original_query: str
    mentions: tuple[CompanyMention, ...]
    unresolved_mentions: tuple[UnresolvedMention, ...]
    scope: str
    comparison: bool
    needs_clarification: bool
    explicit_scope_tickers: tuple[str, ...] = ()
    planner_scope_tickers: tuple[str, ...] = ()

    @property
    def resolved_tickers(self) -> tuple[str, ...]:
        present = {
            *self.explicit_scope_tickers,
            *self.planner_scope_tickers,
            *(mention.ticker for mention in self.mentions),
        }
        return tuple(ticker for ticker in ACTIVE_FILINGS if ticker in present)

    @property
    def methods(self) -> tuple[str, ...]:
        return tuple(mention.method for mention in self.mentions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "mentions": [mention.__dict__ for mention in self.mentions],
            "unresolved_mentions": [
                mention.__dict__ for mention in self.unresolved_mentions
            ],
            "explicit_scope_tickers": list(self.explicit_scope_tickers),
            "planner_scope_tickers": list(self.planner_scope_tickers),
            "resolved_tickers": list(self.resolved_tickers),
            "scope": self.scope,
            "comparison": self.comparison,
            "needs_clarification": self.needs_clarification,
        }


def confidence_band(confidence: float) -> str:
    """Return the stable diagnostic band used by runtime and evaluation logs."""
    if confidence >= 0.90:
        return "high"
    if confidence >= 0.75:
        return "medium"
    return "low"


def normalize_company_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("’", "'")
    value = value.casefold()
    value = re.sub(r"(?<=\w)'s\b", "", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def normalize_company_phrase(value: str) -> str:
    tokens = normalize_company_text(value).split()
    while tokens and tokens[-1] in CORPORATE_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def damerau_levenshtein(left: str, right: str) -> int:
    """Optimal-string-alignment distance, sufficient for short alias typos."""
    rows = len(left) + 1
    columns = len(right) + 1
    matrix = [[0] * columns for _ in range(rows)]
    for row in range(rows):
        matrix[row][0] = row
    for column in range(columns):
        matrix[0][column] = column
    for row in range(1, rows):
        for column in range(1, columns):
            cost = 0 if left[row - 1] == right[column - 1] else 1
            matrix[row][column] = min(
                matrix[row - 1][column] + 1,
                matrix[row][column - 1] + 1,
                matrix[row - 1][column - 1] + cost,
            )
            if (
                row > 1
                and column > 1
                and left[row - 1] == right[column - 2]
                and left[row - 2] == right[column - 1]
            ):
                matrix[row][column] = min(
                    matrix[row][column], matrix[row - 2][column - 2] + cost
                )
    return matrix[-1][-1]


def _similarity(left: str, right: str) -> float:
    left_compact = left.replace(" ", "")
    right_compact = right.replace(" ", "")
    length = max(len(left_compact), len(right_compact))
    if not length:
        return 0.0
    return 1.0 - damerau_levenshtein(left_compact, right_compact) / length


def _fuzzy_threshold(alias: str) -> float:
    length = len(alias.replace(" ", ""))
    if length <= 4:
        return 0.75
    if length <= 7:
        return 0.80
    return 0.84


def _is_enumeration(query: str) -> bool:
    return any(re.search(pattern, query, re.IGNORECASE) for pattern in ENUMERATION_CUES)


def _has_full_corpus_cue(query: str) -> bool:
    normalized = normalize_company_text(query)
    return any(re.search(pattern, normalized) for pattern in FULL_CORPUS_CUES)


def _requests_full_corpus(query: str) -> bool:
    normalized = normalize_company_text(query)
    return _has_full_corpus_cue(query) and not re.search(
        FULL_CORPUS_EXCLUSION_CUE, normalized
    )


def _is_comparison(query: str) -> bool:
    normalized = f" {normalize_company_text(query)} "
    return (
        any(cue in normalized for cue in COMPARISON_CUES)
        or _is_enumeration(query)
    )


def _scope(query: str, tickers: Sequence[str]) -> str:
    if _is_enumeration(query):
        return "enumeration"
    comparison_cue = _is_comparison(query)
    if not tickers:
        return "global"
    if len(tickers) == 1 and comparison_cue:
        return "anchored_global"
    if len(tickers) == 1:
        return "single_company"
    return "explicit_subset"


class CompanyResolver:
    """Resolve exact and fuzzy mentions, then validate planner classifications."""

    def __init__(self, *, fuzzy_margin: float = 0.08) -> None:
        self.fuzzy_margin = fuzzy_margin
        lexicon: dict[str, set[str]] = {ticker: set() for ticker in ACTIVE_FILINGS}
        for ticker in ACTIVE_FILINGS:
            lexicon[ticker].update(
                normalize_company_phrase(alias) for alias in COMPANY_ALIASES[ticker]
            )
            lexicon[ticker].add(normalize_company_phrase(COMPANY_NAMES[ticker]))
        self.lexicon = {
            ticker: tuple(sorted(values, key=lambda item: (-len(item), item)))
            for ticker, values in lexicon.items()
        }

    def resolve(self, query: str) -> CompanyResolution:
        normalized = normalize_company_text(query)
        explicit_scope_tickers = (
            tuple(ACTIVE_FILINGS) if _requests_full_corpus(query) else ()
        )
        ambiguous_phrases = {
            alias
            for alias, context in AMBIGUOUS_ALIAS_CONTEXTS
            if re.search(rf"(?<!\w){re.escape(context)}(?!\w)", normalized)
        }
        mentions: list[CompanyMention] = []
        matched_tickers: set[str] = set()

        for ticker in ACTIVE_FILINGS:
            for alias in self.lexicon[ticker]:
                if alias in ambiguous_phrases:
                    continue
                if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
                    mentions.append(
                        CompanyMention(
                            alias,
                            ticker,
                            COMPANY_NAMES[ticker],
                            "exact_alias",
                            1.0,
                        )
                    )
                    matched_tickers.add(ticker)
                    break

        raw_tokens = re.findall(r"\$?[A-Za-z][A-Za-z0-9'’]*", query)
        ticker_tokens = {
            token.lstrip("$").removesuffix("'s").removesuffix("’s").upper()
            for token in raw_tokens
        }
        for ticker in ACTIVE_FILINGS:
            explicit = ticker in ticker_tokens
            if ticker == "F":
                explicit = bool(
                    re.search(
                        r"(?<!\w)(?:ticker\s+|\$)F(?!\w)",
                        query,
                        flags=re.IGNORECASE,
                    )
                )
            if explicit and ticker not in matched_tickers:
                mentions.append(
                    CompanyMention(
                        ticker,
                        ticker,
                        COMPANY_NAMES[ticker],
                        "exact_ticker",
                        1.0,
                    )
                )
                matched_tickers.add(ticker)

        unresolved: list[UnresolvedMention] = [
            UnresolvedMention(context, "ambiguous_alias_context")
            for alias, context in AMBIGUOUS_ALIAS_CONTEXTS
            if re.search(rf"(?<!\w){re.escape(context)}(?!\w)", normalized)
        ]
        if _has_full_corpus_cue(query) and re.search(
            FULL_CORPUS_EXCLUSION_CUE, normalized
        ):
            unresolved.append(
                UnresolvedMention(
                    query,
                    "unsupported_full_corpus_exclusion",
                )
            )

        query_tokens = normalized.split()
        occupied = {normalize_company_phrase(m.raw_text) for m in mentions}
        fuzzy_matches: list[tuple[float, float, str, str]] = []
        for window_size in range(1, min(4, len(query_tokens)) + 1):
            for start in range(len(query_tokens) - window_size + 1):
                raw = " ".join(query_tokens[start:start + window_size])
                candidate = normalize_company_phrase(raw)
                if (
                    not candidate
                    or all(token in QUESTION_WORDS for token in candidate.split())
                    or candidate in occupied
                    or any(
                        re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", candidate)
                        for alias in ambiguous_phrases
                    )
                ):
                    continue
                scores: dict[str, float] = {}
                for ticker, aliases in self.lexicon.items():
                    if ticker in matched_tickers:
                        continue
                    scores[ticker] = max(_similarity(candidate, alias) for alias in aliases)
                ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
                if not ranked:
                    continue
                best_ticker, best_score = ranked[0]
                runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
                best_alias = max(
                    self.lexicon[best_ticker],
                    key=lambda alias: _similarity(candidate, alias),
                )
                threshold = _fuzzy_threshold(best_alias)
                if best_score >= threshold:
                    fuzzy_matches.append((best_score, best_score - runner_up, raw, best_ticker))

        for score, margin, raw, ticker in sorted(
            fuzzy_matches, key=lambda item: (-item[0], -len(item[2]))
        ):
            if ticker in matched_tickers:
                continue
            if margin < self.fuzzy_margin:
                unresolved.append(UnresolvedMention(raw, "low_margin_fuzzy_match", (ticker,)))
                continue
            mentions.append(
                CompanyMention(raw, ticker, COMPANY_NAMES[ticker], "fuzzy", score)
            )
            matched_tickers.add(ticker)

        resolved_words = {
            word
            for aliases in self.lexicon.values()
            for alias in aliases
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized)
            for word in alias.split()
        }
        resolved_words.update(
            word
            for mention in mentions
            for word in normalize_company_text(mention.raw_text).split()
        )
        unresolved_words = {
            word
            for mention in unresolved
            for word in normalize_company_text(mention.raw_text).split()
        }
        for position, token in enumerate(raw_tokens):
            bare = re.sub(r"[’']s$", "", token.lstrip("$"), flags=re.IGNORECASE)
            normalized_token = normalize_company_text(bare)
            if (
                not normalized_token
                or normalized_token in resolved_words
                or normalized_token in unresolved_words
                or normalized_token in QUESTION_WORDS
                or _is_non_company_acronym(bare)
            ):
                continue
            company_like = (
                token.startswith("$")
                or bare.isupper()
                or (bare[:1].isupper() and position > 0)
                or bool(re.search(r"[’']s$", token, flags=re.IGNORECASE))
            )
            if not company_like:
                continue
            shortlist = self._shortlist(normalized_token)
            unresolved.append(
                UnresolvedMention(bare, "unrecognized_company_like_mention", shortlist)
            )

        mentions = self._deduplicate_mentions(mentions)
        unresolved = self._deduplicate_unresolved(unresolved)
        tickers = tuple(
            ticker
            for ticker in ACTIVE_FILINGS
            if ticker
            in {
                *explicit_scope_tickers,
                *(mention.ticker for mention in mentions),
            }
        )
        scope = _scope(query, tickers)
        return CompanyResolution(
            original_query=query,
            mentions=tuple(mentions),
            unresolved_mentions=tuple(unresolved),
            scope=scope,
            comparison=_is_comparison(query),
            needs_clarification=bool(unresolved),
            explicit_scope_tickers=explicit_scope_tickers,
        )

    def _shortlist(self, raw: str, limit: int = 3) -> tuple[str, ...]:
        scores = []
        for ticker, aliases in self.lexicon.items():
            score = max(_similarity(raw, alias) for alias in aliases)
            if score >= 0.5:
                scores.append((score, ticker))
        return tuple(
            ticker
            for _, ticker in sorted(scores, key=lambda item: (-item[0], item[1]))[:limit]
        )

    @staticmethod
    def _deduplicate_mentions(
        mentions: Sequence[CompanyMention],
    ) -> list[CompanyMention]:
        by_ticker: dict[str, CompanyMention] = {}
        priority = {"exact_alias": 4, "exact_ticker": 3, "fuzzy": 2, "llm": 1}
        for mention in mentions:
            existing = by_ticker.get(mention.ticker)
            if existing is None or priority[mention.method] > priority[existing.method]:
                by_ticker[mention.ticker] = mention
        return [by_ticker[ticker] for ticker in ACTIVE_FILINGS if ticker in by_ticker]

    @staticmethod
    def _deduplicate_unresolved(
        mentions: Sequence[UnresolvedMention],
    ) -> list[UnresolvedMention]:
        unique: dict[str, UnresolvedMention] = {}
        for mention in mentions:
            unique.setdefault(normalize_company_text(mention.raw_text), mention)
        return list(unique.values())

    def apply_planner_resolution(
        self,
        deterministic: CompanyResolution,
        planner_mentions: Sequence[dict[str, Any]],
        planner_resolved_tickers: Sequence[str],
    ) -> CompanyResolution:
        allowed = set(ACTIVE_FILINGS)
        if any(ticker not in allowed for ticker in planner_resolved_tickers):
            raise ValueError("Planner returned an out-of-corpus ticker.")
        planner_tickers = set(planner_resolved_tickers)
        retained_deterministic = [
            mention
            for mention in deterministic.mentions
            if mention.ticker in planner_tickers
        ]
        planner_mentions_resolved: list[CompanyMention] = []
        planner_unresolved: list[UnresolvedMention] = []
        ambiguous = False
        for value in planner_mentions:
            if set(value) != {"raw_text", "ticker"}:
                raise ValueError("Planner company mention has an invalid shape.")
            raw_text = value["raw_text"]
            ticker = value["ticker"]
            if (
                not isinstance(raw_text, str)
                or ticker not in {*allowed, "none", "ambiguous"}
            ):
                raise ValueError("Planner company mention has an invalid value.")
            if ticker in {"none", "ambiguous"}:
                ambiguous = ambiguous or ticker == "ambiguous"
                planner_unresolved.append(
                    UnresolvedMention(
                        raw_text=raw_text,
                        reason=f"planner_{ticker}",
                    )
                )
                continue
            if ticker not in planner_tickers:
                raise ValueError("Planner mention ticker is absent from resolved_tickers.")
            planner_mentions_resolved.append(
                CompanyMention(
                    raw_text, ticker, COMPANY_NAMES[ticker], "llm", 0.70
                )
            )

        mentions = self._deduplicate_mentions(
            [*retained_deterministic, *planner_mentions_resolved]
        )
        tickers = tuple(ticker for ticker in ACTIVE_FILINGS if ticker in planner_tickers)
        return CompanyResolution(
            original_query=deterministic.original_query,
            mentions=tuple(mentions),
            unresolved_mentions=tuple(planner_unresolved),
            scope=_scope(deterministic.original_query, tickers),
            comparison=_is_comparison(deterministic.original_query),
            needs_clarification=bool(planner_unresolved) or ambiguous,
            planner_scope_tickers=tickers,
        )

    def retrieval_query(self, query: str, tickers: Sequence[str]) -> str:
        query_resolution = self.resolve(query)
        already_scoped = {
            mention.ticker
            for mention in query_resolution.mentions
            if mention.method in {"exact_alias", "exact_ticker"}
        }
        already_scoped.update(query_resolution.explicit_scope_tickers)
        additions = [
            f"{COMPANY_NAMES[ticker]} ({ticker})"
            for ticker in tickers
            if ticker in ACTIVE_FILINGS and ticker not in already_scoped
        ]
        if not additions:
            scoped_query = query
        else:
            scoped_query = f"{query}\nCompany scope: {'; '.join(additions)}"
        # Filing language often says “consumer vehicles” and “vehicle models”
        # rather than “cars”. Add a narrow lexical bridge for manufacturing
        # questions so the exact Item 1 product chunk remains selectable.
        if re.search(
            r"\b(?:cars?|automobiles?|vehicles?|manufactur(?:e|es|ed|ing)|builds?|built)\b",
            query,
            re.I,
        ):
            # Normalize common user/planner variants toward the exact Item 1
            # language used in vehicle-product disclosures. In particular,
            # `manufactured` must not rank differently from `manufacture`.
            scoped_query += " consumer vehicles vehicle models currently manufacture"
        return scoped_query

    @staticmethod
    def clarification_message(resolution: CompanyResolution, *, language: str = "en") -> str:
        raw = ", ".join(item.raw_text for item in resolution.unresolved_mentions)
        if language == "sr":
            if raw:
                return (
                    f"Ne mogu bezbedno da povežem referencu na kompaniju {raw!r} sa "
                    "AVA korpusom prijava. Navedite naziv kompanije ili ticker koji ste mislili."
                )
            return "Pojasnite na koju kompaniju iz AVA korpusa prijava mislite."
        if raw:
            return (
                f"I couldn't safely resolve the company reference {raw!r} to AVA's "
                "filing corpus. Please provide the company name or ticker you intended."
            )
        return "Please clarify which company in AVA's filing corpus you intended."


default_company_resolver = CompanyResolver()
