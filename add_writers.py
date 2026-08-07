#!/usr/bin/env python3
"""Add beat writers to the source registry, skipping any already present.

    python3 add_writers.py            # report only
    python3 add_writers.py --apply

An earlier version wrote a broken registry: a regex cleaning team headings
out of names turned "Brooke Pryor" into "port", which yaml then could not
parse. So this one validates the block it has built -- parses it, checks
every entry has a handle and a team, checks the ids are unique -- and
refuses to touch the file if any of that fails.

It also backs the registry up first, because a source list is hand-curated
and losing it would cost real work.
"""
import argparse
import json
import pathlib
import re
import shutil
import sys
from collections import Counter

import yaml

ap = argparse.ArgumentParser()
ap.add_argument("--writers", default="writers.json")
ap.add_argument("--sources", default="sources/nfl.yaml")
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

w = json.loads(pathlib.Path(args.writers).read_text())
src = pathlib.Path(args.sources)
text = src.read_text()

existing = yaml.safe_load(text)
have = {str(s.get("handle", "")).lower()
        for s in existing.get("sources", []) if s.get("handle")}
ids = {s["id"] for s in existing.get("sources", []) if s.get("id")}
print(f"  registry: {len(existing['sources'])} sources, {len(have)} handles")


def slug(name, handle):
    last = name.split()[-1] if name.split() else handle
    return re.sub(r"[^a-z0-9]", "", last.lower())[:12] or handle.lower()[:12]


def q(v):
    v = str(v)
    return '"' + v.replace('"', "'") + '"' if re.search(r'[:#\'"\[\]{}]|^\s|\s$', v) else v


new, block = [], []
for x in w:
    if x["handle"].lower() in have or not x.get("team"):
        continue
    base = f"nfl-{x['team'].lower()}-tapi-{slug(x['name'], x['handle'])}"
    sid, i = base, 2
    while sid in ids:
        sid, i = f"{base}{i}", i + 1
    ids.add(sid)
    new.append((sid, x))
    block.append(f"- id: {sid}\n  kind: twitterapi\n  handle: {x['handle']}\n"
                 f"  name: {q(x['name'])}\n  outlet: {q(x['outlet'])}\n"
                 f"  teams:\n  - {x['team']}\n")

print(f"  {len(w)} in the document, {len(new)} new\n")
if not new:
    sys.exit("  nothing to add")
print("  by team:", dict(Counter(x["team"] for _, x in new).most_common(8)))
print()
for sid, x in new[:10]:
    print(f"    {sid:<34} @{x['handle']:<20} {x['outlet'][:22]}")
if len(new) > 10:
    print(f"    … and {len(new) - 10} more")

# ---- refuse to write anything that will not parse -----------------------
try:
    probe = yaml.safe_load("sources:\n" + "".join(block))
except yaml.YAMLError as e:
    sys.exit(f"\n  REFUSING: the block does not parse -- {str(e)[:100]}")
bad = [s for s in probe["sources"]
       if not s.get("handle") or not s.get("teams") or not s.get("id")]
if bad:
    sys.exit(f"\n  REFUSING: {len(bad)} entries missing a handle, team or id")
print(f"\n  validated: {len(probe['sources'])} entries parse cleanly")

if not args.apply:
    print("  Nothing written. Re-run with --apply.")
    sys.exit()

backup = src.with_suffix(".yaml.bak")
shutil.copy2(src, backup)
merged = text.rstrip("\n") + "\n" + "".join(block)
try:
    d = yaml.safe_load(merged)
    assert len(d["sources"]) == len(existing["sources"]) + len(new)
except Exception as e:
    sys.exit(f"  REFUSING: merged file is bad -- {str(e)[:90]}")
src.write_text(merged)
print(f"\n  wrote {len(new)}. Registry now {len(d['sources'])}: "
      f"{dict(Counter(s.get('kind') for s in d['sources']))}")
print(f"  backup at {backup.name}")
print(f"  next: python3 -m beatwire.cli doctor --sport nfl")
