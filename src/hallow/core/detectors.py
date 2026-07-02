"""Dead code detectors — each returns a list of Findings."""

from __future__ import annotations

import sys
import tomllib
from collections import defaultdict

from hallow.config.loader import HallowConfig
from hallow.graph.module_graph import ModuleGraph
from hallow.types import (
    Finding,
    FixAction,
    Location,
    RuleId,
    Severity,
)

# Decorators that mean a symbol is registered/consumed by a framework or the
# runtime rather than imported by name — so it must not be reported as unused.
# Matched on the decorator's leaf name (e.g. `@app.command(...)` -> "command",
# `@register("x")` -> "register").
_ENTRYPOINT_DECORATORS = frozenset(
    {
        # Typer / Click
        "command",
        "callback",
        "group",
        # FastAPI / Flask / Starlette routing
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
        "route",
        "websocket",
        "middleware",
        "exception_handler",
        "on_event",
        # Celery / task queues
        "task",
        "shared_task",
        "periodic_task",
        # generic registries / plugin systems / pytest
        "register",
        "hookimpl",
        "fixture",
        # bare app/router objects and property accessors / interface markers
        "app",
        "router",
        "setter",
        "getter",
        "deleter",
        "abstractmethod",
        "override",
        "overload",
    }
)


def detect_unused_files(
    graph: ModuleGraph,
    config: HallowConfig,
) -> list[Finding]:
    severity = config.rules.severity_for(RuleId.UNUSED_FILES)
    if severity == Severity.OFF:
        return []

    findings: list[Finding] = []
    for path in graph.unreachable_files():
        module = graph.modules[path]
        if module.is_test or module.is_conftest:
            continue
        if module.line_count == 0:
            continue
        findings.append(
            Finding(
                rule=RuleId.UNUSED_FILES,
                severity=severity,
                message="File is not imported by any other module",
                location=Location(file=path, line=1),
                suggestion="Delete this file if it is no longer needed",
                fix=FixAction(kind="delete_file", target=path),
            )
        )
    return findings


def detect_unused_imports(
    graph: ModuleGraph,
    config: HallowConfig,
) -> list[Finding]:
    severity = config.rules.severity_for(RuleId.UNUSED_IMPORTS)
    if severity == Severity.OFF:
        return []

    findings: list[Finding] = []

    for path, module in graph.modules.items():
        if module.is_init:
            continue

        # A name actually used anywhere in the module body (as a value). Names
        # re-exported via __all__ also count as used.
        referenced = module.referenced_names
        exported = set(module.all_list or [])

        for imp in module.imports:
            if imp.is_type_checking:
                continue
            # `from __future__ import ...` are compiler directives, never
            # referenced as names — they must never be reported as unused.
            if imp.module == "__future__":
                continue
            # Respect a noqa/F401 suppression on the import line (intentional
            # re-exports and side-effect imports).
            if imp.line in module.noqa_lines:
                continue

            if imp.is_from_import:
                bound_list = imp.bound_names or imp.names
                for src_name, bound in zip(imp.names, bound_list, strict=False):
                    if src_name == "*":
                        continue
                    if bound in referenced or bound in exported or src_name in exported:
                        continue
                    findings.append(
                        Finding(
                            rule=RuleId.UNUSED_IMPORTS,
                            severity=severity,
                            message=f"'{src_name}' imported from '{imp.module}' is unused",
                            location=Location(file=path, line=imp.line, col=imp.col),
                            fix=FixAction(kind="remove_import", target=src_name),
                        )
                    )
            else:
                # `import a.b.c` binds `a`; `import a.b as x` binds `x`.
                bound_list = imp.bound_names or [imp.alias or imp.module.split(".")[0]]
                for bound in bound_list:
                    if bound in referenced or bound in exported:
                        continue
                    findings.append(
                        Finding(
                            rule=RuleId.UNUSED_IMPORTS,
                            severity=severity,
                            message=f"'{bound}' (import {imp.module}) is unused",
                            location=Location(file=path, line=imp.line, col=imp.col),
                            fix=FixAction(kind="remove_import", target=bound),
                        )
                    )

    return findings


