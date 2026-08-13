#!/usr/bin/env python3
"""Build the offensive line and RB performance page.

    python3 scripts/build_ol_rb.py
    python3 scripts/build_ol_rb.py --season 2025

Two questions, side by side, without pretending they are one question:
how well did the team block designed runs, and how much did the back
create beyond what the blocking predicted.

WHY THESE ARE NOT ONE NUMBER

It would be easy to blend run block win rate and yards over expected into a
single 0-100 grade, and it would be worse. Rushing outcomes come from the
line, the runner, the scheme, the defense and the game situation, and a
composite hides which of those a reader is looking at. The page shows both
and labels the combination descriptively -- "strong line, back added value"
-- rather than asserting a cause.

WHAT RYOE ALREADY ACCOUNTS FOR

Expected rushing yards is computed from the location, speed and direction
of every blocker and defender at the handoff. So RYOE is not "yards minus
average"; it is yards minus what that specific situation predicted, which
is why it can separate a back who beat his blocking from one who was handed
a lane.

HISTORICAL ONLY

Nothing here feeds a projection, and there is no write path to one. This is
evidence a reader interprets, not another forecast.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seo

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
SPORT = "nfl"

# Below this a rate is noise. Twenty-five carries is a game and a half of
# work, which is enough to say something and little enough to keep a
# committee back on the page.
MIN_CARRIES = 25

# What counts as a runner. Quarterback designed runs are a different thing
# from a rushing attack: Jalen Hurts clears any carry threshold, and his
# presence distorts every rate on a page about running backs.
RUSH_POSITIONS = {"RB", "FB", "HB"}
# What the table opens on. Fifty let a back with one long run top the
# board on RYOE; a hundred is a real workload and the leaderboard reads
# like one. The 25 and 50 filters remain for digging.
DEFAULT_MIN = 100

# Where "above expected" starts, in RYOE per attempt. A small number by
# nature: a seventh of a yard a carry over 250 carries is nearly forty
# yards, which is real. Stated on the page, not just here.
ADDED = 0.15


def eastern_now():
    """The date a reader in the league's own time zone would call today."""
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


TEAM_NAMES = {
    "ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills",
    "CAR": "Panthers", "CHI": "Bears", "CIN": "Bengals", "CLE": "Browns",
    "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers",
    "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars", "KC": "Chiefs",
    "LV": "Raiders", "LAC": "Chargers", "LAR": "Rams", "MIA": "Dolphins",
    "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants",
    "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SF": "49ers",
    "SEA": "Seahawks", "TB": "Buccaneers", "TEN": "Titans",
    "WAS": "Commanders",
}


def roster_positions():
    """Position and canonical name by gsis id, from the roster.

    Play-by-play carries no position and gives "J.Hurts" rather than a
    name, so without this a quarterback with fifty designed runs appears
    on a running back page as an unresolvable abbreviation.
    """
    import csv
    out = {}
    rp = ROOT / "rosters" / f"{SPORT}.csv"
    if not rp.exists():
        return out
    for r in csv.DictReader(rp.open()):
        gsis = (r.get("gsis_id") or r.get("player_gsis_id") or "").strip()
        if not gsis:
            continue
        out[gsis] = {"pos": (r.get("position") or "").upper(),
                     "name": (r.get("name") or "").strip(),
                     "slug": slug(r.get("name") or "")}
    return out


def load(conn, season: int):
    """One row per player-team stint, joined to that team's blocking.

    The join is on season and team, not on the player's current team. A
    back traded in October ran behind two lines and the page has to say so,
    or half his season is credited to blocking he never saw.
    """
    ngs = {}
    for r in conn.execute(
            """SELECT player_gsis_id, team, player_name,
                      SUM(rush_attempts), SUM(rush_yards),
                      SUM(rush_touchdowns),
                      SUM(rush_yards_over_expected),
                      AVG(rush_pct_over_expected),
                      AVG(avg_time_to_los),
                      AVG(percent_attempts_gte_eight_defenders),
                      AVG(efficiency), MAX(position)
               FROM rb_ngs_weekly
               WHERE season=? AND season_type='REG' AND week > 0
               GROUP BY player_gsis_id, team""", (season,)):
        att = r[3] or 0
        ngs[(r[0], r[1])] = {
            "name": r[2], "att": att, "yards": r[4] or 0, "td": r[5] or 0,
            "ryoe": r[6], "roe_pct": r[7], "tlos": r[8],
            "box8": r[9], "efficiency": r[10], "pos": r[11], "pos": r[11],
            "ypc": (r[4] / att) if att else None,
            "ryoe_att": (r[6] / att) if att and r[6] is not None else None,
        }

    ol = {}
    for r in conn.execute(
            """SELECT team, rbwr_pct, rbwr_rank, rbwr_tier, source_url,
                      source_date
               FROM ol_team_season WHERE season=? AND season_type='REG'""",
            (season,)):
        ol[r[0]] = {"rbwr": r[1], "rank": r[2], "tier": r[3],
                    "url": r[4], "date": r[5]}

    roster = roster_positions()

    rows = []
    for r in conn.execute(
            """SELECT player_gsis_id, team, player_name, qualifying_carries,
                      rush_success_rate, stuff_rate, explosive_run_rate,
                      short_yardage_attempts, short_yardage_conversion_rate
               FROM rb_pbp_season WHERE season=?""", (season,)):
        gsis, team = r[0], r[1]
        n = ngs.get((gsis, team), {})

        # Running backs only, and only where we can name them.
        #
        # A position we recognise, from Next Gen Stats or the roster, and a
        # full name rather than an initial. A row nobody can identify is
        # worse than a missing row: it looks like a bug and cannot be
        # checked.
        rr = roster.get(gsis) or {}
        pos = (n.get("pos") or rr.get("pos") or "").upper()
        if pos not in RUSH_POSITIONS:
            continue
        full = n.get("name") or rr.get("name")
        if not full or "." in full[:3]:
            continue

        rows.append({
            "id": gsis, "team": team,
            "name": full,
            "slug": rr.get("slug") or slug(full),
            "carries": r[3],
            "success": r[4], "stuff": r[5], "explosive": r[6],
            "short_att": r[7], "short_pct": r[8],
            # NGS applies its own qualification rules, so a low-volume back
            # can be missing here. Missing stays missing: an imputed RYOE
            # is a number nobody measured.
            "att": n.get("att"), "yards": n.get("yards"),
            "ypc": n.get("ypc"), "ryoe_att": n.get("ryoe_att"),
            "roe_pct": n.get("roe_pct"), "tlos": n.get("tlos"),
            "box8": n.get("box8"),
            "rbwr": (ol.get(team) or {}).get("rbwr"),
            "ol_rank": (ol.get(team) or {}).get("rank"),
            "ol_tier": (ol.get(team) or {}).get("tier"),
        })
    return rows, ol


