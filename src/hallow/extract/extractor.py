"""Parse .py files via ast.parse() and produce ModuleInfo."""

from __future__ import annotations

import ast
import hashlib
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from hallow.types import ExportInfo, FunctionComplexity, ImportInfo, ModuleInfo

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


def _extract_all_list(tree: ast.Module) -> list[str] | None:
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
                    ]
    return None


def _classify_export(name: str, node: ast.AST) -> ExportInfo | None:
    is_private = name.startswith("_") and not name.startswith("__")
    is_dunder = name.startswith("__") and name.endswith("__")

    decorators: list[str] = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                decorators.append(dec.attr)
        return ExportInfo(
            name=name,
            kind="function",
            line=node.lineno,
            col=node.col_offset,
            decorators=decorators,
            is_dunder=is_dunder,
            is_private=is_private,
        )

    if isinstance(node, ast.ClassDef):
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                decorators.append(dec.attr)
        return ExportInfo(
            name=name,
            kind="class",
            line=node.lineno,
            col=node.col_offset,
            decorators=decorators,
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


def _extract_functions(tree: ast.Module) -> list[FunctionComplexity]:
    results: list[FunctionComplexity] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "function"
            for parent in ast.walk(tree):
                if isinstance(parent, ast.ClassDef):
                    for child in ast.iter_child_nodes(parent):
                        if child is node:
                            kind = "method"
                            for dec in node.decorator_list:
                                if isinstance(dec, ast.Name):
                                    if dec.id == "classmethod":
                                        kind = "classmethod"
                                    elif dec.id == "staticmethod":
                                        kind = "staticmethod"
                                    elif dec.id == "property":
                                        kind = "property"

            end_line = getattr(node, "end_lineno", node.lineno)
            results.append(
                FunctionComplexity(
                    name=node.name,
                    kind=kind,
                    line=node.lineno,
                    end_line=end_line or node.lineno,
                    cyclomatic=_compute_cyclomatic(node),
                    cognitive=_compute_cognitive(node),
                    parameters=(
                        len(node.args.args) + len(node.args.posonlyargs) + len(node.args.kwonlyargs)
                    ),
                    lines_of_code=(end_line or node.lineno) - node.lineno + 1,
                )
            )
    return results


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

    all_list = _extract_all_list(tree)

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
    noqa_lines = _collect_noqa_lines(source)

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
        functions=functions,
        classes=classes,
        global_variables=global_vars,
        referenced_names=referenced_names,
        noqa_lines=noqa_lines,
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
