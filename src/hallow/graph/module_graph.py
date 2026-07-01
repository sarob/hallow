"""Module graph construction, reachability, and cycle detection."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from hallow.graph.resolver import resolve_import
from hallow.types import ImportCycle, ModuleInfo


class ModuleGraph:
    def __init__(self, modules: dict[str, ModuleInfo], root: Path) -> None:
        self.modules = modules
        self.root = root
        self._edges: dict[str, set[str]] = defaultdict(set)
        self._reverse_edges: dict[str, set[str]] = defaultdict(set)
        self._symbol_edges: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        self._reachable: set[str] = set()
        self._test_reachable: set[str] = set()
        self._entry_points: set[str] = set()
        self._external_imports: dict[str, set[str]] = defaultdict(set)

        self._build()

    def _build(self) -> None:
        all_paths = set(self.modules.keys())

        for path, module in self.modules.items():
            for imp in module.imports:
                target = resolve_import(imp, path, self.root, all_paths)
                if target and target in all_paths:
                    self._edges[path].add(target)
                    self._reverse_edges[target].add(path)
                    if imp.names:
                        names = imp.names
                    elif imp.module:
                        names = [imp.module.split(".")[-1]]
                    else:
                        names = []
                    self._symbol_edges[path][target].extend(names)
                elif not imp.is_relative:
                    top_level = imp.module.split(".")[0] if imp.module else ""
                    if top_level:
                        self._external_imports[path].add(top_level)

        self._identify_entry_points()
        self._compute_reachability()

    def _identify_entry_points(self) -> None:
        for path, module in self.modules.items():
            if module.is_init and "/" not in path.replace("\\", "/").rstrip("/"):
                self._entry_points.add(path)
                continue
            if module.is_main:
                self._entry_points.add(path)
                continue

            has_importers = path in self._reverse_edges and len(self._reverse_edges[path]) > 0
            if not has_importers and not module.is_test and not module.is_conftest:
                self._entry_points.add(path)

    def _compute_reachability(self) -> None:
        runtime_entries = {p for p in self._entry_points if not self.modules[p].is_test}
        test_entries = {
            p for p in self.modules if self.modules[p].is_test or self.modules[p].is_conftest
        }

        self._reachable = self._bfs(runtime_entries)
        self._test_reachable = self._bfs(test_entries)

    def _bfs(self, starts: set[str]) -> set[str]:
        visited: set[str] = set()
        queue = list(starts)
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for neighbor in self._edges.get(node, set()):
                if neighbor not in visited:
                    queue.append(neighbor)
        return visited

    def is_reachable(self, path: str) -> bool:
        return path in self._reachable or path in self._test_reachable

    def is_entry_point(self, path: str) -> bool:
        return path in self._entry_points

    def importers_of(self, path: str) -> set[str]:
        return self._reverse_edges.get(path, set())

    def imports_of(self, path: str) -> set[str]:
        return self._edges.get(path, set())

    def symbols_imported_from(self, importer: str, target: str) -> list[str]:
        return self._symbol_edges.get(importer, {}).get(target, [])

    def external_imports(self, path: str) -> set[str]:
        return self._external_imports.get(path, set())

    def all_external_imports(self) -> set[str]:
        result: set[str] = set()
        for imports in self._external_imports.values():
            result.update(imports)
        return result

    def find_cycles(self) -> list[ImportCycle]:
        sccs = self._tarjan_scc()
        cycles: list[ImportCycle] = []
        for scc in sccs:
            if len(scc) > 1:
                edges: list[tuple[str, str]] = []
                scc_set = set(scc)
                for node in scc:
                    for target in self._edges.get(node, set()):
                        if target in scc_set:
                            edges.append((node, target))
                cycles.append(ImportCycle(modules=sorted(scc), edges=edges))
        return cycles

    def _tarjan_scc(self) -> list[list[str]]:
        index_counter = [0]
        stack: list[str] = []
        lowlink: dict[str, int] = {}
        index: dict[str, int] = {}
        on_stack: set[str] = set()
        result: list[list[str]] = []

        all_nodes = set(self.modules.keys())

        def strongconnect(v: str) -> None:
            index[v] = index_counter[0]
            lowlink[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack.add(v)

            for w in self._edges.get(v, set()):
                if w not in all_nodes:
                    continue
                if w not in index:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], index[w])

            if lowlink[v] == index[v]:
                component: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == v:
                        break
                result.append(component)

        for v in all_nodes:
            if v not in index:
                strongconnect(v)

        return result

    def unreachable_files(self) -> list[str]:
        all_files = set(self.modules.keys())
        reachable = self._reachable | self._test_reachable | self._entry_points
        conftest_files = {p for p, m in self.modules.items() if m.is_conftest}
        init_files = {p for p, m in self.modules.items() if m.is_init}
        return sorted(all_files - reachable - conftest_files - init_files)