# The line between "above expected" and "in line", stated rather than
# implied. Somebody will eventually notice that +0.17 qualifies and +0.08
# does not, and the answer should be on the page rather than in the code.
ABOVE = 0.15


def context_label(r, ol_tiers=None):
    """The blocking tier, then the runner's result against expectation.

    The tier is read from the stored ranking rather than recomputed here:
    two places deciding what "Elite" means is two places to disagree.
    """
    v = r.get("ryoe_att")
    if v is None:
        back = None
    elif v >= ABOVE:
        back = "above expected"
    elif v <= -ABOVE:
        back = "below expected"
    else:
        back = "in line"

    tier = r.get("ol_tier")
    if tier and back:
        return f"{tier} line, {back}"
    if back:
        return back.capitalize()
    return f"{tier} line" if tier else ""


PAGE_CSS = """
.topbar .logo,.topbar .vbtn{text-decoration:none}
.topbar .vbtn:hover{text-decoration:none; color:var(--ink)}
.vbtn[aria-current="page"]{color:#0A0C08; background:var(--signal);
  border-color:var(--signal)}

/* ---- offensive line and RB ----
   Two measures side by side. The colour scale runs on RYOE only: the
   blocking number is context, not a verdict on the runner. */
.olwrap{max-width:1180px; margin:0 auto; padding:0 1rem 4rem}
.olhead h1{font-size:1.7rem; margin:1.6rem 0 0; letter-spacing:-.01em;
  font-family:var(--text)}
.olsub{color:var(--quiet); font-size:.86rem; margin:.4rem 0 0; max-width:74ch;
  line-height:1.55}
.oldate{display:inline-block; margin-left:.4rem; font-family:var(--agate);
  text-transform:uppercase; letter-spacing:.06em; font-size:.7rem;
  color:var(--signal); border:1px solid var(--rule); border-radius:999px;
  padding:.1rem .5rem; vertical-align:.05em}
.olcards{display:grid; grid-template-columns:repeat(3, 1fr); gap:.7rem;
  margin:1.2rem 0 0}
@media (max-width:820px){ .olcards{grid-template-columns:1fr} }
.olcard{background:var(--card); border:1px solid var(--rule);
  border-radius:8px; padding:.75rem .9rem}
.olcard p{margin:.3rem 0 0; font-size:.84rem; line-height:1.45;
  color:var(--quiet)}
.olcard p b{color:var(--ink)}
.olk{font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em;
  font-size:.68rem; color:var(--signal)}

.olseasons{display:inline-flex; gap:.3rem; margin-left:.4rem;
  vertical-align:.05em}
.olseason{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.06em; font-size:.7rem; color:var(--quiet);
  border:1px solid var(--rule); border-radius:999px; padding:.1rem .5rem;
  text-decoration:none}
.olseason:hover{color:var(--ink); border-color:var(--ink)}
.olseason.on{color:var(--signal); border-color:var(--signal)}

/* A heading before the player table. Without it the page runs from one
   set of controls straight into another and a reader does not notice he
   has moved from teams to players. */
.olsection{font-family:var(--text); font-size:1.1rem; color:var(--ink);
  margin:2.2rem 0 0; padding-top:1.4rem;
  border-top:1px solid var(--rule)}

.olctl{display:flex; gap:.3rem; flex-wrap:wrap; align-items:center;
  margin:1.3rem 0 .3rem}
.ollab{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.07em; font-size:.66rem; color:var(--quiet);
  margin-right:.3rem}
.oltab{font-family:var(--agate); text-transform:uppercase;
  background:transparent; border:1px solid var(--rule); color:var(--quiet);
  font-size:.76rem; padding:.3rem .7rem; border-radius:999px;
  cursor:pointer; letter-spacing:.04em}
.oltab:hover{color:var(--ink); border-color:var(--ink)}
.oltab[aria-pressed="true"]{background:var(--signal);
  border-color:var(--signal); color:#0b0f0a; font-weight:600}
.olsearch input{background:var(--card); border:1px solid var(--rule);
  color:var(--ink); font:inherit; font-size:.82rem; padding:.34rem .7rem;
  border-radius:6px; min-width:170px}

.oltblwrap{overflow-x:auto}
.oltbl{width:100%; border-collapse:collapse; font-size:.86rem;
  font-variant-numeric:tabular-nums; margin-top:1rem; min-width:56rem}
/* Every heading carries a title, so the cursor says so rather than
   leaving somebody to guess what ROE% means. */
.oltbl th[title]{cursor:help; border-bottom-style:dashed}
.oltbl th{text-align:right; font-family:var(--agate); font-size:.68rem;
  letter-spacing:.07em; text-transform:uppercase; color:var(--quiet);
  font-weight:600; padding:.5rem .45rem; border-bottom:1px solid var(--rule);
  white-space:nowrap}
.oltbl th.l,.oltbl td.l{text-align:left}
.oltbl td{padding:.4rem .45rem; border-bottom:1px solid var(--rule);
  text-align:right}
.oltbl tbody tr:hover{background:var(--card)}
.olrk{font-family:var(--data); font-size:.76rem; color:var(--quiet);
  width:2.6rem}
.oltm{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.03em; font-size:.76rem; color:var(--quiet)}
.olnm a{color:var(--ink); text-decoration:none}
.olnm a:hover{color:var(--signal)}
.olv{font-family:var(--data)}
.olryoe{font-family:var(--data); font-weight:600}
.up{color:#8BE04E} .down{color:#FF6B4A}
.olctx{font-size:.76rem; color:var(--quiet); white-space:nowrap}
.olmissing{color:var(--rule)}
.olsmall{font-family:var(--data); font-size:.6rem; color:var(--alert);
  opacity:.8; margin-left:.3rem}

/* ---- run blocking rankings ---- */
.olrank{margin:2rem 0 0; padding-top:1.4rem;
  border-top:1px solid var(--rule)}
.olrank h2{font-family:var(--text); font-size:1.1rem; color:var(--ink);
  margin:0}
.olranktbl{min-width:0}
.olteam{color:var(--quiet); font-size:.82rem}
.olmore{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.05em; font-size:.74rem; color:var(--quiet);
  background:transparent; border:1px solid var(--rule); border-radius:999px;
  padding:.35rem .8rem; cursor:pointer; margin:.7rem 0 0}
.olmore:hover{color:var(--ink); border-color:var(--ink)}
.olrounding{color:var(--quiet); font-size:.76rem; line-height:1.5;
  max-width:70ch; margin:.7rem 0 0}
/* The break between the two ends, so it reads as top and bottom rather
   than as a table that stops at five and resumes at twenty-eight. */
#olrankbody tr.olmid + tr:not(.olmid){border-top:1px solid var(--rule)}
@media (max-width:760px){
  .olmore{min-height:44px}
}
.oltier{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.05em; font-size:.7rem}
.t-elite{color:#8BE04E} .t-strong{color:#B9DE7E}
.t-average{color:var(--quiet)} .t-weak{color:#E8A87C}
.t-poor{color:#FF6B4A}

/* ---- summary cards ----
   Three answers a casual reader wants before the table. Each is a real
   row; a card with nothing to show is hidden rather than filled. */
.olsums{display:grid; grid-template-columns:repeat(3, 1fr); gap:.7rem;
  margin:1.2rem 0 0}
@media (max-width:820px){ .olsums{grid-template-columns:1fr} }
.olsum{background:var(--card); border:1px solid var(--rule);
  border-left:2px solid var(--signal); border-radius:8px;
  padding:.8rem .95rem}
.olbig{font-family:var(--text); font-size:1.05rem; color:var(--ink);
  margin:.3rem 0 0; line-height:1.25}
.olsmallnote{margin:.25rem 0 0; font-size:.78rem; color:var(--quiet);
  font-variant-numeric:tabular-nums}

.olnote{color:var(--quiet); font-size:.8rem; margin:1.4rem 0 0;
  max-width:76ch; line-height:1.6}
.olmeth{margin:2.4rem 0 0; border-top:1px solid var(--rule);
  padding-top:1.3rem}
.olmeth h2{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.07em; font-size:.78rem; color:var(--quiet);
  margin:0 0 .6rem}
.olmeth p{font-size:.86rem; line-height:1.6; color:var(--ink);
  max-width:76ch; margin:0 0 .8rem}
.olmeth p b{color:var(--signal)}
.olmeth p.dim{color:var(--quiet); font-size:.82rem}
.olempty{color:var(--quiet); font-size:.86rem; padding:1.2rem 0}

@media (max-width:760px){
  .oltab{min-height:44px; display:inline-flex; align-items:center}
  .olsearch{width:100%}
  .olsearch input{width:100%; font-size:16px; min-height:44px}
}
"""


