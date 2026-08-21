#!/usr/bin/env python3
"""Record one discovery observation per team, and report the window.

    python3 scripts/wire_discovery_watch.py --run       # one observation
    python3 scripts/wire_discovery_watch.py --report    # the window so far

A team does not pass because its landing page answered 200. It passes by
producing at least one relevant eligible candidate during the window, and a
team that produced none because nothing happened that day is recorded as an
exception for a person to look at -- not silently passed and not silently
failed. A quiet news day and a broken adapter look identical in a count.

The window is elapsed wall-clock time. It cannot be compressed, and this
script does not pretend otherwise: --report says how many hours have actually
passed since the first observation.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import capture, si
from wire import registry as artreg
from wire.store import WireStore, now

OUT = Path("data/wire_discovery_window.json")


def observe(store, sources) -> dict:
    """One pass over every website source. Discovery only, no capture."""
    stamp = now()
    obs = {"observed_at": stamp, "teams": {}}
    for src in sources:
        if src.paid or not src.adapter:
            continue
        team = src.teams[0] if src.teams else "?"
        row = obs["teams"].setdefault(team, {
            "discovery_runs": 0, "ok": 0, "methods": [], "discovered": 0,
            "accepted": 0, "excluded": 0, "reasons": {}, "sources": []})
        row["sources"].append(src.source_id)
        row["discovery_runs"] += 1
        try:
            found = capture.discover(src, limit=40)
        except Exception as e:
            row["reasons"][f"discovery error: {type(e).__name__}"] = \
                row["reasons"].get(f"discovery error: {type(e).__name__}", 0) + 1
            continue
        row["ok"] += 1
        row["methods"].append(
            "SI_TEAM_PAGE" if src.adapter == artreg.SI_TEAM_PAGE else src.adapter)
        row["discovered"] += len(found)
        for item in found:
            why = item.get("si_exclusion_reason", "")
            if why:
                row["excluded"] += 1
                key = why.split("(")[0].strip()
                if key.startswith("canonical url is a"):
                    key = "wrong team"
                elif "not in the registry" in why:
                    key = "unknown author"
                row["reasons"][key] = row["reasons"].get(key, 0) + 1
            else:
                row["accepted"] += 1
    return obs


def candidate_counts(store) -> dict:
    """What each team's stored evidence currently looks like."""
    out: dict = {}
    for r in store.evidence():
        sid = r["source_id"]
        src_team = None
        for s in artreg.load():
            if s.source_id == sid and s.teams:
                src_team = s.teams[0]
                break
        team = src_team or r["team"]
        if not team:
            continue
        d = out.setdefault(team, {"candidates": 0, "firsthand": 0,
                                  "quotation": 0, "analysis": 0,
                                  "uncertain": 0, "duplicates": 0})
        if r["review_status"] in ("SUPERSEDED", "EXCLUDED"):
            continue
        d["candidates"] += 1
        if r["duplicate_of"]:
            d["duplicates"] += 1
        k = r["evidence_class"]
        d["firsthand"] += k == "FIRSTHAND_OBSERVATION"
        d["quotation"] += k == "DIRECT_QUOTATION"
        d["analysis"] += k == "ANALYSIS_OR_OPINION"
        d["uncertain"] += k == "UNCERTAIN"
    return out


def load_window() -> dict:
    if OUT.exists():
        return json.loads(OUT.read_text())
    return {"observations": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    store = WireStore()
    window = load_window()

    if args.run:
        obs = observe(store, artreg.load())
        window["observations"].append(obs)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(window, indent=1) + "\n")
        print(f"  observation {len(window['observations'])} recorded at "
              f"{obs['observed_at']}, {len(obs['teams'])} teams")

    if args.report or not args.run:
        obs = window["observations"]
        if not obs:
            print("  no observations yet; run --run first")
            return 1
        first = datetime.fromisoformat(obs[0]["observed_at"])
        last = datetime.fromisoformat(obs[-1]["observed_at"])
        hours = (datetime.now(timezone.utc) - first).total_seconds() / 3600
        print(f"  {len(obs)} observation(s)")
        print(f"  window opened {obs[0]['observed_at']}")
        print(f"  latest        {obs[-1]['observed_at']}")
        print(f"  elapsed       {hours:.1f}h of the 72h required "
              f"-- {'PASS' if hours >= 72 else 'NOT YET MET'}")

        counts = candidate_counts(store)
        agg: dict = {}
        for o in obs:
            for team, row in o["teams"].items():
                a = agg.setdefault(team, {"runs": 0, "ok": 0, "discovered": 0,
                                          "accepted": 0, "excluded": 0,
                                          "reasons": {}, "methods": set()})
                a["runs"] += row["discovery_runs"]
                a["ok"] += row["ok"]
                a["discovered"] += row["discovered"]
                a["accepted"] += row["accepted"]
                a["excluded"] += row["excluded"]
                a["methods"].update(row["methods"])
                for k, v in row["reasons"].items():
                    a["reasons"][k] = a["reasons"].get(k, 0) + v

        print(f"\n  {'TM':<4}{'RUN':>4}{'OK':>4}{'DISC':>6}{'ACC':>5}{'EXCL':>6}"
              f"{'CAND':>6}{'FH':>4}{'DQ':>4}{'AN':>5}{'DUP':>5}  NOTE")
        exceptions = []
        for team in sorted(agg):
            a = agg[team]
            c = counts.get(team, {})
            cand = c.get("candidates", 0)
            note = ""
            if not a["ok"]:
                note = "NO SUCCESSFUL DISCOVERY"
            elif cand == 0:
                note = "no eligible candidate -- manual exception"
                exceptions.append(team)
            print(f"  {team:<4}{a['runs']:>4}{a['ok']:>4}{a['discovered']:>6}"
                  f"{a['accepted']:>5}{a['excluded']:>6}{cand:>6}"
                  f"{c.get('firsthand',0):>4}{c.get('quotation',0):>4}"
                  f"{c.get('analysis',0):>5}{c.get('duplicates',0):>5}  {note}")
        print(f"\n  teams with at least one eligible candidate: "
              f"{sum(1 for t in agg if counts.get(t,{}).get('candidates',0))}"
              f"/{len(agg)}")
        if exceptions:
            print(f"  documented exceptions for manual review: "
                  f"{', '.join(exceptions)}")
        merged: dict = {}
        for a in agg.values():
            for k, v in a["reasons"].items():
                merged[k] = merged.get(k, 0) + v
        print("  exclusion reasons across the window:")
        for k, v in sorted(merged.items(), key=lambda x: -x[1])[:8]:
            print(f"    {v:>5}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
