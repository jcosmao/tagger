"""Advanced SQL-like search: parsing, safety, and the search endpoints."""
from pathlib import Path

import pytest

from core.database import db
from core.query import QueryError, compile_query, looks_advanced


def _insert(name, **cols):
    path = Path("/music") / name
    row = {"path": str(path), "filename": path.name, "directory": str(path.parent),
           "format": path.suffix.lstrip("."), "scanned_at": 1.0, **cols}
    with db() as conn:
        conn.execute(
            f"INSERT INTO tracks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
            list(row.values()),
        )


@pytest.fixture()
def library(client):
    _insert("a.mp3", title="Alpha", artist="The Band", year="1999", genre="Rock; Pop", bitrate=128)
    _insert("b.flac", title="Beta", artist="Solo", year="2004-05-12", genre="Jazz", bitrate=900)
    _insert("c.mp3", title="Gamma", artist="the band", year="2009", genre="Pop Rock", bitrate=320)
    _insert("d.mp3", title="Delta", artist="Other", year="", genre=None, bitrate=None)
    return client


def _titles(client, q):
    r = client.get("/api/library/search", params={"q": q, "limit": 200})
    assert r.status_code == 200, r.text
    return sorted(t["title"] for t in r.json()["tracks"])


@pytest.mark.parametrize("q, expected", [
    ("year >= 2000 AND year < 2010", ["Beta", "Gamma"]),
    ("year BETWEEN 1999 AND 2004", ["Alpha", "Beta"]),
    ("year < 2000", ["Alpha"]),  # blank year is not 0
    ("year IS NULL", ["Delta"]),
    ("genre IS NOT NULL", ["Alpha", "Beta", "Gamma"]),
    ("artist = 'THE BAND'", ["Alpha", "Gamma"]),
    ("artist LIKE 'the%' AND NOT bitrate > 200", ["Alpha"]),
    ("genre = 'rock'", ["Alpha"]),  # exact value, not "Pop Rock"
    ("genre LIKE '%rock%'", ["Alpha", "Gamma"]),
    ("format IN ('flac', 'ogg') OR bitrate <= 128", ["Alpha", "Beta"]),
    ("title NOT IN ('Alpha', 'Beta')", ["Delta", "Gamma"]),
    ("(artist = 'Solo' OR artist = 'Other') and ext = 'mp3'", ["Delta"]),
    ("title = 'It''s'", []),
])
def test_advanced_queries(library, q, expected):
    assert _titles(library, q) == expected


def test_order_by(library):
    r = library.get("/api/library/search", params={"q": "year IS NOT NULL ORDER BY year DESC"})
    assert [t["title"] for t in r.json()["tracks"]] == ["Gamma", "Beta", "Alpha"]


def test_plain_text_still_uses_full_text_search(library):
    assert _titles(library, "band") == ["Alpha", "Gamma"]
    assert _titles(library, "year") == []


def test_track_ids_and_export_accept_advanced_queries(library):
    assert len(library.get("/api/library/track-ids", params={"q": "bitrate > 200"}).json()) == 2
    body = library.get("/api/library/export.m3u", params={"q": "format = 'flac'"}).text
    assert "/music/b.flac" in body and "a.mp3" not in body


@pytest.mark.parametrize("q", [
    "year >= ",
    "year = 'abc'",
    "title = 'x' AND",
    "title = 'x'; DROP TABLE tracks",
    "title = x",
])
def test_invalid_advanced_query_is_400(library, q):
    r = library.get("/api/library/search", params={"q": q})
    assert r.status_code == 400
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 4


def test_literals_are_bound_not_inlined():
    where, params, _ = compile_query("title = 'x'' OR 1=1 --'")
    assert "1=1" not in where and params == ["x' OR 1=1 --"]


def test_errors_and_detection():
    with pytest.raises(QueryError, match="Unknown field"):
        compile_query("nope = 1")
    assert looks_advanced("Year >= 2000")
    assert looks_advanced("(genre like 'a%')")
    assert not looks_advanced("the beatles")
    assert not looks_advanced("yearning")