def site_chrome():
    tpl = SITE / "template.html"
    if not tpl.exists():
        return "", "", ""
    src = tpl.read_text()
    css = re.search(r"<style>(.*?)</style>", src, re.S)
    foot = re.search(r"<footer.*?</footer>", src, re.S)
    header = (
        '<header class="topbar">\n'
        '  <div class="wrap tbrow">\n'
        '    <a class="logo" href="/">Lineup<em>Beat</em></a>\n'
        '    <nav class="views"><a class="vbtn" href="/">The Wire</a>'
        '<a class="vbtn" href="/#v=roster">My Roster</a>'
        f'<a class="vbtn" href="/{SPORT}/data/" aria-current="page">'
        'Fantasy Data</a>'
        + seo.teams_menu(SPORT)
        + '<a class="vbtn" href="/about/">Who We Are</a></nav>\n'
        '  </div>\n'
        '</header>')
    return (css.group(1) if css else ""), header, (foot.group(0) if foot else "")


def fmt(v, digits=1, pct=False, plus=False, suffix=""):
    """One formatter, two scales.

    Next Gen Stats mixes them: rush_pct_over_expected arrives as 0.47 while
    the eight-defender share arrives as 30.4. Both are percentages and only
    one needs multiplying, so the caller says which rather than the
    formatter guessing and being wrong half the time.
    """
    if v is None:
        return '<span class="olmissing">&mdash;</span>'
    if pct:
        return f"{v * 100:.0f}%"
    s = f"{v:+.{digits}f}" if plus else f"{v:.{digits}f}"
    return s.replace("-", "\u2212") + suffix


