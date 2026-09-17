"""Whole-album lookup: release choice, track pairing (MusicBrainz mocked)."""
import pytest

import core.album_match as am
from core.database import db

REL_A = "aaaaaaaa-0000-0000-0000-000000000001"
REL_B = "aaaaaaaa-0000-0000-0000-000000000002"


def _release(rid, titles, discs=1):
    per = len(titles) // discs
    return {
        "id": rid, "title": "Nevermind", "date": "2011-09-26", "country": "XE",
        "artist-credit": [{"name": "Nirvana", "artist": {"id": "nirvana-id"}}],
        "label-info": [{"label": {"name": "DGC"}}],
        "release-group": {"first-release-date": "1991-09-24"},
        "media": [
            {"position": d + 1, "tracks": [
                {"position": i + 1, "title": t, "length": 200_000 + 10_000 * (d * per + i),
                 "recording": {"id": f"rec-{d}-{i}"}}
                for i, t in enumerate(titles[d * per:(d + 1) * per])
            ]}
            for d in range(discs)
        ],
    }


class FakeMB:
    def __init__(self):
        self.calls = []

    def __call__(self, path, params):
        self.calls.append(path)
        if path == "release":
            return {"releases": [
                {"id": REL_B, "score": 100, "title": "Nevermind (Deluxe)", "track-count": 40, "status": "Official"},
                {"id": REL_A, "score": 98, "title": "Nevermind", "track-count": 3, "status": "Official", "date": "1991"},
                {"id": "x", "score": 100, "title": "Nevermore", "track-count": 3},
            ]}
        if path == f"release/{REL_A}":
            return _release(REL_A, ["Smells Like Teen Spirit", "In Bloom", "Come as You Are"])
        if path == f"release/{REL_B}":
            return _release(REL_B, ["Intro", "Outro"])
        raise AssertionError(path)


@pytest.fixture()
def mb(monkeypatch):
    fake = FakeMB()
    monkeypatch.setattr(am, "_mb_get", fake)
    return fake


def _local(i, **kw):
    return {"id": i, "title": None, "artist": "Nirvana", "album": "Nevermind", "track_number": None,
            "disc_number": None, "duration": None, "filename": f"{i}.mp3", "mb_album_id": None, **kw}


def test_one_search_and_one_release_fetch_for_the_album(mb):
    local = [
        _local(1, title="smells like teen spirit"),
        _local(2, filename="02 - In Bloom.mp3"),                  # title from filename
        _local(3, title="Come As You Are (Remastered)", track_number="3", duration=221),
        _local(4, title="Something Else Entirely", duration=999),
    ]
    out = am.lookup_album(local)
    assert mb.calls == ["release", f"release/{REL_A}"]          # same track count wins
    assert out["release"]["year"] == "1991"                     # original, not this edition's 2011
    by_id = {m["track_id"]: m["update"] for m in out["matches"]}
    assert by_id[1]["title"] == "Smells Like Teen Spirit" and by_id[1]["track_number"] == "1"
    assert by_id[2]["title"] == "In Bloom" and by_id[2]["mb_track_id"] == "rec-0-1"
    assert by_id[3]["title"] == "Come as You Are"
    assert by_id[1]["album_artist"] == "Nirvana" and by_id[1]["mb_album_id"] == REL_A
    assert by_id[1]["label"] == "DGC"
    assert out["unmatched"] == [4]


def test_tagged_release_skips_the_search(mb):
    out = am.lookup_album([_local(1, title="In Bloom", mb_album_id=REL_A)])
    assert mb.calls == [f"release/{REL_A}"]
    assert out["matches"][0]["update"]["track_number"] == "2"


def test_positions_break_title_ties_across_discs():
    rel = {"disc_count": 2, "tracks": [
        {"title": "Intro", "track_number": "1", "disc_number": "1", "length": 60, "artist": "A",
         "mb_artist_id": None, "mb_track_id": "d1"},
        {"title": "Intro", "track_number": "1", "disc_number": "2", "length": 60, "artist": "A",
         "mb_artist_id": None, "mb_track_id": "d2"},
    ], "title": "X", "artist": "A", "artist_id": None, "year": "2000", "label": "L", "id": REL_A}
    matches, unmatched = am.match_tracks(
        [_local(1, title="Intro", track_number="1", disc_number="2"),
         _local(2, title="Intro", track_number="1", disc_number="1")], rel)
    assert {m["track_id"]: m["update"]["mb_track_id"] for m in matches} == {1: "d2", 2: "d1"}
    assert unmatched == []


def test_no_album_tag_or_no_release(mb, monkeypatch):
    assert am.lookup_album([_local(1, album=None)])["unmatched"] == [1]
    monkeypatch.setattr(am, "find_release", lambda *a: [])
    assert am.lookup_album([_local(1)])["release"] is None


def test_endpoint(client, mb):
    with db() as conn:
        for i, title in [(1, "In Bloom"), (2, "Come as you are")]:
            conn.execute(
                "INSERT INTO tracks (id, path, filename, directory, format, scanned_at, title, album, artist) "
                "VALUES (?, ?, ?, '/m', 'mp3', 1, ?, 'Nevermind', 'Nirvana')",
                (i, f"/m/{i}.mp3", f"{i}.mp3", title),
            )
    r = client.post("/api/lookup/album", json={"track_ids": [1, 2]}).json()
    assert {m["update"]["track_number"] for m in r["matches"]} == {"2", "3"}
    assert client.post("/api/lookup/album", json={"track_ids": [1], "release_id": "../x"}).status_code == 400
