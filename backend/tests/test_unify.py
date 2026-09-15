"""Album tag unification: proposal rules, preview API, background job, undo."""
import time

import pytest

from core.database import db
from core.tagger import read_tags, write_tags
from core.unify import UNIFY_FIELDS, propose


def _t(**tags):
    return {f: tags.get(f) for f in UNIFY_FIELDS}


# ─── Rules ────────────────────────────────────────────────────────────────────

def test_consistent_album_has_no_changes():
    tracks = [_t(genre="Rock; Pop", year="2010-07-23", album="A") for _ in range(3)]
    assert propose(tracks) == {}


def test_genre_keeps_majority_genres_in_the_most_common_order():
    tracks = [_t(genre="Metal; Industrial; 2000")] * 9 + [
        _t(genre="Industrial; Metal; Heavy Metal; Electronic; 2000"),
        _t(genre="Metal; 2000"),
    ]
    change = propose(tracks)["genre"]
    # Order follows the most common list, so the 9 matching tracks stay untouched.
    assert change["after"] == "Metal; Industrial; 2000"
    assert change["before"] == {
        "Metal; Industrial; 2000": 9,
        "Industrial; Metal; Heavy Metal; Electronic; 2000": 1,
        "Metal; 2000": 1,
    }
    assert change["changed"] == 2


def test_genre_fills_tracks_without_one():
    tracks = [_t(genre="Jazz")] * 3 + [_t(genre=None)]
    assert propose(tracks)["genre"]["after"] == "Jazz"


def test_genre_without_majority_is_left_alone():
    tracks = [_t(genre="Rock"), _t(genre="Pop"), _t(genre="Jazz"), _t(genre=None)]
    assert "genre" not in propose(tracks)


def test_year_takes_earliest_as_four_digits():
    tracks = [_t(year="2010-07-23")] * 16 + [_t(year="2010")] * 14 + [_t(year="1984")] * 2
    change = propose(tracks)["year"]
    assert change["after"] == "1984"
    assert change["changed"] == 30


def test_year_ignores_unparseable_values():
    tracks = [_t(year="unknown"), _t(year="1999-01-01"), _t(year=None)]
    assert propose(tracks)["year"]["after"] == "1999"
    assert "year" not in propose([_t(year="unknown"), _t(year="n/a")])


def test_text_fields_take_most_common_non_empty_value():
    tracks = [_t(album_artist="Beatles")] * 3 + [_t(album_artist="The Beatles")] + [_t(album_artist=None)]
    change = propose(tracks)["album_artist"]
    assert change["after"] == "Beatles"
    assert change["changed"] == 2


def test_text_field_tie_is_left_alone():
    assert "album" not in propose([_t(album="A"), _t(album="B")])


def test_compilation_counts_empty_as_a_vote():
    # One stray compilation flag must not turn the whole album into one.
    tracks = [_t(compilation="1")] + [_t(compilation=None)] * 5
    assert propose(tracks)["compilation"]["after"] == ""


def test_fields_filter():
    tracks = [_t(genre="Rock", year="1990"), _t(genre="Pop", year="1991"), _t(genre="Rock", year="1990")]
    assert set(propose(tracks)) == {"genre", "year"}
    assert set(propose(tracks, fields=["year"])) == {"year"}


# ─── API + job ────────────────────────────────────────────────────────────────

def _insert(path, **extra):
    cols = {"path": str(path), "filename": path.name, "directory": str(path.parent),
            "format": path.suffix.lstrip("."), "scanned_at": 1.0, **extra}
    with db() as conn:
        names = ", ".join(cols)
        ph = ", ".join("?" * len(cols))
        return conn.execute(f"INSERT INTO tracks ({names}) VALUES ({ph})", list(cols.values())).lastrowid


@pytest.fixture()
def albums(client, tmp_path, make_audio):
    """Album A: inconsistent genre/year over 3 real files. Album B: consistent."""
    out = {}
    specs = {
        "A": [("Rock; Pop", "2001-05-01"), ("Rock", "2001"), ("Rock; Pop", "1999")],
        "B": [("Jazz", "1959"), ("Jazz", "1959")],
    }
    for album, tracks in specs.items():
        d = tmp_path / "music" / album
        d.mkdir(parents=True)
        for i, (genre, year) in enumerate(tracks):
            src = make_audio(".flac")
            f = src.rename(d / f"{i}.flac")
            write_tags(f, {"genre": genre, "year": year, "album": album, "title": f"t{i}"})
            out[f"{album}{i}"] = (_insert(f, genre=genre, year=year, album=album, title=f"t{i}"), f)
    return out


def _wait_job(client, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_preview_lists_only_inconsistent_albums(client, albums):
    body = client.get("/api/library/album-inconsistencies").json()
    assert len(body) == 1
    album = body[0]
    assert album["directory"].endswith("/A")
    assert album["album"] == "A"
    assert album["track_count"] == 3
    assert album["changes"]["genre"]["after"] == "Rock; Pop"
    assert album["changes"]["year"]["after"] == "1999"
    assert client.get("/api/library/issues").json()["inconsistent_albums"] == 1


def test_unify_job_rewrites_files_and_is_undoable(client, albums):
    directory = str(albums["A0"][1].parent)
    r = client.post("/api/tags/unify-albums", json={"directories": [directory], "fields": ["genre", "year"]})
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["kind"] == "unify"
    assert job["scanned"] == 2  # A2 already matches

    for key in ("A0", "A1", "A2"):
        tid, path = albums[key]
        row = client.get(f"/api/library/track/{tid}").json()
        assert (row["genre"], row["year"]) == ("Rock; Pop", "1999")
        tags = read_tags(path)
        assert (tags["genre"], tags["year"]) == ("Rock; Pop", "1999")
    assert client.get("/api/library/album-inconsistencies").json() == []

    change = client.get("/api/library/history").json()[0]
    assert "2 tracks" in change["summary"]
    client.post(f"/api/library/history/{change['id']}/undo")
    assert read_tags(albums["A1"][1])["year"] == "2001"


def test_unify_only_touches_requested_fields(client, albums):
    directory = str(albums["A0"][1].parent)
    r = client.post("/api/tags/unify-albums", json={"directories": [directory], "fields": ["year"]})
    _wait_job(client, r.json()["job_id"])
    assert client.get(f"/api/library/track/{albums['A1'][0]}").json()["genre"] == "Rock"


def test_unify_rejects_unknown_field(client):
    r = client.post("/api/tags/unify-albums", json={"directories": ["/x"], "fields": ["title"]})
    assert r.status_code == 400


def test_large_undo_runs_as_job(client, albums, monkeypatch):
    import api.library
    monkeypatch.setattr(api.library, "UNDO_JOB_THRESHOLD", 1)
    directory = str(albums["A0"][1].parent)
    _wait_job(client, client.post("/api/tags/unify-albums",
                                  json={"directories": [directory], "fields": ["year"]}).json()["job_id"])
    change = client.get("/api/library/history").json()[0]
    r = client.post(f"/api/library/history/{change['id']}/undo").json()
    job = _wait_job(client, r["job_id"])
    assert job["status"] == "done" and job["kind"] == "undo"
    assert read_tags(albums["A1"][1])["year"] == "2001"
    assert client.get("/api/library/history").json()[0]["undone"] == 1


def test_jobs_block_each_other(client, albums):
    from core.tasks import create_job
    create_job("unify")  # left pending: counts as active
    assert client.post("/api/jobs/scan").status_code == 409
    assert client.post("/api/tags/unify-albums", json={"directories": [], "fields": ["year"]}).status_code == 409
