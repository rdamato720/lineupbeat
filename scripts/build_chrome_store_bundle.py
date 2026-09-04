#!/usr/bin/env python3
"""Build deterministic Chrome Web Store release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions" / "lineupbeat-espn"
LISTING = ROOT / "chrome-web-store"
DEFAULT_OUTPUT = ROOT / "build" / "chrome-web-store"
FIXED_TIME = (2026, 9, 1, 0, 0, 0)
RUNTIME_FILES = (
    "manifest.json",
    "background.js",
    "espn-roster-parser.js",
    "espn-history-parser.js",
    "yahoo-roster-parser.js",
    "cbs-roster-parser.js",
    "cbs-history-parser.js",
    "content.js",
    "icons/icon-16.png",
    "icons/icon-32.png",
    "icons/icon-48.png",
    "icons/icon-128.png",
)
LISTING_FILES = (
    "STORE_LISTING.md",
    "assets/store-icon-128.png",
    "assets/small-promo-440x280.png",
    "assets/screenshots/01-local-connection-1280x800.png",
    "assets/screenshots/02-lineup-decision-1280x800.png",
    "assets/screenshots/03-local-roster-1280x800.png",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_TIME)
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data, compresslevel=9)


def write_package(package: Path) -> None:
    package.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package, "w") as archive:
        for name in RUNTIME_FILES:
            write_member(archive, name, (EXTENSION / name).read_bytes())


def build(output: Path) -> dict:
    manifest = json.loads((EXTENSION / "manifest.json").read_text())
    version = manifest["version"]
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"lineupbeat-espn-connector-{version}.zip"
    write_package(package)

    listing = output / "listing-materials"
    if listing.exists():
        shutil.rmtree(listing)
    listing.mkdir(parents=True)
    for name in LISTING_FILES:
        target = listing / Path(name).name if name == "STORE_LISTING.md" else listing / name.removeprefix("assets/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(LISTING / name, target)

    members = []
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            data = archive.read(info.filename)
            members.append({"path": info.filename, "bytes": len(data), "sha256": sha256_bytes(data)})
    assets = []
    for path in sorted(listing.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            assets.append({"path": path.relative_to(listing).as_posix(), "bytes": len(data), "sha256": sha256_bytes(data)})
    report = {
        "schemaVersion": "lineupbeat-cws-bundle-v1",
        "extensionVersion": version,
        "package": package.name,
        "packageBytes": package.stat().st_size,
        "packageSha256": sha256_bytes(package.read_bytes()),
        "packageFileCount": len(members),
        "packageFiles": members,
        "listingFiles": assets,
    }
    (listing / "package-inventory.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Built {package.name} sha256 {report['packageSha256']}")
    print(f"Package files: {len(members)}; listing files: {len(assets)}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
