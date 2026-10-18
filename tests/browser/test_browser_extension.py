"""The Chrome extension: static checks (manifest, files, syntax, generic content) and its real service
worker running in Node against a real bridge (fake_chrome.js stands in for Chrome's APIs)."""

from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess

import httpx
import pytest

from browser_helpers import EXTENSION_DIR, REPO, wait_until

NODE = shutil.which("node")
MANIFEST = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
JS_FILES = sorted(EXTENSION_DIR.rglob("*.js"))
HTML_FILES = sorted(EXTENSION_DIR.glob("*.html"))


def _png_size(path):
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


def test_manifest_basics():
    assert MANIFEST["manifest_version"] == 3
    assert MANIFEST["name"] == "Orbital Agents Browser Controller"
    assert MANIFEST["version"] == "0.1.0"
    assert MANIFEST["action"]["default_title"] == "Orbital Agents Browser Controller"
    perms = set(MANIFEST["permissions"])
    for needed in ("tabs", "scripting", "storage", "debugger", "webRequest", "sidePanel", "alarms", "system.display"):
        assert needed in perms
    assert "http://127.0.0.1/*" in MANIFEST["host_permissions"]
    assert "externally_connectable" not in MANIFEST            # no web page may message the extension


def test_every_manifest_file_exists():
    refs = [MANIFEST["background"]["service_worker"], MANIFEST["action"]["default_popup"],
            MANIFEST["options_ui"]["page"], MANIFEST["side_panel"]["default_path"]]
    refs += list(MANIFEST["icons"].values()) + list(MANIFEST["action"]["default_icon"].values())
    for cs in MANIFEST.get("content_scripts", []):
        refs += cs.get("js", []) + cs.get("css", [])
    for ref in refs:
        assert (EXTENSION_DIR / ref).is_file(), ref


def test_icons_have_their_sizes():
    for size, ref in MANIFEST["icons"].items():
        assert _png_size(EXTENSION_DIR / ref) == (int(size), int(size))


def test_scripts_loaded_by_the_worker_exist():
    bg = (EXTENSION_DIR / "background.js").read_text(encoding="utf-8")
    imports = re.search(r"importScripts\(([^)]*)\)", bg).group(1)
    for ref in re.findall(r'"([^"]+)"', imports):
        assert (EXTENSION_DIR / ref).is_file(), ref
    capture = (EXTENSION_DIR / "lib" / "capture.js").read_text(encoding="utf-8")
    for ref in re.findall(r'js: \["([^"]+)"\]', capture):
        assert (EXTENSION_DIR / ref).is_file(), ref                # registered while capture is on
    settings = (EXTENSION_DIR / "lib" / "settings.js").read_text(encoding="utf-8")
    page = re.search(r'AUTOMATION_PAGE = "([^"]+)"', settings).group(1)
    assert (EXTENSION_DIR / page).is_file()


def test_html_pages_reference_existing_files_and_no_inline_scripts():
    for html in HTML_FILES:
        text = html.read_text(encoding="utf-8")
        for ref in re.findall(r'(?:src|href)="([^"#:]+)"', text):
            assert (EXTENSION_DIR / ref).is_file(), f"{html.name} -> {ref}"
        # Manifest V3 pages may not run inline scripts.
        for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", text, re.S):
            assert "src=" in attrs and not body.strip(), f"inline script in {html.name}"
        assert not re.search(r"\son[a-z]+=\"", text), f"inline event handler in {html.name}"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("js", JS_FILES, ids=lambda p: str(p.relative_to(EXTENSION_DIR)))
