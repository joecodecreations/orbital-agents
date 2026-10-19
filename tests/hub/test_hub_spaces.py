"""The hub's own spaces: they claim sections over the sections' own tags."""

from __future__ import annotations

from hub_helpers import AUTH, make_client, section_body


def section(ident: str, space: str | None = None) -> dict:
    return section_body(ident, space=space) if space else section_body(ident)


def test_declared_spaces_claim_sections_and_survive_feed_tags(ports):
    client, hub = make_client()
    for doc in (section("games", "misc"), section("robux", "misc"), section("weather", "daily")):
        assert client.put(f"/v1/sections/{doc['id']}", headers=AUTH, json=doc).status_code == 200
    body = {"spaces": [{"id": "acme-games", "title": "Acme Games", "icon": "gamecontroller.fill", "order": 1,
                        "sections": ["games", "robux", "not-there"], "aliases": ["Acme Games LLC"]}]}
    saved = client.put("/v1/spaces", headers=AUTH, json=body)
    assert saved.status_code == 200, saved.text
    spaces = {s["id"]: s for s in client.get("/v1/catalog", headers=AUTH).json()["spaces"]}
    assert spaces["acme-games"]["sections"] == ["games", "robux"], "unknown sections are left out"
    assert spaces["acme-games"]["aliases"] == ["Acme Games LLC"]
    assert "misc" not in spaces, "a declared space wins over the sections' own tag"
    assert spaces["daily"]["sections"] == ["weather"]
    # A feed rewriting its section with the old tag doesn't move it.
    client.put("/v1/sections/games", headers=AUTH, json=section("games", "misc"))
    spaces = {s["id"]: s for s in client.get("/v1/catalog", headers=AUTH).json()["spaces"]}
    assert "games" in spaces["acme-games"]["sections"]
    assert client.put("/v1/spaces", headers=AUTH, json={"spaces": []}).json()["declared"] == []
    spaces = {s["id"]: s for s in client.get("/v1/catalog", headers=AUTH).json()["spaces"]}
    assert spaces["misc"]["sections"] == ["games", "robux"]


def test_bad_spaces_are_refused(ports):
    client, hub = make_client()

    def put(spaces):
        return client.put("/v1/spaces", headers=AUTH, json={"spaces": spaces})

    assert put([{"id": "Bad Id", "title": "x"}]).status_code == 400
    assert put([{"id": "a", "title": ""}]).status_code == 400
    assert put([{"id": "a", "title": "A", "sections": ["s"]}, {"id": "b", "title": "B", "sections": ["s"]}]).status_code == 400
    assert put([{"id": "a", "title": "A"}, {"id": "a", "title": "Again"}]).status_code == 400
    assert client.get("/v1/spaces").status_code == 401
