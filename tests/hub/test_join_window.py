"""Joining without the token: only while the join window is open, only from the owner's own tailnet."""

from __future__ import annotations

from hub_helpers import AUTH, JOIN, SATELLITE, make_client

from orbital.core.net import Peer


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def body(**extra) -> dict:
    out = {"name": "Mini", "machine": "mini", "ip": "127.0.0.1", "port": 17777, "os": "macos",
           "satellite_token": SATELLITE, "version": "0.1.0", "capabilities": {"agents": ["grok"]}}
    out.update(extra)
    return out


def owned(monkeypatch, yes: bool = True) -> None:
    peer = Peer(name="mini", dns="mini.example.ts.net", ip="100.64.0.9", os="macOS", online=True)
    monkeypatch.setattr("orbital.core.net.own_tailnet_peer", lambda ip: peer if yes else None)


def test_closed_window_asks_to_open_it(ports, monkeypatch):
    owned(monkeypatch)
    client, hub = make_client()
    res = client.post("/v1/satellites/join", json=body())
    assert res.status_code == 401 and res.json()["code"] == "join_closed"
    assert "orbital satellites invite" in res.json()["error"]
    bad = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": "wrong-token-xxxxxxxx"}, json=body())
    assert bad.status_code == 401 and bad.json()["code"] == "unauthorized"


def test_open_window_lets_the_owners_machines_in_without_a_token(ports, monkeypatch):
    owned(monkeypatch)
    clock = Clock()
    client, hub = make_client(clock=clock)
    assert client.post("/v1/satellites/invite", json={"minutes": 15}).status_code == 401, "only the app or the CLI opens it"
    opened = client.post("/v1/satellites/invite", headers=AUTH, json={"minutes": 15, "by": "cli"}).json()
    assert opened["open"] is True and opened["seconds_left"] == 900
    joined = client.post("/v1/satellites/join", json=body())
    assert joined.status_code == 200, joined.text
    assert joined.json()["join_token"] == JOIN, "it can rejoin later by itself"
    assert client.get("/v1/satellites/invite", headers=AUTH).json()["joined"] == ["Mini"]
    assert client.get("/v1/satellites", headers=AUTH).json()["join_window"]["open"] is True
    clock.now += 901
    assert client.get("/v1/satellites/invite", headers=AUTH).json()["open"] is False
    late = client.post("/v1/satellites/join", json=body(machine="laptop", name="Laptop", satellite_token=SATELLITE + "-2"))
    assert late.status_code == 401 and late.json()["code"] == "join_closed"


def test_someone_elses_device_is_refused_even_while_open(ports, monkeypatch):
    owned(monkeypatch, yes=False)
    client, hub = make_client()
    client.post("/v1/satellites/invite", headers=AUTH, json={})
    res = client.post("/v1/satellites/join", json=body())
    assert res.status_code == 401
    closed = client.delete("/v1/satellites/invite", headers=AUTH).json()
    assert closed["open"] is False


def test_the_token_still_works_with_the_window_closed(ports, monkeypatch):
    owned(monkeypatch, yes=False)
    client, hub = make_client()
    res = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=body())
    assert res.status_code == 200 and "join_token" not in res.json()