def test_javascript_parses(js):
    out = subprocess.run([NODE, "--check", str(js)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


DENY = ["grok", "instagram", "facebook", "threads.net", "youtube", "reddit", "envato", "tiktok",
        "lg qhd", "9876", "sapisid"]


def test_extension_and_bridge_are_generic():
    """No private sites, crawlers or social-posting flows: this is a general controller."""
    files = [p for p in EXTENSION_DIR.rglob("*") if p.suffix in (".js", ".html", ".json", ".css", ".md")]
    files += [p for p in (REPO / "orbital" / "browser").rglob("*.py")]
    hits = []
    for f in files:
        text = f.read_text(encoding="utf-8").lower()
        for word in DENY:
            if word == "sapisid" and f.name == "capture.py":
                continue            # the bridge redacts that cookie name, a generic safety rule
            if word in text:
                hits.append(f"{f.relative_to(REPO)}: {word}")
    assert not hits, hits


def test_every_action_is_documented():
    actions_js = (EXTENSION_DIR / "lib" / "actions.js").read_text(encoding="utf-8")
    actions = set(re.findall(r"^ACTIONS\.([a-z_]+) =", actions_js, re.M))
    readme = (REPO / "orbital" / "browser" / "README.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+)`", readme, re.M))
    assert actions, "no actions found"
    assert actions - documented == set(), f"undocumented actions: {sorted(actions - documented)}"
    assert documented - actions == set(), f"README lists actions that do not exist: {sorted(documented - actions)}"
    for required in ("navigate", "click", "type", "eval", "screenshot", "upload_file", "list_tabs", "wait_for",
                     "ax_dump", "dom", "set_capture", "automation_window", "page_request"):
        assert required in actions


# ── the real service worker in Node ───────────────────────────────

@pytest.fixture
def node_extension(bridge):
    if NODE is None:
        pytest.skip("node is not installed")
    proc = subprocess.Popen([NODE, str(REPO / "tests" / "browser" / "fake_chrome.js"), str(EXTENSION_DIR), str(bridge.port), "180"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_until(lambda: httpx.get(bridge.url + "/health").json()["extension_connected"], timeout=20)
    except AssertionError:
        proc.kill()
        raise AssertionError("extension never polled: " + proc.communicate()[1][-2000:]) from None
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.communicate()


def cmd(bridge, action, **params):
    r = httpx.post(bridge.url + "/command", json={"action": action, "params": params, "timeout": 8}, timeout=15)
    return r.json()


def test_worker_answers_commands(bridge, node_extension):
    assert cmd(bridge, "ping")["result"]["pong"] is True
    status = cmd(bridge, "status")["result"]
    assert status["version"] == "0.1.0" and status["port"] == bridge.port
    assert status["bridgeUrl"] == f"http://127.0.0.1:{bridge.port}"
    for action in ("navigate", "click", "type", "eval", "screenshot", "upload_file", "ax_dump"):
        assert action in status["actions"]
    tabs = cmd(bridge, "tabs")["result"]                     # alias of list_tabs
    assert tabs[0]["url"] == "https://user.example/"
    bad = cmd(bridge, "no_such_action")
    assert bad["ok"] is False and "unknown action" in bad["error"]
    assert httpx.get(bridge.url + "/status").json()["extension"]["build"] == status["build"]


def test_worker_opens_agent_window_without_touching_user_tab(bridge, node_extension):
    nav = cmd(bridge, "navigate", url="example.com")["result"]
    assert nav["url"] == "https://example.com" and nav["session"] == "main" and nav["automation"] is True
    tabs = cmd(bridge, "list_tabs")["result"]
    user = next(t for t in tabs if t["url"] == "https://user.example/")
    assert user["automation"] is False                      # the user's tab is left alone
    agent = [t for t in tabs if t["session"] == "main"]
    assert {t["url"].split("?")[0].rsplit("/", 1)[-1] for t in agent} >= {"automation.html", "example.com"}
    second = cmd(bridge, "navigate", url="https://example.org/", session="second")["result"]
    assert second["session"] == "second" and second["windowId"] != nav["windowId"]
    win = cmd(bridge, "automation_window")["result"]
    assert {s["session"] for s in win["sessions"]} == {"main", "second"}
    shot = cmd(bridge, "screenshot")["result"]
    assert shot["dataUrl"].startswith("data:image/png;base64,") and shot["via"] == "cdp"
    assert cmd(bridge, "eval", code="document.title")["result"]["value"] == "evaluated:document.title"


def test_worker_capture_flows_to_the_bridge_redacted(bridge, node_extension):
    assert cmd(bridge, "set_capture", enabled=True)["result"] == {"capturing": True}
    events = wait_until(lambda: httpx.get(bridge.url + "/network").json()["events"], timeout=20)
    e = events[0]
    assert e["headers"]["Authorization"] == "[redacted]" and "api_key=[redacted]" in e["url"]
    eps = httpx.get(bridge.url + "/endpoints").json()["endpoints"]
    assert eps[0]["path_key"] == "/v2/things/{id}" and eps[0]["auth_header_present"] is True
    assert cmd(bridge, "set_capture", enabled=False)["result"] == {"capturing": False}
    node_extension.terminate()
    out, _ = node_extension.communicate(timeout=15)
    report = json.loads(out.strip().splitlines()[-1])
    assert report["registered"] == []                       # page hook removed with capture off
    assert report["storage"]["capturing"] is False


def test_worker_follows_a_port_change(make_bridge, bridge):
    """Saving a new port (popup or options page) moves the worker to that bridge."""
    if NODE is None:
        pytest.skip("node is not installed")
    other = make_bridge()
    proc = subprocess.Popen([NODE, str(REPO / "tests" / "browser" / "fake_chrome.js"), str(EXTENSION_DIR), str(bridge.port), "180",
                             f"switch:2:{other.port}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_until(lambda: httpx.get(bridge.url + "/health").json()["extension_connected"], timeout=20)
        wait_until(lambda: other.state.extension_connected(), timeout=20)
        r = httpx.post(other.url + "/command", json={"action": "status", "timeout": 8}, timeout=15).json()
        assert r["ok"] and r["result"]["port"] == other.port
    finally:
        proc.kill()
        proc.communicate()


# ── optional: the real extension in Chrome for Testing ────────────

def _chrome_for_testing():
    import os
    from pathlib import Path

    env = os.environ.get("ORBITAL_TEST_CHROME")
    if env and Path(env).is_file():
        return env
    for pattern in ("chrome/*/chrome-mac-*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
                    "chrome/*/chrome-linux64/chrome", "chromium/*/chrome-linux/chrome"):
        found = sorted(Path("~/.cache/puppeteer").expanduser().glob(pattern))
        if found:
            return str(found[-1])
    for name in ("chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    return None


@pytest.mark.slow
def test_real_chrome_end_to_end(bridge, tmp_path):
    """Load the unpacked extension in Chrome for Testing (branded Chrome ignores --load-extension),
    point it at the test bridge through the DevTools protocol, and drive a page."""
    chrome = _chrome_for_testing()
    if not chrome:
        pytest.skip("no Chrome for Testing / Chromium found (set ORBITAL_TEST_CHROME)")
    import asyncio
    import socket

    import websockets

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        devtools = s.getsockname()[1]
    profile = tmp_path / "profile"
    proc = subprocess.Popen([chrome, "--headless=new", f"--user-data-dir={profile}", f"--remote-debugging-port={devtools}",
                             f"--load-extension={EXTENSION_DIR}", f"--disable-extensions-except={EXTENSION_DIR}",
                             "--no-first-run", "--no-default-browser-check", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        def worker():
            try:
                targets = httpx.get(f"http://127.0.0.1:{devtools}/json/list", timeout=2).json()
            except (httpx.HTTPError, ValueError):
                return None
            return next((t for t in targets if t.get("type") == "service_worker" and t["url"].endswith("/background.js")), None)

        target = wait_until(worker, timeout=30, interval=0.3)

        async def set_port():
            async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
                expr = f"chrome.storage.local.set({{bridgePort: {bridge.port}}}).then(() => 'ok')"
                await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "awaitPromise": True}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == 1:
                        return msg

        asyncio.run(set_port())
        wait_until(lambda: httpx.get(bridge.url + "/health").json()["extension_connected"], timeout=30)
        # Serve the page over http: extensions cannot script file:// pages unless the user allows it.
        site = tmp_path / "site"
        site.mkdir()
        (site / "index.html").write_text("<title>E2E</title><input id=q><button id=go onclick=\"document.title='clicked:'+q.value\">Go</button>")
        import functools
        import threading
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=str(site)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        nav = cmd(bridge, "navigate", url=f"http://127.0.0.1:{httpd.server_address[1]}/index.html")
        assert nav["ok"], nav
        assert cmd(bridge, "type", selector="#q", text="hello")["ok"]
        assert cmd(bridge, "click", selector="#go")["ok"]
        assert cmd(bridge, "eval", code="document.title")["result"]["value"] == "clicked:hello"
        shot = cmd(bridge, "screenshot")
        assert shot["ok"] and shot["result"]["dataUrl"].startswith("data:image/png")
        httpd.shutdown()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)
