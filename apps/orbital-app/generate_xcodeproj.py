#!/usr/bin/env python3
"""Generate OrbitalAgents.xcodeproj (app + unit tests) from the sources on disk.

The project is generated, not committed: it carries your Apple Team ID and bundle id, which differ
for everyone. Settings come from, in order of precedence:

  1. flags:        --team ABCDE12345 --bundle-prefix com.yourname --app-name "Orbital Agents" --apns
  2. environment:  ORBITAL_TEAM_ID, ORBITAL_BUNDLE_PREFIX, ORBITAL_APP_NAME, ORBITAL_APNS (1/0),
                   ORBITAL_APNS_ENVIRONMENT (development|production)
  3. the resolved config the setup wizard writes: $ORBITAL_HOME/config.resolved.json
     (default ~/.orbital-agents), keys apple.team_id, apple.bundle_prefix, apple.app_name,
     apple.ios_device (scripts/deploy-iphone.sh), notifications.apns, notifications.apns_environment
  4. defaults:     no team (fine for `CODE_SIGNING_ALLOWED=NO` simulator builds), com.example,
                   "Orbital Agents", no push

The bundle id is <bundle_prefix>.orbitalagents (the tests: <bundle_prefix>.orbitalagents.tests).
The push entitlement (aps-environment) is only written when push is on, so a free Apple account
can still build and install the app.

  python3 apps/orbital-app/generate_xcodeproj.py            # write the project
  python3 apps/orbital-app/generate_xcodeproj.py --print    # show the settings it would use (JSON)
  python3 apps/orbital-app/generate_xcodeproj.py --icons    # rebuild the app icons from docs/assets
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
SOURCE = ROOT / "OrbitalAgents"
TESTS = ROOT / "OrbitalAgentsTests"
FIXTURES = ROOT / "Fixtures"
PROJECT = ROOT / "OrbitalAgents.xcodeproj"
TARGET = "OrbitalAgents"
TEST_TARGET = "OrbitalAgentsTests"
ENTITLEMENTS = SOURCE / "OrbitalAgents.entitlements"
# The Mac (Catalyst) build runs under the hardened runtime, which needs its own microphone entitlement.
MAC_ENTITLEMENTS = SOURCE / "OrbitalAgents-Mac.entitlements"
ASSETS = SOURCE / "Assets.xcassets"

DEFAULTS = {
    "team_id": "",
    "bundle_prefix": "com.example",
    "app_name": "Orbital Agents",
    "apns": False,
    "apns_environment": "development",
    "ios_device": "",
}


# ---------------------------------------------------------------- settings


def resolved_config() -> dict:
    home = Path(os.environ.get("ORBITAL_HOME") or Path.home() / ".orbital-agents").expanduser()
    path = home / "config.resolved.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def settings(args: argparse.Namespace) -> dict:
    out = dict(DEFAULTS)
    cfg = resolved_config()
    apple = cfg.get("apple") or {}
    notes = cfg.get("notifications") or {}
    for key in ("team_id", "bundle_prefix", "app_name", "ios_device"):
        if apple.get(key):
            out[key] = str(apple[key])
    if "apns" in notes:
        out["apns"] = truthy(notes["apns"])
    if notes.get("apns_environment"):
        out["apns_environment"] = str(notes["apns_environment"])
    env = {
        "team_id": os.environ.get("ORBITAL_TEAM_ID"),
        "bundle_prefix": os.environ.get("ORBITAL_BUNDLE_PREFIX"),
        "app_name": os.environ.get("ORBITAL_APP_NAME"),
        "apns_environment": os.environ.get("ORBITAL_APNS_ENVIRONMENT"),
        "ios_device": os.environ.get("ORBITAL_IOS_DEVICE"),
    }
    for key, value in env.items():
        if value:
            out[key] = value
    if os.environ.get("ORBITAL_APNS"):
        out["apns"] = truthy(os.environ["ORBITAL_APNS"])
    if args.team is not None:
        out["team_id"] = args.team
    if args.bundle_prefix:
        out["bundle_prefix"] = args.bundle_prefix
    if args.app_name:
        out["app_name"] = args.app_name
    if args.apns is not None:
        out["apns"] = args.apns
    if args.apns_environment:
        out["apns_environment"] = args.apns_environment

    out["team_id"] = out["team_id"].strip()
    if out["team_id"] and not re.fullmatch(r"[A-Z0-9]{10}", out["team_id"]):
        sys.exit(f"Team ID must be 10 letters and digits (Xcode → Settings → Accounts), got {out['team_id']!r}")
    out["bundle_prefix"] = out["bundle_prefix"].strip().strip(".")
    if not re.fullmatch(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*", out["bundle_prefix"]):
        sys.exit(f"bundle prefix must look like com.yourname, got {out['bundle_prefix']!r}")
    out["app_name"] = out["app_name"].strip() or DEFAULTS["app_name"]
    if out["apns_environment"] not in {"development", "production"}:
        sys.exit("apns environment must be development or production")
    out["bundle_id"] = f"{out['bundle_prefix']}.orbitalagents"
    return out


# ---------------------------------------------------------------- icons


def sips(src: Path, size: int, dest: Path) -> None:
    subprocess.run(["sips", "-z", str(size), str(size), str(src), "--out", str(dest)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_icons(force: bool) -> None:
    """AppIcon (iOS single 1024 + every Mac size) and the in-app BrandMark, from docs/assets."""
    ios_src = REPO / "docs" / "assets" / "icon-1024.png"
    mac_src = REPO / "docs" / "assets" / "icon-macos-1024.png"
    icon_dir = ASSETS / "AppIcon.appiconset"
    mark_dir = ASSETS / "BrandMark.imageset"
    if not force and (icon_dir / "Contents.json").is_file() and (mark_dir / "Contents.json").is_file():
        return
    if not ios_src.is_file() or not mac_src.is_file():
        sys.exit(f"icons missing: {ios_src} and {mac_src}")
    if shutil.which("sips") is None:
        sys.exit("sips (macOS) is needed to resize the icons")
    icon_dir.mkdir(parents=True, exist_ok=True)
    mark_dir.mkdir(parents=True, exist_ok=True)
    images = []
    shutil.copyfile(ios_src, icon_dir / "icon-ios-1024.png")
    images.append({"filename": "icon-ios-1024.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"})
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = points * scale
            name = f"icon-mac-{points}@{scale}x.png"
            if pixels == 1024:
                shutil.copyfile(mac_src, icon_dir / name)
            else:
                sips(mac_src, pixels, icon_dir / name)
            images.append({"filename": name, "idiom": "mac", "scale": f"{scale}x", "size": f"{points}x{points}"})
    (icon_dir / "Contents.json").write_text(json.dumps({"images": images, "info": {"author": "xcode", "version": 1}}, indent=2) + "\n")
    sips(ios_src, 128, mark_dir / "brandmark.png")
    sips(ios_src, 256, mark_dir / "brandmark@2x.png")
    sips(ios_src, 384, mark_dir / "brandmark@3x.png")
    (mark_dir / "Contents.json").write_text(json.dumps({
        "images": [
            {"filename": "brandmark.png", "idiom": "universal", "scale": "1x"},
            {"filename": "brandmark@2x.png", "idiom": "universal", "scale": "2x"},
            {"filename": "brandmark@3x.png", "idiom": "universal", "scale": "3x"},
        ],
        "info": {"author": "xcode", "version": 1},
    }, indent=2) + "\n")
    print(f"wrote icons in {icon_dir.relative_to(REPO)} and {mark_dir.relative_to(REPO)}")


# ---------------------------------------------------------------- entitlements


def write_entitlements(s: dict) -> None:
    body = ""
    if s["apns"] and not s["team_id"]:
        print("note: push (notifications.apns) needs an Apple Team ID; leaving the push entitlement out", file=sys.stderr)
    elif s["apns"]:
        body = f"\t<key>aps-environment</key>\n\t<string>{s['apns_environment']}</string>\n"
    def plist(inner: str) -> str:
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n<dict>\n' + inner + "</dict>\n</plist>\n")

    ENTITLEMENTS.write_text(plist(body), encoding="utf-8")
    # Without audio-input the hardened runtime refuses the microphone silently: no prompt, and the
    # app never appears under System Settings > Privacy > Microphone.
    MAC_ENTITLEMENTS.write_text(plist(body + "\t<key>com.apple.security.device.audio-input</key>\n\t<true/>\n"),
                                encoding="utf-8")


# ---------------------------------------------------------------- project


def xid(seed: str) -> str:
    return hashlib.sha1(seed.encode()).hexdigest()[:24].upper()


def quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def settings_block(pairs: dict) -> str:
    lines = []
    for key in sorted(pairs):
        value = pairs[key]
        if isinstance(value, list):
            inner = "".join(f"\t\t\t\t\t{quote(v)},\n" for v in value)
            lines.append(f"\t\t\t\t{key} = (\n{inner}\t\t\t\t);")
        else:
            lines.append(f"\t\t\t\t{quote(key) if '[' in key else key} = {quote(str(value))};")
    return "\n".join(lines)


def project(s: dict) -> str:
    app_files = sorted(p for p in SOURCE.rglob("*.swift"))
    test_files = sorted(p for p in TESTS.rglob("*.swift"))
    if not app_files:
        sys.exit(f"no swift files under {SOURCE}")

    objects: list[str] = []

    def ref_line(ident: str, rel: str, kind: str, name: str | None = None, tree: str = '"<group>"') -> str:
        display = name or rel
        extra = f"name = {quote(name)}; " if name else ""
        return f"\t\t{ident} /* {display} */ = {{isa = PBXFileReference; lastKnownFileType = {kind}; {extra}path = {quote(rel)}; sourceTree = {tree}; }};"

    build_files: list[str] = []
    file_refs: list[str] = []

    app_children: list[str] = []
    app_sources: list[str] = []
    for path in app_files:
        rel = path.relative_to(SOURCE).as_posix()
        ref = xid(f"swift:{rel}")
        build = xid(f"build:{rel}")
        file_refs.append(ref_line(ref, rel, "sourcecode.swift"))
        build_files.append(f"\t\t{build} /* {rel} in Sources */ = {{isa = PBXBuildFile; fileRef = {ref} /* {rel} */; }};")
        app_children.append(f"\t\t\t\t{ref} /* {rel} */,")
        app_sources.append(f"\t\t\t\t{build} /* {rel} in Sources */,")

    test_children: list[str] = []
    test_sources: list[str] = []
    for path in test_files:
        rel = path.relative_to(TESTS).as_posix()
        ref = xid(f"test:{rel}")
        build = xid(f"testbuild:{rel}")
        file_refs.append(ref_line(ref, rel, "sourcecode.swift"))
        build_files.append(f"\t\t{build} /* {rel} in Sources */ = {{isa = PBXBuildFile; fileRef = {ref} /* {rel} */; }};")
        test_children.append(f"\t\t\t\t{ref} /* {rel} */,")
        test_sources.append(f"\t\t\t\t{build} /* {rel} in Sources */,")

    info_ref = xid("file:Info.plist")
    entitlements_ref = xid("file:entitlements")
    assets_ref = xid("file:assets")
    fixtures_ref = xid("file:fixtures")
    assets_build = xid("build:assets")
    fixtures_build = xid("build:fixtures")
    app_product = xid("product:app")
    test_product = xid("product:tests")
    file_refs += [
        ref_line(info_ref, "Info.plist", "text.plist.xml"),
        ref_line(entitlements_ref, "OrbitalAgents.entitlements", "text.plist.entitlements"),
        ref_line(assets_ref, "Assets.xcassets", "folder.assetcatalog"),
        f"\t\t{fixtures_ref} /* Fixtures */ = {{isa = PBXFileReference; lastKnownFileType = folder; path = Fixtures; sourceTree = \"<group>\"; }};",
        f"\t\t{app_product} /* {TARGET}.app */ = {{isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = {TARGET}.app; sourceTree = BUILT_PRODUCTS_DIR; }};",
        f"\t\t{test_product} /* {TEST_TARGET}.xctest */ = {{isa = PBXFileReference; explicitFileType = wrapper.cfbundle; includeInIndex = 0; path = {TEST_TARGET}.xctest; sourceTree = BUILT_PRODUCTS_DIR; }};",
    ]
    build_files += [
        f"\t\t{assets_build} /* Assets.xcassets in Resources */ = {{isa = PBXBuildFile; fileRef = {assets_ref} /* Assets.xcassets */; }};",
        f"\t\t{fixtures_build} /* Fixtures in Resources */ = {{isa = PBXBuildFile; fileRef = {fixtures_ref} /* Fixtures */; }};",
    ]

    group_root = xid("group:root")
    group_app = xid("group:app")
    group_tests = xid("group:tests")
    group_products = xid("group:products")
    app_target = xid("target")
    test_target = xid("target:tests")
    project_id = xid("project")
    proxy = xid("proxy:tests")
    dependency = xid("dependency:tests")
    phases = {name: xid(f"phase:{name}") for name in ("sources", "frameworks", "resources", "tsources", "tframeworks", "tresources")}
    lists = {name: xid(f"cfglist:{name}") for name in ("project", "app", "tests")}
    configs = {f"{scope}:{kind}": xid(f"cfg:{scope}:{kind}") for scope in ("project", "app", "tests") for kind in ("debug", "release")}

    team = s["team_id"]
    common_project = {
        "ALWAYS_SEARCH_USER_PATHS": "NO",
        "CLANG_ENABLE_MODULES": "YES",
        "COPY_PHASE_STRIP": "NO",
        "IPHONEOS_DEPLOYMENT_TARGET": "17.0",
        "SDKROOT": "iphoneos",
        "SWIFT_VERSION": "5.0",
        "ENABLE_USER_SCRIPT_SANDBOXING": "YES",
    }
    project_debug = {**common_project, "DEBUG_INFORMATION_FORMAT": "dwarf", "ENABLE_TESTABILITY": "YES", "GCC_DYNAMIC_NO_PIC": "NO",
                     "ONLY_ACTIVE_ARCH": "YES", "SWIFT_ACTIVE_COMPILATION_CONDITIONS": "DEBUG", "SWIFT_OPTIMIZATION_LEVEL": "-Onone"}
    project_release = {**common_project, "DEBUG_INFORMATION_FORMAT": "dwarf-with-dsym", "SWIFT_COMPILATION_MODE": "wholemodule", "VALIDATE_PRODUCT": "YES"}
    app_common = {
        "ASSETCATALOG_COMPILER_APPICON_NAME": "AppIcon",
        "ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME": "AccentColor",
        "CODE_SIGN_ENTITLEMENTS": "OrbitalAgents/OrbitalAgents.entitlements",
        "CODE_SIGN_ENTITLEMENTS[sdk=macosx*]": "OrbitalAgents/OrbitalAgents-Mac.entitlements",
        "CODE_SIGN_STYLE": "Automatic",
        "CURRENT_PROJECT_VERSION": "1",
        "DEVELOPMENT_TEAM": team,
        "DERIVE_MACCATALYST_PRODUCT_BUNDLE_IDENTIFIER": "NO",
        "GENERATE_INFOPLIST_FILE": "NO",
        "INFOPLIST_FILE": "OrbitalAgents/Info.plist",
        "IPHONEOS_DEPLOYMENT_TARGET": "17.0",
        "LD_RUNPATH_SEARCH_PATHS": ["$(inherited)", "@executable_path/Frameworks"],
        "MARKETING_VERSION": "1.0",
        "ORBITAL_APP_NAME": s["app_name"],
        "PRODUCT_BUNDLE_IDENTIFIER": s["bundle_id"],
        "PRODUCT_MODULE_NAME": TARGET,
        "PRODUCT_NAME": "$(TARGET_NAME)",
        "SUPPORTED_PLATFORMS": "iphoneos iphonesimulator",
        "SUPPORTS_MACCATALYST": "YES",
        "SWIFT_EMIT_LOC_STRINGS": "YES",
        "TARGETED_DEVICE_FAMILY": "1,2,6",
    }
    if any((SOURCE / "Private").rglob("*.swift")):
        # Local-only code in OrbitalAgents/Private (git-ignored): compiled in behind ORBITAL_PRIVATE.
        app_common["SWIFT_ACTIVE_COMPILATION_CONDITIONS"] = ["$(inherited)", "ORBITAL_PRIVATE"]
    test_common = {
        "BUNDLE_LOADER": "$(TEST_HOST)",
        "CODE_SIGN_STYLE": "Automatic",
        "CURRENT_PROJECT_VERSION": "1",
        "DEVELOPMENT_TEAM": team,
        "GENERATE_INFOPLIST_FILE": "YES",
        "IPHONEOS_DEPLOYMENT_TARGET": "17.0",
        "MARKETING_VERSION": "1.0",
        "PRODUCT_BUNDLE_IDENTIFIER": f"{s['bundle_id']}.tests",
        "PRODUCT_NAME": "$(TARGET_NAME)",
        "SUPPORTED_PLATFORMS": "iphoneos iphonesimulator",
        "SUPPORTS_MACCATALYST": "YES",
        "DERIVE_MACCATALYST_PRODUCT_BUNDLE_IDENTIFIER": "NO",
        "TARGETED_DEVICE_FAMILY": "1,2,6",
        "TEST_HOST": f"$(BUILT_PRODUCTS_DIR)/{TARGET}.app/{TARGET}",
        "TEST_HOST[sdk=macosx*]": f"$(BUILT_PRODUCTS_DIR)/{TARGET}.app/Contents/MacOS/{TARGET}",
    }

    def config(ident: str, name: str, pairs: dict) -> str:
        return f"\t\t{ident} /* {name} */ = {{\n\t\t\tisa = XCBuildConfiguration;\n\t\t\tbuildSettings = {{\n{settings_block(pairs)}\n\t\t\t}};\n\t\t\tname = {name};\n\t\t}};"

    def config_list(ident: str, label: str, debug: str, release: str) -> str:
        return (f"\t\t{ident} /* Build configuration list for {label} */ = {{\n\t\t\tisa = XCConfigurationList;\n\t\t\tbuildConfigurations = (\n"
                f"\t\t\t\t{debug} /* Debug */,\n\t\t\t\t{release} /* Release */,\n\t\t\t);\n\t\t\tdefaultConfigurationIsVisible = 0;\n\t\t\tdefaultConfigurationName = Release;\n\t\t}};")

    def phase(ident: str, isa: str, name: str, files: list[str]) -> str:
        body = "\n".join(files)
        return (f"\t\t{ident} /* {name} */ = {{\n\t\t\tisa = {isa};\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n{body}\n\t\t\t);\n"
                f"\t\t\trunOnlyForDeploymentPostprocessing = 0;\n\t\t}};")

    nl = "\n"
    return f"""// !$*UTF8*$!
{{
	archiveVersion = 1;
	classes = {{
	}};
	objectVersion = 56;
	objects = {{

/* Begin PBXBuildFile section */
{nl.join(build_files)}
/* End PBXBuildFile section */

/* Begin PBXContainerItemProxy section */
		{proxy} /* PBXContainerItemProxy */ = {{
			isa = PBXContainerItemProxy;
			containerPortal = {project_id} /* Project object */;
			proxyType = 1;
			remoteGlobalIDString = {app_target};
			remoteInfo = {TARGET};
		}};
/* End PBXContainerItemProxy section */

/* Begin PBXFileReference section */
{nl.join(file_refs)}
/* End PBXFileReference section */

/* Begin PBXFrameworksBuildPhase section */
{phase(phases['frameworks'], 'PBXFrameworksBuildPhase', 'Frameworks', [])}
{phase(phases['tframeworks'], 'PBXFrameworksBuildPhase', 'Frameworks', [])}
/* End PBXFrameworksBuildPhase section */

/* Begin PBXGroup section */
		{group_root} = {{
			isa = PBXGroup;
			children = (
				{group_app} /* OrbitalAgents */,
				{group_tests} /* OrbitalAgentsTests */,
				{fixtures_ref} /* Fixtures */,
				{group_products} /* Products */,
			);
			sourceTree = "<group>";
		}};
		{group_app} /* OrbitalAgents */ = {{
			isa = PBXGroup;
			children = (
{nl.join(app_children)}
				{assets_ref} /* Assets.xcassets */,
				{info_ref} /* Info.plist */,
				{entitlements_ref} /* OrbitalAgents.entitlements */,
			);
			path = OrbitalAgents;
			sourceTree = "<group>";
		}};
		{group_tests} /* OrbitalAgentsTests */ = {{
			isa = PBXGroup;
			children = (
{nl.join(test_children)}
			);
			path = OrbitalAgentsTests;
			sourceTree = "<group>";
		}};
		{group_products} /* Products */ = {{
			isa = PBXGroup;
			children = (
				{app_product} /* {TARGET}.app */,
				{test_product} /* {TEST_TARGET}.xctest */,
			);
			name = Products;
			sourceTree = "<group>";
		}};
/* End PBXGroup section */

/* Begin PBXNativeTarget section */
		{app_target} /* {TARGET} */ = {{
			isa = PBXNativeTarget;
			buildConfigurationList = {lists['app']} /* Build configuration list for PBXNativeTarget "{TARGET}" */;
			buildPhases = (
				{phases['sources']} /* Sources */,
				{phases['frameworks']} /* Frameworks */,
				{phases['resources']} /* Resources */,
			);
			buildRules = (
			);
			dependencies = (
			);
			name = {TARGET};
			productName = {TARGET};
			productReference = {app_product} /* {TARGET}.app */;
			productType = "com.apple.product-type.application";
		}};
		{test_target} /* {TEST_TARGET} */ = {{
			isa = PBXNativeTarget;
			buildConfigurationList = {lists['tests']} /* Build configuration list for PBXNativeTarget "{TEST_TARGET}" */;
			buildPhases = (
				{phases['tsources']} /* Sources */,
				{phases['tframeworks']} /* Frameworks */,
				{phases['tresources']} /* Resources */,
			);
			buildRules = (
			);
			dependencies = (
				{dependency} /* PBXTargetDependency */,
			);
			name = {TEST_TARGET};
			productName = {TEST_TARGET};
			productReference = {test_product} /* {TEST_TARGET}.xctest */;
			productType = "com.apple.product-type.bundle.unit-test";
		}};
/* End PBXNativeTarget section */

/* Begin PBXProject section */
		{project_id} /* Project object */ = {{
			isa = PBXProject;
			attributes = {{
				BuildIndependentTargetsInParallel = 1;
				LastSwiftUpdateCheck = 1600;
				LastUpgradeCheck = 1600;
				TargetAttributes = {{
					{app_target} = {{
						CreatedOnToolsVersion = 16.0;
					}};
					{test_target} = {{
						CreatedOnToolsVersion = 16.0;
						TestTargetID = {app_target};
					}};
				}};
			}};
			buildConfigurationList = {lists['project']} /* Build configuration list for PBXProject "{TARGET}" */;
			compatibilityVersion = "Xcode 14.0";
			developmentRegion = en;
			hasScannedForEncodings = 0;
			knownRegions = (
				en,
				Base,
			);
			mainGroup = {group_root};
			productRefGroup = {group_products} /* Products */;
			projectDirPath = "";
			projectRoot = "";
			targets = (
				{app_target} /* {TARGET} */,
				{test_target} /* {TEST_TARGET} */,
			);
		}};
/* End PBXProject section */

/* Begin PBXResourcesBuildPhase section */
{phase(phases['resources'], 'PBXResourcesBuildPhase', 'Resources', [f'				{assets_build} /* Assets.xcassets in Resources */,'])}
{phase(phases['tresources'], 'PBXResourcesBuildPhase', 'Resources', [f'				{fixtures_build} /* Fixtures in Resources */,'])}
/* End PBXResourcesBuildPhase section */

/* Begin PBXSourcesBuildPhase section */
{phase(phases['sources'], 'PBXSourcesBuildPhase', 'Sources', app_sources)}
{phase(phases['tsources'], 'PBXSourcesBuildPhase', 'Sources', test_sources)}
/* End PBXSourcesBuildPhase section */

/* Begin PBXTargetDependency section */
		{dependency} /* PBXTargetDependency */ = {{
			isa = PBXTargetDependency;
			target = {app_target} /* {TARGET} */;
			targetProxy = {proxy} /* PBXContainerItemProxy */;
		}};
/* End PBXTargetDependency section */

/* Begin XCBuildConfiguration section */
{config(configs['project:debug'], 'Debug', project_debug)}
{config(configs['project:release'], 'Release', project_release)}
{config(configs['app:debug'], 'Debug', app_common)}
{config(configs['app:release'], 'Release', app_common)}
{config(configs['tests:debug'], 'Debug', test_common)}
{config(configs['tests:release'], 'Release', test_common)}
/* End XCBuildConfiguration section */

/* Begin XCConfigurationList section */
{config_list(lists['project'], f'PBXProject "{TARGET}"', configs['project:debug'], configs['project:release'])}
{config_list(lists['app'], f'PBXNativeTarget "{TARGET}"', configs['app:debug'], configs['app:release'])}
{config_list(lists['tests'], f'PBXNativeTarget "{TEST_TARGET}"', configs['tests:debug'], configs['tests:release'])}
/* End XCConfigurationList section */
	}};
	rootObject = {project_id} /* Project object */;
}}
"""


def scheme(app_target: str, test_target: str) -> str:
    def ref(ident: str, name: str, product: str) -> str:
        return (f'            <BuildableReference\n               BuildableIdentifier = "primary"\n               BlueprintIdentifier = "{ident}"\n'
                f'               BuildableName = "{product}"\n               BlueprintName = "{name}"\n'
                f'               ReferencedContainer = "container:{PROJECT.name}">\n            </BuildableReference>')
    app_ref = ref(app_target, TARGET, f"{TARGET}.app")
    test_ref = ref(test_target, TEST_TARGET, f"{TEST_TARGET}.xctest")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Scheme
   LastUpgradeVersion = "1600"
   version = "1.7">
   <BuildAction
      parallelizeBuildables = "YES"
      buildImplicitDependencies = "YES">
      <BuildActionEntries>
         <BuildActionEntry
            buildForTesting = "YES"
            buildForRunning = "YES"
            buildForProfiling = "YES"
            buildForArchiving = "YES"
            buildForAnalyzing = "YES">
{app_ref}
         </BuildActionEntry>
      </BuildActionEntries>
   </BuildAction>
   <TestAction
      buildConfiguration = "Debug"
      selectedDebuggerIdentifier = "Xcode.DebuggerFoundation.Debugger.LLDB"
      selectedLauncherIdentifier = "Xcode.DebuggerFoundation.Launcher.LLDB"
      shouldUseLaunchSchemeArgsEnv = "YES">
      <Testables>
         <TestableReference
            skipped = "NO">
{test_ref.replace('            ', '               ')}
         </TestableReference>
      </Testables>
   </TestAction>
   <LaunchAction
      buildConfiguration = "Debug"
      selectedDebuggerIdentifier = "Xcode.DebuggerFoundation.Debugger.LLDB"
      selectedLauncherIdentifier = "Xcode.DebuggerFoundation.Launcher.LLDB"
      launchStyle = "0"
      useCustomWorkingDirectory = "NO"
      ignoresPersistentStateOnLaunch = "NO"
      debugDocumentVersioning = "YES"
      debugServiceExtension = "internal"
      allowLocationSimulation = "YES">
      <BuildableProductRunnable
         runnableDebuggingMode = "0">
{app_ref.replace('            ', '         ', 1)}
      </BuildableProductRunnable>
   </LaunchAction>
   <ProfileAction
      buildConfiguration = "Release"
      shouldUseLaunchSchemeArgsEnv = "YES"
      savedToolIdentifier = ""
      useCustomWorkingDirectory = "NO"
      debugDocumentVersioning = "YES">
   </ProfileAction>
   <AnalyzeAction
      buildConfiguration = "Debug">
   </AnalyzeAction>
   <ArchiveAction
      buildConfiguration = "Release"
      revealArchiveInOrganizer = "YES">
   </ArchiveAction>
</Scheme>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--team", help="Apple Developer Team ID (10 characters). Empty: unsigned builds only.")
    parser.add_argument("--bundle-prefix", help="e.g. com.yourname → com.yourname.orbitalagents")
    parser.add_argument("--app-name", help="Home-screen name (default Orbital Agents)")
    parser.add_argument("--apns", dest="apns", action="store_true", default=None, help="add the push entitlement")
    parser.add_argument("--no-apns", dest="apns", action="store_false", help="leave the push entitlement out")
    parser.add_argument("--apns-environment", choices=["development", "production"])
    parser.add_argument("--icons", action="store_true", help="rebuild the app icons from docs/assets")
    parser.add_argument("--print", dest="show", action="store_true", help="print the settings as JSON and exit")
    parser.add_argument("--get", metavar="KEY", help="print one setting (bundle_id, app_name, ios_device, has_team) and exit")
    args = parser.parse_args()
    s = settings(args)
    if args.get:
        values = {**s, "has_team": "1" if s["team_id"] else ""}
        if args.get not in values or args.get == "team_id":
            sys.exit(f"unknown setting {args.get!r}")
        print(values[args.get])
        return
    if args.show:
        shown = dict(s)
        shown["team_id"] = "(set)" if s["team_id"] else "(none: unsigned builds only)"
        print(json.dumps(shown, indent=2))
        return
    write_icons(force=args.icons)
    write_entitlements(s)
    PROJECT.mkdir(parents=True, exist_ok=True)
    (PROJECT / "project.pbxproj").write_text(project(s), encoding="utf-8")
    schemes = PROJECT / "xcshareddata" / "xcschemes"
    schemes.mkdir(parents=True, exist_ok=True)
    (schemes / f"{TARGET}.xcscheme").write_text(scheme(xid("target"), xid("target:tests")), encoding="utf-8")
    count = len(list(SOURCE.rglob("*.swift")))
    tests = len(list(TESTS.rglob("*.swift")))
    print(f"wrote {PROJECT.relative_to(REPO)} · {count} app files, {tests} test files · "
          f"bundle {s['bundle_id']} · team {'set' if s['team_id'] else 'none'} · push {'on' if s['apns'] and s['team_id'] else 'off'}")


if __name__ == "__main__":
    main()
