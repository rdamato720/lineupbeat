#!/usr/bin/env python3
"""Build the projection changes page from the workbook's Weekly Update tab.

    python3 scripts/build_changes.py
    python3 scripts/build_changes.py --projections data/projections.xlsx

WHY THIS PAGE EXISTS

The board says what we project. It does not say what we changed our mind
about, or why, and that is the more interesting claim: anybody can publish
numbers, far fewer will publish the moment their numbers moved and point at
the report that moved them.

It is also the honest version of "fantasy analysis". Every line here is a
decision somebody made with a stated reason and a source link, rather than
a sentence generated to sound like insight.

WHAT THE DECISIONS MEAN

  UPDATED           the projection moved on this evidence
  RECONCILED        moved because somebody else's did -- a backfield only
                    has so many carries, so raising one back lowers another
  OUT, ZEROED       ruled out for the season, projection set to zero
  WATCH, NO CHANGE  we are aware and have not moved anything yet

That last one matters most. A changelog that only records changes implies
everything else went unexamined; recording the things looked at and left
alone is what makes it a record rather than a highlight reel.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import seo

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
SPORT = "nfl"
POSITIONS = ["QB", "RB", "WR", "TE"]

DECISION_NOTE = {
    "UPDATED": "The projection moved on this evidence.",
    "RECONCILED": "Moved to stay consistent with another change. A backfield "
                  "only has so many carries.",
    "OUT, ZEROED": "Ruled out for the season. Projection set to zero.",
    "WATCH, NO CHANGE": "Looked at, nothing moved yet.",
}


# Reader-facing wording for dated historical entries. Original reasons and
# all projection values remain unchanged in the source workbook.
PUBLIC_REASONS = {
    'Cousins remains the Raiders starter while Mendoza works as QB2. Reallocated the existing Raiders QB budget toward Cousins without changing team-level totals.':
        'Cousins remains the Raiders starter while Mendoza works as QB2.',
    'Minnesota named Murray the starter. Reallocated the existing Vikings QB passing and rushing budget toward Murray without changing the team-level totals.':
        'Minnesota named Murray the starter.',
    'Brooks is healthy and sharing first-team work. Increased his expected role modestly while preserving Carolina backfield opportunity totals.':
        'Brooks is healthy and sharing first-team work.',
    'Tracy is handling most two-minute work and the backfield is trending toward a 1A/1B split. Narrowed the workload gap modestly.':
        'Tracy is handling most two-minute work and the backfield is trending toward a 1A/1B split.',
    'Tuten and Rodriguez are listed as co-starters on the initial unofficial depth chart. Made only a small workload shift because the depth chart is preliminary.':
        'Tuten and Rodriguez are listed as co-starters on the initial unofficial depth chart.',
    'Pearsall is out for the 2026 season. His projected receiving opportunity was removed and redistributed across the remaining San Francisco pass catchers.':
        'Pearsall is out for the 2026 season.',
    'Brazzell is out for the 2026 season. His receiving opportunity was removed and redistributed within the Carolina receiver room.':
        'Brazzell is out for the 2026 season.',
    'Boston has generated strong first-team camp buzz and growing trust. Increased his target share modestly and reconciled the Browns receiving totals.':
        'Boston has generated strong first-team camp buzz and growing trust.',
    'Douglas is listed with the first unit on Miami’s current depth chart. Applied a conservative target-share increase, not a full breakout projection.':
        'Douglas is listed with the first unit on Miami’s current depth chart.',
    'Golden has continued to earn a larger first-team role in camp. Shifted a modest share of Green Bay receiver opportunity toward him.':
        'Golden has continued to earn a larger first-team role in camp.',
    'Seattle camp reports point to Shaheed as a major factor and frequent two-TE personnel. Shifted modest opportunity toward Shaheed and Barner while leaving JSN unchanged.':
        'Seattle camp reports point to Shaheed as a major factor and frequent two-TE personnel.',
    'Boston has generated strong first-team camp buzz and growing trust. Increased his target share modestly and reconciled the Browns receiving totals. Also corrected an invalid negative rushing-yard value.':
        'Boston has generated strong first-team camp buzz and growing trust.',
    'Ferguson has been heavily involved throughout Cowboys camp. Added a modest target bump and reconciled the Dallas receiving totals.':
        'Ferguson has been heavily involved throughout Cowboys camp.',
    'Brazzell is out for the 2026 season. His receiving opportunity was removed and redistributed within the Carolina receiver room. Also corrected an invalid negative rushing-yard value.':
        'Brazzell is out for the 2026 season.',
    'Groin soreness is being monitored, and possible discipline remains unresolved. No regular-season absence is established, so no games or targets were deducted.':
        'Groin soreness is being monitored, and possible discipline remains unresolved. No regular-season absence has been established.',
    'Groin injury is expected to keep him out of camp work at least this week, but no regular-season absence is established. Possible discipline is also unresolved, so the baseline remains unchanged.':
        'Groin injury is expected to keep him out of camp work at least this week, but no regular-season absence is established. Possible discipline remains unresolved.',
    'Expected to miss the preseason with a groin injury, but current reporting still points toward regular-season availability. No games deducted.':
        'Expected to miss the preseason with a groin injury, but current reporting still points toward regular-season availability.',
    'Hubbard is week-to-week with a legitimate hamstring injury. Carolina remains confident he will be ready for Week 1, so no games were deducted; a modest share of existing backfield opportunity moved to Brooks while team totals stayed unchanged.':
        'Hubbard is week-to-week with a hamstring injury. Carolina remains confident he will be ready for Week 1.',
    'Hubbard is week-to-week and Brooks is expected to receive significant preseason work. Increased Brooks modestly without adding Carolina team opportunity or assuming Hubbard misses regular-season games.':
        'Hubbard is week-to-week and Brooks is expected to receive significant preseason work.',
    'Lemon continues to miss practice with a hamstring issue after repeated spring and camp absences. Reduced his season-long target share modestly for early-role risk, without projecting missed regular-season games.':
        'Lemon continues to miss practice with a hamstring issue after repeated spring and camp absences.',
    "Lemon's modest target reduction was redistributed within the Eagles receiver room. Wicks received the largest share because he has already taken advantage of additional camp reps.":
        'Wicks is expected to benefit from Lemon’s reduced role after taking advantage of additional camp reps.',
    'Received a small share of the Eagles targets removed from Lemon. No independent breakout adjustment was applied.':
        'Expected to receive a small increase in targets as Lemon’s projected role decreases.',
    'Received a small share of the Eagles targets removed from Lemon. Cooper has continued to impress in camp and Nick Sirianni acknowledged the possibility of offensive playing time.':
        'Expected to receive a small increase in targets as Lemon’s projected role decreases. Cooper has continued to impress in camp, and Nick Sirianni acknowledged the possibility of offensive playing time.',
    'Toe injury may cost preseason time, but the exact severity and regular-season impact are not established. No games or targets deducted yet.':
        'Toe injury may cost preseason time, but the exact severity and regular-season impact are not established.',
    'Rehab is progressing and he could begin 7-on-7/11-on-11 work soon, but he has not yet completed full team work. Current recovery discount remains unchanged.':
        'Rehab is progressing and he could begin 7-on-7/11-on-11 work soon, but he has not yet completed full team work.',
    "Watson will start preseason Game 1, while Sanders will start Game 2. Todd Monken says the competition remains open, so Cleveland's existing QB allocation is unchanged.":
        'Watson will start preseason Game 1, while Sanders will start Game 2. Todd Monken says the competition remains open.',
    "Sanders will start preseason Game 2 after Watson starts Game 1. The coach says the competition remains open, so Cleveland's existing QB allocation is unchanged.":
        'Sanders will start preseason Game 2 after Watson starts Game 1. The coach says the competition remains open.',
    'Indianapolis signed Allen to a one-year deal to stabilize a thin receiver room. Added an 80-target, 52-catch possession-role projection and redistributed only existing Colts WR receiving volume; team WR totals remain unchanged.':
        'Indianapolis signed Allen to a one-year deal to stabilize a thin receiver room.',
    "Allen's projected role was funded entirely from the existing Colts receiver room. This is a reconciliation adjustment, not an independent downgrade; Indianapolis WR targets, catches, yards and TDs remain unchanged in aggregate.":
        'Allen’s arrival reduces the projected workload for other Colts receivers.',
    'Tyson is expected to miss up to roughly two months with a hamstring injury. Modeled 12.5 of 17 games of baseline opportunity, less than the maximum reported five-game absence, and redistributed the removed Saints WR volume without increasing team totals.':
        'Tyson is expected to miss up to roughly two months with a hamstring injury.',
    'Received a share of the opportunity removed from Tyson because of his expected early-season absence. No independent breakout adjustment was applied; the Saints WR room remains fully reconciled to its prior team totals.':
        'Expected to receive more opportunities during Tyson’s early-season absence.',
    'Love has a high ankle sprain and is expected to miss the rest of the preseason, though Arizona remains hopeful for Week 1. Applied only a modest season-long risk adjustment, shifting 6 carries and 1.5 targets within the existing Cardinals backfield.':
        'Love has a high ankle sprain and is expected to miss the rest of the preseason, though Arizona remains hopeful for Week 1.',
    'Received the modest opportunity shifted from Love for high-ankle-sprain risk. Arizona RB targets, receptions, receiving yards/TDs, carries, rushing yards/TDs and fumbles lost remain unchanged in aggregate.':
        'Love’s ankle injury leads to a small increase in expected opportunities for his backfield teammates.',
    'Nabers participated in live 11-on-11 and 7-on-7 team work for the first time in his ACL/meniscus recovery, but remained in a no-contact jersey and was not yet full speed. The existing recovery discount stays in place until medical clearance is firmer.':
        'Nabers participated in live 11-on-11 and 7-on-7 team work for the first time in his ACL/meniscus recovery, but remained in a no-contact jersey and was not yet full speed.',
    "Hall's groin strain is expected to sideline him for a few weeks, but current reporting says he is on track for Week 1. No regular-season games or touches were deducted.":
        "Hall's groin strain is expected to sideline him for a few weeks, but current reporting says he is on track for Week 1.",
    "Nacua's psoas/hip issue has kept him out longer than initially expected and his practice return was pushed to at least next week. No regular-season absence is established, so the baseline remains unchanged.":
        'Nacua’s psoas/hip issue has delayed his practice return. No regular-season absence has been established, so his season projection is unchanged.',
    'Specialist evaluation identified a stable toe sprain and Tampa Bay remains optimistic about Week 1. No regular-season absence is established, so the baseline remains unchanged.':
        'Specialist evaluation identified a stable toe sprain and Tampa Bay remains optimistic about Week 1.',
    "Price returned as a full participant and worked with Seattle's starting offense after missing time with leg soreness. The existing projection is retained with no injury discount.":
        "Price returned as a full participant and worked with Seattle's starting offense after missing time with leg soreness.",
    'Cleveland named Watson the Week 1 starter. Assigned him 75% of the existing Browns QB budget while retaining a meaningful Sanders share for performance and durability risk; team totals remain unchanged.':
        'Cleveland named Watson the Week 1 starter.',
    'Cleveland named Watson the Week 1 starter. Reduced Sanders to 25% of the existing Browns QB budget; team passing and rushing totals remain unchanged.':
        'Cleveland named Watson the Week 1 starter.',
    'Kamara is expected to miss at least a month with a sprained MCL. Applied an early-season availability discount within the existing Saints backfield budget.':
        'Kamara is expected to miss at least a month with a sprained MCL.',
    "Etienne is expected to receive the bulk of New Orleans' work while Kamara is sidelined. Increased his share without adding team opportunity.":
        "Etienne is expected to receive the bulk of New Orleans' work while Kamara is sidelined.",
    "New Orleans acquired White after Kamara's injury. Added a modest depth role funded entirely from the existing Saints RB room.":
        "New Orleans acquired White after Kamara's injury.",
    'Retained a small complementary role in the rebalanced Saints backfield; New Orleans RB totals remain unchanged.':
        'A small complementary role is expected in the Saints backfield.',
    "White's arrival and Etienne's lead role reduce Chandler's projected share; New Orleans RB totals remain unchanged.":
        'White’s arrival and Etienne’s lead role reduce Chandler’s projected share of the workload.',
    "Rebalanced the depth chart after Kamara's injury and White's arrival without changing New Orleans team opportunity.":
        'Expected roles in the Saints backfield changed after Kamara’s injury and White’s arrival.',
    'Higgins tore his ACL and was placed on injured reserve, ending his 2026 season. Removed his full projection.':
        'Higgins tore his ACL and was placed on injured reserve, ending his 2026 season.',
    "Received the largest share of Higgins' vacated opportunity. Houston WR targets, catches, yards, TDs, rushing stats and fumbles remain unchanged in aggregate.":
        'Expected to receive the largest increase in opportunities following Higgins’ season-ending injury.',
    "Received a measured share of Higgins' vacated opportunity while preserving all Houston WR team totals.":
        'Expected to receive more opportunities following Higgins’ season-ending injury.',
    "Received a modest share of Higgins' vacated opportunity while preserving all Houston WR team totals.":
        'Expected to receive a small increase in opportunities following Higgins’ season-ending injury.',
    "Houston signed Jones after Higgins' injury. Added a conservative role funded only from Higgins' removed projection.":
        "Houston signed Jones after Higgins' injury.",
    "Houston signed Shepard after Higgins' injury. Added a conservative role funded only from Higgins' removed projection.":
        "Houston signed Shepard after Higgins' injury.",
    'Kittle was activated from PUP, avoiding a mandatory absence through Week 4 and giving him a chance to play Week 1. Restored 15 targets conservatively.':
        'Kittle was activated from PUP, avoiding a mandatory absence through Week 4 and giving him a chance to play Week 1.',
    "Kittle's restored availability reduces Tonges' replacement-role volume; San Francisco receiving totals remain unchanged.":
        'Kittle’s return reduces Tonges’ expected workload.',
    "Funded a small share of Kittle's restored volume from the existing San Francisco receiver room; team receiving totals remain unchanged.":
        'Kittle’s return slightly reduces the expected workload for other San Francisco receivers.',
    'Jeanty is believed to have a sprained ankle, but reporting says the injury is not considered long term and the team has not established a regular-season absence. No games or touches deducted.':
        'Jeanty is believed to have a sprained ankle, but reporting says the injury is not considered long term and the team has not established a regular-season absence.',
    'McCaffrey returned to practice after planned management of tightness. The existing projection is retained with no injury discount.':
        'McCaffrey returned to practice after planned management of tightness.',
    "Warren's groin injury is considered minor and there is no concern about Week 1. No regular-season volume deducted.":
        "Warren's groin injury is considered minor and there is no concern about Week 1.",
    "Detroit described LaPorta's hip issue as a long-term non-concern. No regular-season absence is established, so the baseline remains unchanged.":
        "Detroit described LaPorta's hip issue as a long-term non-concern.",
    'Team updated after Houston officially acquired Boutte from New England; workload projection is unchanged pending role evidence.':
        'Houston acquired Boutte from New England. His workload projection is unchanged while his role becomes clearer.',
    'Team updated after the Rams officially reacquired Atwell from Miami; workload projection is unchanged pending role evidence.':
        'The Rams reacquired Atwell from Miami. His workload projection is unchanged while his role becomes clearer.',
    'Cleveland waived Tillman, so his active-team workload is removed until he signs elsewhere and a new role is established.':
        'Cleveland waived Tillman. No workload is projected until he signs elsewhere and establishes a role.',
}

DECISION_LABELS = {"RECONCILED": "Related workload change", "OUT, ZEROED": "Out for season"}

EVIDENCE_ORDER = {"High": 0, "Medium": 1, "Medium-Low": 2, "Uncertain": 3}


def eastern_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now(timezone.utc) - timedelta(hours=4)


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def slug(s):
    s = re.sub(r"[^\w\s-]", "", (s or "").lower())
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"[\s_]+", "-", s).strip("-")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_changes(path: Path):
    """The Weekly Update tab, if the workbook has one.

    Returns (rows, meta). A workbook without the tab is not an error: it
    means nothing changed this week, and the page simply is not built.
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    if "Weekly Update" not in wb.sheetnames:
        return [], {}
    ws = wb["Weekly Update"]

    meta, header_at = {}, None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=12,
                                         values_only=True), 1):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if cells and cells[0] == "Updated":
            meta["updated"] = cells[1]
        if cells and cells[0] == "Method":
            meta["method"] = cells[1]
        if cells and cells[0] == "Position" and "Player" in cells:
            header_at = i
            head = [c.lower() for c in cells]
            break
    if header_at is None:
        return [], meta

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        return None

    ci = {"pos": col("position"), "player": col("player"), "team": col("team"),
          "decision": col("decision"), "rb": col("rank before"),
          "ra": col("rank after"), "pb": col("ppr before"),
          "pa": col("ppr after"), "d": col("ppr delta"),
          "ev": col("evidence"), "why": col("reason"), "src": col("source")}

    out = []
    for row in ws.iter_rows(min_row=header_at + 1, values_only=True):
        def g(k):
            i = ci.get(k)
            if i is None or i >= len(row) or row[i] is None:
                return None
            v = str(row[i]).strip()
            return v or None

        name = g("player")
        pos = g("pos")
        if not name or not pos or pos not in POSITIONS:
            continue
        out.append({
            "pos": pos, "name": name, "team": (g("team") or "").upper(),
            "decision": (g("decision") or "").upper(),
            "rank_before": num(g("rb")), "rank_after": num(g("ra")),
            "ppr_before": num(g("pb")), "ppr_after": num(g("pa")),
            "delta": num(g("d")), "evidence": g("ev") or "",
            "reason": g("why") or "", "source": g("src") or "",
            "slug": slug(name),
        })
    return out, meta


