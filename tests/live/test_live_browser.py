"""Live: every browser node (this machine + satellites with a browser) drives a real Chrome tab."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live

TEST_URL = "https://example.com"


def _nodes(cfg):
    from orbital.browser.nodes import list_nodes
    nodes, error = list_nodes(cfg, remote=cfg.role == "hub")
    if not cfg.get("browser.enabled"):
        nodes = [n for n in nodes if n.kind != "local"]
    return nodes, error


def test_browser_nodes(cfg):
    """browser: each node's bridge answers, its extension is connected, and it opens, reads and closes a tab"""
    try:
        from orbital.browser.client import BrowserClient, BrowserError
    except ImportError:
        pytest.skip("browser controller isn't in this checkout")
    nodes, error = _nodes(cfg)
    if not nodes:
        pytest.skip("no browser nodes (browser.enabled = false here and no satellite has a browser)")
    allow_missing_ext = os.environ.get("ORBITAL_LIVE_ALLOW_NO_EXTENSION") == "1"
    failed, skipped, passed = [], [], []
    for node in nodes:
        label = f"{node.name}" if node.kind == "local" else f"{node.name} (satellite)"
        if node.kind == "satellite" and node.status and node.status != "online":
            skipped.append(f"{label}: offline")
            continue
        client = BrowserClient(node, session="orbital-live-test", timeout=45, config=cfg)
        try:
            health = client.health()
            if not health.get("ok", True):
                failed.append(f"{label}: bridge unhealthy")
                continue
            status = client.status(extension=False)
            if not (status.get("extension") or {}).get("connected") and not health.get("extension_connected"):
                msg = f"{label}: extension not connected (load browser-extension/ in chrome://extensions)"
                (skipped if allow_missing_ext else failed).append(msg)
                continue
            opened = client.navigate(TEST_URL, new_tab=True, session="orbital-live-test")
            tab_id = (opened or {}).get("tabId")
            try:
                title = (opened or {}).get("title") or client.eval("document.title")
                if "Example Domain" not in str(title):
                    failed.append(f"{label}: page title was {title!r}")
                else:
                    passed.append(label)
            finally:
                if tab_id is not None:
                    try:
                        client.close_tab(tab_id)
                    except BrowserError:
                        pass
        except BrowserError as exc:
            failed.append(f"{label}: {exc}")
    if error and cfg.role == "hub":
        failed.append(f"satellite list: {error}")
    assert not failed, "; ".join(failed + skipped)
    if not passed and skipped:
        pytest.skip("; ".join(skipped))
