"""Parse .py files via ast.parse() and produce ModuleInfo."""

from __future__ import annotations

import ast
import hashlib
import io
import re
import tokenize
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from hallow.types import ExportInfo, FunctionComplexity, ImportInfo, ModuleInfo, Suppression

# ruff/flake8 F401 == "imported but unused"; hallow maps it to `unused-imports`.
_NOQA_RE = re.compile(r"#\s*noqa(?::\s*(?P<codes>[A-Z0-9, ]+))?", re.IGNORECASE)
_UNUSED_IMPORT_CODES = {"F401"}


def _hash_content(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def _collect_noqa_lines(source: str) -> set[int]:
    """Line numbers whose `# noqa` suppresses an unused-import finding.

    A bare `# noqa` suppresses everything; `# noqa: F401` (or a code list that
    includes F401) suppresses unused-imports specifically.
    """
    lines: set[int] = set()
    for i, line in enumerate(source.splitlines(), 1):
        m = _NOQA_RE.search(line)
        if not m:
            continue
        codes = m.group("codes")
        if codes is None or {c.strip().upper() for c in codes.split(",")} & _UNUSED_IMPORT_CODES:
            lines.add(i)
    return lines


# `# hallow-ignore[-next-line|-file] [rule[, rule...]] [-- reason]`
_SUPPRESSION_RE = re.compile(
    r"^#\s*hallow-ignore(?P<kind>-next-line|-file)?(?![\w-])\s*(?P<rest>.*)$"
)


def _collect_suppressions(source: str) -> list[Suppression]:
    """Parse `# hallow-ignore` comments.

    Comments are read from the token stream, so the marker inside a string
    literal is not a suppression.
    """
    if "hallow-ignore" not in source:
        return []

    comments: list[tuple[int, str]] = []
    code_lines: set[int] = set()
    non_code = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comments.append((tok.start[0], tok.string))
            elif tok.type not in non_code:
                code_lines.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):
        return []

    sorted_code = sorted(code_lines)
    result: list[Suppression] = []
    for line, text in comments:
        m = _SUPPRESSION_RE.match(text)
        if not m:
            continue
        rules_part, _, reason = m.group("rest").partition("--")
        rules = [r for r in re.split(r"[,\s]+", rules_part.strip()) if r]
        kind_suffix = m.group("kind")
        if kind_suffix == "-file":
            kind, target = "file", None
        elif kind_suffix == "-next-line":
            kind = "next-line"
            target = next((n for n in sorted_code if n > line), None)
        else:
            kind, target = "line", line
        result.append(
            Suppression(
                kind=kind,
                line=line,
                target_line=target,
                rules=rules,
                reason=reason.strip() or None,
            )
        )
    return result


def _is_type_checking_block(node: ast.AST) -> bool:
    if isinstance(node, ast.If):
        test = node.test
        if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
            return True
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            return True
    return False


