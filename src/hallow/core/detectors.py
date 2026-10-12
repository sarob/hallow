"""Dead code detectors — each returns a list of Findings."""

from __future__ import annotations

import sys
from collections import defaultdict

from hallow.config.loader import HallowConfig
from hallow.core.dependencies import (
    dependency_manifest,
    normalize_name,
    read_declared_dependencies,
)
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

# Import name -> normalized PyPI distribution name, for packages whose import
# name differs from the distribution declared in pyproject. Used by both the
# unused- and unlisted-dependency detectors so aliases resolve consistently.
_KNOWN_IMPORT_ALIASES: dict[str, str] = {
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

    declared = [d for d in read_declared_dependencies(config.root) if not d.optional]
    if not declared:
        return []

    normalized_imports = {normalize_name(i) for i in graph.all_external_imports()}
    ignored = {normalize_name(d) for d in config.ignore_dependencies}

    findings: list[Finding] = []
    for dep in declared:
        if dep.name in ignored:
            continue

        is_used = dep.name in normalized_imports
        if not is_used:
            for imp, alias in _KNOWN_IMPORT_ALIASES.items():
                if alias == dep.name and imp in normalized_imports:
                    is_used = True
                    break

        if not is_used:
            findings.append(
                Finding(
                    rule=RuleId.UNUSED_DEPENDENCIES,
                    severity=severity,
                    message=f"Dependency '{dep.name}' is declared but never imported",
                    location=Location(file=dep.file, line=dep.line),
                    suggestion=f"Remove '{dep.name}' from {dep.file} if no longer needed",
                    fix=FixAction(kind="remove_dependency", target=dep.name),
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

    manifest = dependency_manifest(config.root)
    if manifest is None:
        return []

    declared = {d.name for d in read_declared_dependencies(config.root)}
    stdlib = _get_stdlib_modules()

    # First import site of each external top-level name, so the finding points
    # at the code that introduced the import.
    sites: dict[str, tuple[str, int, int]] = {}
    for path in sorted(graph.modules):
        external = graph.external_imports(path)
        for imp in sorted(graph.modules[path].imports, key=lambda i: i.line):
            top = imp.module.split(".")[0] if imp.module and not imp.is_relative else ""
            if top in external and top not in sites:
                sites[top] = (path, imp.line, imp.col)

    findings: list[Finding] = []
    for imp in sorted(graph.all_external_imports()):
        normalized = normalize_name(imp)
        if normalized in declared:
            continue
        # The package may be declared under a distribution name that differs
        # from the import name (e.g. `import jwt` -> PyJWT, `import cv2` ->
        # opencv-python).
        alias = _KNOWN_IMPORT_ALIASES.get(normalized)
        if alias and alias in declared:
            continue
        if imp in stdlib:
            continue
        if imp.startswith("_"):
            continue

        file, line, col = sites.get(imp, (manifest, 1, 0))
        findings.append(
            Finding(
                rule=RuleId.UNLISTED_DEPENDENCIES,
                severity=severity,
                message=f"'{imp}' is imported but not declared in {manifest}",
                location=Location(file=file, line=line, col=col),
                suggestion=f"Add '{imp}' to the dependencies in {manifest}",
            )
        )

    return findings


def detect_unused_all_entries(
    graph: ModuleGraph,
    config: HallowConfig,
) -> list[Finding]:
    """`__all__` entries that name nothing the module binds.

    `from module import *` raises AttributeError on such an entry. In a
    package `__init__.py`, an entry may name a submodule, which star-import
    loads, so those are allowed. Modules that bind names dynamically
    (`from x import *`, `globals()`, `exec`, `setattr`, `vars`) or define a
    module `__getattr__` are skipped.
    """
    severity = config.rules.severity_for(RuleId.UNUSED_ALL_ENTRIES)
    if severity == Severity.OFF:
        return []

    findings: list[Finding] = []
    for path, module in graph.modules.items():
        if module.all_list is None or module.has_dynamic_bindings:
            continue
        if "__getattr__" in module.bound_names:
            continue

        package_dir = path.rsplit("/", 1)[0] if "/" in path else ""
        for name in module.all_list:
            if name in module.bound_names:
                continue
            if module.is_init:
                base = f"{package_dir}/{name}" if package_dir else name
                if f"{base}.py" in graph.modules or f"{base}/__init__.py" in graph.modules:
                    continue
            findings.append(
                Finding(
                    rule=RuleId.UNUSED_ALL_ENTRIES,
                    severity=severity,
                    message=f"'{name}' is listed in __all__ but not defined or imported",
                    location=Location(file=path, line=module.all_line or 1),
                    suggestion=f"Remove '{name}' from __all__ or define it",
                    fix=FixAction(kind="remove_from_all", target=name, auto_fixable=False),
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