def detect_unused_exports(
    graph: ModuleGraph,
    config: HallowConfig,
) -> list[Finding]:
    func_severity = config.rules.severity_for(RuleId.UNUSED_FUNCTIONS)
    class_severity = config.rules.severity_for(RuleId.UNUSED_CLASSES)
    var_severity = config.rules.severity_for(RuleId.UNUSED_VARIABLES)

    if all(s == Severity.OFF for s in [func_severity, class_severity, var_severity]):
        return []

    findings: list[Finding] = []

    imported_symbols: dict[str, set[str]] = defaultdict(set)
    for path in graph.modules:
        for target in graph.imports_of(path):
            symbols = graph.symbols_imported_from(path, target)
            imported_symbols[target].update(symbols)

    for path, module in graph.modules.items():
        if module.is_init or module.is_test or module.is_conftest:
            continue
        if module.is_main:
            continue

        consumed = imported_symbols.get(path, set())
        has_all = module.all_list is not None

        for export in module.exports:
            if export.is_dunder or export.is_private:
                continue
            if has_all and export.name not in (module.all_list or []):
                continue

            # Only report exports in modules that are part of the import graph;
            # orphan modules are covered by the unused-file rule instead.
            if export.name not in consumed and not graph.importers_of(path):
                continue

            # Used by another module.
            if export.name in consumed:
                continue

            # Used within its own module (called, referenced as a value, used in
            # an annotation, etc.). Without this, internally-used helpers, config
            # classes referenced as field types, and module-level `logger`s were
            # all false-flagged.
            if export.name in module.referenced_names:
                continue

            # Consumed by an importer via module-attribute access rather than a
            # named import — e.g. main.py does `from . import bootstrap` then
            # `app.command(...)(bootstrap.bootstrap_agent)`. Not a symbol edge, so
            # check importers' accessed attributes.
            if any(
                (imp_mod := graph.modules.get(imp)) is not None
                and export.name in imp_mod.accessed_attributes
                for imp in graph.importers_of(path)
            ):
                continue

            # Registered via a framework/entry-point decorator (Typer command,
            # FastAPI route, `@register(...)`, ...) — live code, not dead.
            if any(d in _ENTRYPOINT_DECORATORS for d in export.decorators):
                continue

            if export.kind == "function":
                sev = func_severity
                rule = RuleId.UNUSED_FUNCTIONS
            elif export.kind == "class":
                sev = class_severity
                rule = RuleId.UNUSED_CLASSES
            else:
                sev = var_severity
                rule = RuleId.UNUSED_VARIABLES

            if sev == Severity.OFF:
                continue

            findings.append(
                Finding(
                    rule=rule,
                    severity=sev,
                    message=f"'{export.name}' is defined but never used or imported",
                    location=Location(file=path, line=export.line, col=export.col),
                    suggestion=f"Remove '{export.name}' if it is no longer used",
                    fix=FixAction(kind="remove_export", target=export.name, auto_fixable=False),
                )
            )

    return findings