class _ImportCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[ImportInfo] = []
        self._in_type_checking = False
        self._in_try_except = False
        self._in_conditional = False

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_block(node):
            prev = self._in_type_checking
            self._in_type_checking = True
            self.generic_visit(node)
            self._in_type_checking = prev
        else:
            prev = self._in_conditional
            self._in_conditional = True
            self.generic_visit(node)
            self._in_conditional = prev

    def visit_Try(self, node: ast.Try) -> None:
        prev = self._in_try_except
        self._in_try_except = True
        self.generic_visit(node)
        self._in_try_except = prev

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            # `import a.b.c` binds the top-level name `a`; `import a.b as x` binds `x`.
            bound = alias.asname or alias.name.split(".")[0]
            self.imports.append(
                ImportInfo(
                    module=alias.name,
                    names=[],
                    bound_names=[bound],
                    alias=alias.asname,
                    is_from_import=False,
                    is_relative=False,
                    level=0,
                    is_type_checking=self._in_type_checking,
                    is_conditional=self._in_conditional,
                    is_try_except=self._in_try_except,
                    line=node.lineno,
                    col=node.col_offset,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        level = node.level or 0
        names = [a.name for a in node.names] if node.names else []
        bound_names = [a.asname or a.name for a in node.names] if node.names else []
        self.imports.append(
            ImportInfo(
                module=module,
                names=names,
                bound_names=bound_names,
                is_from_import=True,
                is_relative=level > 0,
                level=level,
                is_type_checking=self._in_type_checking,
                is_conditional=self._in_conditional,
                is_try_except=self._in_try_except,
                line=node.lineno,
                col=node.col_offset,
            )
        )


def _collect_referenced_names(tree: ast.Module) -> set[str]:
    """Every name loaded (used as a value) anywhere in the module.

    Import statements bind names via ``alias`` nodes, not ``ast.Name``, and
    def/class names are strings — so this set is pure usage, never self-reference.
    For ``a.b.c`` the root ``a`` is a ``Name`` load, so dotted-attribute use is
    captured. Decorators, annotations, f-string expressions, etc. are included.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
    return names


def _collect_accessed_attributes(tree: ast.Module) -> set[str]:
    """Attribute names accessed as ``obj.attr`` (loads).

    Lets the dead-code detector recognize symbols consumed via module-attribute
    access — e.g. ``from . import bootstrap`` then ``bootstrap.bootstrap_agent``,
    which is not a named import and so is absent from the graph's symbol edges.
    """
    attrs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            attrs.add(node.attr)
    return attrs


def _extract_all_list(tree: ast.Module) -> tuple[list[str] | None, int]:
    """The literal `__all__` list and the line it is assigned on."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "__all__"
                    and isinstance(node.value, (ast.List, ast.Tuple))
                ):
                    return [
                        elt.value
                        for elt in node.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ], node.lineno
    return None, 0


_DYNAMIC_BINDERS = frozenset({"globals", "exec", "setattr", "vars"})


# list methods that change the contents of `__all__`
_MUTATING_METHODS = frozenset(
    {"append", "extend", "insert", "remove", "pop", "clear", "__iadd__", "__imul__",
     "__setitem__", "__delitem__"}
)  # fmt: skip


def _all_is_modified(tree: ast.Module) -> bool:
    """True unless module-level `__all__` is bound exactly once and never changed.

    Counts as a change: a second module-level binding (assignment, walrus,
    annotated assignment), augmented assignment (`+=`, `*=`), item or slice
    assignment or deletion (`__all__[:] = ...`, `del __all__[:]`), `del
    __all__`, or a mutating list method (`append`, `remove`, `clear`, ...).
    Bindings inside a function count only when the function declares
    `global __all__`; read-only methods (`count`, `index`, `copy`) do not.
    """
    bindings = 0

    def visit(node: ast.AST, in_function: bool, global_all: bool) -> bool:
        nonlocal bindings
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            declares = any(
                isinstance(n, ast.Global) and "__all__" in n.names for n in ast.walk(node)
            )
            return any(visit(c, True, declares) for c in ast.iter_child_nodes(node))
        if isinstance(node, ast.ClassDef):
            return any(visit(c, in_function, global_all) for c in ast.iter_child_nodes(node))

        module_binding = not in_function or global_all
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if _names_all(t) and module_binding:
                    bindings += 1
                if isinstance(t, ast.Subscript) and _names_all(t.value):
                    return True
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            binds = isinstance(node, ast.NamedExpr) or node.value is not None
            if _names_all(node.target) and module_binding and binds:
                bindings += 1
        elif isinstance(node, ast.AugAssign):
            if _names_all(node.target) or (
                isinstance(node.target, ast.Subscript) and _names_all(node.target.value)
            ):
                return True
        elif isinstance(node, ast.Delete):
            for t in node.targets:
                if (_names_all(t) and module_binding) or (
                    isinstance(t, ast.Subscript) and _names_all(t.value)
                ):
                    return True
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and _names_all(node.func.value)
            and node.func.attr in _MUTATING_METHODS
        ):
            return True
        return any(visit(c, in_function, global_all) for c in ast.iter_child_nodes(node))

    if visit(tree, False, False):
        return True
    return bindings > 1


