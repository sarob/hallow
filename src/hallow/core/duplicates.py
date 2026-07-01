"""Duplication detection — tokenize, build suffix array, find clones."""

from __future__ import annotations

import tokenize
from collections import defaultdict
from io import StringIO
from pathlib import Path

from hallow.config.loader import HallowConfig
from hallow.types import (
    DuplicateFragment,
    DuplicateGroup,
    Finding,
    Location,
    RuleId,
    Severity,
)

_SKIP_TOKENS = {
    tokenize.COMMENT,
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENCODING,
    tokenize.ENDMARKER,
}


class _Token:
    __slots__ = ("kind", "value", "file", "line")

    def __init__(self, kind: int, value: str, file: str, line: int) -> None:
        self.kind = kind
        self.value = value
        self.file = file
        self.line = line


def tokenize_file(path: Path, root: Path, mode: str) -> list[_Token]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    rel = str(path.relative_to(root))
    tokens: list[_Token] = []

    try:
        for tok in tokenize.generate_tokens(StringIO(source).readline):
            if tok.type in _SKIP_TOKENS:
                continue
            value = _normalize_token(tok, mode)
            tokens.append(_Token(tok.type, value, rel, tok.start[0]))
    except tokenize.TokenError:
        pass

    return tokens


def _normalize_token(tok: tokenize.TokenInfo, mode: str) -> str:
    if mode == "strict":
        return tok.string
    if mode == "mild":
        if tok.type == tokenize.NAME and not _is_keyword(tok.string):
            return "$ID"
        if tok.type == tokenize.STRING:
            return "$STR"
        if tok.type == tokenize.NUMBER:
            return "$NUM"
        return tok.string
    if mode == "weak":
        if tok.type == tokenize.NAME:
            return "$ID" if not _is_keyword(tok.string) else tok.string
        if tok.type == tokenize.STRING:
            return "$STR"
        if tok.type == tokenize.NUMBER:
            return "$NUM"
        if tok.type == tokenize.OP:
            return "$OP"
        return tok.string
    if mode == "semantic":
        if tok.type == tokenize.NAME and not _is_keyword(tok.string):
            return "$ID"
        if tok.type == tokenize.STRING:
            return "$STR"
        if tok.type == tokenize.NUMBER:
            return "$NUM"
        return tok.string
    return tok.string


_KEYWORDS = frozenset(
    {
        "False",
        "None",
        "True",
        "and",
        "as",
        "assert",
        "async",
        "await",
        "break",
        "class",
        "continue",
        "def",
        "del",
        "elif",
        "else",
        "except",
        "finally",
        "for",
        "from",
        "global",
        "if",
        "import",
        "in",
        "is",
        "lambda",
        "nonlocal",
        "not",
        "or",
        "pass",
        "raise",
        "return",
        "try",
        "while",
        "with",
        "yield",
        "match",
        "case",
        "type",
    }
)


def _is_keyword(s: str) -> bool:
    return s in _KEYWORDS


def detect_duplicates(
    files: list[Path],
    root: Path,
    config: HallowConfig,
) -> tuple[list[DuplicateGroup], list[Finding]]:
    dupes_config = config.duplicates
    mode = dupes_config.mode
    min_tokens = dupes_config.min_tokens
    min_lines = dupes_config.min_lines
    min_occurrences = dupes_config.min_occurrences

    severity = config.rules.severity_for(RuleId.DUPLICATE_CODE)

    all_tokens: list[_Token] = []
    file_boundaries: list[int] = []

    for path in files:
        file_start = len(all_tokens)
        tokens = tokenize_file(path, root, mode)
        all_tokens.extend(tokens)
        file_boundaries.append(file_start)

    if len(all_tokens) < min_tokens:
        return [], []

    groups = _find_clones(all_tokens, min_tokens, min_lines, min_occurrences)

    findings: list[Finding] = []
    if severity != Severity.OFF:
        for group in groups:
            first = group.fragments[0]
            locations = ", ".join(f"{f.file}:{f.start_line}-{f.end_line}" for f in group.fragments)
            findings.append(
                Finding(
                    rule=RuleId.DUPLICATE_CODE,
                    severity=severity,
                    message=(
                        f"Duplicate code block ({group.token_count} tokens, "
                        f"{group.line_count} lines) appears {len(group.fragments)} times"
                    ),
                    location=Location(
                        file=first.file,
                        line=first.start_line,
                        end_line=first.end_line,
                    ),
                    suggestion="Extract duplicate code into a shared function or module",
                    metadata={
                        "fragments": locations,
                        "token_count": group.token_count,
                        "occurrences": len(group.fragments),
                    },
                )
            )

    return groups, findings


