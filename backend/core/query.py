"""
Advanced track search: a small SQL-like WHERE language compiled to
parameterised SQLite. User input never reaches the SQL text — fields come
from a whitelist and every literal is bound as a parameter.

    year >= 2000 AND year < 2010
    genre = 'Rock' AND (artist LIKE 'The %' OR bitrate < 192)
    album IS NULL ORDER BY year DESC, title
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


class QueryError(ValueError):
    pass


# Column kinds: "text" (compared case-insensitively), "num" (numeric column),
# "numtext" (TEXT column holding numbers, e.g. year "2004-05-12", track "3/12").
_COLUMNS = {
    "title": "text", "artist": "text", "album": "text", "album_artist": "text",
    "genre": "text", "comment": "text", "composer": "text", "lyrics": "text",
    "compilation": "text", "path": "text", "filename": "text", "directory": "text",
    "format": "text", "mb_track_id": "text", "mb_artist_id": "text",
    "mb_album_id": "text", "mb_album_artist_id": "text",
    "year": "numtext", "track_number": "numtext", "disc_number": "numtext", "bpm": "numtext",
    "size": "num", "duration": "num", "bitrate": "num", "sample_rate": "num",
    "channels": "num", "mtime": "num", "scanned_at": "num", "tagged_at": "num",
}
_ALIASES = {
    "albumartist": "album_artist", "date": "year", "track": "track_number",
    "tracknumber": "track_number", "disc": "disc_number", "discnumber": "disc_number",
    "samplerate": "sample_rate", "dir": "directory", "folder": "directory",
    "file": "filename", "ext": "format",
}
FIELDS = sorted({*_COLUMNS, *_ALIASES})

_KEYWORDS = {"AND", "OR", "NOT", "LIKE", "IN", "IS", "NULL", "BETWEEN", "ORDER", "BY", "ASC", "DESC"}

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>-?\d+(?:\.\d+)?(?![\w.]))
  | (?P<str>'(?:[^']|'')*'|"(?:[^"]|"")*")
  | (?P<op><=|>=|<>|!=|==|=|<|>)
  | (?P<punct>[(),])
  | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
""", re.VERBOSE)


@dataclass
class _Tok:
    kind: str  # num | str | op | punct | word | kw | end
    value: object
    pos: int


def _tokenize(text: str) -> list[_Tok]:
    toks, pos = [], 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise QueryError(f"Unexpected character {text[pos]!r} at position {pos + 1}")
        kind, raw = m.lastgroup, m.group()
        if kind == "num":
            toks.append(_Tok("num", float(raw) if "." in raw else int(raw), pos))
        elif kind == "str":
            toks.append(_Tok("str", raw[1:-1].replace(raw[0] * 2, raw[0]), pos))
        elif kind == "word" and raw.upper() in _KEYWORDS:
            toks.append(_Tok("kw", raw.upper(), pos))
        elif kind != "ws":
            toks.append(_Tok(kind, raw, pos))
        pos = m.end()
    toks.append(_Tok("end", None, len(text)))
    return toks


