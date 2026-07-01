# hallow

**Deterministic codebase intelligence for Python.**

Dead code, duplication, circular dependencies, complexity scoring, architecture boundaries — one tool, zero config, machine-readable output.

Inspired by [fallow](https://github.com/fallow-rs/fallow) (JS/TS), adapted for Python's import system, packaging, and dynamic features.

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Foundation + Dead Code | **Complete** |
| **Phase 2** | Complexity + Health + Duplication | In progress |
| Phase 3 | Boundaries + Plugins + Fix | Planned |
| Phase 4 | Output Formats + Audit + CI | Planned |
| Phase 5 | Security + MCP + Polish | Planned |

## Install

```bash
pip install hallow
```

Or from source:

```bash
git clone https://github.com/sarob/hallow.git
cd hallow
pip install -e ".[dev]"
```

## Usage

```bash
# Full scan — dead code + duplication + health
$ hallow

# Dead code only
$ hallow check --format sarif --output findings.sarif

# Health score
$ hallow health --score
  Health: 82/100 (B)
  Complexity hotspots: 3
  Maintainability: 71.4 avg

# Duplicate detection
$ hallow dupes --mode mild --min-lines 8

# PR audit — only flag new issues in changed files
$ hallow audit --changed-since origin/main --ci

# Auto-fix
$ hallow fix --dry-run
  Would remove: 4 unused imports, 2 dead files, 1 unused dep

# Architecture boundary check
$ hallow check --boundaries --preset layered

# Security scan
$ hallow security

# MCP server
$ hallow mcp --stdio
```

## Programmatic API

```python
from hallow.api import detect_dead_code, detect_circular_dependencies, compute_complexity

# Dead code analysis
results = detect_dead_code(root="./my-project")
for finding in results.findings:
    print(f"{finding.location.file}:{finding.location.line} {finding.message}")

# Circular imports
cycles = detect_circular_dependencies(root="./my-project")
for cycle in cycles:
    print(" -> ".join(cycle["modules"]))

# Complexity metrics
for func in compute_complexity(root="./my-project"):
    if func["cyclomatic"] > 10:
        print(f"{func['file']}:{func['name']} cc={func['cyclomatic']}")
```

## Why Hallow

No single tool covers Python the way Fallow covers JS/TS. Today you need vulture for dead code, radon for complexity, import-linter for circular deps, pylint for duplication — each with its own config, output format, and no awareness of the others. None detect unused files, unused dependencies, or offer project-level quality scoring.

Hallow runs all analyses through one pipeline, one config, one data model:

```
Discover → Extract → Resolve → Graph → Analyze → Report
```

It uses Python's built-in `ast` module — no external parser — and produces unified output in JSON, SARIF, Markdown, or Rich ANSI.

## Architecture

Nine modules mirroring Fallow's crate structure:

| Module | Role |
|--------|------|
| `hallow.types` | Pydantic models: ModuleInfo, Finding, AnalysisResults, Severity. Pure schema, no logic. |
| `hallow.config` | Config from `.hallowrc.toml` or `pyproject.toml [tool.hallow]`. Zero-config defaults. |
| `hallow.extract` | AST extraction via `ast.parse()`. Produces ModuleInfo per file: imports, exports, `__all__`, complexity. |
| `hallow.graph` | Module graph from ModuleInfo. Import resolution, reachability, cycle detection (Tarjan's SCC). |
| `hallow.core` | Analysis orchestration. Detectors run in parallel: dead code, unused deps, circular imports, duplication, boundaries. |
| `hallow.plugins` | Framework awareness. Auto-activated from `pyproject.toml` deps: Django, Flask, FastAPI, pytest, Celery. |
| `hallow.output` | Formatters: JSON, SARIF, Markdown, CodeClimate, GitHub annotations, compact, human (Rich ANSI). |
| `hallow.cli` | Typer CLI: `hallow check`, `health`, `dupes`, `audit`, `fix`, `security`, `watch`. |
| `hallow.mcp` | MCP server. 12+ tools for agent-driven codebase intelligence. |

## Features

**Dead code detection**
- Unused files (unreachable from entry points)
- Unused imports, functions, classes, variables
- Unused dependencies (declared in `pyproject.toml` but never imported)
- Unlisted dependencies (imported but not declared)
- Unused `__all__` entries

**Complexity & health**
- Per-function cyclomatic + cognitive complexity
- Project-level health score (0-100, A-F grade)
- Maintainability index
- Complexity hotspot identification

**Duplication**
- Clone detection via tokenization + suffix array + LCP
- Four modes: strict (exact), mild (normalized identifiers), weak (structure only), semantic

**Circular dependencies**
- Detection via Tarjan's strongly connected components
- Full cycle chain visualization

**Architecture boundaries** *(planned)*
- Layered, hexagonal, feature-sliced presets
- Custom zone definitions with import rules
- Namespace package awareness

**Security** *(planned)*
- Hardcoded secrets (API keys, tokens, passwords)
- Taint propagation from user input to dangerous sinks

**Auto-fix** *(planned)*
- Remove unused imports
- Delete dead files
- Remove unused dependencies from `pyproject.toml`

**Output formats**
- Human (Rich ANSI), JSON, compact
- SARIF, Markdown, CodeClimate, GitHub annotations *(planned)*

**CI integration** *(planned)*
- GitHub Action
- Pre-commit hook
- `--ci` exit code gating
- PR-scoped audit with `--changed-since`
- Baseline files for regression prevention

**MCP server** *(planned)*
- 12+ tools: analyze, audit, dupes, health, security, explain, fix, impact

## Configuration

Zero config by default. Customize via `.hallowrc.toml` or `pyproject.toml`:

```toml
# .hallowrc.toml
entry = ["src/main.py"]
ignore_patterns = ["**/migrations/**"]

[rules]
unused-files = "error"      # error (default), warn, off
unused-imports = "error"
unused-variables = "warn"
circular-dependencies = "error"
high-complexity = "warn"

[duplicates]
mode = "mild"               # strict, mild, weak, semantic
min_tokens = 50
min_lines = 5

[health]
max_cyclomatic = 20
max_cognitive = 15
```

Or in `pyproject.toml`:

```toml
[tool.hallow]
entry = ["src/main.py"]

[tool.hallow.rules]
unused-files = "error"
unused-imports = "warn"
```

## Python-Specific Design

Hallow is not a port of Fallow — it's built for Python's idioms:

- **`ast.parse()` over OXC** — Python's built-in AST is fast, complete, and zero-dependency. Handles all 3.11+ syntax.
- **Import resolution** — relative imports, namespace packages, conditional imports (`try/except`), lazy imports, `sys.path` manipulation.
- **No explicit exports** — Python uses `__all__` (optional) and convention (leading underscore). Hallow treats all public module-level names as exports unless `__all__` narrows the surface.
- **Dynamic features** — `getattr()`, `**kwargs`, metaclasses, decorator registries. Framework plugins handle common patterns; suppression comments handle the rest.
- **`pyproject.toml` over `package.json`** — dependency parsing from `[project.dependencies]`, `[project.optional-dependencies]`, `requirements.txt` fallback.
- **Parallelism via `ProcessPoolExecutor`** — Python's GIL makes threading insufficient for CPU-bound AST parsing. Content-hash caching keeps re-analysis fast.

## Stack

- **Python 3.11+** — `tomllib`, `StrEnum`, `ExceptionGroup`
- **Typer** — typed CLI with Rich integration
- **Pydantic v2** — config validation, result schemas, JSON serialization
- **Rich** — ANSI terminal output
- **Hatch** — build backend
- **pytest** — test suite

## Development

```bash
git clone https://github.com/sarob/hallow.git
cd hallow
uv venv && uv pip install -e ".[dev]"

uv run pytest              # run tests
uv run ruff check .        # lint
uv run ruff format .       # format
uv run hallow check --root .  # self-analysis
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
