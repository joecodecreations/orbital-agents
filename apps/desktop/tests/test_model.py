"""ui/model.js (spaces, areas, platforms, navigation) under node, against a generic payload.

The same rules as the iPhone/Mac app (Models/Spaces.swift, Views/SpaceViews.swift, Models/Navigation.swift).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MODEL = HERE.parent / "orbital_desktop" / "ui" / "model.js"


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(HERE / "model_cases.js"), str(MODEL)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_spaces_and_system(out):
    assert out["visible"] == ["studio", "homelab"]          # "system" is filed under System, not shown as a space
    assert out["system"] == ["health"]
    assert out["alias"] == "studio"                          # declared aliases resolve to the space


def test_areas_in_fixed_order(out):
    assert out["all"]["areas"] == ["overview", "site", "social", "players", "money", "videos", "pages"]
    assert out["homelab"] == ["overview", "health", "pages"]  # tagged with the space by name, from another section


def test_platforms_from_ledger_titles(out):
    assert out["all"]["platforms"] == ["ios", "android", "roblox"]
    assert out["ios"]["ledgers"] == ["ios"] and out["ios"]["refs"] == ["Puzzle Cat"]
    assert out["ios"]["areas"] == ["overview", "players", "money", "videos", "pages"]
    assert out["roblox"]["refs"] == ["Blocky Run"] and out["roblox"]["ledgers"] == ["robux"]
    assert out["android"]["ledgers"] == ["android"]
    assert out["all"]["products"] == ["Puzzle Cat"]          # product blocks take their node's title
    assert out["platformOf"] == ["ios", "android", "roblox", "web", None]


def test_switching_space_resets_a_missing_platform(out):
    assert out["keep"] == ["ios", "all", "all"]


def test_default_sidebar_and_nav_lookup(out):
    assert out["sidebar"] == [["Studio", ["Overview", "Games", "Website"]], ["Home Lab", ["Open Home Lab"]],
                              ["Sections", ["Reading list"]]]
    assert out["navItem"] == "X"
    assert out["navItemSide"] == "Games"
    assert out["routes"] == ["#/section/games/puzzle/money", "#/section/games", "#/space/studio",
                             "#/system/automations", "#/nav/side%3Ag"]
