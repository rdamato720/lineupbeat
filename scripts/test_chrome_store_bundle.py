#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_chrome_store_bundle
import build_my_team

EXTENSION = ROOT / "extensions" / "lineupbeat-espn"
LISTING = ROOT / "chrome-web-store"


def png_header(path: Path) -> tuple[int, int, int, int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise AssertionError(f"not a PNG: {path}")
    width, height, depth, color_type, _compression, _filter, interlace = struct.unpack(
        ">IIBBBBB", data[16:29]
    )
    return width, height, depth, color_type, interlace


def rgba_pixels(path: Path) -> tuple[int, int, bytes]:
    width, height, depth, color_type, interlace = png_header(path)
    if (depth, color_type, interlace) != (8, 6, 0):
        raise AssertionError(f"expected non-interlaced 8-bit RGBA PNG: {path}")
    data = path.read_bytes()
    compressed = bytearray()
    offset = 8
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + length]
        if kind == b"IDAT":
            compressed.extend(chunk)
        offset += 12 + length
        if kind == b"IEND":
            break
    raw = zlib.decompress(compressed)
    stride = width * 4
    prior = bytearray(stride)
    pixels = bytearray()
    cursor = 0
    for _row in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scanline = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        for index, value in enumerate(scanline):
            left = scanline[index - 4] if index >= 4 else 0
            above = prior[index]
            upper_left = prior[index - 4] if index >= 4 else 0
            if filter_type == 1:
                scanline[index] = (value + left) & 255
            elif filter_type == 2:
                scanline[index] = (value + above) & 255
            elif filter_type == 3:
                scanline[index] = (value + ((left + above) // 2)) & 255
            elif filter_type == 4:
                predictor = left + above - upper_left
                distances = (abs(predictor - left), abs(predictor - above),
                             abs(predictor - upper_left))
                scanline[index] = (value + (left, above, upper_left)[distances.index(min(distances))]) & 255
            elif filter_type != 0:
                raise AssertionError(f"unknown PNG filter {filter_type}: {path}")
        pixels.extend(scanline)
        prior = scanline
    return width, height, bytes(pixels)


class ChromeStoreManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((EXTENSION / "manifest.json").read_text())

    def test_exact_hosts_minimal_permissions_and_beta_version(self):
        self.assertEqual(self.manifest["manifest_version"], 3)
        self.assertEqual(self.manifest["version"], "0.2.3")
        self.assertTrue(self.manifest["name"].endswith("BETA"))
        self.assertLessEqual(len(self.manifest["description"]), 132)
        self.assertIn("THIS EXTENSION IS FOR BETA TESTING", self.manifest["description"])
        self.assertEqual(self.manifest["permissions"], ["storage"])
        self.assertNotIn("host_permissions", self.manifest)
        self.assertEqual(
            [script["matches"] for script in self.manifest["content_scripts"]],
            [["https://fantasy.espn.com/football/*"],
             ["https://lineupbeat-dev.pages.dev/my-team/*"]],
        )
        self.assertEqual(
            [script["js"] for script in self.manifest["content_scripts"]],
            [["espn-roster-parser.js", "content.js"], ["content.js"]],
        )
        encoded = json.dumps(self.manifest)
        for forbidden in ("lineupbeat.com", "localhost", "127.0.0.1", "<all_urls>", "cookies", "tabs"):
            self.assertNotIn(forbidden, encoded)

    def test_icons_are_required_png_dimensions_with_store_padding(self):
        expected = {"16": (16, 16), "32": (32, 32), "48": (48, 48), "128": (128, 128)}
        self.assertEqual(set(self.manifest["icons"]), set(expected))
        for size, dimensions in expected.items():
            path = EXTENSION / self.manifest["icons"][size]
            width, height, depth, color_type, _interlace = png_header(path)
            self.assertEqual((width, height), dimensions)
            self.assertEqual((depth, color_type), (8, 6))
        width, _height, pixels = rgba_pixels(EXTENSION / self.manifest["icons"]["128"])
        self.assertEqual(pixels[3], 0)
        self.assertGreater(pixels[((64 * width) + 64) * 4 + 3], 0)

    def test_authored_runtime_is_local_only_and_reviewable(self):
        background = (EXTENSION / "background.js").read_text()
        parser = (EXTENSION / "espn-roster-parser.js").read_text()
        content = (EXTENSION / "content.js").read_text()
        for source in (background, parser, content):
            self.assertNotIn("eval(", source)
            self.assertNotIn("new Function", source)
            self.assertGreater(source.count("\n"), 20)
        for network_api in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon"):
            self.assertNotIn(network_api, background)
            self.assertNotIn(network_api, parser)
            self.assertNotIn(network_api, content)
        self.assertIn("chrome.storage.local.set", background)
        self.assertIn("chrome.storage.local.remove", background)
        self.assertIn("chrome.tabs.create({url: MY_TEAM_URL})", background)
        self.assertIn("Roster saved locally. Use Open My Team to continue.", content)
        self.assertIn("Privacy details", content)
        self.assertIn("Copy safe diagnostics", content)
        self.assertIn("Safe diagnostics copied. Paste the JSON into Codex.", content)
        self.assertIn("error.message === globalThis.LineupBeatEspnRosterParser.EMPTY_ERROR", content)
        self.assertIn("error.message === globalThis.LineupBeatEspnRosterParser.AMBIGUOUS_ERROR", content)


class ChromeStoreBundleTests(unittest.TestCase):
    def test_store_package_is_rooted_minimal_deterministic_and_inventoried(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            report_a = build_chrome_store_bundle.build(Path(first))
            report_b = build_chrome_store_bundle.build(Path(second))
            package_a = Path(first) / report_a["package"]
            package_b = Path(second) / report_b["package"]
            self.assertEqual(package_a.read_bytes(), package_b.read_bytes())
            self.assertEqual(report_a["packageSha256"], hashlib.sha256(package_a.read_bytes()).hexdigest())
            self.assertEqual(report_a["packageSha256"], report_b["packageSha256"])
            with zipfile.ZipFile(package_a) as archive:
                names = archive.namelist()
                self.assertEqual(names, list(build_chrome_store_bundle.RUNTIME_FILES))
                self.assertEqual(len(names), 8)
                self.assertIn("manifest.json", names)
                self.assertFalse(any(name.startswith("lineupbeat-espn/") for name in names))
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["version"], report_a["extensionVersion"])
                decoded = "\n".join(
                    archive.read(name).decode(errors="ignore") for name in names
                )
            for forbidden in (
                "node_modules", ".env", "BEGIN PRIVATE KEY", "Private Manager",
                "must-not-survive", "Test League", "personal league export",
            ):
                self.assertNotIn(forbidden, decoded)
                self.assertFalse(any(forbidden in name for name in names))
            self.assertIsNone(re.search(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_-]{12,}", decoded))
            inventory = json.loads((Path(first) / "listing-materials" / "package-inventory.json").read_text())
            self.assertEqual(inventory["packageSha256"], report_a["packageSha256"])
            self.assertEqual(inventory["packageFileCount"], 8)
            self.assertEqual(inventory["packageFileCount"], len(inventory["packageFiles"]))
            self.assertEqual([row["path"] for row in inventory["packageFiles"]], names)

    def test_listing_assets_have_required_formats_and_dimensions(self):
        required = {
            LISTING / "assets" / "store-icon-128.png": (128, 128),
            LISTING / "assets" / "small-promo-440x280.png": (440, 280),
            LISTING / "assets" / "screenshots" / "01-local-connection-1280x800.png": (1280, 800),
            LISTING / "assets" / "screenshots" / "02-lineup-decision-1280x800.png": (1280, 800),
            LISTING / "assets" / "screenshots" / "03-local-roster-1280x800.png": (1280, 800),
        }
        for path, dimensions in required.items():
            width, height, _depth, _color_type, _interlace = png_header(path)
            self.assertEqual((width, height), dimensions)

    def test_listing_is_complete_and_version_consistent(self):
        listing = (LISTING / "STORE_LISTING.md").read_text()
        for required in (
            "Lineup Beat ESPN My Team BETA", "0.2.3", "Short summary",
            "Detailed description", "Single purpose", "Permission justification",
            "Data-use selections", "Support URL", "Privacy policy URL",
            "Test instructions", "Unlisted", "Future Chrome Web Store steps — currently blocked",
            "Ralph's manual installed-extension QA", "Install version 0.2.3",
            "Save roster locally for My Team", "Open My Team",
            "Disconnect & clear", "Load reviewer demo roster",
        ):
            self.assertIn(required, listing)
        self.assertIn("No credentials are required", listing)
        self.assertIn("row-first repair awaits Ralph's live ESPN save test", listing)
        self.assertIn("Chrome Web Store", listing)
        self.assertIn("upload and submission remain blocked", listing)
        self.assertNotIn("Ralph's private", listing)

    def test_privacy_support_and_validated_package_are_public(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bundle:
            site = Path(directory)
            build_my_team.build(site)
            privacy = site / "my-team" / "extension" / "privacy" / "index.html"
            support = site / "my-team" / "extension" / "index.html"
            self.assertTrue(privacy.is_file())
            self.assertTrue(support.is_file())
            package = site / "my-team" / "lineupbeat-espn-extension.zip"
            report = build_chrome_store_bundle.build(Path(bundle))
            expected = Path(bundle) / report["package"]
            self.assertEqual(package.read_bytes(), expected.read_bytes())
            self.assertIn("Download version 0.2.3", support.read_text())
            text = privacy.read_text()
            for required in (
                "chrome.storage.local", "No roster upload", "No ESPN password, cookie, session token",
                "Disconnect &amp; clear", "hello@lineupbeat.com",
                "fantasy.espn.com/football", "lineupbeat-dev.pages.dev/my-team",
            ):
                self.assertIn(required, text)

    def test_public_workflow_uploads_only_scoped_store_artifacts(self):
        workflow = (ROOT / ".github" / "workflows" / "dev-site.yml").read_text()
        self.assertIn("python scripts/build_chrome_store_bundle.py", workflow)
        self.assertIn("lineupbeat-espn-cws-submission-${{ github.run_id }}", workflow)
        self.assertIn("lineupbeat-espn-cws-listing-${{ github.run_id }}", workflow)
        self.assertIn(
            "build/chrome-web-store/lineupbeat-espn-my-team-beta-0.2.3.zip",
            workflow,
        )
        self.assertIn("build/chrome-web-store/listing-materials", workflow)
        self.assertNotIn("site/my-team/lineupbeat-espn", workflow)


if __name__ == "__main__":
    unittest.main()
