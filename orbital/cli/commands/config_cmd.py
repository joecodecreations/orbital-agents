"""`orbital config`: show the merged settings, where files live, and refresh config.resolved.json."""

from __future__ import annotations

import json

from orbital.core.config import load_config

NAME = "config"
HELP = "Show settings and file locations; --apply rewrites config.resolved.json"


def register(p):
    p.add_argument("key", nargs="?", help="dotted key, e.g. hub.port")
    p.add_argument("--paths", action="store_true", help="print where config, secrets, data and logs live")
    p.add_argument("--apply", action="store_true", help="rewrite config.resolved.json for the Swift apps")


def run(args) -> int:
    cfg = load_config()
    if args.apply:
        print(cfg.write_resolved())
        return 0
    if args.paths:
        for k, v in cfg.resolved()["paths"].items():
            print(f"{k:15} {v}")
        print(f"{'config':15} {cfg.paths.config_file}")
        return 0
    if args.key:
        value = cfg.get(args.key)
        if value is None:
            print(f"no setting {args.key}")
            return 1
        print(json.dumps(value) if not isinstance(value, str) else value)
        return 0
    print(json.dumps(cfg.data, indent=2))
    return 0
