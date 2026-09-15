"""Artist view: MusicBrainz genre fetching (HTTP mocked), cache, and genre retagging."""
import time

import pytest

import core.artist_genres as ag
from core.database import db
from core.tagger import read_tags, write_tags

NIRVANA_US = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
NIRVANA_UK = "9282c8b4-ca0b-4c6b-b7e3-4f7762dfc4d6"


class FakeMB:
    """Stands in for MusicBrainz: records calls, serves canned JSON."""

    def __init__(self):
        self.calls = []
        self.search = {
            "nirvana": [
                {"id": NIRVANA_US, "score": 100, "name": "Nirvana", "disambiguation": "US grunge band"},
                {"id": NIRVANA_UK, "score": 75, "name": "Nirvana", "disambiguation": "60s band from the UK"},
                {"id": "c3a6", "score": 74, "name": "Approaching Nirvana"},
            ],
            "daft punk": [{"id": "dp", "score": 100, "name": "Daft Punk"}],
            "nobody": [{"id": "x", "score": 40, "name": "Somebody Else"}],
        }
        self.artists = {
            NIRVANA_US: {"name": "Nirvana", "disambiguation": "US grunge band",
                         "genres": [{"name": "rock", "count": 25}, {"name": "grunge", "count": 66},
                                    {"name": "punk rock", "count": 3}]},
            NIRVANA_UK: {"name": "Nirvana", "disambiguation": "60s band from the UK",
                         "genres": [{"name": "psychedelic pop", "count": 4}]},
            "dp": {"name": "Daft Punk", "genres": [{"name": "french house", "count": 20}]},
        }

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if path == "artist":
            name = params["query"].split('"')[1].lower()
            return {"artists": self.search.get(name, [])}
        mbid = path.split("/", 1)[1]
        if mbid not in self.artists:
            raise ag.MusicBrainzError("404 Not Found")
        return {"id": mbid, **self.artists[mbid]}


@pytest.fixture()
def mb(monkeypatch):
    fake = FakeMB()
    monkeypatch.setattr(ag, "_mb_get", fake)
    return fake


def _insert(path, **extra):
    cols = {"path": str(path), "filename": path.name, "directory": str(path.parent),
            "format": path.suffix.lstrip("."), "scanned_at": 1.0, **extra}
    with db() as conn:
        names = ", ".join(cols)
        ph = ", ".join("?" * len(cols))
        return conn.execute(f"INSERT INTO tracks ({names}) VALUES ({ph})", list(cols.values())).lastrowid


# ─── Pure helpers ─────────────────────────────────────────────────────────────

def test_library_casing_prefers_existing_names():
    known = ["Punk Rock", "R&B"]
    assert ag.library_case("punk rock", known) == "Punk Rock"
    assert ag.library_case("r&b", known) == "R&B"
    assert ag.library_case("alternative rock", known) == "Alternative Rock"
    assert ag.library_case("k-pop", known) == "K-Pop"


def test_pick_match_prefers_exact_name_then_score():
    cands = FakeMB().search["nirvana"]
    assert ag.pick_match("nirvana", cands)["id"] == NIRVANA_US
    assert ag.pick_match("Somebody", FakeMB().search["nobody"]) is None


# ─── Fetching + cache ─────────────────────────────────────────────────────────

def test_fetch_by_name_search_stores_candidates(temp_db, tmp_path, mb):
    _insert(tmp_path / "a.mp3", artist="Nirvana")
    with db() as conn:
        row = ag.fetch_artist(conn, "Nirvana")
    assert row["mbid"] == NIRVANA_US
    assert row["disambiguation"] == "US grunge band"
    assert [g["name"] for g in row["genres"]] == ["grunge", "rock", "punk rock"]  # by votes
    assert [c["id"] for c in row["candidates"]] == [NIRVANA_US, NIRVANA_UK]  # exact-name homonyms only
    with db() as conn:
        assert ag.cached(conn, "Nirvana")["mbid"] == NIRVANA_US


