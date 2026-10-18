"""Id-addressed edits for one section. A failed op rolls the batch back."""

from __future__ import annotations

import copy
import re

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
LAYOUTS = {"page", "tabs", "sidebar"}
NODE_LAYOUTS = {"page", "tabs"}
MERGE_KEYS = {"title", "subtitle", "icon", "icon_image", "order", "accent", "layout", "summary", "space", "space_title", "space_icon"}
NODE_MERGE_KEYS = {"title", "subtitle", "icon", "icon_image", "order"}
OPS = {
    "merge_section",
    "merge_node",
    "replace_section",
    "delete_section",
    "upsert_node",
    "delete_node",
    "reorder_nodes",
    "upsert_tab",
    "delete_tab",
    "reorder_tabs",
    "upsert_block",
    "delete_block",
    "reorder_blocks",
}


class OpsError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _slug(value: object, label: str) -> str:
    if not isinstance(value, str) or not SLUG.match(value):
        raise OpsError(f"invalid {label} {value!r}")
    return value


def _find(items: list, item_id: str, label: str) -> dict:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise OpsError(f"unknown {label} {item_id}")


def _dupes(items: list, label: str) -> None:
    seen: set[str] = set()
    for item in items:
        ident = item.get("id")
        if ident in seen:
            raise OpsError(f"duplicate {label} id {ident}")
        if isinstance(ident, str):
            seen.add(ident)


def _check_blocks(blocks: list, where: str) -> None:
    from orbital.hub.blocks import check_block

    if len(blocks) > 200:
        raise OpsError(f"{where} has more than 200 blocks")
    _dupes(blocks, f"block on {where}")
    for block in blocks:
        if not isinstance(block, dict) or "type" not in block or "id" not in block:
            raise OpsError(f"block on {where} needs type and id")
        _slug(block["id"], "block id")
        check_block(block, where)


def _check_tabs(tabs: list, where: str) -> None:
    if len(tabs) > 30:
        raise OpsError(f"{where} has more than 30 tabs")
    _dupes(tabs, f"tab on {where}")
    for tab in tabs:
        _slug(tab.get("id"), "tab id")
        if not isinstance(tab.get("title"), str) or not tab["title"] or len(tab["title"]) > 40:
            raise OpsError(f"tab {tab.get('id')} needs a title of 1–40 characters")
        tab.setdefault("blocks", [])
        _check_blocks(tab["blocks"], f"tab {tab['id']}")


