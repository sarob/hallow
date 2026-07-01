# hallow

Deterministic codebase intelligence for Python.

Dead code detection, duplication, circular dependencies, complexity scoring, architecture boundaries — one tool, zero config.

## Install

```bash
pip install hallow
```

## Usage

```bash
hallow check          # dead code analysis
hallow check --format json
hallow check --ci     # exit 1 on errors
```

## Programmatic API

```python
from hallow.api import detect_dead_code

results = detect_dead_code(root="./my-project")
for finding in results.findings:
    print(f"{finding.location.file}:{finding.location.line} {finding.message}")
```