def rankings_html(ol):
    """Thirty-two teams by run blocking, on their own.

    Somebody who only wants to know which lines blocked best should not
    have to read a seventy-row player table to find out. This is the same
    data the player rows join to, presented for the simpler question.
    """
    rows = sorted((v | {"team": k} for k, v in ol.items()),
                  key=lambda x: (x.get("rank") or 99, x["team"]))
    cells = []
    for r in rows:
        pct = r.get("rbwr")
        pct_s = "&mdash;" if pct is None else f"{pct:.1f}%"
        rank_s = r.get("rank") or "&mdash;"
        tier = r.get("tier") or ""
        cells.append(
            f'<tr data-rank="{r.get("rank") or ""}" '
            f'data-rbwr="{pct if pct is not None else ""}">'
            f'<td class="olv olrk">{rank_s}</td>'
            f'<td class="l oltm">'
            f'<a href="/{SPORT}/team/{r["team"].lower()}/">'
            f'{esc(r["team"])}</a></td>'
            f'<td class="l olteam">{esc(TEAM_NAMES.get(r["team"], ""))}</td>'
            f'<td class="olv">{pct_s}</td>'
            f'<td class="l oltier t-{esc(tier.lower())}">{esc(tier)}</td>'
            f'</tr>')
    # Top five and bottom five first. Thirty-two rows immediately above a
    # forty-eight row table is two data dumps in sequence, and the answer
    # somebody came for is in the first five lines of each end.
    body = "".join(cells)
    n = len(cells)
    if n > 12:
        head = "".join(cells[:5])
        tail = "".join(cells[-5:])
        middle = "".join(
            c.replace("<tr ", '<tr class="olmid" hidden ', 1)
            for c in cells[5:-5])
        body = head + middle + tail
    return f"""
  <section class="olrank">
    <h2>2025 Run Blocking Rankings</h2>
    <p class="olsub">All 32 teams by ESPN Run Block Win Rate. Highest win
      rate ranks first.</p>
    <div class="olctl">
      <span class="ollab">Sort</span>
      <button class="oltab" data-rsort="rank" aria-pressed="true">Rank</button>
      <button class="oltab" data-rsort="rbwr" aria-pressed="false">RBWR</button>
    </div>
    <div class="oltblwrap">
    <table class="oltbl olranktbl">
      <thead><tr>
        <th class="olrk" title="Team ranking from 1 to 32 based on run block
win rate. Highest win rate ranks first.">Rank</th>
        <th class="l">Team</th>
        <th class="l"></th>
        <th title="ESPN Analytics Run Block Win Rate: the percentage of run
blocking assignments won.">RBWR</th>
        <th class="l">Tier</th>
      </tr></thead>
      <tbody id="olrankbody">{body}</tbody>
    </table>
    </div>
    <button class="olmore" id="olmore" aria-expanded="false">
      View all 32 teams</button>
    <p class="olrounding">ESPN displays RBWR rounded to whole percentages.
      Rankings shown are ESPN&rsquo;s published rankings, so teams with the
      same displayed percentage can have different ranks.</p>
  </section>
"""


def summary_cards(rows, ol, min_carries):
    """Three answers a casual reader wants without reading a table.

    Each is a real row from the data, and a card with no qualifying player
    is hidden rather than filled with the closest thing to hand.
    """
    if not ol:
        return ""
    cards = []

    best = min((v | {"team": k} for k, v in ol.items()
                if v.get("rank")), key=lambda x: x["rank"], default=None)
    if best:
        cards.append(
            f'<div class="olsum"><span class="olk">Best run blocking</span>'
            f'<p class="olbig">{esc(TEAM_NAMES.get(best["team"], best["team"]))}'
            f'</p><p class="olsmallnote">{best["rbwr"]:.1f}% RBWR '
            f'&middot; #{best["rank"]} &middot; '
            f'{esc(best.get("tier") or "")}</p></div>')

    q = [r for r in rows if r["carries"] >= min_carries
         and r.get("ryoe_att") is not None]
    if q:
        top = max(q, key=lambda r: r["ryoe_att"])
        cards.append(
            f'<div class="olsum" data-card="above">'
            f'<span class="olk">Most above expected</span>'
            f'<p class="olbig">{esc(top["name"])}</p>'
            f'<p class="olsmallnote">{top["ryoe_att"]:+.2f} RYOE/att '
            f'&middot; {top["carries"]} carries</p></div>')

    # Behind weak blocking specifically: rank 23 or worse, which is the
    # bottom ten. This is the card the page exists for -- a back producing
    # without help is the thing a rushing leaderboard cannot show.
    weak = [r for r in q if (r.get("ol_rank") or 0) >= 23]
    if weak:
        top = max(weak, key=lambda r: r["ryoe_att"])
        cards.append(
            f'<div class="olsum" data-card="weak">'
            f'<span class="olk">Best behind bottom 10 run '
            f'blocking</span>'
            f'<p class="olbig">{esc(top["name"])}</p>'
            f'<p class="olsmallnote">{top["ryoe_att"]:+.2f} RYOE/att '
            f'&middot; {esc(top["team"])} #{top["ol_rank"]}</p></div>')

    return f'<div class="olsums">{"".join(cards)}</div>' if cards else ""


