#!/usr/bin/env python3
"""Render the complete digest as one human-review issue."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wire import digest_approval

PUBLICATIONS = ROOT / "data/wire_digest_publications.json"


def render(manifest: dict) -> str:
    updates = manifest.get("updates") or []
    lines = ["# Fantasy Football News Updates You Need to Know", "",
             f"**{len(updates)} updates** · batch `{manifest['batch_id'][:12]}` · "
             f"{manifest['model_calls']} batch call · ${manifest['cost_usd']:.4f}", "",
             "Nothing is live yet. Approve, reject or edit the exact numbered bullets below.", "",
             "```text", "approve all", "approve 1,2,3", "reject 4",
             "edit 5 | Alec Pierce returned to practice.", "```", "",
             "Only comments from `rdamato720` can publish.", ""]
    for number, update in enumerate(updates, 1):
        lines.append(f"{number}. {update['bullet']} [Source]({update['source_url']})")
    if updates:
        lines += ["", "<details><summary>Exact evidence audit</summary>", ""]
        for number, update in enumerate(updates, 1):
            lines += [f"### {number}. {update['player']} — {update['event_type']}", "",
                      f"[{update['author']} · {update['source_name']}]({update['source_url']})", "",
                      f"> {update['evidence_quote']}", ""]
        lines += ["</details>"]
    lines += ["", digest_approval.encode(manifest)]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "data/wire_digest_dark_batch.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/wire_digest_dark_inbox.md")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/wire_digest_inbox.json")
    args = parser.parse_args()
    source = json.loads(args.source.read_text())
    publications = json.loads(PUBLICATIONS.read_text())
    manifest = digest_approval.make_manifest(
        source.get("proposals") or [], source["generated_at"],
        hashlib.sha256(args.source.read_bytes()).hexdigest(),
        hashlib.sha256(PUBLICATIONS.read_bytes()).hexdigest(),
        int(publications.get("count") or 0), int(source.get("model_calls") or 0),
        float(source.get("cost_usd") or 0))
    body = render(manifest)
    if len(body.encode()) > 62_000:
        raise SystemExit("digest issue exceeds GitHub limit")
    args.output.write_text(body)
    args.manifest.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    print("  Digest approval issue rendered; 0 publications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
