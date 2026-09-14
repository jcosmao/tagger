"""
Multi-valued genre handling.

A track's genres live in the DB as one canonical string, "Rock; Pop", so every
existing tag-write path (patch, bulk, find/replace, undo) keeps treating genre
as a plain text field. Files store the individual values natively (see
core.tagger.write_tags).
"""
from __future__ import annotations

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
