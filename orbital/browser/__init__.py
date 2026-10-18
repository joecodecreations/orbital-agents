"""Orbital Agents Browser Controller: the bridge between agents and the Chrome extension.

    from orbital.browser import BrowserClient
    BrowserClient().navigate("https://example.com")

Bridge: `python -m orbital.browser.server` (or `orbital browser serve`). Extension: browser-extension/.
"""

from orbital.browser.client import BrowserClient, BrowserError
from orbital.browser.nodes import Node, NodeError, list_nodes, resolve_node

SERVICE = "orbital-browser-bridge"

__all__ = ["SERVICE", "BrowserClient", "BrowserError", "Node", "NodeError", "list_nodes", "resolve_node"]