def _names_all(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "__all__"


def _collect_module_bindings(tree: ast.Module) -> tuple[set[str], bool]:
    """Names bound at module scope, and whether the module binds names dynamically.

    Walks every module-level statement including the bodies of `if`, `try`,
    `with`, `for`, `while` and `match`, but not function or class bodies. Errs
    toward over-reporting bindings: a name counted as bound can only hide an
    `__all__` finding, never invent one.
    """
    bound: set[str] = set()
    dynamic = False

    def add_target(target: ast.AST) -> None:
        for n in ast.walk(target):
            if isinstance(n, ast.Name):
                bound.add(n.id)

    def visit(stmts: list[ast.stmt]) -> None:
        nonlocal dynamic
        for node in stmts:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
                continue
            if isinstance(node, ast.Import):
                for a in node.names:
                    bound.add(a.asname or a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == "*":
                        dynamic = True
                    else:
                        bound.add(a.asname or a.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    add_target(t)
            elif isinstance(node, ast.AnnAssign):
                # `x: int` alone declares an annotation but binds no value
                if node.value is not None:
                    add_target(node.target)
            elif isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor)):
                add_target(node.target)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        add_target(item.optional_vars)
            elif type(node).__name__ == "TypeAlias":
                add_target(node.name)  # type: ignore[attr-defined]

            for field in ("body", "orelse", "finalbody"):
                visit(getattr(node, field, []) or [])
            for handler in getattr(node, "handlers", []) or []:
                if handler.name:
                    bound.add(handler.name)
                visit(handler.body)
            if isinstance(node, ast.Match):
                for case in node.cases:
                    for p in ast.walk(case.pattern):
                        name = getattr(p, "name", None) or getattr(p, "rest", None)
                        if isinstance(name, str):
                            bound.add(name)
                    visit(case.body)

    visit(tree.body)

    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            bound.update(node.names)
        elif isinstance(node, ast.NamedExpr):
            add_target(node.target)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _DYNAMIC_BINDERS
        ):
            dynamic = True

    return bound, dynamic


def _decorator_name(dec: ast.expr) -> str | None:
    """Leaf name of a decorator, unwrapping calls.

    `@foo` -> "foo", `@a.b.command` -> "command", `@app.command("x")` -> "command",
    `@register("name")` -> "register". Call forms (`@deco(...)`) are unwrapped to
    their callee so registration decorators like Typer's `@app.command(...)` are
    captured (previously dropped entirely).
    """
    if isinstance(dec, ast.Call):
        dec = dec.func
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return dec.attr
    return None


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    names: list[str] = []
    for dec in node.decorator_list:
        name = _decorator_name(dec)
        if name:
            names.append(name)
    return names


def _classify_export(name: str, node: ast.AST) -> ExportInfo | None:
    is_private = name.startswith("_") and not name.startswith("__")
    is_dunder = name.startswith("__") and name.endswith("__")

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ExportInfo(
            name=name,
            kind="function",
            line=node.lineno,
            col=node.col_offset,
            decorators=_decorator_names(node),
            is_dunder=is_dunder,
            is_private=is_private,
        )

    if isinstance(node, ast.ClassDef):
        return ExportInfo(
            name=name,
            kind="class",
            line=node.lineno,
            col=node.col_offset,
            decorators=_decorator_names(node),
            is_dunder=is_dunder,
            is_private=is_private,
        )

    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                kind = "constant" if name.isupper() else "variable"
                return ExportInfo(
                    name=name,
                    kind=kind,
                    line=node.lineno,
                    col=node.col_offset,
                    is_dunder=is_dunder,
                    is_private=is_private,
                )

    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == name
    ):
        return ExportInfo(
            name=name,
            kind="variable",
            line=node.lineno,
            col=node.col_offset,
            is_dunder=is_dunder,
            is_private=is_private,
        )

    return None


def _compute_cyclomatic(node: ast.AST) -> int:
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor)):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, (ast.ExceptHandler, ast.Assert)):
            complexity += 1
        elif isinstance(child, ast.comprehension):
            complexity += 1
            complexity += len(child.ifs)
        elif isinstance(child, ast.IfExp):
            complexity += 1
        elif isinstance(child, ast.Match):
            complexity += len(child.cases) - 1
    return complexity


def _compute_cognitive(node: ast.AST, nesting: int = 0) -> int:
    score = 0
    increments = (ast.If, ast.While, ast.For, ast.AsyncFor, ast.ExceptHandler)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, increments):
            score += 1 + nesting
            score += _compute_cognitive(child, nesting + 1)
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
            score += _compute_cognitive(child, nesting)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            score += _compute_cognitive(child, nesting + 1)
        elif isinstance(child, ast.Match):
            score += 1 + nesting
            score += _compute_cognitive(child, nesting + 1)
        else:
            score += _compute_cognitive(child, nesting)
    return score


