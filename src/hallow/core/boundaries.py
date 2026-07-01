"""Architecture boundary enforcement — zones, rules, and presets."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath

from hallow.config.loader import BoundaryConfig, BoundaryRule, BoundaryZone, HallowConfig
from hallow.graph.module_graph import ModuleGraph
from hallow.types import Finding, Location, RuleId, Severity

_LAYERED_PRESET = BoundaryConfig(
    preset="layered",
    zones=[
        BoundaryZone(name="presentation", patterns=["**/views/**", "**/routes/**", "**/api/**"]),
        BoundaryZone(name="business", patterns=["**/services/**", "**/domain/**", "**/core/**"]),
        BoundaryZone(name="data", patterns=["**/models/**", "**/repositories/**", "**/db/**"]),
    ],
    rules=[
        BoundaryRule(**{"from": "presentation", "allow": ["business"]}),
        BoundaryRule(**{"from": "business", "allow": ["data"]}),
        BoundaryRule(**{"from": "data", "allow": []}),
    ],
)

_HEXAGONAL_PRESET = BoundaryConfig(
    preset="hexagonal",
    zones=[
        BoundaryZone(name="adapters", patterns=["**/adapters/**", "**/infra/**"]),
        BoundaryZone(name="ports", patterns=["**/ports/**", "**/interfaces/**"]),
        BoundaryZone(name="domain", patterns=["**/domain/**", "**/core/**", "**/models/**"]),
    ],
    rules=[
        BoundaryRule(**{"from": "adapters", "allow": ["ports", "domain"]}),
        BoundaryRule(**{"from": "ports", "allow": ["domain"]}),
        BoundaryRule(**{"from": "domain", "allow": []}),
    ],
)

_FEATURE_SLICED_PRESET = BoundaryConfig(
    preset="feature-sliced",
    zones=[
        BoundaryZone(name="app", patterns=["**/app/**"]),
        BoundaryZone(name="features", patterns=["**/features/**"]),
        BoundaryZone(name="shared", patterns=["**/shared/**", "**/common/**", "**/lib/**"]),
    ],
    rules=[
        BoundaryRule(**{"from": "app", "allow": ["features", "shared"]}),
        BoundaryRule(**{"from": "features", "allow": ["shared"]}),
        BoundaryRule(**{"from": "shared", "allow": []}),
    ],
)

_PRESETS: dict[str, BoundaryConfig] = {
    "layered": _LAYERED_PRESET,
    "hexagonal": _HEXAGONAL_PRESET,
    "feature-sliced": _FEATURE_SLICED_PRESET,
}


def get_preset(name: str) -> BoundaryConfig | None:
    return _PRESETS.get(name)


def detect_boundary_violations(
    graph: ModuleGraph,
    config: HallowConfig,
) -> list[Finding]:
    severity = config.rules.severity_for(RuleId.BOUNDARY_VIOLATION)
    if severity == Severity.OFF:
        return []

    boundary_config = _resolve_boundary_config(config.boundaries)
    if not boundary_config.zones or not boundary_config.rules:
        return []

    zone_map = _build_zone_map(graph.modules.keys(), boundary_config.zones)
    rule_map = {r.from_zone: set(r.allow) for r in boundary_config.rules}

    findings: list[Finding] = []

    for path in graph.modules:
        source_zone = zone_map.get(path)
        if not source_zone:
            continue

        allowed = rule_map.get(source_zone)
        if allowed is None:
            continue

        for target in graph.imports_of(path):
            target_zone = zone_map.get(target)
            if not target_zone or target_zone == source_zone:
                continue

            if target_zone not in allowed:
                findings.append(
                    Finding(
                        rule=RuleId.BOUNDARY_VIOLATION,
                        severity=severity,
                        message=(
                            f"'{path}' ({source_zone}) imports "
                            f"'{target}' ({target_zone}) — "
                            f"not allowed by boundary rules"
                        ),
                        location=Location(file=path, line=1),
                        suggestion=(
                            f"Zone '{source_zone}' may only import from: "
                            f"{', '.join(sorted(allowed)) or 'nothing'}"
                        ),
                        metadata={
                            "source_zone": source_zone,
                            "target_zone": target_zone,
                            "allowed": sorted(allowed),
                        },
                    )
                )

    return findings


def _resolve_boundary_config(config: BoundaryConfig) -> BoundaryConfig:
    if config.preset and not config.zones:
        preset = _PRESETS.get(config.preset)
        if preset:
            return preset
    return config


def _build_zone_map(paths: dict.keys, zones: list[BoundaryZone]) -> dict[str, str]:
    zone_map: dict[str, str] = {}
    for path in paths:
        posix = str(PurePosixPath(path))
        for zone in zones:
            for pattern in zone.patterns:
                if _match_path(posix, pattern):
                    zone_map[path] = zone.name
                    break
            if path in zone_map:
                break
    return zone_map


def _match_path(path: str, pattern: str) -> bool:
    if fnmatch(path, pattern):
        return True
    if pattern.startswith("**/"):
        stripped = pattern[3:]
        if fnmatch(path, stripped):
            return True
    parts = path.split("/")
    pattern_parts = pattern.replace("**/", "").split("/")
    if pattern_parts:
        key_dir = pattern_parts[0].replace("*", "")
        if key_dir and key_dir in parts:
            return True
    return False