PAGE_CSS = """
.topbar .logo,.topbar .vbtn{text-decoration:none}
.topbar .vbtn:hover{text-decoration:none; color:var(--ink)}
.vbtn[aria-current="page"]{color:#0A0C08; background:var(--signal);
  border-color:var(--signal)}

/* ---- changes ----
   A record, not a highlight reel. Grouped by the reason rather than by
   player, because one piece of news usually moves several players and
   showing them apart hides the fact that they are the same decision. */
.chwrap{max-width:1080px; margin:0 auto; padding:0 1rem 4rem}
.chhead h1{font-size:1.7rem; margin:1.6rem 0 0; letter-spacing:-.01em;
  font-family:var(--text)}
.chsub{color:var(--quiet); font-size:.86rem; margin:.4rem 0 0; max-width:70ch;
  line-height:1.55}
.chdate{display:inline-block; margin-left:.4rem; font-family:var(--agate);
  text-transform:uppercase; letter-spacing:.06em; font-size:.7rem;
  color:var(--signal); border:1px solid var(--rule); border-radius:999px;
  padding:.1rem .5rem; vertical-align:.05em}

.chctl{display:flex; gap:.3rem; flex-wrap:wrap; align-items:center;
  margin:1.4rem 0 .3rem}
.chlab{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.07em; font-size:.66rem; color:var(--quiet);
  margin-right:.3rem}
.chtab{font-family:var(--agate); text-transform:uppercase;
  background:transparent; border:1px solid var(--rule); color:var(--quiet);
  font-size:.76rem; padding:.3rem .7rem; border-radius:999px;
  cursor:pointer; letter-spacing:.04em}
.chtab:hover{color:var(--ink); border-color:var(--ink)}
.chtab[aria-pressed="true"]{background:var(--signal);
  border-color:var(--signal); color:#0b0f0a; font-weight:600}

.chgroup{background:var(--card); border:1px solid var(--rule);
  border-radius:10px; padding:1rem 1.1rem; margin:.8rem 0 0}
.chwhy{font-size:.92rem; line-height:1.6; color:var(--ink); margin:0;
  max-width:76ch}
.chmeta{display:flex; gap:.5rem; flex-wrap:wrap; align-items:center;
  margin:.6rem 0 0}
.chev{font-family:var(--agate); text-transform:uppercase; font-size:.62rem;
  letter-spacing:.06em; border:1px solid var(--rule); border-radius:999px;
  padding:.1rem .5rem; color:var(--quiet)}
.chev.e-high{color:#8BE04E; border-color:#8BE04E}
.chev.e-medium{color:#B9DE7E; border-color:#B9DE7E}
.chev.e-uncertain{color:var(--standing); border-color:var(--standing)}
.chsrc{font-size:.74rem; color:var(--quiet); text-decoration:underline}
.chsrc:hover{color:var(--signal)}

.chmoves{display:grid; gap:.3rem; margin:.8rem 0 0}
.chmove{display:flex; gap:.6rem; align-items:baseline; flex-wrap:wrap;
  padding:.35rem 0; border-top:1px solid var(--rule);
  font-variant-numeric:tabular-nums}
.chpos{font-family:var(--data); font-size:.7rem; color:var(--quiet);
  width:2rem; flex:none}
.chname{font-size:.88rem; color:var(--ink); flex:1 1 9rem}
.chname a{color:var(--ink); text-decoration:none}
.chname a:hover{color:var(--signal)}
.chrank{font-family:var(--data); font-size:.76rem; color:var(--quiet);
  flex:none}
.chpts{font-family:var(--data); font-size:.82rem; font-weight:600;
  flex:none; min-width:5rem; text-align:right}
.up{color:#8BE04E} .down{color:#FF6B4A} .flat{color:var(--quiet)}
.chdec{font-family:var(--agate); text-transform:uppercase; font-size:.58rem;
  letter-spacing:.06em; color:var(--quiet); flex:none}

.chnote{color:var(--quiet); font-size:.78rem; margin:2rem 0 0;
  max-width:74ch; line-height:1.55}
.chnote h2{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.07em; font-size:.78rem; color:var(--quiet);
  margin:0 0 .5rem}
.chnote dt{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.05em; font-size:.66rem; color:var(--ink);
  margin-top:.6rem}
.chnote dd{margin:.1rem 0 0; font-size:.8rem; line-height:1.5}
.chempty{color:var(--quiet); font-size:.86rem; padding:1.2rem 0}

@media (max-width:760px){
  .chtab{min-height:44px; display:inline-flex; align-items:center}
  .chpts{min-width:4.2rem}
}
"""