class _FunctionCollector(ast.NodeVisitor):
    """Per-function complexity, with the dotted qualname of each function."""

    def __init__(self) -> None:
        self.results: list[FunctionComplexity] = []
        self._stack: list[tuple[str, bool]] = []  # (name, is_class)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._stack.append((node.name, True))
        self.generic_visit(node)
        self._stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "function"
        if self._stack and self._stack[-1][1]:
            kind = "method"
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name):
                    if dec.id == "classmethod":
                        kind = "classmethod"
                    elif dec.id == "staticmethod":
                        kind = "staticmethod"
                    elif dec.id == "property":
                        kind = "property"

        parts: list[str] = []
        for name, is_class in self._stack:
            parts.append(name)
            if not is_class:
                parts.append("<locals>")
        parts.append(node.name)

        end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
        self.results.append(
            FunctionComplexity(
                name=node.name,
                qualname=".".join(parts),
                kind=kind,
                line=node.lineno,
                end_line=end_line,
                cyclomatic=_compute_cyclomatic(node),
                cognitive=_compute_cognitive(node),
                parameters=(
                    len(node.args.args) + len(node.args.posonlyargs) + len(node.args.kwonlyargs)
                ),
                lines_of_code=end_line - node.lineno + 1,
            )
        )

        self._stack.append((node.name, False))
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def _extract_functions(tree: ast.Module) -> list[FunctionComplexity]:
    collector = _FunctionCollector()
    collector.visit(tree)
    return collector.results


def extract_module(path: Path, root: Path | None = None) -> ModuleInfo | None:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None

    root = root or Path.cwd()
    rel_path = str(path.relative_to(root))

    collector = _ImportCollector()
    collector.visit(tree)

    all_list, all_line = _extract_all_list(tree)
    bound_names, has_dynamic_bindings = _collect_module_bindings(tree)
    all_modified = all_list is not None and _all_is_modified(tree)

    exports: list[ExportInfo] = []
    classes: list[str] = []
    global_vars: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info = _classify_export(node.name, node)
            if info:
                exports.append(info)
        elif isinstance(node, ast.ClassDef):
            info = _classify_export(node.name, node)
            if info:
                exports.append(info)
            classes.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    info = _classify_export(target.id, node)
                    if info:
                        exports.append(info)
                    global_vars.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if not name.startswith("_"):
                info = _classify_export(name, node)
                if info:
                    exports.append(info)
                global_vars.append(name)

    docstring = ast.get_docstring(tree)
    functions = _extract_functions(tree)
    referenced_names = _collect_referenced_names(tree)
    accessed_attributes = _collect_accessed_attributes(tree)
    noqa_lines = _collect_noqa_lines(source)
    suppressions = _collect_suppressions(source)

    is_init = path.name == "__init__.py"
    is_main = path.name == "__main__.py"
    is_test = path.name.startswith("test_") or path.name.endswith("_test.py")
    is_conftest = path.name == "conftest.py"

    try:
        package = str(path.relative_to(root).with_suffix("")).replace("/", ".").replace("\\", ".")
        if package.endswith(".__init__"):
            package = package[: -len(".__init__")]
    except ValueError:
        package = path.stem

    return ModuleInfo(
        path=rel_path,
        package=package,
        imports=collector.imports,
        exports=exports,
        all_list=all_list,
        all_line=all_line,
        bound_names=bound_names,
        has_dynamic_bindings=has_dynamic_bindings,
        all_modified=all_modified,
        functions=functions,
        classes=classes,
        global_variables=global_vars,
        referenced_names=referenced_names,
        accessed_attributes=accessed_attributes,
        noqa_lines=noqa_lines,
        suppressions=suppressions,
        docstring=docstring,
        is_init=is_init,
        is_main=is_main,
        is_test=is_test,
        is_conftest=is_conftest,
        line_count=len(source.splitlines()),
        content_hash=_hash_content(source),
    )


def extract_modules_parallel(
    paths: list[Path],
    root: Path,
    max_workers: int | None = None,
) -> dict[str, ModuleInfo]:
    results: dict[str, ModuleInfo] = {}

    if len(paths) < 50:
        for p in paths:
            info = extract_module(p, root)
            if info:
                results[info.path] = info
        return results

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(extract_module, p, root): p for p in paths}
        for future in as_completed(futures):
            try:
                info = future.result()
                if info:
                    results[info.path] = info
            except Exception:
                pass

    return results