def _find_clones(
    tokens: list[_Token],
    min_tokens: int,
    min_lines: int,
    min_occurrences: int,
) -> list[DuplicateGroup]:
    n = len(tokens)
    if n < min_tokens:
        return []

    hash_to_positions: dict[int, list[int]] = defaultdict(list)
    for i in range(n - min_tokens + 1):
        h = _hash_window(tokens, i, min_tokens)
        hash_to_positions[h].append(i)

    raw_clones: list[tuple[list[int], int]] = []
    for positions in hash_to_positions.values():
        if len(positions) < min_occurrences:
            continue

        filtered = _filter_overlapping(positions, min_tokens)
        if len(filtered) < min_occurrences:
            continue

        length = _extend_match(tokens, filtered, min_tokens)
        raw_clones.append((filtered, length))

    raw_clones.sort(key=lambda x: -x[1])

    # Largest clones are processed first and claim their line ranges. A single
    # clone region surfaces once per starting offset (offsets 0, 1, 2, ... each
    # form a distinct hash bucket), so without collapsing we would emit the same
    # region many times shifted by a line. Track claimed intervals per file and
    # skip any fragment that overlaps one already claimed — this collapses the
    # shifted duplicates while preserving genuinely distinct, non-overlapping
    # clones in the same file.
    used_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    groups: list[DuplicateGroup] = []

    for positions, length in raw_clones:
        fragments: list[DuplicateFragment] = []
        claimed: list[tuple[str, int, int]] = []
        for pos in positions:
            file = tokens[pos].file
            start_line = tokens[pos].line
            end_pos = min(pos + length - 1, n - 1)
            end_line = tokens[end_pos].line
            line_count = end_line - start_line + 1

            if line_count < min_lines:
                continue

            prior = used_intervals[file] + [(s, e) for f, s, e in claimed if f == file]
            if _overlaps(prior, start_line, end_line):
                continue

            claimed.append((file, start_line, end_line))
            fragments.append(
                DuplicateFragment(
                    file=file,
                    start_line=start_line,
                    end_line=end_line,
                    lines_of_code=line_count,
                )
            )

        if len(fragments) >= min_occurrences:
            for file, start_line, end_line in claimed:
                used_intervals[file].append((start_line, end_line))
            line_count = max(f.lines_of_code for f in fragments)
            groups.append(
                DuplicateGroup(
                    fragments=fragments,
                    token_count=length,
                    line_count=line_count,
                )
            )

    return groups


def _overlaps(intervals: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start <= e and s <= end for s, e in intervals)


def _hash_window(tokens: list[_Token], start: int, length: int) -> int:
    h = 0
    for i in range(start, start + length):
        h = (h * 31 + hash(tokens[i].value)) & 0xFFFF_FFFF_FFFF_FFFF
    return h


def _filter_overlapping(positions: list[int], min_length: int) -> list[int]:
    positions = sorted(positions)
    result: list[int] = [positions[0]]
    for p in positions[1:]:
        if p - result[-1] >= min_length:
            result.append(p)
    return result


def _extend_match(
    tokens: list[_Token],
    positions: list[int],
    current_length: int,
) -> int:
    n = len(tokens)
    length = current_length
    while True:
        can_extend = True
        for pos in positions:
            if pos + length >= n:
                can_extend = False
                break
        if not can_extend:
            break

        ref_val = tokens[positions[0] + length].value
        all_match = all(tokens[p + length].value == ref_val for p in positions[1:])
        if not all_match:
            break
        length += 1

    return length