def site_chrome():
    tpl = SITE / "template.html"
    if not tpl.exists():
        return "", "", ""
    src = tpl.read_text()
    css = re.search(r"<style>(.*?)</style>", src, re.S)
    header = seo.site_nav("projections")
    return (css.group(1) if css else ""), header, seo.site_footer()


def group_changes(rows):
    """By reason, because one piece of news moves several players.

    Pearsall going out raised four other San Francisco receivers. Listing
    them separately would show four changes; listing them together shows
    one decision, which is what actually happened.
    """
    groups = {}
    for r in rows:
        key = (r["reason"] or r["name"])[:400]
        g = groups.setdefault(key, {"reason": r["reason"], "rows": [],
                                    "evidence": r["evidence"],
                                    "source": r["source"]})
        g["rows"].append(r)
        # The strongest evidence in the group describes the group.
        if EVIDENCE_ORDER.get(r["evidence"], 9) < \
                EVIDENCE_ORDER.get(g["evidence"], 9):
            g["evidence"] = r["evidence"]
    out = list(groups.values())
    for g in out:
        g["rows"].sort(key=lambda r: -(abs(r["delta"] or 0)))
        g["biggest"] = max((abs(r["delta"] or 0) for r in g["rows"]),
                           default=0)
    out.sort(key=lambda g: (EVIDENCE_ORDER.get(g["evidence"], 9),
                            -g["biggest"]))
    return out


