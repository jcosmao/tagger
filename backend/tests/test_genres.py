"""Multi-valued genres: parsing, file round-trips, browsing, and bulk retagging."""
import subprocess

import pytest

from core.database import db, init_db
from core.genres import edit_genres, join_genres, split_genres
from core.tagger import read_tags, write_tags
from tests.conftest import FFMPEG

FORMATS = [".mp3", ".flac", ".ogg", ".m4a"]


# ─── Pure helpers ─────────────────────────────────────────────────────────────

def test_split_trims_dedupes_and_drops_empty():
    assert split_genres(" Rock ;Pop;; Rock ") == ["Rock", "Pop"]
    assert split_genres(None) == []
    assert split_genres("") == []


def test_split_accepts_lists_and_extra_separators():
    assert split_genres(["Rock", "Pop; Jazz"]) == ["Rock", "Pop", "Jazz"]
    assert split_genres("Rock / Pop", separators=["/"]) == ["Rock", "Pop"]
    # ';' is the canonical separator and always applies.
    assert split_genres("Rock; Pop / Jazz", separators=["/"]) == ["Rock", "Pop", "Jazz"]


def test_split_keeps_case_distinct_values():
    assert split_genres("Rock; rock") == ["Rock", "rock"]


def test_join_is_canonical():
    assert join_genres(["Rock", "Pop"]) == "Rock; Pop"
    assert join_genres([]) == ""


def test_edit_add_remove_rename():
    assert edit_genres("Rock; Pop", add=["Jazz", "Rock"]) == "Rock; Pop; Jazz"
    assert edit_genres("Rock; Pop", remove=["Rock"]) == "Pop"
    assert edit_genres("Rock; Pop", remove=["Pop", "Rock"]) == ""
    assert edit_genres("Hip Hop; Rap", rename=("Hip Hop", "Hip-Hop")) == "Hip-Hop; Rap"
    # Renaming onto a genre the track already has merges them.
    assert edit_genres("Hip Hop; Hip-Hop", rename=("Hip Hop", "Hip-Hop")) == "Hip-Hop"
    # Renaming to nothing deletes; renaming to a list expands in place.
    assert edit_genres("Rock; Pop", rename=("Rock", "")) == "Pop"
    assert edit_genres("Rock; Pop", rename=("Rock", "Hard Rock; Metal")) == "Hard Rock; Metal; Pop"


# ─── File round-trips ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ext", FORMATS)
def test_multi_genre_written_as_native_values(make_audio, ext):
    import mutagen

    path = make_audio(ext)
    write_tags(path, {"genre": "Rock; Pop"})
    raw = mutagen.File(str(path), easy=True).tags["genre"]
    assert list(raw) == ["Rock", "Pop"]
    assert read_tags(path)["genre"] == "Rock; Pop"


@pytest.mark.parametrize("ext", FORMATS)
def test_legacy_joined_genre_is_split_on_read(make_audio, ext):
    import mutagen

    path = make_audio(ext)
    f = mutagen.File(str(path), easy=True)
    if f.tags is None:
        f.add_tags()
    f.tags["genre"] = ["Rock / Pop"]
    f.save()
    assert read_tags(path)["genre"] == "Rock / Pop"
    assert read_tags(path, genre_separators=["/"])["genre"] == "Rock; Pop"


# ─── DB migration ─────────────────────────────────────────────────────────────

def test_migration_normalizes_genres_and_forces_rescan(temp_db):
    with db() as conn:
        conn.execute("PRAGMA user_version = 0")
        conn.execute(
            "INSERT INTO tracks (path, filename, directory, format, mtime, genre, scanned_at) "
            "VALUES ('/a.mp3', 'a.mp3', '/', 'mp3', 123.0, 'Rock ;Pop', 1.0)"
        )
    init_db()
    with db() as conn:
        row = conn.execute("SELECT genre, mtime FROM tracks").fetchone()
        assert row["genre"] == "Rock; Pop"
        assert row["mtime"] is None
        conn.execute("UPDATE tracks SET mtime = 5.0")
    init_db()  # idempotent: a second start doesn't force another rescan
    with db() as conn:
        assert conn.execute("SELECT mtime FROM tracks").fetchone()["mtime"] == 5.0


# ─── API ──────────────────────────────────────────────────────────────────────

def _insert(path, **extra):
    cols = {"path": str(path), "filename": path.name, "directory": str(path.parent),
            "format": path.suffix.lstrip("."), "scanned_at": 1.0, **extra}
    with db() as conn:
        names = ", ".join(cols)
        ph = ", ".join("?" * len(cols))
        return conn.execute(f"INSERT INTO tracks ({names}) VALUES ({ph})", list(cols.values())).lastrowid


def _make_mp3(path):
    subprocess.run(
        [FFMPEG, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "0.3",
         "-c:a", "libmp3lame", "-y", str(path)],
        check=True, capture_output=True,
    )


