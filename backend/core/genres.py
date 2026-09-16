"""
Multi-valued genre handling.

A track's genres live in the DB as one canonical string, "Rock; Pop", so every
existing tag-write path (patch, bulk, find/replace, undo) keeps treating genre
as a plain text field. Files store the individual values natively (see
core.tagger.write_tags).
"""
from __future__ import annotations

import json
import re
from typing import Iterable

SEP = "; "

# The canonical separator is always honoured; user-configured ones add to it.
_CANONICAL = ";"


def split_genres(value: str | Iterable[str] | None, separators: Iterable[str] = ()) -> list[str]:
    """Split raw genre value(s) into trimmed, de-duplicated names (order kept)."""
    if value is None:
        return []
    items = [value] if isinstance(value, str) else [str(v) for v in value]
    seps = {_CANONICAL, *(s for s in separators if s)}
    for sep in seps:
        items = [part for item in items for part in item.split(sep)]
    out: list[str] = []
    for item in items:
        name = item.strip()
        if name and name not in out:
            out.append(name)
    return out


def join_genres(genres: Iterable[str]) -> str:
    return SEP.join(split_genres(list(genres)))


def normalize_genre(value: str | None) -> str | None:
    """Canonical form of a stored/submitted genre string; None stays None."""
    return None if value is None else join_genres(split_genres(value))


def edit_genres(
    value: str | None,
    add: Iterable[str] = (),
    remove: Iterable[str] = (),
    rename: tuple[str, str] | None = None,
) -> str:
    """Apply rename, then removals, then additions; returns the canonical string."""
    genres = split_genres(value)
    if rename:
        old, new = rename
        replaced: list[str] = []
        for g in genres:
            replaced.extend(split_genres(new) if g == old else [g])
        genres = replaced
    dropped = set(split_genres(list(remove)))
    genres = [g for g in genres if g not in dropped]
    return join_genres([*genres, *split_genres(list(add))])


_DECADE = re.compile(r"\d{3}0")
_YEAR = re.compile(r"\s*(\d{4})")


def with_decade_genre(genre: str | None, year: str | None) -> str | None:
    """
    Genres with the decade of `year` ("2005" → "2000") added and any other
    decade genre dropped. Without a readable year the genres are left alone
    (returns the input unchanged).
    """
    m = _YEAR.match(year or "")
    if not m:
        return genre
    decade = str(int(m.group(1)) // 10 * 10)
    kept = [g for g in split_genres(genre) if not _DECADE.fullmatch(g) or g == decade]
    new = join_genres([*kept, decade])
    return genre if new == (genre or "") else new


def sync_genre_separators(conn, separators: Iterable[str]) -> None:
    """
    Bring indexed genres in line with the configured separators.

    The scanner skips files whose mtime hasn't changed, so a separator change
    would otherwise never reach them. An added separator re-splits the stored
    values in place (same result as re-reading the files); a removed one can't
    be undone from the index, so mtimes are cleared and the next scan re-reads.
    """
    wanted = sorted({_CANONICAL, *(s for s in separators if s)})
    row = conn.execute("SELECT value FROM meta WHERE key = 'genre_separators'").fetchone()
    applied = json.loads(row[0]) if row else [_CANONICAL]
    if wanted == applied:
        return
    if set(applied) - set(wanted):
        conn.execute("UPDATE tracks SET mtime = NULL")
    else:
        changes = []
        for tid, genre in conn.execute("SELECT id, genre FROM tracks WHERE genre != ''").fetchall():
            new = join_genres(split_genres(genre, wanted))
            if new != genre:
                changes.append((new, tid))
        conn.executemany("UPDATE tracks SET genre = ? WHERE id = ?", changes)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('genre_separators', ?)",
        (json.dumps(wanted),),
    )
