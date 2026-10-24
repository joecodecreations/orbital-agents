"""The `subscriptions` block: every subscription of one product plus failed payments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hub_helpers import AUTH, make_client
from orbital.feeds import blocks as b
from orbital.hub.blocks import check_block
from orbital.hub.contract import AGGREGATED_BLOCKS, APP_BLOCKS
from orbital.hub.ops import OpsError

ROOT = Path(__file__).resolve().parents[2]


def sample(**extra) -> dict:
    block = b.subscriptions("example-shop", [
        b.subscription("sub_1001", "active", "2026-08-30T00:59:07Z", customer="someone@example.com", plan="Pro",
                       interval="month", amount=9.99, mrr=9.99, period_end="2026-10-30T00:59:07Z",
                       cancel_at_period_end=False, paid=True, billing="stripe"),
        b.subscription("trial-42", "trialing", "2026-10-15", plan="Free trial", mrr=0, trial_end="2026-10-22",
                       paid=False, billing="app"),
        b.subscription("sub_1002", "canceled", "2026-07-01", canceled_at="2026-09-01T10:00:00Z", paid=True),
    ], product="Example Shop", title="Example Shop subscriptions", space="Example Shop", source="Stripe",
        failed=[{"at": "2026-10-16T12:00:00Z", "amount": 9.99, "customer": "someone@example.com",
                 "reason": "card_declined", "id": "ch_2001"}],
        metrics=[b.metric("Active", "1"), b.metric("MRR", "$9.99")], as_of="2026-10-17T09:00:00Z")
    block.update(extra)
    return block


def test_contract_and_schema_list_subscriptions():
    assert "subscriptions" in dict(APP_BLOCKS)
    assert "subscriptions" in AGGREGATED_BLOCKS
    schema = json.loads((ROOT / "schema" / "orbital.section.v1.schema.json").read_text())
    assert "subscriptions" in schema["$defs"]
    assert "subscriptions" in schema["$defs"]["block"]["properties"]["type"]["examples"]


def test_valid_block_passes():
    block = sample()
    assert block["type"] == "subscriptions" and block["connected"] is True and block["currency"] == "USD"
    check_block(block, "page")
    check_block(sample(company="Older Name", space=None), "page")
    check_block({"type": "subscriptions", "id": "empty", "product": "Example Shop", "connected": False,
                 "note": "Stripe could not be read."}, "page")


@pytest.mark.parametrize(("change", "field"), [
    ({"product": None}, "product"),
    ({"product": " "}, "product"),
    ({"currency": "usd"}, "currency"),
    ({"connected": "yes"}, "connected"),
    ({"items": [{"id": "s", "status": "shipped", "started": "2026-10-01"}]}, "items[0].status"),
    ({"items": [{"id": "s", "status": "active", "started": "2026-10-01", "amount": -1}]}, "items[0].amount"),
    ({"items": [{"id": "s", "status": "active", "started": "2026-10-01", "mrr": "9.99"}]}, "items[0].mrr"),
    ({"items": [{"id": 7, "status": "active", "started": "2026-10-01"}]}, "items[0].id"),
    ({"items": [{"status": "active", "started": "2026-10-01"}]}, "items[0].id"),
    ({"items": [{"id": "s", "status": "active"}]}, "items[0].started"),
    ({"items": [{"id": "s", "status": "active", "started": "Oct 1"}]}, "items[0].started"),
    ({"items": [{"id": "s", "status": "active", "started": "2026-10-01", "interval": "quarter"}]}, "items[0].interval"),
    ({"items": [{"id": "s", "status": "active", "started": "2026-10-01", "billing": "paypal"}]}, "items[0].billing"),
    ({"items": [{"id": "s", "status": "active", "started": "2026-10-01", "paid": 1}]}, "items[0].paid"),
    ({"items": [{"id": "s", "status": "active", "started": "2026-10-01"}] * 2}, "repeats"),
    ({"failed": [{"amount": 3}]}, "failed[0].at"),
    ({"failed": [{"at": "2026-10-01", "amount": -3}]}, "failed[0].amount"),
])
def test_bad_fields_are_named(change, field):
    with pytest.raises(OpsError, match=field.replace("[", r"\[").replace("]", r"\]")):
        check_block(sample(**change), "page")


def test_validate_renders_without_warnings(ports):
    client, _hub = make_client()
    try:
        page = b.section("shop-money", "Shop money", [sample()], icon="dollarsign.circle")
        checked = client.post("/v1/sections/shop-money/validate", headers=AUTH, json={"section": page})
        assert checked.status_code == 200, checked.text
        assert checked.json()["warnings"] == []
        bad = client.post("/v1/sections/shop-money/validate", headers=AUTH,
                          json={"section": {**page, "blocks": [sample(product=None)]}})
        assert bad.status_code == 400 and "product" in bad.json()["error"]
    finally:
        client.close()