def detect_unused_dependencies(
    graph: ModuleGraph,
    config: HallowConfig,
) -> list[Finding]:
    severity = config.rules.severity_for(RuleId.UNUSED_DEPENDENCIES)
    if severity == Severity.OFF:
        return []

    pyproject = config.root / "pyproject.toml"
    if not pyproject.exists():
        return []

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return []

    declared_deps: list[str] = []
    project = data.get("project", {})
    raw_deps = project.get("dependencies", [])
    for dep in raw_deps:
        name = dep.split(">")[0].split("<")[0].split("=")[0].split("[")[0].split(";")[0].strip()
        if name:
            declared_deps.append(name.lower().replace("-", "_").replace(".", "_"))

    all_imports = graph.all_external_imports()
    normalized_imports = {i.lower().replace("-", "_").replace(".", "_") for i in all_imports}

    known_aliases: dict[str, str] = {
        "pil": "pillow",
        "cv2": "opencv_python",
        "sklearn": "scikit_learn",
        "yaml": "pyyaml",
        "gi": "pygobject",
        "attr": "attrs",
        "bs4": "beautifulsoup4",
        "dateutil": "python_dateutil",
        "dotenv": "python_dotenv",
        "jwt": "pyjwt",
        "serial": "pyserial",
        "usb": "pyusb",
        "magic": "python_magic",
        "lxml": "lxml",
        "google": "google_cloud",
    }

    findings: list[Finding] = []
    for dep in declared_deps:
        if dep in config.ignore_dependencies:
            continue

        is_used = dep in normalized_imports
        if not is_used:
            for imp, alias in known_aliases.items():
                if alias == dep and imp in normalized_imports:
                    is_used = True
                    break

        if not is_used:
            findings.append(
                Finding(
                    rule=RuleId.UNUSED_DEPENDENCIES,
                    severity=severity,
                    message=f"Dependency '{dep}' is declared but never imported",
                    location=Location(file="pyproject.toml", line=1),
                    suggestion=f"Remove '{dep}' from [project.dependencies] if no longer needed",
                    fix=FixAction(kind="remove_dependency", target=dep),
                )
            )

    return findings


def detect_unlisted_dependencies(
    graph: ModuleGraph,
    config: HallowConfig,
) -> list[Finding]:
    severity = config.rules.severity_for(RuleId.UNLISTED_DEPENDENCIES)
    if severity == Severity.OFF:
        return []

    pyproject = config.root / "pyproject.toml"
    if not pyproject.exists():
        return []

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return []

    declared: set[str] = set()
    project = data.get("project", {})
    for dep in project.get("dependencies", []):
        name = dep.split(">")[0].split("<")[0].split("=")[0].split("[")[0].split(";")[0].strip()
        if name:
            declared.add(name.lower().replace("-", "_").replace(".", "_"))

    for group_deps in project.get("optional-dependencies", {}).values():
        for dep in group_deps:
            name = dep.split(">")[0].split("<")[0].split("=")[0].split("[")[0].split(";")[0].strip()
            if name:
                declared.add(name.lower().replace("-", "_").replace(".", "_"))

    stdlib = _get_stdlib_modules()

    all_imports = graph.all_external_imports()
    findings: list[Finding] = []

    for imp in sorted(all_imports):
        normalized = imp.lower().replace("-", "_").replace(".", "_")
        if normalized in declared:
            continue
        if imp in stdlib:
            continue
        if imp.startswith("_"):
            continue

        findings.append(
            Finding(
                rule=RuleId.UNLISTED_DEPENDENCIES,
                severity=severity,
                message=f"'{imp}' is imported but not declared in pyproject.toml",
                location=Location(file="pyproject.toml", line=1),
                suggestion=f"Add '{imp}' to [project.dependencies]",
            )
        )

    return findings