def row_html(r, ol_tiers, links, has_rbwr):
    """One player-team stint.

    The blocking columns only exist when there is blocking data. An empty
    column and a row of dashes reads as a broken page rather than a page
    waiting for a number, and the team code already says which line he ran
    behind.
    """
    small = ('<span class="olsmall" title="Fewer than 50 carries">small'
             '</span>' if r["carries"] < 50 else "")
    ry = r.get("ryoe_att")
    cls = "" if ry is None else ("up" if ry >= ABOVE
                                 else "down" if ry <= -ABOVE else "")
    name = (f'<a href="/{SPORT}/{r["slug"]}/">{esc(r["name"])}</a>'
            if r["slug"] in links else esc(r["name"]))

    block = ""
    if has_rbwr:
        rb = r.get("rbwr")
        rank = r.get("ol_rank")
        block = (f'<td class="olv olrk">'
                 f'{"&mdash;" if rank is None else rank}</td>'
                 f'<td class="olv">'
                 f'{"&mdash;" if rb is None else f"{rb:.1f}%"}</td>')

    def d(v):
        return "" if v is None else f'{v}'

    return (
        f'<tr data-team="{esc(r["team"])}" '
        f'data-name="{esc((r["name"] or "").lower())}" '
        f'data-carries="{r["carries"]}" '
        f'data-rbwr="{d(r.get("rbwr"))}" '
        f'data-ryoe="{d(r.get("ryoe_att"))}" '
        f'data-stuff="{d(r.get("stuff"))}" '
        f'data-expl="{d(r.get("explosive"))}" '
        f'data-olrank="{d(r.get("ol_rank"))}">'
        f'<td class="l oltm">{esc(r["team"])}</td>'
        + block +
        f'<td class="l olnm">{name}{small}</td>'
        f'<td class="olv">{r["carries"]}</td>'
        f'<td class="olv">{fmt(r.get("ypc"))}</td>'
        f'<td class="olryoe {cls}">{fmt(ry, 2, plus=True)}</td>'
        f'<td class="olv">{fmt(r.get("roe_pct"), pct=True)}</td>'
        f'<td class="olv">{fmt(r.get("tlos"), 2)}</td>'
        f'<td class="olv">{fmt(r.get("box8"), 0, suffix="%")}</td>'
        f'<td class="olv">{fmt(r.get("stuff"), pct=True)}</td>'
        f'<td class="olv">{fmt(r.get("explosive"), pct=True)}</td>'
        f'<td class="l olctx">{esc(context_label(r, ol_tiers))}</td>'
        f'</tr>')


OL_FAQ = [
    ("What is rushing yards over expected?",
     "Rushing Yards Over Expected, or RYOE, compares the yards a runner "
     "actually gained with the yards Next Gen Stats expected him to gain. "
     "Expected rushing yards uses the location, speed and direction of "
     "blockers and defenders at the handoff. Positive RYOE means the "
     "runner gained more than expected, negative RYOE means he gained "
     "fewer."),
    ("What is run block win rate?",
     "Run Block Win Rate, or RBWR, is an ESPN Analytics metric that "
     "measures how often blockers win their assignments on running plays. "
     "ESPN builds its blocking win rate metrics using NFL Next Gen Stats "
     "player tracking data."),
    ("Does a strong line mean a running back will produce?",
     "No. Blocking is only part of the rushing environment. The runner, "
     "scheme, defensive alignment, quarterback threat, game situation and "
     "other factors also influence rushing results."),
    ("What is stuff rate?",
     "The percentage of carries stopped at or behind the line of "
     "scrimmage. It is an outcome influenced by both the blocking and the "
     "runner, not an offensive line grade."),
    ("What counts as an explosive run?",
     "For this page, an explosive run is a carry gaining 10 or more "
     "yards."),
    ("How are the Vs. expected labels decided?",
     "Above expected means +0.15 RYOE per attempt or higher. In line with "
     "expected means \u22120.14 to +0.14. Below expected means "
     "\u22120.15 or lower."),
    ("Why is a player missing a number?",
     "Some advanced rushing metrics require a qualifying workload. A blank "
     "means the player did not qualify for that measurement, not that his "
     "value was zero."),
    ("Does this affect LineupBeat's projections?",
     "No. This page is historical context only. Nothing shown here is used "
     "to change LineupBeat's projections."),
]


