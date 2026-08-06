#!/usr/bin/env python3
"""Everything, checked at once.

    python3 scripts/qa.py
    python3 scripts/qa.py --strict     # exit 1 if anything fails

The existing tests each cover one thing well -- wire_test the reporting,
stress_test the projections, test_resolve the matcher. What none of them
does is look across the seams, and that is where nearly every real bug in
this project has lived:

  a constant tuned for SAMPLE_POWER 1.5 that silently dropped 63 players
  when it went to 2.5
  offense_pct read as a percentage when it is a fraction
  a crosswalk that prefixed an id the exporter also prefixed
  a rule written for James Conner that quietly demoted Josh Allen

Every one passed the tests for its own file. So this checks the things that
sit between files, plus the invariants nobody would think to assert -- and
where it can, it checks a value against what it MEANS rather than against
what it was yesterday.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sqlite3
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS: list[str] = []
WARNS: list[str] = []


def head(t):
    print(f"\n  {t}")
    print(f"  {'-' * len(t)}")


def ok(label, good, detail="", warn_only=False):
    print(f"    {'pass' if good else 'FAIL':<5} {label}"
          + (f"   {detail}" if detail else ""))
    if not good:
        (WARNS if warn_only else FAILS).append(f"{label}: {detail}" if detail else label)
    return good


def load(name):
    p = ROOT / "scripts" / f"{name}.py"
    if not p.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, str(p))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:
        FAILS.append(f"{name}.py will not import: {str(exc)[:70]}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    # ---------------------------------------------------------------- code
    head("1. Everything imports and parses")
    for f in sorted((ROOT / "scripts").glob("*.py")) + \
             sorted((ROOT / "beatwire").glob("*.py")):
        try:
            compile(f.read_text(), str(f), "exec")
        except SyntaxError as e:
            ok(f"{f.name} parses", False, f"line {e.lineno}: {e.msg}")
    print(f"    pass  all python files parse")

    m = load("project5")
    if not m:
        print("\n  project5 will not load; stopping.")
        return

    # ------------------------------------------------------------ constants
    head("2. Constants mean what they claim")

    ok("year weights sum to 1",
       abs(sum(m.YEAR_WEIGHTS) - 1.0) < 0.001,
       f"{sum(m.YEAR_WEIGHTS):.3f}")
    ok("availability weights sum to 1",
       abs(sum(m.AVAIL_WEIGHTS) - 1.0) < 0.001,
       f"{sum(m.AVAIL_WEIGHTS):.3f}")
    ok("recency is decreasing",
       all(a >= b for a, b in zip(m.YEAR_WEIGHTS, m.YEAR_WEIGHTS[1:])))

    for pos, tiers in m.SLOT_PPG.items():
        vals = [tiers[s] for s in sorted(tiers)]
        ok(f"{pos} slot rates decrease with depth",
           all(a >= b for a, b in zip(vals, vals[1:])), str(vals))
    for pos, tiers in m.SLOT_GAMES.items():
        vals = [tiers[s] for s in sorted(tiers)]
        ok(f"{pos} slot games decrease with depth",
           all(a >= b for a, b in zip(vals, vals[1:])), str(vals))
        ok(f"{pos} slot games are plausible",
           all(0 <= v <= 17 for v in vals), str(vals))

    ok("availability norms are fractions",
       all(0.5 <= v <= 1.0 for v in m.AVAIL_NORM.values()),
       str(m.AVAIL_NORM))

    # The threshold that broke: it must not depend on a weighting constant.
    src = (ROOT / "scripts" / "project5.py").read_text()
    ok("record threshold counts games, not weight",
       "games_seen <" in src and "wsum < 0.12" not in src,
       "a threshold tuned to SAMPLE_POWER breaks when it changes")

    ok("no fullbacks in the skill set", "FB" not in m.SKILL, str(sorted(m.SKILL)))
    ok("status map only ends a season for season-ending things",
       all(k in ("INJURY_RESERVE", "RETIRED", "SUSPENSION",
                 "NON_FOOTBALL_INJURY", "OUT") for k in m.STATUS_GAMES)
       and m.STATUS_GAMES.get("OUT", 0) > 0.5,
       "a game-status designation is not a season")

    # --------------------------------------------------------------- joins
    head("3. The joins between files")

    xw = m.crosswalk(conn) if "id_map" in tables else {}
    ros = m.roster()
    ok("roster loaded", len(ros) > 100, f"{len(ros)} entries")
    if xw:
        sample = next(iter(xw.values()))
        ok("crosswalk ids are BARE, not prefixed",
           not str(sample).startswith("nfl-"),
           f"exporter adds its own prefix; got {sample!r}")
        joined = sum(1 for v in xw.values() if f"nfl-{v}" in ros)
        pct = joined / max(len(xw), 1)
        ok("crosswalk reaches the roster", pct > 0.5,
           f"{joined}/{len(xw)} = {pct:.0%}")
    else:
        ok("crosswalk present", False, "no id_map; snaps not imported",
           warn_only=True)

    if "snap_counts" in tables:
        r = conn.execute("""SELECT MAX(offense_pct) hi FROM snap_counts
                            WHERE offense_pct IS NOT NULL""").fetchone()
        if r and r["hi"] is not None:
            frac = r["hi"] <= 1.5
            ok("snap share read on the right scale",
               ("sh > 1.5 else sh" in src) or not frac,
               f"stored max {r['hi']}, so it is a "
               f"{'fraction' if frac else 'percentage'}")

    # ---------------------------------------------------------- projections
    head("4. The projections themselves")
    if "weekly_stats" not in tables:
        ok("weekly_stats present", False, "import_stats has not run",
           warn_only=True)
    else:
        rows = m.build(conn, 2025, ros, xw)
        ok("projections produced", len(rows) > 50, f"{len(rows)} players")
        if rows:
            bad = [r for r in rows if not (0 <= r["ppr"] < 600)]
            ok("no impossible totals", not bad,
               ", ".join(f"{r['name']} {r['ppr']:.0f}" for r in bad[:3]))
            bad = [r for r in rows if r["adjusted"] > r["ppr"] + 1]
            ok("adjusted never exceeds the full season", not bad,
               ", ".join(r["name"] for r in bad[:3]))
            bad = [r for r in rows if not (0 <= r["games"] <= 17.01)]
            ok("expected games within a season", not bad,
               ", ".join(f"{r['name']} {r['games']:.1f}" for r in bad[:3]))
            names = Counter(r["name"] for r in rows)
            dupes = [n for n, c in names.items() if c > 1]
            ok("nobody projected twice", not dupes, ", ".join(dupes[:3]))
            nohist = [r for r in rows if r["note"] == "no NFL history"]
            ok("few players lack any record",
               len(nohist) < len(rows) * 0.25,
               f"{len(nohist)}/{len(rows)}; a spike here means a broken join")

            # Position sanity: the best QB should out-score the best TE.
            best = {}
            for pos in ("QB", "RB", "WR", "TE"):
                grp = [r["ppr"] for r in rows if r["pos"] == pos]
                if grp:
                    best[pos] = max(grp)
            if len(best) == 4:
                ok("top QB outscores top TE", best["QB"] > best["TE"],
                   f"QB {best['QB']:.0f} vs TE {best['TE']:.0f}")
                ok("every position has a plausible leader",
                   all(150 < v < 500 for v in best.values()),
                   ", ".join(f"{k} {v:.0f}" for k, v in best.items()))

    # ---------------------------------------------------------------- wire
    head("5. The wire")
    if "nuggets" not in tables:
        ok("nuggets table", False, "pipeline has not run", warn_only=True)
    else:
        n = conn.execute("SELECT COUNT(*) n FROM nuggets").fetchone()["n"]
        ok("nuggets present", n > 0, f"{n:,}")
        unres = conn.execute("""SELECT COUNT(*) n FROM nuggets
                                WHERE player_id IS NULL""").fetchone()["n"]
        ok("resolution rate", unres / max(n, 1) < 0.08,
           f"{unres} unresolved = {unres/max(n,1):.1%}")
        noattr = conn.execute("""SELECT COUNT(*) n FROM nuggets
                                 WHERE attributions IS NULL
                                 OR attributions = '[]'""").fetchone()["n"]
        ok("everything is attributed", noattr == 0, f"{noattr} without a source")
        bad = conn.execute("""SELECT COUNT(*) n FROM nuggets
                              WHERE actionability NOT BETWEEN 0 AND 3""").fetchone()["n"]
        ok("actionability in range", bad == 0, f"{bad} outside 0-3")
        future = conn.execute("""SELECT COUNT(*) n FROM nuggets
            WHERE published_at > datetime('now', '+2 hours')""").fetchone()["n"]
        ok("nothing dated in the future", future == 0,
           f"{future} ahead of now; check the RSS date parser")
        if "items" in tables:
            it = conn.execute("SELECT COUNT(*) n FROM items").fetchone()["n"]
            ok("source text retained", it > 0,
               f"{it:,} items; needed to audit paraphrasing")
        else:
            ok("source text retained", False,
               "no items table, paraphrasing cannot be checked", warn_only=True)

    # ---------------------------------------------------------------- site
    head("6. The site")
    tpl = ROOT / "site" / "template.html"
    if not tpl.exists():
        ok("template present", False, "missing")
    else:
        h = tpl.read_text()
        ok("exactly one h1", h.count("<h1") == 1, f"{h.count('<h1')} found")
        ok("title present", bool(re.search(r"<title>.{10,70}</title>", h)))
        d = re.search(r'name="description" content="([^"]*)"', h)
        ok("description is a sensible length",
           bool(d) and 80 <= len(d.group(1)) <= 165,
           f"{len(d.group(1))} chars" if d else "missing")
        ok("canonical present", 'rel="canonical"' in h)
        ok("og:image present", "og:image" in h)
        ok("favicon present", 'rel="icon"' in h)
        ok("structured data present", "application/ld+json" in h)
        ok("no localStorage misuse outside the roster",
           h.count("localStorage") <= 4, f"{h.count('localStorage')} uses")
        ok("player links carry a sport segment",
           "`/${sport}/${slugName" in h or "/${sport}/" in h,
           "urls must match build_pages.py")

        # the slug functions must agree, or every link 404s
        bp = load("build_pages")
        if bp:
            js = re.search(r"function slugName\(name\)\{(.*?)\n\}", h, re.S)
            ok("slug logic present in both", bool(js) and hasattr(bp, "slug"))
            if bp and hasattr(bp, "slug"):
                for name in ("Ja'Marr Chase", "Kenneth Walker III", "A.J. Brown"):
                    py = bp.slug(name)
                    manual = re.sub(r"[\s_]+", "-",
                                    re.sub(r"[^\w\s-]", "", name.lower())).strip("-")
                    ok(f"slug agrees for {name}", py == manual, f"{py} vs {manual}")

    for f in ("favicon.ico", "og.png", "apple-touch-icon.png"):
        ok(f"{f} exists", (ROOT / "site" / f).exists())

    # ------------------------------------------------------------ workflow
    head("7. Deployment")
    wf = ROOT / ".github" / "workflows" / "refresh.yml"
    if not wf.exists():
        ok("workflow present", False, "missing")
    else:
        w = wf.read_text()
        ok("anthropic key passed", "ANTHROPIC_API_KEY" in w)
        ok("twitter key passed", "TWITTERAPI_IO_KEY" in w,
           "without it the X writers are skipped and the run still succeeds")
        ok("export limit raised", "--limit" in w,
           "the default is 500 and the site shows what it exports")
        ok("pages built", "build_pages" in w)
        ok("overlapping runs cancelled", "cancel-in-progress: true" in w,
           "otherwise runs queue and re-extract each other's work")
        ok("schedule covers both coasts", "10-23" in w and "0-8" in w,
           "a west coast practice report lands at 9pm Eastern")

    gi = (ROOT / ".gitignore").read_text() if (ROOT / ".gitignore").exists() else ""
    ok("database ignored", "beatwire.db" in gi)
    ok("generated pages ignored", "site/nfl" in gi or "site/player" in gi)
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=20).stdout
        ok("database not tracked", "beatwire.db" not in tracked)
        ok("icons tracked", "site/favicon.ico" in tracked)
        ok("generated pages not tracked", "site/nfl/" not in tracked)
    except Exception:
        pass

    # -------------------------------------------------------------- secrets
    head("8. Secrets")
    leaked = []
    for f in list((ROOT / "scripts").glob("*.py")) + \
             list((ROOT / "beatwire").glob("*.py")):
        s = f.read_text()
        for pat in (r"sk-ant-[A-Za-z0-9_\-]{20,}", r"['\"][0-9a-f]{32}['\"]"):
            if re.search(pat, s):
                leaked.append(f.name)
                break
    ok("no keys in source", not leaked, ", ".join(sorted(set(leaked))[:4]))


    # ------------------------------------------------------ one source of truth
    head("9. One model, not five")

    models = sorted((ROOT / "scripts").glob("project*.py"))
    print(f"    found {len(models)}: {', '.join(f.name for f in models)}")
    ok("only one projection model", len(models) == 1,
       f"{len(models)} present; every extra one is a second answer to "
       f"'what does this player score'")

    # Who points at what?
    refs = {}
    for f in [ROOT / "beatwire" / "cli.py",
              ROOT / ".github" / "workflows" / "refresh.yml"] + \
             sorted((ROOT / "scripts").glob("*.py")):
        if not f.exists() or f.name.startswith("project"):
            continue
        found = set(re.findall(r"project\d*\.py", f.read_text()))
        if found:
            refs[f.name] = found
    used = set()
    for v in refs.values():
        used |= v
    ok("everything references the same model", len(used) <= 1,
       "; ".join(f"{k} -> {','.join(sorted(v))}" for k, v in refs.items()))

    # Constants that live in more than one file will drift.
    for const in ("SLOT_PPG", "SLOT_GAMES", "AVAIL_NORM", "YEAR_WEIGHTS",
                  "SAMPLE_POWER", "SCORING"):
        where = [f.name for f in models
                 if re.search(rf"^{const}\s*=", f.read_text(), re.M)]
        if len(where) > 1:
            ok(f"{const} defined once", False,
               f"in {', '.join(where)} -- these can disagree silently",
               warn_only=True)

    # ------------------------------------------------------------ the roster
    head("10. Roster integrity")
    if ros:
        by_id = {k: v for k, v in ros.items() if k.startswith("nfl-")}
        names = Counter(v["name"] for v in by_id.values())
        dupes = [n for n, c in names.items() if c > 1]
        ok("no duplicate players", not dupes, ", ".join(dupes[:4]))

        teams = Counter(v["team"] for v in by_id.values() if v.get("team"))
        ok("all 32 teams represented", len(teams) >= 32,
           f"{len(teams)} teams")
        thin = [t for t, c in teams.items() if c < 30]
        ok("no team suspiciously thin", not thin,
           ", ".join(f"{t}:{c}" for t, c in
                     sorted(((t, teams[t]) for t in thin))[:4]))

        slots = [v["slot"] for v in by_id.values() if v.get("slot")]
        ok("depth slots populated", len(slots) > 300,
           f"{len(slots)} of {len(by_id)}")
        ok("depth slots are sane", all(1 <= s <= 20 for s in slots),
           f"range {min(slots)}-{max(slots)}" if slots else "")

        qb1 = [v for v in by_id.values()
               if v.get("pos") == "QB" and v.get("slot") == 1]
        ok("about 32 first-string quarterbacks",
           28 <= len(qb1) <= 40, f"{len(qb1)} found")

        with_adp = sum(1 for v in by_id.values() if str(v.get("adp") or "").strip())
        ok("ADP present", with_adp > 100,
           f"{with_adp} players; import_adp after any roster rebuild",
           warn_only=True)

    # -------------------------------------------------------------- the data
    head("11. Data freshness")
    for tbl, col, why in (("weekly_stats", "season", "projections"),
                          ("snap_counts", "season", "the QB games cap"),
                          ("id_map", "season", "every join"),
                          ("espn_proj", "season", "the comparison"),
                          ("fp_projections", "season", "the consensus base")):
        if tbl not in tables:
            ok(f"{tbl} present", False, f"needed for {why}", warn_only=True)
            continue
        r = conn.execute(f"SELECT COUNT(*) n, MAX({col}) latest FROM {tbl}").fetchone()
        ok(f"{tbl} populated", r["n"] > 0,
           f"{r['n']:,} rows, latest {col} {r['latest']}")

    if "weekly_stats" in tables:
        seasons = [r["season"] for r in conn.execute(
            "SELECT DISTINCT season FROM weekly_stats ORDER BY season")]
        ok("at least three seasons of stats", len(seasons) >= 3,
           f"{seasons}")
        gaps = [s for s in range(min(seasons), max(seasons))
                if s not in seasons] if seasons else []
        ok("no missing season in the middle", not gaps, str(gaps))

    # ------------------------------------------------------------- sources
    head("12. Source configuration")
    sy = ROOT / "sources" / "nfl.yaml"
    if sy.exists():
        try:
            import yaml
            cfg = yaml.safe_load(sy.read_text())
            srcs = cfg.get("sources", cfg) if isinstance(cfg, dict) else cfg
            srcs = [s for s in srcs if isinstance(s, dict)]
            ok("sources configured", len(srcs) > 50, f"{len(srcs)}")
            ids = Counter(s.get("id") for s in srcs)
            dupes = [i for i, c in ids.items() if c > 1]
            ok("no duplicate source ids", not dupes, ", ".join(map(str, dupes[:3])))
            nourl = [s.get("id") for s in srcs if not s.get("url")
                     and not s.get("handle")]
            ok("every source has a url or handle", not nourl,
               ", ".join(map(str, nourl[:3])))
            todo = [s.get("id") for s in srcs
                    if "TODO" in str(s.get("url", "")) + str(s.get("handle", ""))]
            ok("no placeholder sources", not todo,
               f"{len(todo)} unfilled", warn_only=True)
        except Exception as exc:
            ok("sources parse", False, str(exc)[:60])

    # -------------------------------------------------- the site, rendered
    head("13. The site, actually rendered")
    idx = ROOT / "site" / "index.html"
    if not idx.exists():
        ok("index.html built", False, "run export", warn_only=True)
    else:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                pg = b.new_page(viewport={"width": 1280, "height": 900})
                errs = []
                pg.on("pageerror", lambda e: errs.append(str(e)[:80]))
                pg.goto("file://" + str(idx.resolve()))
                pg.wait_for_timeout(1200)
                ok("no javascript errors", not errs, "; ".join(errs[:2]))
                ok("cards rendered",
                   pg.eval_on_selector_all(".tile,.big", "e=>e.length") > 0)
                ok("no horizontal overflow at 1280",
                   not pg.evaluate("document.documentElement.scrollWidth "
                                   "> window.innerWidth + 1"))
                pg.set_viewport_size({"width": 390, "height": 844})
                pg.wait_for_timeout(500)
                ok("no horizontal overflow at 390",
                   not pg.evaluate("document.documentElement.scrollWidth "
                                   "> window.innerWidth + 1"))
                small = pg.evaluate("""() => [...document.querySelectorAll('a,button')]
                    .filter(e => {const r = e.getBoundingClientRect();
                      return r.height > 0 && r.height < 40
                             && !e.closest('h4,h3,.meta,.tfoot');}).length""")
                ok("touch targets 40px+ on mobile", small == 0,
                   f"{small} under 40px")
                b.close()
        except ImportError:
            ok("browser check", False, "playwright not installed",
               warn_only=True)
        except Exception as exc:
            ok("browser check ran", False, str(exc)[:60], warn_only=True)

    # --------------------------------------------------------- link integrity
    head("14. Links resolve to files")
    feed = ROOT / "site" / "data" / "feed.json"
    if feed.exists() and (ROOT / "site" / "nfl").exists():
        try:
            data = json.loads(feed.read_text())
            bp2 = load("build_pages")
            missing = []
            players = [p for p in data.get("players", []) if p.get("sport") == "nfl"]
            for p in players[:400]:
                if not p.get("name"):
                    continue
                path = ROOT / "site" / "nfl" / bp2.slug(p["name"]) / "index.html"
                if not path.exists():
                    missing.append(p["name"])
            checked = min(len(players), 400)
            ok("player links resolve",
               len(missing) < checked * 0.5,
               f"{len(missing)} of {checked} have no page "
               f"(players with no reports get none)")
        except Exception as exc:
            ok("link check ran", False, str(exc)[:60], warn_only=True)
    else:
        ok("pages built", False, "run build_pages", warn_only=True)

    # --------------------------------------------------------------- verdict
    print()
    for w in WARNS:
        print(f"  WARN   {w}")
    for f in FAILS:
        print(f"  FAIL   {f}")
    if not FAILS and not WARNS:
        print("  Clean.")
    elif not FAILS:
        print(f"\n  {len(WARNS)} warning{'s' if len(WARNS) != 1 else ''}, "
              f"nothing blocking.")
    else:
        print(f"\n  {len(FAILS)} failure{'s' if len(FAILS) != 1 else ''} "
              f"to fix before shipping.")
    if args.strict and FAILS:
        sys.exit(1)


if __name__ == "__main__":
    main()
