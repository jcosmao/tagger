"""
Album tag unification.

Albums are directories holding more than one track. For an album whose tracks
disagree on an album-level tag, `propose` picks the value every track should
carry; fields it can't decide (no majority, a tie, nothing parseable) are left
out rather than guessed.
"""
from __future__ import annotations

import re
from collections import Counter

from core.genres import join_genres, split_genres

UNIFY_FIELDS = ["genre", "year", "album", "album_artist", "compilation"]

_YEAR = re.compile(r"\s*(\d{4})")


def _genre(values: list[str]) -> str | None:
    n = len(values)
    counts = Counter(g for v in values for g in split_genres(v))
    keep = {g for g, c in counts.items() if c * 2 > n}
    if not keep:
        return None
    # Follow the order of the most common lists, so tracks that already carry
    # the majority list are not rewritten just to reorder it.
    ordered: list[str] = []
    for value, _ in Counter(values).most_common():
        ordered.extend(g for g in split_genres(value) if g in keep and g not in ordered)
    return join_genres(ordered)


def _year(values: list[str]) -> str | None:
    years = [m.group(1) for v in values if (m := _YEAR.match(v))]
    return min(years) if years else None


def _most_common(values: list[str], count_empty: bool) -> str | None:
    counts = Counter(v for v in values if count_empty or v)
    top = counts.most_common(2)
    if not top or (len(top) == 2 and top[0][1] == top[1][1]):
        return None
    return top[0][0]


def propose(tracks: list[dict], fields: list[str] | None = None) -> dict[str, dict]:
    """
    Return {field: {"before": {value: count}, "after": value, "changed": n}}
    for each inconsistent field that can be unified.
    """
    out: dict[str, dict] = {}
    for field in fields or UNIFY_FIELDS:
        values = [(t.get(field) or "") for t in tracks]
        before = Counter(values)
        if len(before) < 2:
            continue
        if field == "genre":
            after = _genre(values)
        elif field == "year":
            after = _year(values)
        else:
            # An empty compilation flag means "not a compilation": it votes.
            after = _most_common(values, count_empty=field == "compilation")
        if after is None:
            continue
        changed = sum(1 for v in values if v != after)
        if changed:
            out[field] = {"before": dict(before.most_common()), "after": after, "changed": changed}
    return out


_PREVIEW_COLUMNS = ["id", "directory", "artist", *UNIFY_FIELDS]


def album_groups(conn, directories: list[str] | None = None, columns: list[str] | None = None) -> dict[str, list]:
    """Track rows grouped by directory, for directories holding 2+ tracks."""
    cols = ", ".join(columns) if columns else "*"
    where, params = "", []
    if directories is not None:
        if not directories:
            return {}
        where = f"WHERE directory IN ({','.join('?' * len(directories))})"
        params = directories
    rows = conn.execute(f"SELECT {cols} FROM tracks {where} ORDER BY directory, path", params).fetchall()
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["directory"], []).append(r)
    return {d: ts for d, ts in groups.items() if len(ts) > 1}


def inconsistencies(conn) -> list[dict]:
    """Preview of every album that `propose` would change."""
    out = []
    for directory, rows in album_groups(conn, columns=_PREVIEW_COLUMNS).items():
        changes = propose([dict(r) for r in rows])
        if changes:
            album = Counter(r["album"] for r in rows if r["album"]).most_common(1)
            artist = Counter((r["album_artist"] or r["artist"]) for r in rows
                             if r["album_artist"] or r["artist"]).most_common(1)
            out.append({
                "directory": directory,
                "album": album[0][0] if album else None,
                "artist": artist[0][0] if artist else None,
                "track_count": len(rows),
                "changes": changes,
            })
    return out
