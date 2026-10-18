"""hub.allow_networks is enforced before anything else, for HTTP and websockets."""

from __future__ import annotations

import pytest

from hub_helpers import AUTH, make_client, write_config


@pytest.mark.parametrize("ip", ["192.168.1.5", "203.0.113.7", "10.0.0.2", "2001:db8::1", "testclient"])
def test_outside_networks_get_403_on_every_route(ports, ip):
    client, hub = make_client(client=(ip, 40000))
    for method, path in (("get", "/v1/health"), ("get", "/v1/hub/info"), ("get", "/v1/catalog"), ("post", "/v1/login"),
                         ("post", "/v1/satellites/join")):
        res = getattr(client, method)(path, headers=AUTH)
        assert res.status_code == 403, (ip, path)
        assert res.json()["code"] == "forbidden"


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "100.101.102.103", "fd7a:115c:a1e0::1"])
def test_loopback_and_tailscale_are_answered(ports, ip):
    client, hub = make_client(client=(ip, 40000))
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/catalog", headers=AUTH).status_code == 200


def test_websocket_from_outside_is_closed(ports):
    from starlette.websockets import WebSocketDisconnect

    client, hub = make_client(client=("192.168.1.5", 40000))
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/live", headers=AUTH) as ws:
            ws.receive_json()
    assert closed.value.code == 4403


def test_a_lan_range_can_be_added(home):
    write_config(home, extra="")
    text = (home / "config.toml").read_text().replace(
        "[hub]\n", '[hub]\nallow_networks = ["127.0.0.0/8", "192.168.1.0/24"]\n')
    (home / "config.toml").write_text(text)
    lan, _ = make_client(client=("192.168.1.5", 40000))
    assert lan.get("/v1/health").status_code == 200
    tailnet, _ = make_client(client=("100.101.102.103", 40000))
    assert tailnet.get("/v1/health").status_code == 403, "only the listed networks"