@pytest.fixture()
def library(client, tmp_path):
    """Four real MP3s with assorted genres; returns {name: (id, path)}."""
    if not FFMPEG:
        pytest.skip("ffmpeg not available")
    music = tmp_path / "m"
    music.mkdir()
    genres = {"a": "Rock; Pop", "b": "Rock and Roll", "c": "Pop", "d": None}
    out = {}
    for name, genre in genres.items():
        f = music / f"{name}.mp3"
        _make_mp3(f)
        if genre:
            write_tags(f, {"genre": genre})
        out[name] = (_insert(f, genre=genre), f)
    return out


def test_genre_listing_counts_split_values(client, library):
    body = client.get("/api/library/genres").json()
    assert body == [
        {"genre": "", "track_count": 1},
        {"genre": "Pop", "track_count": 2},
        {"genre": "Rock", "track_count": 1},
        {"genre": "Rock and Roll", "track_count": 1},
    ]


def test_tracks_filter_by_genre_is_exact(client, library):
    rock = client.get("/api/library/tracks", params={"genre": "Rock"}).json()
    assert [t["filename"] for t in rock["tracks"]] == ["a.mp3"]
    pop = client.get("/api/library/tracks", params={"genre": "Pop"}).json()
    assert sorted(t["filename"] for t in pop["tracks"]) == ["a.mp3", "c.mp3"]
    none = client.get("/api/library/tracks", params={"genre": ""}).json()
    assert [t["filename"] for t in none["tracks"]] == ["d.mp3"]


def test_track_ids_returns_every_match(client, library):
    ids = client.get("/api/library/track-ids", params={"genre": "Pop"}).json()
    assert sorted(ids) == sorted([library["a"][0], library["c"][0]])
    assert len(client.get("/api/library/track-ids").json()) == 4


def test_rename_genre_merges_and_is_undoable(client, library):
    r = client.post("/api/tags/genres/rename", json={"old": "Pop", "new": "Rock"}).json()
    assert r == {"changed": 2, "errors": []}

    by_name = {t["filename"]: t["genre"] for t in client.get("/api/library/tracks").json()["tracks"]}
    assert by_name["a.mp3"] == "Rock"          # merged, not "Rock; Rock"
    assert by_name["c.mp3"] == "Rock"
    assert read_tags(library["a"][1])["genre"] == "Rock"

    change = client.get("/api/library/history").json()[0]
    assert "Pop" in change["summary"]
    client.post(f"/api/library/history/{change['id']}/undo")
    assert read_tags(library["a"][1])["genre"] == "Rock; Pop"
    by_name = {t["filename"]: t["genre"] for t in client.get("/api/library/tracks").json()["tracks"]}
    assert by_name["a.mp3"] == "Rock; Pop"


def test_rename_genre_to_empty_deletes_it(client, library):
    client.post("/api/tags/genres/rename", json={"old": "Pop", "new": ""})
    by_name = {t["filename"]: t["genre"] for t in client.get("/api/library/tracks").json()["tracks"]}
    assert by_name["a.mp3"] == "Rock"
    assert by_name["c.mp3"] == ""
    assert read_tags(library["c"][1])["genre"] in (None, "")


def test_rename_genre_rejects_empty_source(client):
    assert client.post("/api/tags/genres/rename", json={"old": " ", "new": "X"}).status_code == 400


def test_bulk_add_and_remove_genres(client, library):
    ids = [library["a"][0], library["c"][0], library["d"][0]]
    r = client.post("/api/tags/bulk", json={
        "track_ids": ids, "tags": {}, "genre_add": ["Jazz"], "genre_remove": ["Pop"],
    })
    assert r.status_code == 200
    by_name = {t["filename"]: t["genre"] for t in client.get("/api/library/tracks").json()["tracks"]}
    assert by_name["a.mp3"] == "Rock; Jazz"
    assert by_name["c.mp3"] == "Jazz"
    assert by_name["d.mp3"] == "Jazz"
    assert read_tags(library["a"][1])["genre"] == "Rock; Jazz"


def test_bulk_genre_ops_skip_untouched_tracks(client, library):
    ids = [library["b"][0], library["d"][0]]
    client.post("/api/tags/bulk", json={"track_ids": ids, "tags": {}, "genre_remove": ["Pop"]})
    assert client.get("/api/library/history").json() == []


def test_bulk_requires_some_change(client):
    assert client.post("/api/tags/bulk", json={"track_ids": [1], "tags": {}}).status_code == 400


def test_patch_normalizes_genre(client, library):
    tid, path = library["c"]
    client.patch(f"/api/tags/{tid}", json={"genre": " Pop ;Jazz;"})
    assert client.get(f"/api/library/track/{tid}").json()["genre"] == "Pop; Jazz"
    assert read_tags(path)["genre"] == "Pop; Jazz"


def test_genre_separators_setting_roundtrip(client):
    assert client.get("/api/config").json()["genre_separators"] == [";"]
    client.patch("/api/config", json={"genre_separators": [";", "/"]})
    assert client.get("/api/config").json()["genre_separators"] == [";", "/"]