def test_fetch_uses_musicbrainz_id_from_tags(temp_db, tmp_path, mb):
    _insert(tmp_path / "a.mp3", artist="Nirvana", album_artist="Nirvana", mb_album_artist_id=NIRVANA_UK)
    with db() as conn:
        row = ag.fetch_artist(conn, "Nirvana")
    assert row["mbid"] == NIRVANA_UK
    assert [p for p, _ in mb.calls] == [f"artist/{NIRVANA_UK}"]  # no search needed


def test_fetch_with_explicit_mbid_overrides_match(temp_db, tmp_path, mb):
    _insert(tmp_path / "a.mp3", artist="Nirvana")
    with db() as conn:
        ag.fetch_artist(conn, "Nirvana")
        row = ag.fetch_artist(conn, "Nirvana", mbid=NIRVANA_UK)
    assert row["mbid"] == NIRVANA_UK
    assert row["genres"] == [{"name": "psychedelic pop", "count": 4}]
    assert len(row["candidates"]) == 2  # previous candidates kept to switch back


def test_fetch_unmatched_artist_caches_empty_result(temp_db, tmp_path, mb):
    _insert(tmp_path / "a.mp3", artist="Nobody")
    with db() as conn:
        row = ag.fetch_artist(conn, "Nobody")
    assert row["mbid"] is None and row["genres"] == [] and row["error"] is None


def test_fetch_error_is_recorded(temp_db, tmp_path, mb):
    with db() as conn:
        row = ag.fetch_artist(conn, "Nirvana", mbid="missing")
    assert "404" in row["error"]


# ─── API ──────────────────────────────────────────────────────────────────────

