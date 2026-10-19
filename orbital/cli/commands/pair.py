"""`orbital pair`: a one-time link that signs the iPhone or Mac app in, no typing.

    orbital pair            print the orbital://connect link (works once, for 10 minutes)
    orbital pair --link     print only the link (scripts: deploy-iphone.sh opens it on the phone)
    orbital pair --json     {code, hub, link, expires_at}

Open the link on the device (AirDrop, Messages, or a QR code when `qrencode` is installed).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

NAME = "pair"
HELP = "One-time link that signs the app in without typing the password"


def register(p):
    p.add_argument("--link", action="store_true", help="print only the link")
    p.add_argument("--json", action="store_true", help="print JSON")


def run(args) -> int:
    from orbital.feeds import HubClient, HubError

    try:
        with HubClient.from_config(startup_wait=0) as hub:
            answer = hub.request("POST", "/v1/pair/new", json_body={})
    except HubError as exc:
        print(f"orbital pair: {exc.message}", file=sys.stderr)
        return 1
    link = answer.get("link") or ""
    if args.json:
        print(json.dumps(answer, indent=2))
        return 0
    if not link:
        print("orbital pair: the hub has no address other devices can reach (start Tailscale, or set network.public_url).",
              file=sys.stderr)
        return 1
    if args.link:
        print(link)
        return 0
    print("Open this on the iPhone or Mac to sign the app in (works once, until "
          f"{answer.get('expires_at')}):\n\n  {link}\n")
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "ANSIUTF8", link], check=False)
    return 0
