"""Stable, line-free finding identity.

Every finding gets a canonical key and a `finding_id`, after fallow's `dc1`
scheme (crates/types/src/identity.rs):

- the key is `<rule>:<part>:<part>...`, each part with `%` written `%25` and
  `:` written `%3A`
- the id is `hl1:<rule>:<16 hex>`, the FNV-1a 64 hash of
  `["hl1", rule, *parts]` with a 0xff byte after each part
- line and column are never parts, so moving code keeps the id; renaming the
  file or the symbol gives a new one
- findings sharing a key are ordered by line, column and message; the first
  keeps the base id, the k-th repeat (k >= 1) gets a `~k` suffix

Baselines and the audit gate compare canonical keys.
"""

from __future__ import annotations

from collections import defaultdict

from hallow.types import Finding, RuleId

IDENTITY = "hl1"

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MASK = 0xFFFF_FFFF_FFFF_FFFF

# Rules whose finding is about a declaration in a project-level file and is
# identified by the declared name alone, wherever the finding is located.
_NAME_ONLY = {RuleId.UNLISTED_DEPENDENCIES, RuleId.DUPLICATE_CODE}


def _fnv1a64(parts: list[str]) -> int:
    h = _FNV_OFFSET
    for part in parts:
        for byte in part.encode("utf-8"):
            h = ((h ^ byte) * _FNV_PRIME) & _MASK
        h = ((h ^ 0xFF) * _FNV_PRIME) & _MASK
    return h


def _escape(part: str) -> str:
    return part.replace("%", "%25").replace(":", "%3A")


def identity_parts(finding: Finding, path_map: dict[str, str] | None = None) -> list[str]:
    """The line-free parts that identify a finding.

    `path_map` rewrites file paths (old -> new), so findings from a renamed
    file compare equal across a `git mv`.
    """

    def p(path: str) -> str:
        return path_map.get(path, path) if path_map else path

    rule = finding.rule
    symbol = finding.symbol or ""
    if rule == RuleId.UNUSED_FILES:
        return [p(finding.location.file)]
    if rule in _NAME_ONLY:
        return [symbol]
    if rule == RuleId.CIRCULAR_DEPENDENCIES:
        # Each member escaped before joining, so a `|` inside a filename
        # cannot merge two different member sets into one key.
        modules = finding.metadata.get("modules") or [m for m in symbol.split("|") if m]
        members = sorted(_escape(p(m)).replace("|", "%7C") for m in modules)
        return ["|".join(members)]
    if rule == RuleId.HIGH_COMPLEXITY:
        return [p(finding.location.file), symbol, str(finding.metadata.get("kind", ""))]
    if rule == RuleId.BOUNDARY_VIOLATION:
        return [p(finding.location.file), p(symbol)]
    return [p(finding.location.file), symbol]


def canonical_key(finding: Finding, path_map: dict[str, str] | None = None) -> str:
    parts = identity_parts(finding, path_map)
    if finding.rule == RuleId.CIRCULAR_DEPENDENCIES:
        return f"{finding.rule.value}:{parts[0]}"  # members already escaped
    return ":".join([finding.rule.value, *(_escape(x) for x in parts)])


def _finding_id(finding: Finding, parts: list[str]) -> str:
    digest = _fnv1a64([IDENTITY, finding.rule.value, *parts])
    return f"{IDENTITY}:{finding.rule.value}:{digest:016x}"


def stamp_identities(findings: list[Finding]) -> None:
    """Set `key` and `finding_id` on every finding, in place."""
    groups: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        f.key = canonical_key(f)
        groups[f.key].append(f)

    for same in groups.values():
        same.sort(key=lambda f: (f.location.line, f.location.col, f.message))
        base = _finding_id(same[0], identity_parts(same[0]))
        for k, f in enumerate(same):
            f.finding_id = base if k == 0 else f"{base}~{k}"
