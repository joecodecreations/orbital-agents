"""An unjoined satellite finds the hub and joins by itself once the hub opens joining."""

from __future__ import annotations

import time

from orbital.core.config import load_config
from orbital.satellite import heartbeat as hb
from orbital.satellite.join import JoinClosed


def test_waits_while_closed_then_joins(home, monkeypatch):
    (home / "config.toml").write_text('[system]\nrole = "satellite"\n')
    calls: list[tuple[str, str]] = []
    outcomes = [JoinClosed("joining is closed"), {"satellite_id": "mini"}]

    def fake_join(url, token, cfg, write_config=True):
        calls.append((url, token))
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(hb, "join_hub", fake_join)
    monkeypatch.setattr("orbital.core.net.discover_hubs", lambda port=7700: [
        {"name": "me", "url": "http://100.64.0.2:7700", "is_self": True},
        {"name": "hub", "url": "http://100.64.0.1:7700", "is_self": False}])
    beat = hb.Heartbeat(load_config(home))
    assert beat.auto_join(load_config(home)) is False
    assert calls == [("http://100.64.0.1:7700", "")], "found the hub on the tailnet, tried without a token"
    assert beat.auto_join(load_config(home)) is False, "waits AUTO_JOIN_EVERY between tries"
    beat._next_auto_join = time.monotonic() - 1
    assert beat.auto_join(load_config(home)) is True
    assert len(calls) == 2
