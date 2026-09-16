"""Album years: one lookup per album (HTTP mocked), cache, review and apply."""
import pytest

import core.album_years as ay
from core.artist_genres import MusicBrainzError, ProviderError
from core.database import db
from core.tagger import read_tags, write_tags
from tests.conftest import FFMPEG
from tests.test_genres import _insert, _make_mp3

RELEASE = "11111111-2222-3333-4444-555555555555"


class FakeMB:
    def __init__(self):
        self.calls = []

    def __call__(self, path, params):
        self.calls.append(path)
        if path == f"release/{RELEASE}":
            return {"release-group": {"id": "rg-dsotm", "first-release-date": "1973-03-01"}}
        if path == "release-group":
            if "Nevermind" in params["query"]:
                return {"release-groups": [
                    {"id": "rg-live", "score": 100, "title": "Nevermind", "first-release-date": "2011"},
                    {"id": "rg-orig", "score": 95, "title": "Nevermind", "first-release-date": "1991-09-24"},
                    {"id": "rg-other", "score": 60, "title": "Nevermind Else", "first-release-date": "1980"},
                ]}
            if "Down" in params["query"]:
                raise MusicBrainzError("503 Service Unavailable")
            return {"release-groups": []}
        raise AssertionError(path)


@pytest.fixture()
def mb(monkeypatch):
    fake = FakeMB()
    monkeypatch.setattr(ay, "_mb_get", fake)
    monkeypatch.setattr(ay, "_discogs_token", lambda: "")
    monkeypatch.setattr(ay, "itunes_year", lambda artist, album: "2001" if album == "Obscure" else None)
    return fake


def test_release_id_is_read_directly(mb):
    assert ay.fetch_year("Pink Floyd", "The Dark Side of the Moon", RELEASE) == \
        {"year": "1973", "source": "musicbrainz", "mbid": "rg-dsotm", "error": None}
    assert mb.calls == [f"release/{RELEASE}"]


def test_search_picks_earliest_same_title_group(mb):
    assert ay.fetch_year("Nirvana", "Nevermind (Deluxe Edition)", None)["year"] == "1991"


def test_fallback_and_errors(mb):
    assert ay.fetch_year("X", "Obscure", None) == {"year": "2001", "source": "itunes", "mbid": None, "error": None}
    down = ay.fetch_year("X", "Down", None)
    assert down["year"] is None and "MusicBrainz: 503" in down["error"]


def test_title_normalisation():
    assert ay._norm("Abbey Road (2019 Remaster)") == ay._norm("abbey road")
    assert ay._norm("Live (at Leeds)") != ay._norm("Live")


@pytest.fixture()
def albums(client, tmp_path):
    """Two albums of real MP3s: one tagged with a release id, one to search."""
    if not FFMPEG:
        pytest.skip("ffmpeg not available")
    out = {}
    for folder, album, year, rid in [("dsotm", "The Dark Side of the Moon", "2011", RELEASE),
                                     ("nevermind", "Nevermind", "", None)]:
        d = tmp_path / folder
        d.mkdir()
        for n in (1, 2):
            f = d / f"{n}.mp3"
            _make_mp3(f)
            write_tags(f, {"album": album, "year": year})
            out[f"{folder}{n}"] = f
            _insert(f, album=album, album_artist="Pink Floyd" if rid else "Nirvana",
                    year=year or None, mb_album_id=rid)
    return out


def test_fetch_review_apply_undo(client, albums, mb):
    job = client.post("/api/albums/years/fetch", json={}).json()
    assert client.get(f"/api/jobs/{job['job_id']}").json()["scanned"] == 2

    by_album = {a["album"]: a for a in client.get("/api/albums/years").json()}
    assert by_album["The Dark Side of the Moon"]["found_year"] == "1973"
    assert by_album["The Dark Side of the Moon"]["years"] == {"2011": 2}
    assert by_album["The Dark Side of the Moon"]["changed"] == 2
    assert by_album["Nevermind"]["found_year"] == "1991"

    # A second fetch only asks about albums not cached yet.
    calls = len(mb.calls)
    client.post("/api/albums/years/fetch", json={})
    assert len(mb.calls) == calls

    directory = by_album["The Dark Side of the Moon"]["directory"]
    job = client.post("/api/albums/years/apply", json={"directories": [directory]}).json()
    assert client.get(f"/api/jobs/{job['job_id']}").json()["scanned"] == 2
    assert read_tags(albums["dsotm1"])["year"] == "1973"
    assert read_tags(albums["nevermind1"])["year"] in (None, "")  # not selected

    change = client.get("/api/library/history").json()[0]
    client.post(f"/api/library/history/{change['id']}/undo")
    assert read_tags(albums["dsotm1"])["year"] == "2011"


def test_failed_albums_are_retried(client, albums, mb):
    with db() as conn:
        for a in ay.library_albums(conn):
            ay.store(conn, a["key"], {"year": None, "source": None, "mbid": None, "error": "boom"})
    client.post("/api/albums/years/fetch", json={})
    assert {a["found_year"] for a in client.get("/api/albums/years").json()} == {"1973", "1991"}


def test_discogs_matches_title_and_artist(monkeypatch):
    monkeypatch.setattr(ay, "_pace_fallback", lambda: None)
    monkeypatch.setattr(ay, "_get_json", lambda url: {"results": [
        {"title": "Air (2) - Moon Safari", "year": 1998},
        {"title": "Air - Moon Safari (Remixes)", "year": 1999},
        {"title": "Someone - Moon Safari", "year": 1990},
    ]})
    assert ay.discogs_year("Air", "Moon Safari", "tok") == "1998"


def test_itunes_errors_propagate(monkeypatch):
    def boom(url):
        raise ProviderError("403 Forbidden")
    monkeypatch.setattr(ay, "_pace_fallback", lambda: None)
    monkeypatch.setattr(ay, "_get_json", boom)
    with pytest.raises(ProviderError):
        ay.itunes_year("A", "B")