def _get_stdlib_modules() -> set[str]:
    if hasattr(sys, "stdlib_module_names"):
        return sys.stdlib_module_names  # type: ignore[return-value]
    return {
        "abc",
        "aifc",
        "argparse",
        "array",
        "ast",
        "asynchat",
        "asyncio",
        "asyncore",
        "atexit",
        "base64",
        "bdb",
        "binascii",
        "binhex",
        "bisect",
        "builtins",
        "bz2",
        "calendar",
        "cgi",
        "cgitb",
        "chunk",
        "cmath",
        "cmd",
        "code",
        "codecs",
        "codeop",
        "collections",
        "colorsys",
        "compileall",
        "concurrent",
        "configparser",
        "contextlib",
        "contextvars",
        "copy",
        "copyreg",
        "cProfile",
        "crypt",
        "csv",
        "ctypes",
        "curses",
        "dataclasses",
        "datetime",
        "dbm",
        "decimal",
        "difflib",
        "dis",
        "distutils",
        "doctest",
        "email",
        "encodings",
        "enum",
        "errno",
        "faulthandler",
        "fcntl",
        "filecmp",
        "fileinput",
        "fnmatch",
        "formatter",
        "fractions",
        "ftplib",
        "functools",
        "gc",
        "getopt",
        "getpass",
        "gettext",
        "glob",
        "grp",
        "gzip",
        "hashlib",
        "heapq",
        "hmac",
        "html",
        "http",
        "idlelib",
        "imaplib",
        "imghdr",
        "imp",
        "importlib",
        "inspect",
        "io",
        "ipaddress",
        "itertools",
        "json",
        "keyword",
        "lib2to3",
        "linecache",
        "locale",
        "logging",
        "lzma",
        "mailbox",
        "mailcap",
        "marshal",
        "math",
        "mimetypes",
        "mmap",
        "modulefinder",
        "multiprocessing",
        "netrc",
        "nis",
        "nntplib",
        "numbers",
        "operator",
        "optparse",
        "os",
        "ossaudiodev",
        "parser",
        "pathlib",
        "pdb",
        "pickle",
        "pickletools",
        "pipes",
        "pkgutil",
        "platform",
        "plistlib",
        "poplib",
        "posix",
        "posixpath",
        "pprint",
        "profile",
        "pstats",
        "pty",
        "pwd",
        "py_compile",
        "pyclbr",
        "pydoc",
        "queue",
        "quopri",
        "random",
        "re",
        "readline",
        "reprlib",
        "resource",
        "rlcompleter",
        "runpy",
        "sched",
        "secrets",
        "select",
        "selectors",
        "shelve",
        "shlex",
        "shutil",
        "signal",
        "site",
        "smtpd",
        "smtplib",
        "sndhdr",
        "socket",
        "socketserver",
        "spwd",
        "sqlite3",
        "sre_compile",
        "sre_constants",
        "sre_parse",
        "ssl",
        "stat",
        "statistics",
        "string",
        "stringprep",
        "struct",
        "subprocess",
        "sunau",
        "symtable",
        "sys",
        "sysconfig",
        "syslog",
        "tabnanny",
        "tarfile",
        "telnetlib",
        "tempfile",
        "termios",
        "test",
        "textwrap",
        "threading",
        "time",
        "timeit",
        "tkinter",
        "token",
        "tokenize",
        "tomllib",
        "trace",
        "traceback",
        "tracemalloc",
        "tty",
        "turtle",
        "turtledemo",
        "types",
        "typing",
        "unicodedata",
        "unittest",
        "urllib",
        "uu",
        "uuid",
        "venv",
        "warnings",
        "wave",
        "weakref",
        "webbrowser",
        "winreg",
        "winsound",
        "wsgiref",
        "xdrlib",
        "xml",
        "xmlrpc",
        "zipapp",
        "zipfile",
        "zipimport",
        "zlib",
        "_thread",
    }


def detect_circular_imports(
    graph: ModuleGraph,
    config: HallowConfig,
) -> list[Finding]:
    severity = config.rules.severity_for(RuleId.CIRCULAR_DEPENDENCIES)
    if severity == Severity.OFF:
        return []

    findings: list[Finding] = []
    for cycle in graph.find_cycles():
        chain = " → ".join(cycle.modules + [cycle.modules[0]])
        findings.append(
            Finding(
                rule=RuleId.CIRCULAR_DEPENDENCIES,
                severity=severity,
                message=f"Circular import: {chain}",
                location=Location(file=cycle.modules[0], line=1),
                suggestion="Break the cycle by extracting shared types or using lazy imports",
                metadata={"modules": cycle.modules, "edges": [list(e) for e in cycle.edges]},
            )
        )

    return findings
