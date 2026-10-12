"""Line numbers of string values in TOML arrays.

tomllib returns values without positions. This scanner walks the TOML text
just far enough to record, for every array of strings, which line each
element starts on — keyed by the full dotted key path, e.g.
("project", "dependencies") or ("project", "optional-dependencies", "cli").
Values are decoded (escapes included), table headers and keys may be quoted
or dotted, and arrays may sit in inline tables. Anything the scanner does
not understand is skipped; callers fall back to line 1.
"""

from __future__ import annotations

import bisect
import contextlib
import re

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}

Path = tuple[str, ...]


class _Scanner:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.line_starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
        self.found: dict[Path, list[tuple[int, str]]] = {}

    def line(self, pos: int) -> int:
        return bisect.bisect_right(self.line_starts, pos)

    def peek(self, k: int = 1) -> str:
        return self.text[self.pos : self.pos + k]

    def skip(self, newlines: bool = True) -> None:
        while self.pos < len(self.text):
            c = self.text[self.pos]
            if c in " \t" or (newlines and c in "\r\n"):
                self.pos += 1
            elif c == "#":
                while self.pos < len(self.text) and self.text[self.pos] != "\n":
                    self.pos += 1
            else:
                return

    def string(self) -> str:
        t, p = self.text, self.pos
        if t.startswith('"""', p) or t.startswith("'''", p):
            q = t[p : p + 3]
            end = t.index(q, p + 3)
            raw = t[p + 3 : end]
            self.pos = end + 3
            raw = raw[1:] if raw.startswith("\n") else raw
            return _unescape(raw) if q == '"""' else raw
        q = t[p]
        i = p + 1
        while t[i] != q:
            i += 2 if (q == '"' and t[i] == "\\") else 1
        self.pos = i + 1
        raw = t[p + 1 : i]
        return _unescape(raw) if q == '"' else raw

    def key(self) -> list[str]:
        parts: list[str] = []
        while True:
            self.skip(newlines=False)
            if self.peek() in ('"', "'"):
                parts.append(self.string())
            else:
                m = _BARE_KEY.match(self.text, self.pos)
                if not m:
                    raise ValueError("bad key")
                parts.append(m.group())
                self.pos = m.end()
            self.skip(newlines=False)
            if self.peek() == ".":
                self.pos += 1
                continue
            return parts

    def value(self, path: Path) -> None:
        self.skip(newlines=False)
        c = self.peek()
        if c == "[":
            self.array(path)
        elif c == "{":
            self.inline_table(path)
        elif c in ('"', "'"):
            self.string()
        else:  # number, bool, date: up to a delimiter
            while self.pos < len(self.text) and self.text[self.pos] not in ",]}\n#":
                self.pos += 1

    def array(self, path: Path) -> None:
        self.pos += 1
        while True:
            self.skip()
            if self.peek() == "]":
                self.pos += 1
                return
            if self.peek() in ('"', "'"):
                start = self.pos
                value = self.string()
                self.found.setdefault(path, []).append((self.line(start), value))
            else:
                self.value(path + ("[]",))  # nested values are not elements of `path`
            self.skip()
            if self.peek() == ",":
                self.pos += 1

    def inline_table(self, path: Path) -> None:
        self.pos += 1
        while True:
            self.skip()
            if self.peek() == "}":
                self.pos += 1
                return
            k = self.key()
            self.skip(newlines=False)
            self.pos += 1  # "="
            self.value(path + tuple(k))
            self.skip()
            if self.peek() == ",":
                self.pos += 1

    def document(self) -> None:
        table: Path = ()
        while True:
            self.skip()
            if self.pos >= len(self.text):
                return
            if self.peek() == "[":
                double = self.peek(2) == "[["
                self.pos += 2 if double else 1
                table = tuple(self.key())
                self.skip(newlines=False)
                self.pos += 2 if double else 1
                continue
            k = self.key()
            self.skip(newlines=False)
            if self.peek() != "=":
                raise ValueError("expected =")
            self.pos += 1
            self.value(table + tuple(k))


def _unescape(raw: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        n = raw[i + 1] if i + 1 < len(raw) else ""
        if n in ("u", "U"):
            width = 4 if n == "u" else 8
            out.append(chr(int(raw[i + 2 : i + 2 + width], 16)))
            i += 2 + width
        elif n in _ESCAPES:
            out.append(_ESCAPES[n])
            i += 2
        else:  # line-ending backslash in multi-line strings
            i += 2
            while i < len(raw) and raw[i] in " \t\r\n":
                i += 1
    return "".join(out)


def array_string_lines(text: str) -> dict[Path, list[tuple[int, str]]]:
    """For each array of strings in `text`, its elements' (line, decoded value).

    Best effort: on text the scanner cannot follow, what was found so far is
    returned.
    """
    scanner = _Scanner(text)
    with contextlib.suppress(ValueError, IndexError):
        scanner.document()
    return scanner.found