def move_html(r, links):
    d = r["delta"]
    cls = "flat" if not d else ("up" if d > 0 else "down")
    pts = ("&mdash;" if d is None
           else f'{d:+.1f}'.replace("-", "\u2212"))
    rank = ""
    if r["rank_before"] and r["rank_after"]:
        rank = (f'{r["pos"]}{r["rank_before"]:.0f} '
                f'&rarr; {r["pos"]}{r["rank_after"]:.0f}')
    name = (f'<a href="/{SPORT}/{r["slug"]}/">{esc(r["name"])}</a>'
            if r["slug"] in links else esc(r["name"]))
    dec = ("" if r["decision"] == "UPDATED"
           else f'<span class="chdec">{esc(DECISION_LABELS.get(r["decision"], r["decision"].lower()))}</span>')
    return (f'<div class="chmove">'
            f'<span class="chpos">{esc(r["pos"])}</span>'
            f'<span class="chname">{name}</span>'
            f'<span class="chrank">{rank}</span>'
            f'{dec}'
            f'<span class="chpts {cls}">{pts}</span>'
            f'</div>')


def build_html(rows, meta, links, css, header, footer, built):
    groups = group_changes(rows)
    moved = [r for r in rows if r["decision"] != "WATCH, NO CHANGE"]
    watch = [r for r in rows if r["decision"] == "WATCH, NO CHANGE"]
    updated = meta.get("updated") or built.strftime("%B %-d, %Y")

    blocks = []
    for g in groups:
        ev = g["evidence"] or ""
        evcls = "e-" + ev.lower().split("-")[0] if ev else ""
        src = (f'<a class="chsrc" href="{esc(g["source"])}" '
               f'rel="nofollow noopener" target="_blank">Source</a>'
               if g["source"].startswith("http") else "")
        posns = sorted({r["pos"] for r in g["rows"]})
        blocks.append(
            f'<article class="chgroup" data-pos="{esc(",".join(posns))}" '
            f'data-ev="{esc(ev)}">\n'
            f'  <p class="chwhy">{esc(PUBLIC_REASONS.get(g["reason"], g["reason"]))}</p>\n'
            f'  <div class="chmeta">'
            f'{f_ev(ev, evcls)}{src}</div>\n'
            f'  <div class="chmoves">'
            f'{"".join(move_html(r, links) for r in g["rows"])}</div>\n'
            f'</article>')

    body = f"""<main class="chwrap">
  <nav class="crumbs" aria-label="Breadcrumb">
    <a href="/">LineupBeat</a><span>/</span>
    <a href="/{SPORT}/data/">Fantasy data</a><span>/</span>
    <a href="/{SPORT}/projections/">Projections</a><span>/</span>
    <b>Changes</b></nav>

  <div class="chhead">
    <h1>What changed in our projections</h1>
    <p class="chsub">A dated record of season projection changes and the reports behind them.
      <span class="chdate">{esc(updated)}</span></p>
  </div>

  <div class="chctl" role="group" aria-label="Position">
    <span class="chlab">Position</span>
    <button class="chtab" data-f="ALL" aria-pressed="true">All</button>
    {''.join(f'<button class="chtab" data-f="{p}" aria-pressed="false">{p}</button>'
             for p in POSITIONS)}
  </div>

  <p class="chsub" style="margin-top:.8rem">
    <b>{len(moved)}</b> projections moved across
    <b>{len(groups)}</b> decisions.
    {f"<b>{len(watch)}</b> looked at and left alone." if watch else ""}</p>

{chr(10).join(blocks)}

  <p class="chempty" id="chempty" hidden>Nothing changed at that
    position.</p>

  <section class="chnote">
    <h2>How to read this</h2>
    <p>Changes are grouped by what caused them, because one piece of news
       usually moves several players. A receiver ruled out for the season
       raises everybody else in that receiving corps, and those are one
       decision rather than five.</p>
    <dl>
      <div><dt>Updated</dt>
        <dd>The projection moved on this evidence.</dd></div>
      <div><dt>Related workload change</dt>
        <dd>Moved to stay consistent with another change. A backfield only
            has so many carries, so raising one back lowers another.</dd></div>
      <div><dt>Out for season</dt>
        <dd>Ruled out for the season. The projection is set to zero and his
            opportunity is redistributed.</dd></div>
      <div><dt>Watch, no change</dt>
        <dd>We looked at it and moved nothing. Uncertain injuries and
            possible discipline are monitored rather than automatically
            deducted.</dd></div>
    </dl>
    <p style="margin-top:.9rem">Evidence is our own read on how firm the
       information is, not a measure of how much a projection moved. A
       confirmed starting change is high; a camp report that a player looks
       good is not.</p>
  </section>
{seo.faq_html(CHANGES_FAQ)}{seo.related_html('projections')}
</main>

<script>
let posFilter = "ALL";
const groups = [...document.querySelectorAll(".chgroup")];
const empty = document.getElementById("chempty");

function draw(){{
  let shown = 0;
  groups.forEach(g => {{
    const ok = posFilter === "ALL" ||
      (g.dataset.pos || "").split(",").includes(posFilter);
    g.hidden = !ok;
    if(ok) shown++;
    // Inside a shown group, dim the moves that are not this position:
    // a Pearsall decision that moved four receivers and one back should
    // still explain itself when you filter to backs.
    g.querySelectorAll(".chmove").forEach(m => {{
      const p = m.querySelector(".chpos").textContent.trim();
      m.style.opacity = (posFilter === "ALL" || p === posFilter) ? "" : ".38";
    }});
  }});
  empty.hidden = shown > 0;
}}

document.querySelectorAll("[data-f]").forEach(b =>
  b.addEventListener("click", () => {{
    posFilter = b.dataset.f;
    document.querySelectorAll("[data-f]").forEach(x =>
      x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    draw();
  }}));
draw();
</script>"""
    return body


