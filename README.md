# hallow

**Deterministic codebase intelligence for Python.**

Dead code, duplication, circular dependencies, complexity scoring, architecture boundaries — one tool, zero config, machine-readable output.

Inspired by [fallow](https://github.com/fallow-rs/fallow) (JS/TS), adapted for Python's import system, packaging, and dynamic features.

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Foundation + Dead Code | **Complete** |
| **Phase 2** | Complexity + Health + Duplication | **Complete** |
| **Phase 3** | Boundaries + Plugins + Fix | **Complete** |
| **Phase 4** | Output Formats + Audit + CI | **Complete** |
| **Phase 5** | Security + MCP + Polish | **Complete** |

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
$ hallow check --boundaries layered

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

The source is organised as these modules and packages, one per pipeline stage or surface:

| Module | Role |
|--------|------|
| `hallow.types` | Pydantic models: ModuleInfo, Finding, AnalysisResults, Severity. Pure schema, no logic. |
| `hallow.config` | Config from `.hallowrc.toml` or `pyproject.toml [tool.hallow]`. Zero-config defaults. |
| `hallow.extract` | AST extraction via `ast.parse()`. Produces ModuleInfo per file: imports, exports, `__all__`, complexity. |
| `hallow.graph` | Module graph from ModuleInfo. Import resolution, reachability, cycle detection (Tarjan's SCC). |
| `hallow.core` | Analysis orchestration. One pipeline runs the detectors in sequence: dead code, unused deps, circular imports and boundaries read the module graph; duplication reads the token stream. |
| `hallow.security` | Hardcoded-secret and taint-sink detectors. |
| `hallow.plugins` | Framework awareness. Auto-activated from declared dependencies: Django, Flask, FastAPI, pytest, Celery. |
| `hallow.output` | Formatters: JSON, SARIF, Markdown, CodeClimate, GitHub annotations, compact, human (Rich ANSI). |
| `hallow.cli` | Typer CLI: `hallow check`, `health`, `dupes`, `audit`, `fix`, `security`, `mcp`. |
| `hallow.mcp` | MCP server. 6 tools for agent-driven codebase intelligence. |

## Features

**Dead code detection**
- Unused files (unreachable from entry points)
- Unused imports, functions, classes, variables
- Unused dependencies (declared in `pyproject.toml`, or `requirements.txt` when there is no `[project]` table, but never imported)
- Unlisted dependencies (imported but not declared)
- `__all__` entries that name nothing the module defines or imports

**Complexity & health**
- Per-function cyclomatic + cognitive complexity
- Project-level health score (0-100, A-F grade)
- Maintainability index
- Complexity hotspot identification

**Duplication**
- Clone detection by hashing fixed-size token windows and extending each match to its longest common run
- Four modes, matching fallow's: strict and mild (exact tokens; comments and whitespace are never tokens), weak (string values ignored), semantic (identifiers, strings and numbers ignored, so renamed copies match)
- Upgrading from 0.1.0: `mild` (the default) used to ignore identifiers and literals, and `weak` also ignored operators. The default now reports exact copies only. To keep reporting renamed copies, set `mode = "semantic"` under `[tool.hallow.duplicates]` (or `[duplicates]` in `.hallowrc.toml`)

**Circular dependencies**
- Detection via Tarjan's strongly connected components
- Full cycle chain visualization

**Architecture boundaries**
- Layered, hexagonal, feature-sliced presets
- Custom zone definitions with import rules
- Namespace package awareness

**Framework plugins**
- Auto-detected from declared dependencies (`pyproject.toml` or `requirements*.txt`)
- Django, Flask, FastAPI, pytest, Celery
- Suppresses false positives for framework-registered exports (decorators, entry files, migrations)

**Auto-fix**
- `hallow fix --dry-run` previews safe changes
- `hallow fix --apply` removes unused imports, deletes dead files, strips unused deps from `pyproject.toml` or `requirements*.txt`

**Security**
- Hardcoded secrets (API keys, tokens, passwords, private keys, JWTs)
- Taint sink detection (eval, exec, os.system, subprocess, pickle, SQL injection)
- Safe-context filtering (env vars, config lookups, placeholders)

**Output formats**
- Human (Rich ANSI), JSON, compact
- SARIF v2.1.0 (GitHub code scanning, VS Code)
- Markdown (PR comments and reports)
- CodeClimate JSON (GitLab Code Quality)
- GitHub Actions annotations (`::error`/`::warning`)

**CI integration**
- This repository's own CI (`.github/workflows/ci.yml`): pytest on Python 3.11–3.14 with an 80% line-coverage floor, ruff, and `hallow check` on its own source
- Pre-commit hook configuration
- Exit-code gating: exit 1 on error-severity findings; `--ci` = SARIF + `--fail-on-issues` + `--quiet`
- PR-scoped audit with `hallow audit --changed-since`
- Baseline files (`--save-baseline` / `--baseline`) for regression prevention

**MCP server**
- JSON-RPC 2.0 over stdio — `hallow mcp --stdio`
- 6 tools: analyze, health, complexity, cycles, duplicates, explain
- Agent-driven codebase intelligence via Model Context Protocol

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

## Suppressing findings

Suppress a finding in the source, optionally with a reason after `--`:

```python
import plugin_hooks  # hallow-ignore unused-imports -- registers hooks on import


# hallow-ignore-next-line unused-functions -- called by name from the job runner
def nightly_rollup(): ...


# hallow-ignore-file unused-files
```

`hallow-ignore` applies to its own line, `hallow-ignore-next-line` to the next line of code, and `hallow-ignore-file` to the whole file. Rule names are comma- or space-separated; with none, every rule is suppressed. A suppression that no longer matches a finding is reported as `stale-suppression`. `# noqa` / `# noqa: F401` still suppresses unused imports.

## Finding IDs and baselines

Every finding carries a `finding_id` such as `hl1:unused-functions:81a349a3b9ea3b15`. It is a hash of the rule, the file path and the symbol, never the line, so moving code keeps the id; renaming the file or the symbol changes it. Repeats of the same key get `~1`, `~2`, … in line order. SARIF output carries it as a `partialFingerprints` entry and CodeClimate output as its `fingerprint`.

Baselines record findings by the same line-free key:

```bash
hallow check --save-baseline hallow-baseline.json   # once
hallow check --baseline hallow-baseline.json        # later runs report only new findings
hallow baselines prune --baseline hallow-baseline.json [--check]
```

A baseline stores one entry per occurrence, so N saved entries hide at most N current findings. The JSON output's `baseline_staleness` reports how many entries matched; when a quarter or more match nothing, a warning goes to stderr. `baselines prune` removes entries that no current finding matches and never adds any (`--check` changes nothing and exits 1 if there is something to prune). A file that is not a hallow `check` baseline — including the line-keyed format earlier versions wrote — suppresses nothing and is reported as unrecognised; re-save it.

## Exit codes and JSON output

| Exit code | Meaning |
|---|---|
| 0 | No enforced gate failed: no error-severity findings |
| 1 | An enforced gate failed: error-severity findings (warn-severity too with `--fail-on-issues`), or a stale baseline with `--fail-on-stale-baseline` |
| 2 | Invalid input or runtime error: bad flag value, unreadable config, unknown format |
| 3 | A requested resource is unavailable: the `--config` file does not exist |

`check`, `security` and bare `hallow` exit 1 on error-severity findings without any flag. `--ci` is `--format sarif --fail-on-issues --quiet`; an explicit `--format` wins. `--quiet` silences status messages on stderr, never the output. Treat 0 and 1 as completed runs; do not hide 2 or 3 with `|| true`.

Scripts and agents should use `--format json`. Each command writes one document with:

- `kind` (`check`, `security`, `health`, `dupes`, `baselines-prune`), `schema_version`, `version`, `elapsed_ms`
- `gate_outcomes` — each gate's `status` (`pass`/`warn`/`fail`/`skipped`) and whether it is `enforced`; the run exits 1 when an enforced gate fails
- `next_steps[]` — `{id, command, reason}`, read-only commands that run as written
- per finding: `finding_id`, `rule`, `severity`, `location`, `symbol` and `actions[]` (`{type, auto_fixable, description, ...}`; `auto_fixable` says whether `hallow fix` applies it)

An error under `--format json` is a document on stdout, not a traceback: `{"error": true, "message": "...", "exit_code": 2, "code": "..."}`.

`hallow schema` prints every command, flag, output format, exit code and rule as JSON. `hallow schema --output-schema` prints the JSON Schema of every document, committed as [`docs/output-schema.json`](docs/output-schema.json).

## Python-Specific Design

Hallow is not a port of Fallow — it's built for Python's idioms:

- **`ast.parse()` over OXC** — Python's built-in AST is fast, complete, and zero-dependency. Handles all 3.11+ syntax.
- **Import resolution** — relative imports, namespace packages (PEP 420, no `__init__.py`), conditional imports (`try/except`), function-level imports.
- **No explicit exports** — Python uses `__all__` (optional) and convention (leading underscore). Hallow treats all public module-level names as exports unless `__all__` narrows the surface.
- **Dynamic features** — `getattr()`, `**kwargs`, metaclasses, decorator registries. Framework plugins handle common patterns; suppression comments handle the rest.
- **`pyproject.toml` over `package.json`** — dependency parsing from `[project.dependencies]`, `[project.optional-dependencies]`, `requirements.txt` fallback.
- **Parallelism via `ProcessPoolExecutor`** — Python's GIL makes threading insufficient for CPU-bound AST parsing, so files are parsed in worker processes when there are 50 or more (`extract_modules_parallel`).

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