class _Parser:
    def __init__(self, text: str):
        self.toks = _tokenize(text)
        self.i = 0
        self.params: list = []

    # ── token helpers ──
    def peek(self, offset: int = 0) -> _Tok:
        return self.toks[min(self.i + offset, len(self.toks) - 1)]

    def accept(self, kind: str, value: object = None) -> Optional[_Tok]:
        t = self.peek()
        if t.kind == kind and (value is None or t.value == value):
            self.i += 1
            return t
        return None

    def expect(self, kind: str, value: object = None, what: str = "") -> _Tok:
        t = self.accept(kind, value)
        if t is None:
            got = self.peek()
            found = "end of query" if got.kind == "end" else repr(got.value)
            raise QueryError(f"Expected {what or value or kind} but found {found} at position {got.pos + 1}")
        return t

    # ── grammar ──
    def parse(self) -> tuple[str, str]:
        where = self.or_expr()
        order = self.order_by() if self.accept("kw", "ORDER") else ""
        self.expect("end", what="AND, OR or ORDER BY")
        return where, order

    def or_expr(self) -> str:
        parts = [self.and_expr()]
        while self.accept("kw", "OR"):
            parts.append(self.and_expr())
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"

    def and_expr(self) -> str:
        parts = [self.not_expr()]
        while self.accept("kw", "AND"):
            parts.append(self.not_expr())
        return parts[0] if len(parts) == 1 else "(" + " AND ".join(parts) + ")"

    def not_expr(self) -> str:
        if self.accept("kw", "NOT"):
            return f"(NOT {self.not_expr()})"
        if self.accept("punct", "("):
            inner = self.or_expr()
            self.expect("punct", ")")
            return inner
        return self.comparison()

    def field(self) -> tuple[str, str]:
        t = self.expect("word", what="a field name")
        name = str(t.value).lower()
        name = _ALIASES.get(name, name)
        if name not in _COLUMNS:
            raise QueryError(f"Unknown field {t.value!r}. Fields: {', '.join(FIELDS)}")
        return name, _COLUMNS[name]

    def literal(self) -> object:
        t = self.peek()
        if t.kind in ("num", "str"):
            self.i += 1
            return t.value
        return self.expect("str", what="a value")  # raises

    def comparison(self) -> str:
        name, kind = self.field()
        col = f"t.{name}"

        if self.accept("kw", "IS"):
            negate = bool(self.accept("kw", "NOT"))
            self.expect("kw", "NULL")
            missing = f"{col} IS NULL" if kind == "num" else f"({col} IS NULL OR {col} = '')"
            return f"(NOT {missing})" if negate else missing

        negate = bool(self.accept("kw", "NOT"))
        if self.accept("kw", "LIKE"):
            pattern = self.literal()
            self.params.append(str(pattern))
            return f"({col} {'NOT ' if negate else ''}LIKE ?)"
        if self.accept("kw", "IN"):
            self.expect("punct", "(")
            values = [self.literal()]
            while self.accept("punct", ","):
                values.append(self.literal())
            self.expect("punct", ")")
            sql = "(" + " OR ".join(self.compare(name, kind, "=", v) for v in values) + ")"
            return f"(NOT {sql})" if negate else sql
        if self.accept("kw", "BETWEEN"):
            low = self.literal()
            self.expect("kw", "AND")
            high = self.literal()
            sql = f"({self.compare(name, kind, '>=', low)} AND {self.compare(name, kind, '<=', high)})"
            return f"(NOT {sql})" if negate else sql
        if negate:
            raise QueryError(f"Expected LIKE, IN or BETWEEN after NOT at position {self.peek().pos + 1}")

        op = str(self.expect("op", what="an operator (=, !=, <, <=, >, >=, LIKE, IN, BETWEEN, IS NULL)").value)
        op = {"==": "=", "<>": "!="}.get(op, op)
        return self.compare(name, kind, op, self.literal())

    def compare(self, name: str, kind: str, op: str, value: object) -> str:
        col = f"t.{name}"
        if kind == "text":
            value = str(value)
            if name == "genre" and op in ("=", "!="):
                # Genres are stored as "A; B": match one value exactly.
                self.params.append(f"; {value.lower()}; ")
                return f"(instr('; ' || lower({col}) || '; ', ?) {'>' if op == '=' else '='} 0)"
            self.params.append(value)
            return f"({col} {op} ? COLLATE NOCASE)"

        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                raise QueryError(f"{name} is numeric; {value!r} is not a number") from None
        self.params.append(value)
        if kind == "num":
            return f"({col} {op} ?)"
        # Leading-number cast ("2004-05-12" → 2004); blanks are not zero.
        return f"({col} != '' AND CAST({col} AS REAL) {op} ?)"

    def order_by(self) -> str:
        self.expect("kw", "BY")
        terms = []
        while True:
            name, kind = self.field()
            expr = f"CAST(t.{name} AS REAL)" if kind == "numtext" else (
                f"t.{name} COLLATE NOCASE" if kind == "text" else f"t.{name}")
            direction = "DESC" if self.accept("kw", "DESC") else "ASC"
            self.accept("kw", "ASC")
            terms.append(f"{expr} {direction}")
            if not self.accept("punct", ","):
                break
        return "ORDER BY " + ", ".join(terms)


def compile_query(text: str) -> tuple[str, list, str]:
    """Compile an advanced query into (WHERE expression, params, ORDER BY or "")."""
    p = _Parser(text)
    where, order = p.parse()
    return where, p.params, order


_FIELD_OP_RE = re.compile(
    r"^\s*\(*\s*(" + "|".join(FIELDS) + r")\s*(=|!|<|>|(not\s+)?(like|in|between)\b|is\b)",
    re.IGNORECASE,
)


def looks_advanced(text: str) -> bool:
    """Whether a query that failed to compile was meant as an advanced one
    (so the error is worth reporting) rather than plain full-text search."""
    return bool(_FIELD_OP_RE.match(text))