def validate_section(doc: dict) -> None:
    from orbital.hub.blocks import check_image

    if not isinstance(doc, dict):
        raise OpsError("section must be an object")
    _slug(doc.get("id"), "section id")
    if doc.get("schema") not in (None, "orbital.section.v1"):
        raise OpsError("schema must be orbital.section.v1")
    doc["schema"] = "orbital.section.v1"
    title = doc.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 80:
        raise OpsError("title must be 1–80 characters")
    if not isinstance(doc.get("icon"), str) or not doc["icon"]:
        raise OpsError("icon is required")
    if not isinstance(doc.get("order"), int) or isinstance(doc.get("order"), bool):
        raise OpsError("order must be an integer")
    if doc.get("layout") not in LAYOUTS:
        raise OpsError("layout must be page, tabs, or sidebar")
    accent = doc.get("accent")
    if accent not in (None, "") and (not isinstance(accent, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", accent)):
        raise OpsError("accent must be #RRGGBB")
    summary = doc.get("summary")
    if summary is not None and (not isinstance(summary, str) or len(summary) > 80):
        raise OpsError("summary must be at most 80 characters")
    if doc.get("subtitle") is not None and not isinstance(doc["subtitle"], str):
        raise OpsError("subtitle must be a string")
    # Spaces group sections into the app's home tiles (a project, a household, a server fleet).
    space = doc.get("space")
    if space is not None and (not isinstance(space, str) or not SLUG.match(space)):
        raise OpsError("space must be an id like home-lab")
    for key, limit in (("space_title", 40), ("space_icon", 64)):
        value = doc.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > limit):
            raise OpsError(f"{key} must be 1–{limit} characters")
    check_image(doc.get("icon_image"), "icon_image")
    for key in ("blocks", "tabs", "nodes"):
        if key not in doc or not isinstance(doc[key], list):
            raise OpsError(f"{key} must be an array")
    if len(doc["nodes"]) > 500:
        raise OpsError("a section can hold at most 500 nodes")
    _dupes(doc["nodes"], "node")
    _check_blocks(doc["blocks"], doc["id"])
    _check_tabs(doc["tabs"], doc["id"])
    for node in doc["nodes"]:
        _slug(node.get("id"), "node id")
        if not isinstance(node.get("title"), str) or not node["title"]:
            raise OpsError(f"node {node.get('id')} needs a title")
        if not isinstance(node.get("order"), int) or isinstance(node.get("order"), bool):
            raise OpsError(f"node {node.get('id')} needs an integer order")
        if node.get("layout") not in NODE_LAYOUTS:
            raise OpsError(f"node {node.get('id')} layout must be page or tabs")
        for key in ("subtitle", "icon"):
            if node.get(key) is not None and not isinstance(node[key], str):
                raise OpsError(f"node {node.get('id')} {key} must be a string")
        check_image(node.get("icon_image"), f"node {node.get('id')} icon_image")
        node.setdefault("blocks", [])
        node.setdefault("tabs", [])
        _check_blocks(node["blocks"], node["id"])
        _check_tabs(node["tabs"], node["id"])


def _sort_nodes(doc: dict) -> None:
    doc["nodes"] = sorted(doc.get("nodes") or [], key=lambda n: (n.get("order", 0), n.get("id", "")))


def _apply_merge(doc: dict, value: dict) -> None:
    if not isinstance(value, dict):
        raise OpsError("merge_section value must be an object")
    extra = set(value) - MERGE_KEYS
    if extra:
        raise OpsError(f"merge_section cannot set {', '.join(sorted(extra))}")
    for key, item in value.items():
        if item is None:
            doc.pop(key, None)
        else:
            doc[key] = item


def _apply_node_merge(node: dict, value: dict) -> None:
    if not isinstance(value, dict):
        raise OpsError("merge_node value must be an object")
    extra = set(value) - NODE_MERGE_KEYS
    if extra:
        raise OpsError(f"merge_node cannot set {', '.join(sorted(extra))}")
    for key, item in value.items():
        if item is None:
            if key in {"title", "order"}:
                raise OpsError(f"merge_node cannot clear {key}")
            node.pop(key, None)
        else:
            node[key] = item


def _upsert(items: list, item: dict) -> None:
    for index, existing in enumerate(items):
        if existing.get("id") == item["id"]:
            items[index] = item
            return
    items.append(item)


def _reorder_with_order(items: list, ids: list[str]) -> list:
    if not isinstance(ids, list) or not ids:
        raise OpsError("ids must be a non-empty array")
    by_id = {item["id"]: item for item in items}
    for ident in ids:
        if ident not in by_id:
            raise OpsError(f"unknown id {ident}")
    listed = [by_id[ident] for ident in ids]
    unlisted = [item for item in items if item["id"] not in set(ids)]
    ordered = listed + unlisted
    for index, item in enumerate(ordered, start=1):
        item["order"] = index * 10
    return ordered


def _reorder_array(items: list, ids: list[str]) -> list:
    if not isinstance(ids, list):
        raise OpsError("ids must be an array")
    by_id = {item["id"]: item for item in items}
    for ident in ids:
        if ident not in by_id:
            raise OpsError(f"unknown id {ident}")
    listed = [by_id[ident] for ident in ids]
    unlisted = [item for item in items if item["id"] not in set(ids)]
    return listed + unlisted


def _tab_parent(doc: dict, node_id: str | None) -> tuple[list, dict]:
    layout = doc.get("layout")
    if layout == "page":
        raise OpsError("section layout is page; merge_section to tabs or sidebar first")
    if layout == "tabs":
        if node_id:
            raise OpsError("omit node_id when tabs live on the section")
        return doc["tabs"], doc
    if not node_id:
        raise OpsError("node_id is required")
    node = _find(doc["nodes"], node_id, "node")
    if node.get("layout") != "tabs":
        node["layout"] = "tabs"
    return node["tabs"], node


def _block_list(doc: dict, node_id: str | None, tab_id: str | None) -> list:
    """The block list an op addresses. Without node_id and tab_id it is the section's own blocks,
    which the apps draw above the tabs or the sidebar content on every layout."""
    layout = doc.get("layout")
    if not node_id and not tab_id:
        return doc["blocks"]
    if layout == "page":
        raise OpsError("page sections address section.blocks only; omit node_id and tab_id")
    if layout == "tabs":
        if node_id:
            raise OpsError("omit node_id when tabs live on the section")
        tab = _find(doc["tabs"], tab_id, "tab")
        return tab["blocks"]
    if not node_id:
        raise OpsError("node_id is required with tab_id on a sidebar section")
    node = _find(doc["nodes"], node_id, "node")
    if node.get("layout") == "page":
        if tab_id:
            raise OpsError("this node is a page; omit tab_id")
        return node["blocks"]
    if not tab_id:
        raise OpsError("tab_id is required")
    tab = _find(node.get("tabs") or [], tab_id, "tab")
    return tab["blocks"]


def apply_ops(doc: dict, ops: list) -> tuple[dict | None, bool]:
    """Return (new_doc_or_None_if_deleted, mutated)."""
    if not isinstance(ops, list):
        raise OpsError("ops must be an array")
    if any(not isinstance(op, dict) or op.get("op") not in OPS for op in ops):
        raise OpsError("unknown op")
    deletes = [op for op in ops if op["op"] == "delete_section"]
    if deletes and (len(ops) != 1):
        raise OpsError("delete_section must be the only op")
    working = copy.deepcopy(doc)
    mutated = False
    for op in ops:
        name = op["op"]
        if name == "merge_section":
            _apply_merge(working, op.get("value") or {})
            mutated = True
        elif name == "replace_section":
            section = op.get("section")
            if not isinstance(section, dict):
                raise OpsError("replace_section needs a section object")
            section = copy.deepcopy(section)
            section["id"] = working["id"]
            section.pop("revision", None)
            section.pop("updated_at", None)
            for key in ("blocks", "tabs", "nodes"):
                section.setdefault(key, [])
            validate_section(section)
            working = section
            mutated = True
        elif name == "delete_section":
            return None, True
        elif name == "merge_node":
            node = _find(working["nodes"], op.get("node_id"), "node")
            _apply_node_merge(node, op.get("value") or {})
            mutated = True
        elif name == "upsert_node":
            node = op.get("node")
            if not isinstance(node, dict):
                raise OpsError("upsert_node needs a node")
            node = copy.deepcopy(node)
            node.setdefault("blocks", [])
            node.setdefault("tabs", [])
            _slug(node.get("id"), "node id")
            if node.get("layout") not in NODE_LAYOUTS:
                raise OpsError("node layout must be page or tabs")
            _upsert(working["nodes"], node)
            mutated = True
        elif name == "delete_node":
            node_id = op.get("node_id")
            _find(working["nodes"], node_id, "node")
            working["nodes"] = [node for node in working["nodes"] if node.get("id") != node_id]
            mutated = True
        elif name == "reorder_nodes":
            working["nodes"] = _reorder_with_order(working["nodes"], op.get("ids"))
            mutated = True
        elif name == "upsert_tab":
            tab = op.get("tab")
            if not isinstance(tab, dict):
                raise OpsError("upsert_tab needs a tab")
            tab = copy.deepcopy(tab)
            tab.setdefault("blocks", [])
            _slug(tab.get("id"), "tab id")
            tabs, _parent = _tab_parent(working, op.get("node_id"))
            _upsert(tabs, tab)
            mutated = True
        elif name == "delete_tab":
            tabs, _parent = _tab_parent(working, op.get("node_id"))
            _find(tabs, op.get("tab_id"), "tab")
            ident = op.get("tab_id")
            if _parent is working:
                working["tabs"] = [tab for tab in tabs if tab.get("id") != ident]
            else:
                _parent["tabs"] = [tab for tab in tabs if tab.get("id") != ident]
            mutated = True
        elif name == "reorder_tabs":
            tabs, parent = _tab_parent(working, op.get("node_id"))
            parent["tabs"] = _reorder_array(tabs, op.get("ids"))
            mutated = True
        elif name == "upsert_block":
            block = op.get("block")
            if not isinstance(block, dict):
                raise OpsError("upsert_block needs a block")
            _slug(block.get("id"), "block id")
            if "type" not in block:
                raise OpsError("block needs a type")
            blocks = _block_list(working, op.get("node_id"), op.get("tab_id"))
            _upsert(blocks, copy.deepcopy(block))
            mutated = True
        elif name == "delete_block":
            blocks = _block_list(working, op.get("node_id"), op.get("tab_id"))
            _find(blocks, op.get("block_id"), "block")
            ident = op.get("block_id")
            blocks[:] = [block for block in blocks if block.get("id") != ident]
            mutated = True
        elif name == "reorder_blocks":
            blocks = _block_list(working, op.get("node_id"), op.get("tab_id"))
            reordered = _reorder_array(list(blocks), op.get("ids"))
            blocks[:] = reordered
            mutated = True
    _sort_nodes(working)
    validate_section(working)
    return working, mutated