def _wait_job(client, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture()
def library(client, tmp_path, make_audio):
    """Nirvana album (album artist set), a Nirvana track on a compilation, and Daft Punk."""
    music = tmp_path / "music"
    specs = [
        ("nevermind/1.flac", dict(artist="Nirvana", album_artist="Nirvana", genre="Grunge; Rock")),
        ("nevermind/2.flac", dict(artist="Nirvana", album_artist=None, genre="Punk Rock")),
        ("compil/1.flac", dict(artist="Nirvana", album_artist="Various Artists", genre="Pop")),
        ("discovery/1.flac", dict(artist="Daft Punk", album_artist="Daft Punk", genre="House")),
    ]
    out = {}
    for rel, tags in specs:
        f = music / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        make_audio(".flac").rename(f)
        write_tags(f, {k: v for k, v in tags.items() if v})
        out[rel] = (_insert(f, **tags), f)
    return out


def test_artist_list_groups_by_album_artist(client, library, mb):
    body = client.get("/api/artists").json()
    assert [(a["artist"], a["track_count"]) for a in body] == [
        ("Daft Punk", 1), ("Nirvana", 2), ("Various Artists", 1)]
    assert all(a["fetched"] is False for a in body)
    tracks = client.get("/api/library/tracks", params={"artist_key": "Nirvana"}).json()["tracks"]
    assert sorted(t["path"].split("music/")[1] for t in tracks) == ["nevermind/1.flac", "nevermind/2.flac"]


def test_artist_detail_fetch_and_casing(client, library, mb):
    detail = client.get("/api/artists/detail", params={"name": "Nirvana"}).json()
    assert detail["current_genres"] == [
        {"genre": "Grunge", "track_count": 1}, {"genre": "Punk Rock", "track_count": 1}, {"genre": "Rock", "track_count": 1}]
    assert detail["mb"] is None

    fetched = client.post("/api/artists/fetch", json={"artist": "Nirvana"}).json()
    labels = [g["label"] for g in fetched["mb"]["genres"]]
    assert labels == ["Grunge", "Rock", "Punk Rock"]  # library casing ("Punk Rock" exists)
    # A track without album artist is keyed by its artist column: must not look fetched.
    _insert(library["compil/1.flac"][1].parent / "solo.flac", artist="Solo")
    assert [(a["artist"], a["fetched"]) for a in client.get("/api/artists").json()] == [
        ("Daft Punk", False), ("Nirvana", True), ("Solo", False), ("Various Artists", False)]


def test_retag_replace_and_add_are_undoable(client, library, mb):
    r = client.post("/api/artists/retag", json={"artist": "Nirvana", "genres": ["Grunge", "Alternative Rock"], "mode": "replace"})
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done" and job["kind"] == "retag" and job["scanned"] == 2
    assert read_tags(library["nevermind/2.flac"][1])["genre"] == "Grunge; Alternative Rock"
    assert read_tags(library["compil/1.flac"][1])["genre"] == "Pop"  # compilation track untouched

    r = client.post("/api/artists/retag", json={"artist": "Nirvana", "genres": ["Rock", "Grunge"], "mode": "add"})
    _wait_job(client, r.json()["job_id"])
    assert read_tags(library["nevermind/1.flac"][1])["genre"] == "Grunge; Alternative Rock; Rock"

    change = client.get("/api/library/history").json()[0]
    assert "Nirvana" in change["summary"]
    client.post(f"/api/library/history/{change['id']}/undo")
    assert read_tags(library["nevermind/1.flac"][1])["genre"] == "Grunge; Alternative Rock"


def test_retag_validation(client, library):
    assert client.post("/api/artists/retag", json={"artist": "Nirvana", "genres": [], "mode": "replace"}).status_code == 400
    assert client.post("/api/artists/retag", json={"artist": "Nirvana", "genres": ["X"], "mode": "zap"}).status_code == 422


def test_fetch_all_job_fills_missing_cache_without_blocking_writes(client, library, mb):
    client.post("/api/artists/fetch", json={"artist": "Nirvana"})
    calls_before = len(mb.calls)
    r = client.post("/api/artists/fetch-all", json={})
    job = _wait_job(client, r.json()["job_id"])
    assert job["kind"] == "fetch" and job["status"] == "done" and job["total"] == 2  # Daft Punk + Various
    assert not any("nirvana" in str(p).lower() for _, p in mb.calls[calls_before:])
    assert client.get("/api/artists/detail", params={"name": "Daft Punk"}).json()["mb"]["mbid"] == "dp"


def test_fetch_job_does_not_block_retag_but_blocks_another_fetch(client, library):
    from core.tasks import create_job
    create_job("fetch")  # left pending
    assert client.post("/api/artists/fetch-all", json={}).status_code == 409
    r = client.post("/api/artists/retag", json={"artist": "Daft Punk", "genres": ["House"], "mode": "add"})
    assert r.status_code == 200
    # ...and the global running-job lookup ignores it, so scans still start.
    from core.tasks import active_job
    assert active_job() is None or active_job()["kind"] != "fetch"


# ─── Grouping by the artist tag (Artist tab) ──────────────────────────────────

def test_detail_and_retag_by_artist_tag_include_compilation_tracks(client, library, mb):
    detail = client.get("/api/artists/detail", params={"name": "Nirvana", "by": "artist"}).json()
    assert detail["track_count"] == 3  # the compilation track counts too
    r = client.post("/api/artists/retag",
                    json={"artist": "Nirvana", "genres": ["Grunge"], "mode": "replace", "by": "artist"})
    job = _wait_job(client, r.json()["job_id"])
    assert job["scanned"] == 3
    assert read_tags(library["compil/1.flac"][1])["genre"] == "Grunge"


def test_fetch_by_artist_tag_uses_track_artist_id(client, library, mb):
    with db() as conn:
        conn.execute("UPDATE tracks SET mb_artist_id = ?, mb_album_artist_id = 'va' WHERE artist = 'Nirvana'",
                     (NIRVANA_UK,))
    body = client.post("/api/artists/fetch", json={"artist": "Nirvana", "by": "artist"}).json()
    assert body["mb"]["mbid"] == NIRVANA_UK
    assert body["track_count"] == 3


def test_retag_remove_drops_one_genre_keeping_others(client, library, mb):
    r = client.post("/api/artists/retag", json={"artist": "Nirvana", "genres": ["Rock"], "mode": "remove"})
    job = _wait_job(client, r.json()["job_id"])
    assert job["scanned"] == 1  # only nevermind/1 had Rock
    assert read_tags(library["nevermind/1.flac"][1])["genre"] == "Grunge"
    assert read_tags(library["nevermind/2.flac"][1])["genre"] == "Punk Rock"
    assert "Removed genres" in client.get("/api/library/history").json()[0]["summary"]