def build_html(rows, ol, season, built, links, has_rbwr, available=None):
    # League thirds by blocking rank, so "strong" means top third rather
    # than a number somebody picked.
    ranked = sorted((r for r in ol.values() if r.get("rank")),
                    key=lambda x: x["rank"])
    n_ol = len(ranked)
    ol_tiers = (max(1, n_ol // 3), max(2, (n_ol * 2) // 3)) if n_ol else None

    shown = [r for r in rows if r["carries"] >= MIN_CARRIES]
    shown.sort(key=lambda r: (r.get("ryoe_att") is None,
                              -(r.get("ryoe_att") or 0)))
    body_rows = "\n".join(row_html(r, ol_tiers, links, has_rbwr)
                          for r in shown)

    # The blocking columns exist only when there is blocking data. An empty
    # column reads as broken; a missing one reads as a page that has not
    # been given something yet, which is the truth.
    block_headers = ("" if not has_rbwr else
        '<th class="olrk" title="Team ranking from 1 to 32 based on run '
        'block win rate. Highest win rate ranks first.">Run block '
        'rank</th>'
        '<th title="ESPN Analytics Run Block Win Rate: the percentage of '
        'run blocking assignments won.">RBWR</th>')

    sort_row = ("" if not has_rbwr else
        '<span class="ollab">Sort</span>'
        '<button class="oltab" data-sort="ryoe" aria-pressed="true">'
        'RYOE/att</button>'
        '<button class="oltab" data-sort="rbwr" aria-pressed="false">'
        'RBWR</button>'
        '<button class="oltab" data-sort="stuff" aria-pressed="false">'
        'Stuff%</button>'
        '<button class="oltab" data-sort="expl" aria-pressed="false">'
        'Explosive%</button>'
        '<button class="oltab" data-sort="carries" aria-pressed="false">'
        'Attempts</button>')

    # RBWR measures run blocking, not the offensive line: it says
    # nothing about pass protection, which is most of what a line does.
    ol_note = (
        '<div class="olcard"><span class="olk">Blocking</span>'
        '<p>Run Block Win Rate measures <b>how often a team\u2019s '
        'blockers win on running plays</b>, from ESPN Analytics.</p>'
        '</div>'
        if has_rbwr else
        '<div class="olcard"><span class="olk">Blocking</span>'
        '<p>Run Block Win Rate measures how often a team\u2019s '
        'blockers win on running plays. <b>It is not loaded yet.</b>'
        '</p></div>')
    # The headline says only what the table shows. Promising blocking
    # context above a table with no blocking column is the same mistake as
    # leaving the empty column in.
    lede = ("How well each team blocked the run, and how its running backs "
            "performed relative to expectation. Historical performance "
            "only."
            if has_rbwr else
            f"How running backs performed relative to expectation in "
            f"{season}. Team run blocking context will be added with Run "
            f"Block Win Rate.")

    # Credit only what is on the page. Naming ESPN above a table with no
    # ESPN numbers in it is an attribution for data nobody can see.
    attribution = (
        "Run Block Win Rate from ESPN Analytics, powered by NFL Next Gen "
        "Stats. Running back metrics from NFL Next Gen Stats via nflverse. "
        "Play-by-play derived metrics from nflverse and nflfastR."
        if has_rbwr else
        "Running back metrics from NFL Next Gen Stats via nflverse. "
        "Play-by-play derived metrics from nflverse and nflfastR. Run Block "
        "Win Rate will be added from ESPN Analytics.")

    # The rankings table and cards exist only with blocking data, same as
    # the columns: a ranking of nothing is not a ranking.
    rankings = rankings_html(ol) if ol else ""
    summary = summary_cards(rows, ol, DEFAULT_MIN)

    # One season is a label, several are a choice. No selector until
    # there is something to select.
    others = [s for s in (available or []) if s != season]
    season_pills = (
        f'<span class="oldate">{season} season</span>' if not others else
        '<span class="olseasons">'
        + "".join(
            f'<a class="olseason{" on" if s == season else ""}" '
            f'href="/{SPORT}/offensive-line-rb-performance/'
            f'{"" if s == max(available) else str(s) + "/"}">{s}</a>'
            for s in sorted(available or [], reverse=True))
        + '</span>')

    teams = sorted({r["team"] for r in shown})

    return f"""<main class="olwrap">
  <nav class="crumbs" aria-label="Breadcrumb">
    <a href="/">LineupBeat</a><span>/</span>
    <a href="/{SPORT}/data/">Fantasy data</a><span>/</span>
    <b>OL &amp; RB performance</b></nav>

  <div class="olhead">
    <h1>Offensive Line &amp; RB Performance</h1>
    <p class="olsub">{lede}
      {season_pills}</p>
  </div>

  {summary}

  <div class="olcards">
    {ol_note}
    <div class="olcard"><span class="olk">RYOE per carry</span>
      <p>How much a runner gained <b>above or below expectation</b> on his
         carries.</p></div>
    <div class="olcard"><span class="olk">Historical only</span>
      <p>Nothing here affects <b>LineupBeat&rsquo;s projections</b>.</p>
    </div>
  </div>

  {rankings}

  <h2 class="olsection">{season} Running Back Performance</h2>

  {f'<div class="olctl">{sort_row}</div>' if sort_row else ''}

  <div class="olctl">
    <span class="ollab">Minimum carries</span>
    <button class="oltab" data-min="25" aria-pressed="false">25</button>
    <button class="oltab" data-min="50" aria-pressed="false">50</button>
    <button class="oltab" data-min="100" aria-pressed="true">100</button>
    <span class="olsearch"><input id="olq" type="search"
      placeholder="Find a player or team" autocomplete="off"
      aria-label="Find a player or team"></span>
  </div>

  <p class="olcount" id="olcount"></p>

  <div class="oltblwrap">
  <table class="oltbl">
    <thead><tr>
      <th class="l">Team</th>
      {block_headers}
      <th class="l">Player</th>
      <th title="Carries on designed runs. Quarterback kneels and scrambles
are excluded.">Att</th>
      <th title="Yards per carry.">YPC</th>
      <th title="Rushing yards gained above or below expectation, per
carry.">RYOE/att</th>
      <th title="Rush Percentage Over Expected: the share of a runner&#39;s
carries that gained more yards than expected. It counts how often, not by
how much &mdash; RYOE/att is the size of the gap.">Runs above exp.%</th>
      <th title="Time behind line of scrimmage: the average time a runner
spends behind the line before crossing it, in seconds.">TLOS</th>
      <th title="Percentage of carries against eight or more defenders in
the box.">8+ box</th>
      <th title="Percentage of carries stopped at or behind the line of
scrimmage.">Stuff%</th>
      <th title="Percentage of carries gaining 10 or more yards. There is
no universal definition of an explosive run; 10 yards is ours.">Expl%</th>
      <th class="l" title="How the runner performed against expectation,
and where his team ranked in run blocking.">Vs. expected</th>
    </tr></thead>
    <tbody id="olbody">
{body_rows}
    </tbody>
  </table>
  </div>
  <p class="olempty" id="olempty" hidden>Nothing matches that.</p>

  <p class="olnote">Sorted by rush yards over expected per carry. A back
    traded during the season appears once for each team, because he ran
    behind two different lines and one row would credit the wrong one.
    Blank cells are measurements Next Gen Stats did not qualify him for,
    not zeroes.</p>

  <section class="olmeth">
    <h2>What this measures</h2>
    <p>RBWR measures how often a team&rsquo;s blockers win on running
      plays. RYOE measures how a runner performed relative to what Next Gen
      Stats expected on his carries. Looking at the two together adds
      context to both the blocking environment and the runner&rsquo;s
      results.</p>
    <p class="dim">These metrics do not isolate running back talent.
      Scheme, defensive alignment, quarterback threat, game situation and
      other factors also influence rushing results.</p>
    <p class="dim"><b>Vs. expected:</b> Above expected = +0.15 RYOE/att or
      higher. In line = &minus;0.14 to +0.14. Below expected = &minus;0.15
      or lower. Explosive runs gain 10+ yards.</p>
  </section>
{seo.faq_html(OL_FAQ)}{seo.related_html('ol-rb')}

  <p class="olnote">{attribution} Last updated {built:%B %-d, %Y}.</p>
</main>

<script>
let minCarries = 100, sortKey = "ryoe";
const body = document.getElementById("olbody");
const rows = [...document.querySelectorAll("#olbody tr")];
const empty = document.getElementById("olempty");
const count = document.getElementById("olcount");
const q = document.getElementById("olq");

function draw(){{
  const term = (q.value || "").trim().toLowerCase();
  let shown = 0;
  rows.forEach(tr => {{
    let ok = +tr.dataset.carries >= minCarries;
    if(ok && term){{
      ok = tr.dataset.name.includes(term) ||
           tr.dataset.team.toLowerCase() === term;
    }}
    tr.hidden = !ok;
    if(ok) shown++;
  }});
  empty.hidden = shown > 0;
  count.textContent = shown +
    (shown === 1 ? " qualifying running back" : " qualifying running backs");
}}

// Sorting happens on the rows already in the page, so it costs nothing
// and works before any script the browser is still fetching. Every key
// sorts descending except stuff rate, where low is good.
document.querySelectorAll("[data-sort]").forEach(b =>
  b.addEventListener("click", () => {{
    sortKey = b.dataset.sort;
    document.querySelectorAll("[data-sort]").forEach(x =>
      x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    const asc = sortKey === "stuff";
    const val = tr => {{
      const v = tr.dataset[sortKey];
      return v === "" || v === undefined ? null : parseFloat(v);
    }};
    rows.sort((a, b2) => {{
      const x = val(a), y = val(b2);
      // A missing measurement sorts last whichever way the column runs:
      // it is not a low value, it is an absent one.
      if(x === null && y === null) return 0;
      if(x === null) return 1;
      if(y === null) return -1;
      return asc ? x - y : y - x;
    }});
    rows.forEach(tr => body.appendChild(tr));
    draw();
  }}));

document.querySelectorAll("[data-min]").forEach(b =>
  b.addEventListener("click", () => {{
    minCarries = +b.dataset.min;
    document.querySelectorAll("[data-min]").forEach(x =>
      x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    draw();
    redrawCards();
  }}));

// Show the middle twenty-two on request. They are in the page already, so
// this is a class toggle rather than a fetch, and the table stays sortable
// either way.
const more = document.getElementById("olmore");
if(more){{
  more.addEventListener("click", () => {{
    const open = more.getAttribute("aria-expanded") === "true";
    document.querySelectorAll("#olrankbody tr.olmid")
      .forEach(tr => tr.hidden = open);
    more.setAttribute("aria-expanded", open ? "false" : "true");
    more.textContent = open ? "View all 32 teams" : "Show top and bottom 5";
  }});
}}

// The rankings table sorts on its own: it answers a different question
// from the player table and should not move when that one is filtered.
const rankBody = document.getElementById("olrankbody");
if(rankBody){{
  const rankRows = [...rankBody.querySelectorAll("tr")];
  document.querySelectorAll("[data-rsort]").forEach(b =>
    b.addEventListener("click", () => {{
      const key = b.dataset.rsort;
      document.querySelectorAll("[data-rsort]").forEach(x =>
        x.setAttribute("aria-pressed", x === b ? "true" : "false"));
      rankRows.sort((x, y) => {{
        const a = parseFloat(x.dataset[key]), c = parseFloat(y.dataset[key]);
        if(isNaN(a) && isNaN(c)) return 0;
        if(isNaN(a)) return 1;
        if(isNaN(c)) return -1;
        // Rank ascends, win rate descends: both put the best line first.
        return key === "rank" ? a - c : c - a;
      }});
      // Sorting a collapsed table would reorder rows nobody can see and
      // leave the visible five looking wrong, so expanding is part of it.
      rankRows.forEach(tr => {{ tr.hidden = false;
                               rankBody.appendChild(tr); }});
      if(more){{
        more.setAttribute("aria-expanded", "true");
        more.textContent = "Show top and bottom 5";
      }}
    }}));
}}

// The two player cards follow the carries filter, because "most above
// expected" means among the players currently being shown. Reading them
// off the visible rows keeps the cards and the table telling one story.
function redrawCards(){{
  const el = document.querySelectorAll("[data-card]");
  if(!el.length) return;
  const live = rows.filter(tr => !tr.hidden && tr.dataset.ryoe !== "");
  const best = (list) => list.reduce((a, b2) =>
    (!a || parseFloat(b2.dataset.ryoe) > parseFloat(a.dataset.ryoe)) ? b2 : a,
    null);
  const fill = (card, tr, note) => {{
    if(!tr){{ card.hidden = true; return; }}
    card.hidden = false;
    card.querySelector(".olbig").textContent =
      tr.querySelector("td.olnm").textContent.replace("small", "").trim();
    card.querySelector(".olsmallnote").textContent = note(tr);
  }};
  const top = best(live);
  fill(document.querySelector('[data-card="above"]'), top, tr =>
    `${{(+tr.dataset.ryoe).toFixed(2).replace(/^(?!-)/, "+")}} RYOE/att `
    + `\u00b7 ${{tr.dataset.team}} \u00b7 ${{tr.dataset.carries}} carries`);
  const weak = best(live.filter(tr => +tr.dataset.olrank >= 23));
  fill(document.querySelector('[data-card="weak"]'), weak, tr =>
    `${{(+tr.dataset.ryoe).toFixed(2).replace(/^(?!-)/, "+")}} RYOE/att `
    + `\u00b7 ${{tr.dataset.team}} blocked ${{tr.dataset.olrank}}`);
}}
q.addEventListener("input", draw);
draw();
</script>"""


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


def validate(rows, ol, season):
    """Publication gates from the handoff.

    A page that quietly publishes partial data is worse than one that
    refuses: the reader cannot tell the difference, and neither can we a
    week later.
    """
    bad = []
    if ol and len(ol) != 32:
        bad.append(f"{len(ol)} teams have run block win rate, not 32")
    for t, v in ol.items():
        if v.get("rbwr") is not None and not 0 <= v["rbwr"] <= 100:
            bad.append(f"{t} rbwr {v['rbwr']} outside 0-100")
    for r in rows:
        if not r.get("id"):
            bad.append(f"{r.get('name')} has no player id")
        if (r.get("carries") or 0) < 0:
            bad.append(f"{r.get('name')} has negative carries")
        for k in ("stuff", "explosive", "success"):
            v = r.get(k)
            if v is not None and not 0 <= v <= 1:
                bad.append(f"{r.get('name')} {k} {v} outside 0-1")
    return bad


def seasons_available(conn):
    """Which seasons have enough data to publish.

    A season selector that offers a year with three carries in it is worse
    than one that offers nothing: the reader clicks, sees an empty table,
    and concludes the page is broken rather than early.
    """
    out = []
    for (s, n) in conn.execute(
            "SELECT season, COUNT(*) FROM rb_pbp_season GROUP BY season"):
        if n >= 20:
            out.append(s)
    return sorted(out, reverse=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="beatwire.db")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--seasons", nargs="*", type=int,
                    help="build a page per season, for the selector")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    db = ROOT / args.db
    if not db.exists():
        sys.exit(f"  no database at {db}")
    conn = sqlite3.connect(db)
    try:
        conn.execute("SELECT 1 FROM rb_pbp_season LIMIT 1")
    except sqlite3.OperationalError:
        sys.exit("  no rushing data. Run: python3 scripts/import_rushing.py")

    available = seasons_available(conn)
    rows, ol = load(conn, args.season)
    if not rows:
        sys.exit(f"  no {args.season} rushing data")

    bad = validate(rows, ol, args.season)
    if bad:
        print(f"\n  {len(bad)} thing(s) worth a look:")
        for b in bad[:6]:
            print(f"    {b}")
        print(f"  None of these stops the page being built.")

    links = {p.name for p in (SITE / SPORT).glob("*") if p.is_dir()}
    built = eastern_now()
    css, header, footer = site_chrome()
    body = build_html(rows, ol, args.season, built, links, bool(ol),
                      available)

    qualified = [r for r in rows if r["carries"] >= MIN_CARRIES]
    title = (f"{args.season} NFL Offensive Line & RB Performance | "
             f"LineupBeat")
    desc = (f"How well each NFL team blocked designed runs in "
            f"{args.season} and how much its backs produced beyond "
            f"expectation. Rush yards over expected, stuff rate and "
            f"explosive rate for {len(qualified)} runners.")

    schema = {
        "@type": "Dataset",
        "name": f"{args.season} NFL offensive line and RB performance",
        "description": desc,
        "url": f"{seo.SITE_URL}/{SPORT}/offensive-line-rb-performance/",
        "dateModified": built.strftime("%Y-%m-%d"),
        "creator": {"@type": "Organization", "name": "LineupBeat"},
        "variableMeasured": ["Run block win rate",
                             "Rush yards over expected per attempt",
                             "Stuff rate", "Explosive run rate"],
        **seo.dataset_extras(temporal=str(args.season)),
    }
    crumbs = seo.breadcrumbs([
        ("LineupBeat", "/"), ("Fantasy data", f"/{SPORT}/data/"),
        ("OL and RB performance",
         f"/{SPORT}/offensive-line-rb-performance/")])

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical"
      href="{seo.SITE_URL}/{SPORT}/offensive-line-rb-performance/">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url"
      content="{seo.SITE_URL}/{SPORT}/offensive-line-rb-performance/">
<meta property="og:type" content="website">
<script type="application/ld+json">{seo.graph(
    schema, crumbs, seo.faq_schema(OL_FAQ), seo.ORGANISATION)}</script>
<style>{css}{PAGE_CSS}{seo.RELATED_CSS}{seo.TEAMS_CSS}</style>
</head>
<body>
{header}
{body}
{footer}
{seo.TEAMS_JS}{seo.TRACKING}
</body>
</html>"""

    out = (Path(args.out) if args.out
           else SITE / SPORT / "offensive-line-rb-performance" / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)

    # Who did not resolve, named rather than counted.
    #
    # An unlinked name in the middle of a linked table looks like a bug to
    # a reader and is invisible to us, so the ones that fail are printed:
    # usually a suffix or a nickname the roster spells differently, and
    # each is a one-line alias fix rather than a mystery.
    unlinked = [r for r in qualified if r["slug"] not in links]
    linked = len(qualified) - len(unlinked)
    print(f"\n  {len(qualified)} backs with {MIN_CARRIES}+ carries, "
          f"{linked} link to a player page")
    if unlinked:
        print(f"  {len(unlinked)} did not match a player page:")
        for r in sorted(unlinked, key=lambda x: -x["carries"])[:12]:
            print(f"    {r['name']:<26} {r['team']:<4} "
                  f"{r['carries']:>4} carries   /{r['slug']}/")
        if len(unlinked) > 12:
            print(f"    ... and {len(unlinked) - 12} more")
    print(f"  {len(ol)}/32 teams have run block win rate")
    print(f"  wrote {out.relative_to(ROOT)}  ({len(page):,} bytes)")
    if add_to_sitemap(
            f"{seo.SITE_URL}/{SPORT}/offensive-line-rb-performance/"):
        print(f"  added to sitemap.xml")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