def f_ev(ev, cls):
    return (f'<span class="chev {cls}">{esc(ev)} evidence</span>'
            if ev else "")


CHANGES_FAQ = [
    ("How often do the projections change?",
     "Whenever the information behind them does. In the preseason that is "
     "usually a few times a week, driven by starting decisions, confirmed "
     "injuries and depth chart moves. This page records each one with the "
     "reason and a source."),
    ("Why did a player's projection change when nothing happened to him?",
     "Because something happened to a team-mate. A backfield has a fixed "
     "number of carries and a passing game a fixed number of targets, so "
     "raising one player lowers another. Those changes are marked "
     "Related workload change and grouped with the news that caused them."),
    ("Does a camp report automatically change a projection?",
     "No. A confirmed starting change or a season-ending injury moves a "
     "projection. A report that somebody looks good in camp is weaker "
     "evidence and usually moves a projection less, or not at all. The "
     "evidence label on each change says which."),
    ("What does Watch, no change mean?",
     "That we looked at something and did not move anything. Uncertain "
     "injuries and possible discipline are monitored rather than "
     "automatically deducted, because guessing at a suspension length is "
     "not analysis."),
]


def add_to_sitemap(url):
    sm = SITE / "sitemap.xml"
    if not sm.exists():
        return False
    text = sm.read_text()
    if url in text:
        return False
    today = eastern_now().strftime("%Y-%m-%d")
    sm.write_text(text.replace(
        "</urlset>",
        f"  <url><loc>{url}</loc><lastmod>{today}</lastmod>"
        f"<changefreq>weekly</changefreq><priority>0.7</priority></url>\n"
        "</urlset>"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projections", default="data/projections.xlsx")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    wb = ROOT / args.projections
    if not wb.exists():
        sys.exit(f"  no {args.projections}")
    rows, meta = read_changes(wb)
    if not rows:
        print(f"\n  no Weekly Update tab in {args.projections}, "
              f"so no changes page")
        return 0

    # Link only where a player page exists, the same rule the board uses.
    links = {p.name for p in (SITE / SPORT).glob("*") if p.is_dir()}

    built = eastern_now()
    css, header, footer = site_chrome()
    body = build_html(rows, meta, links, css, header, footer, built)

    moved = [r for r in rows if r["decision"] != "WATCH, NO CHANGE"]
    updated = meta.get("updated") or built.strftime("%B %-d, %Y")
    title = "What Changed in Our Fantasy Projections | LineupBeat"
    desc = (f"Every 2026 fantasy football projection we changed and why, "
            f"with the report behind each one. {len(moved)} projections "
            f"updated as of {updated}.")

    schema = {
        "@type": "Dataset",
        "name": "LineupBeat fantasy projection changes",
        "description": desc,
        "url": f"{seo.SITE_URL}/{SPORT}/projections/changes/",
        "dateModified": built.strftime("%Y-%m-%d"),
        "creator": {"@type": "Organization", "name": "LineupBeat"},
        **seo.dataset_extras(temporal="2026"),
    }
    crumbs = seo.breadcrumbs([
        ("LineupBeat", "/"), ("Fantasy data", f"/{SPORT}/data/"),
        ("Projections", f"/{SPORT}/projections/"),
        ("Changes", f"/{SPORT}/projections/changes/")])

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap" rel="stylesheet">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical"
      href="{seo.SITE_URL}/{SPORT}/projections/changes/">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url"
      content="{seo.SITE_URL}/{SPORT}/projections/changes/">
<meta property="og:type" content="website">
<script type="application/ld+json">{seo.graph(
    schema, crumbs, seo.faq_schema(CHANGES_FAQ), seo.ORGANISATION)}</script>
<style>{css}{PAGE_CSS}{seo.UI_CSS}{seo.RELATED_CSS}{seo.TEAMS_CSS}{seo.BYLINE_CSS}</style>
</head>
<body>
{header}
{body}
{footer}
{seo.TRACKING}
</body>
</html>"""

    out = (Path(args.out) if args.out
           else SITE / SPORT / "projections" / "changes" / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(seo.check_page(page, str(out)))

    import collections
    dec = collections.Counter(r["decision"] for r in rows)
    print(f"\n  {len(rows)} changes, {len(group_changes(rows))} decisions")
    print("  " + "  ".join(f"{n} {d.lower()}" for d, n in dec.most_common()))
    print(f"\n  wrote {out.relative_to(ROOT)}  ({len(page):,} bytes)")
    if add_to_sitemap(f"{seo.SITE_URL}/{SPORT}/projections/changes/"):
        print(f"  added to sitemap.xml")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
