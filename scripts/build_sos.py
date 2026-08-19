#!/usr/bin/env python3
"""Build the strength of schedule page.

    python3 scripts/build_sos.py
    python3 scripts/build_sos.py --season 2026

Reads site/data/sos.json, written by schedule_strength.py, and renders it.
Run the importer and the calculator first; the workflow does all three in
order so the page follows the season without anybody remembering to.

WHY TWO TABLES AND NOT ONE NUMBER

Opponent win percentage is what every outlet publishes and what a reader
expects to find. Points allowed by position is what decides whether to
start a receiver. Publishing only the first would be publishing the easy
number; publishing only the second would be publishing an unfamiliar one
with nothing to anchor it.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seo
import seo_faqs

ROOT = Path(__file__).resolve().parent.parent


def eastern_now():
    """The date a reader in the league's own time zone would call today.

    UTC rolls over at 8pm Eastern, so a page built in the evening was
    stamped tomorrow and looked a day ahead of the data it was showing.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return eastern_now() - timedelta(hours=4)

SITE = ROOT / "site"
SPORT = "nfl"
POSITIONS = ["QB", "RB", "WR", "TE"]

TEAM_NAMES = {
    "ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills",
    "CAR": "Panthers", "CHI": "Bears", "CIN": "Bengals", "CLE": "Browns",
    "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers",
    "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars", "KC": "Chiefs",
    "LV": "Raiders", "LAC": "Chargers", "LAR": "Rams", "MIA": "Dolphins",
    "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants",
    "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SF": "49ers",
    "SEA": "Seahawks", "TB": "Buccaneers", "TEN": "Titans", "WAS": "Commanders",
}


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


PAGE_CSS = """
/* The nav, identical on every page.
   These are anchors on a static page and buttons in the app, so the browser
   underlined them here and not there -- the same bar looking different
   depending which page you were on. And the accent pill marks where you
   are, which is what the app does and what these pages were not doing. */
.topbar .logo,.topbar .vbtn{text-decoration:none}
.topbar .vbtn:hover{text-decoration:none; color:var(--ink)}
.vbtn[aria-current="page"]{color:#0A0C08; background:var(--signal);
  border-color:var(--signal)}

/* ---- strength of schedule ----
   Two tables, because there are two questions. The colour scale is the
   whole interface: a reader scans for green and stops. */
.sswrap{max-width:1080px; margin:0 auto; padding:0 1rem 4rem}
.sshead{margin:1.6rem 0 .4rem}
.sshead h1{font-size:1.7rem; margin:0; letter-spacing:-.01em;
  font-family:var(--text)}
.sssub{color:var(--quiet); font-size:.86rem; margin:.4rem 0 0; max-width:70ch;
  line-height:1.55}
.ssdate{display:inline-block; margin-left:.5rem; font-family:var(--agate);
  text-transform:uppercase; letter-spacing:.06em; font-size:.7rem;
  color:var(--signal); border:1px solid var(--rule); border-radius:999px;
  padding:.1rem .5rem; vertical-align:.05em}
/* Three boxes, not a paragraph.
   The explanation was six sentences covering two definitions, a caveat and
   a colour key. Nobody reads six sentences to look up a matchup. Each box
   answers one thing and the sentence that matters in each is bold. */
.sscards{display:grid; grid-template-columns:repeat(3, 1fr); gap:.7rem;
  margin:1.2rem 0 0}
@media (max-width:760px){ .sscards{grid-template-columns:1fr} }
.sscard{background:var(--card); border:1px solid var(--rule);
  border-radius:8px; padding:.75rem .9rem}
.sscard p{margin:.3rem 0 0; font-size:.84rem; line-height:1.45;
  color:var(--quiet)}
.sscard p b{color:var(--ink)}
.sck{font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em;
  font-size:.68rem; color:var(--signal)}
.sswhen{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.07em; font-size:.68rem; color:var(--quiet);
  margin:1.5rem 0 .4rem}
/* The playoff window gets a border in the accent colour even unpressed.
   It is the one people came for, and three identical pills say the three
   windows matter equally. They do not. */
/* Opponent record is NFL context, not the product. Positional difficulty
   is what decides a lineup, so the record button reads as the secondary
   thing it is. */
.ssdim{opacity:.7; font-size:.72rem}
.sspo{border-color:var(--signal) !important; color:var(--ink) !important}
.sspo[aria-pressed="true"]{color:#0b0f0a !important}
.sspohint{margin:.5rem 0 0; font-size:.8rem; color:var(--quiet);
  line-height:1.5; max-width:66ch}
.sslegend{margin:.8rem 0 0; font-size:.8rem; color:var(--quiet)}
.sslegend b{font-weight:600}
.ssctl{display:flex; gap:.3rem; flex-wrap:wrap; margin:1.4rem 0 .6rem;
  align-items:center}
.sstab{font-family:var(--agate); text-transform:uppercase;
  background:transparent; border:1px solid var(--rule); color:var(--quiet);
  font-size:.78rem; padding:.32rem .75rem; border-radius:999px;
  cursor:pointer; letter-spacing:.04em}
.sstab:hover{color:var(--ink); border-color:var(--ink)}
.sstab[aria-pressed="true"]{background:var(--signal); border-color:var(--signal);
  color:#0b0f0a; font-weight:600}
.ssh2{font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em;
  font-size:.78rem; color:var(--quiet); margin:2rem 0 .5rem}
/* Fixed widths, not proportional.
   The table filled the full width and divided it evenly, so a three-digit
   number sat four inches from its header and a reader had to trace across
   the row to know which column he was in. Numbers want to be near each
   other and near their label. */
.sstbl{width:100%; max-width:52rem; border-collapse:collapse;
  font-size:.88rem; font-variant-numeric:tabular-nums; table-layout:fixed}
.sstbl col.cw-rk{width:2.6rem}
.sstbl col.cw-tm{width:4.4rem}
.sstbl col.cw-wp{width:5.2rem}
.sstbl col.cw-pos{width:4.2rem}
.sstbl col.c-g{width:4rem}
.sstbl th{text-align:right; font-family:var(--agate); font-size:.7rem;
  letter-spacing:.08em; text-transform:uppercase; color:var(--quiet);
  font-weight:600; padding:.5rem .5rem; border-bottom:1px solid var(--rule)}
.sstbl th.l,.sstbl td.l{text-align:left}
/* Right, like the headers above them.
   The th rule set text-align:right and the td rule set nothing, so every
   number sat at the left edge of a box whose label sat at the right edge.
   The columns were aligned; the contents were not. */
.sstbl td{padding:.4rem .5rem; border-bottom:1px solid var(--rule);
  text-align:right}
.sstbl tbody tr:hover{background:var(--card)}
.sstm{font-family:var(--agate); text-transform:uppercase; letter-spacing:.04em;
  font-weight:600}
.ssrk{color:var(--quiet); font-family:var(--data); font-size:.76rem}
.ssv{font-family:var(--data); font-weight:600}
/* Easiest green, hardest red, with the middle left alone. Five steps, not a
   gradient: a continuous scale invites reading precision that is not there. */
.e1{color:#8BE04E} .e2{color:#B9DE7E}
/* The column you sorted by, marked so the ranking is legible. Without it a
   reader has to remember which button is pressed to read the numbers. */
.ssel{background:rgba(198,245,60,.06);
  box-shadow:inset 1px 0 0 var(--rule), inset -1px 0 0 var(--rule)}
.h1{color:#FF6B4A} .h2{color:#E09478}
.ssopp{color:var(--quiet); font-family:var(--data); font-size:.72rem;
  letter-spacing:.02em}
.ssbye,.ssgm{font-family:var(--data); font-size:.6rem; color:var(--quiet);
  margin-left:.4rem; font-weight:400; letter-spacing:0;
  text-transform:none}
.ssbye{color:var(--alert); opacity:.75}
.sstbl tbody tr.tr{cursor:pointer}
/* ---- expanded schedule ---- */
.ssx td{background:var(--card); border-bottom:1px solid var(--rule)}
.ssxin{padding:.6rem .3rem .9rem; text-align:left}
.ssxh{font-family:var(--agate); text-transform:uppercase; letter-spacing:.06em;
  font-size:.66rem; color:var(--quiet); margin:0 0 .6rem}
.ssxgrid{display:flex; gap:.5rem; flex-wrap:wrap}
.ssw{background:var(--paper); border:1px solid var(--rule); border-radius:6px;
  padding:.35rem .55rem; min-width:5.6rem}
.ssw.bye{opacity:.55}
.sswk{display:block; font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.05em; font-size:.56rem; color:var(--quiet)}
.ssw b{font-family:var(--data); font-size:.82rem; color:var(--ink)}
.sswd{display:block; font-size:.62rem; color:var(--quiet); margin-top:.1rem}
.ssmeth{margin:2.4rem 0 0; border-top:1px solid var(--rule);
  padding-top:1.3rem}
.ssmh{font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em;
  font-size:.78rem; color:var(--quiet); margin:0 0 .6rem}
.ssmeth p{font-size:.86rem; line-height:1.6; color:var(--ink); max-width:74ch;
  margin:0 0 .7rem}
.ssmeth p b{color:var(--signal); font-weight:600}
.ssmeth p:last-child{color:var(--quiet); font-size:.82rem}
.ssfoot{color:var(--quiet); font-size:.78rem; margin:1.6rem 0 0;
  max-width:74ch; line-height:1.55}
/* On a phone the fixed column widths add up to 464px in a 390px viewport,
   so the last column ran off the edge with no way to reach it. Seven
   columns of short numbers do fit; they just cannot each be four rem wide.
   The table is also scrollable as a fallback, because a 320px phone still
   exists. */

/* Touch targets on a phone.
   These pills are ~30px tall, which is fine for a cursor and small for a
   thumb -- the platform guidance is 44. Padding rather than height, so the
   text stays where it is and only the box a finger can hit grows. */
@media (max-width:760px){
  .sstab{min-height:44px; display:inline-flex; align-items:center;
    padding-top:.5rem; padding-bottom:.5rem}
}
/* The opponent column used to be display:none here.
   Opponent win percentage is the number every other outlet publishes and
   the one a reader arrives expecting to find, so hiding it on a phone
   removed the familiar anchor and left only the unfamiliar one. The table
   scrolls sideways instead, with rank and team pinned.

   13px floor: the type dropped to .8rem, which is 12.8. */
@media (max-width:720px){
  .sstbl{table-layout:auto; font-size:.85rem}

  .sstbl col{width:auto !important}
  .sstbl th,.sstbl td{padding:.4rem .45rem}
  .sswrap{padding-left:.6rem; padding-right:.6rem}
}
/* 13px floor, to 900 rather than 720: the table still scrolls at tablet
   widths and the rank sets its own .76rem, which the table rule above
   does not reach. */
@media (max-width:900px){
  .sstbl{font-size:.85rem}
  .sstbl .ssrk{font-size:.82rem}
}
/* First column is a rank, so the shared default width fits. */
.xtab .sstbl{width:auto; min-width:100%}
.xtab .sstbl th, .xtab .sstbl td{border-bottom:0}
"""


def site_chrome():
    tpl = SITE / "template.html"
    if not tpl.exists():
        return "", "", ""
    src = tpl.read_text()
    css = re.search(r"<style>(.*?)</style>", src, re.S)
    foot = re.search(r"<footer.*?</footer>", src, re.S)
    header = seo.site_nav("data")
    return (css.group(1) if css else ""), header, (foot.group(0) if foot else "")


def build_html(data, css, header, footer):
    built = eastern_now()
    rows = data["rows"]
    season = data["season"]
    played = data["weeks_played"]
    blend = data["blend"]
    prev = data["prev_season"]

    if played == 0:
        basis_long = (
            f"Nothing has been played in {season} yet, so the positional "
            f"ratings use {prev} defensive performance. As {season} games "
            f"are played, current-season results progressively replace the "
            f"prior year rather than being switched over at once: early in "
            f"the season the ratings combine both, and by later in the "
            f"season they are primarily {season}.")
        basis_h = f"Using {prev} data"
        basis_b = (f"Nothing has been played yet. It reweights itself as "
                   f"{season} games happen.")
        badge = f"{prev} data"
    elif blend < 0.99:
        basis_long = (
            f"{played} week{'s' if played != 1 else ''} of {season} have "
            f"been played, so the positional ratings combine current-season "
            f"results with {prev} performance. The current season takes "
            f"over progressively as more of it exists, rather than being "
            f"switched on at once, because four games tell you something "
            f"about a defense and not very much.")
        basis_h = f"{blend:.0%} this season"
        basis_b = (f"{played} weeks played, so {1-blend:.0%} is still "
                   f"{prev}. The current season takes over as it goes.")
        badge = f"Week {played}"
    else:
        basis_long = (f"The {season} regular season is complete, so every "
                      f"positional rating uses {season} results.")
        basis_h = f"All {season}"
        basis_b = "The regular season is complete."
        badge = f"{season} final"

    js_rows = json.dumps(rows, separators=(",", ":"))

    # The default view, in the HTML: rest of season, sorted by RB.
    #
    # A crawler with no JavaScript read a page about all 32 schedules that
    # listed none of them.
    def _rank(key, easiest_high):
        vals = [(r.get(key), r["team"]) for r in rows if r.get(key) is not None]
        vals.sort(key=lambda x: -x[0] if easiest_high else x[0])
        return {tm: i + 1 for i, (_v, tm) in enumerate(vals)}
    _rk = {"wp": _rank("opp_win_pct", False)}
    for _p in POSITIONS:
        _rk[_p] = _rank(_p, True)
    _n = len(rows)

    def _shade(rank):
        if not rank:
            return ""
        if rank <= _n * 0.16:
            return "e1"
        if rank <= _n * 0.33:
            return "e2"
        if rank > _n * 0.84:
            return "h1"
        if rank > _n * 0.67:
            return "h2"
        return ""

    _srt = sorted([r for r in rows if r.get("RB") is not None],
                  key=lambda x: -x["RB"])
    _out = []
    for i, r in enumerate(_srt, 1):
        wp = "—" if r.get("opp_win_pct") is None else f'{r["opp_win_pct"]:.3f}'
        cells = "".join(
            f'<td class="ssv {_shade(_rk[p].get(r["team"]))}'
            f'{" ssel" if p == "RB" else ""}">'
            + ("—" if r.get(p) is None else f'{r[p]:.1f}') + "</td>"
            for p in POSITIONS)
        # Same markup the client produces, including the class the click
        # handler binds to. A static row that renders correctly and does
        # not respond to a click looks broken rather than unloaded.
        _bye = (f'<span class="ssbye">Bye '
                f'{", ".join(str(w) for w in r["byes"])}</span>'
                if r.get("byes") else "")
        _out.append(
            f'<tr class="tr" data-team="{esc(r["team"])}">'
            f'<td class="l ssrk c-rk">{i}</td>'
            f'<td class="l sstm c-nm">{esc(r["team"])}{_bye}</td>'
            f'<td class="ssv {_shade(_rk["wp"].get(r["team"]))}">{wp}</td>'
            + cells + "</tr>")
    static = "\n".join(_out)

    body = f"""<main class="sswrap">
  <nav class="crumbs" aria-label="Breadcrumb">
    <a href="/">LineupBeat</a><span>/</span>
    <a href="/{SPORT}/data/">Fantasy data</a><span>/</span>
    <b>Strength of schedule</b></nav>

  <div class="sshead">
    <h1>{season} Strength of Schedule</h1>
    <p class="sssub">Which teams have the easiest remaining schedule, by
      opponent record and by the fantasy points each opponent allows.
      <span class="ssdate">{esc(badge)}</span></p>
  </div>

{seo.byline_html(built, method="Opponent record and fantasy points allowed, by position")}
  <div class="sscards">
    <div class="sscard">
      <span class="sck">Opp win %</span>
      <p>How good the teams they play are. <b>Low is easy.</b></p>
    </div>
    <div class="sscard">
      <span class="sck">QB RB WR TE</span>
      <p>PPR points those teams give up per game, to
         <b>every player at that position combined</b>, not to one player.
         <b>High is easy.</b></p>
    </div>
    <div class="sscard">
      <span class="sck">{esc(basis_h)}</span>
      <p>{esc(basis_b)}</p>
    </div>
  </div>

  <p class="sslegend"><b class="e1">Green</b> is a soft matchup,
     <b class="h1">red</b> is a tough one. Each column is coloured on its
     own.</p>

  <p class="sswhen">Which weeks:</p>
  <div class="ssctl" role="group" aria-label="Weeks">
    <button class="sstab" data-w="all" aria-pressed="true">Rest of season</button>
    <button class="sstab" data-w="next4" aria-pressed="false">Next 4 weeks</button>
    <button class="sstab sspo" data-w="playoffs" aria-pressed="false">
      Fantasy playoffs &middot; 15-17</button>
  </div>
  <p class="sspohint">A favourable early schedule matters, but weeks 15
     through 17 often decide fantasy championships. Use the playoff view to
     find teams with favourable late-season matchups.</p>

  <p class="sswhen">Difficulty for</p>
  <div class="ssctl" role="group" aria-label="Position">
    {''.join(f'<button class="sstab" data-p="{p}" '
             f'aria-pressed="{"true" if p == "RB" else "false"}">{p}</button>'
             for p in POSITIONS)}
    <button class="sstab ssdim" data-p="RECORD" aria-pressed="false">
      Opponent record</button>
  </div>

  <p class="sswhen">Scoring</p>
  <div class="ssctl" role="group" aria-label="Scoring">
    <button class="sstab" data-s="ppr" aria-pressed="true">PPR</button>
    <button class="sstab" data-s="half" aria-pressed="false">Half PPR</button>
    <button class="sstab" data-s="std" aria-pressed="false">Standard</button>
  </div>

  {seo.scroll_hint("points allowed by position")}
  <div class="xtab" tabindex="0" role="region"
       aria-label="Strength of schedule">
  <table class="sstbl">
    <colgroup>
      <col class="cw-rk"><col class="cw-tm"><col class="cw-wp">
      <col class="cw-pos"><col class="cw-pos"><col class="cw-pos">
      <col class="cw-pos">
    </colgroup>
    <thead><tr>
      <th class="l ssrk c-rk">#</th>
      <th class="l c-nm">Team</th>
      <th>Opp win %</th>
      <th>QB</th><th>RB</th><th>WR</th><th>TE</th>
    </tr></thead>
    <tbody id="sstbody">
{static}
    </tbody>
  </table>
  </div>

  <section class="ssmeth">
    <h2 class="ssmh">How this is measured</h2>
    <p>Fantasy strength of schedule measures how favourable each team's
       remaining opponents are for quarterbacks, running backs, wide
       receivers and tight ends. <b>Lower difficulty means the upcoming
       defenses have historically allowed more fantasy production to that
       position.</b></p>
    <p>{basis_long}</p>
  </section>

  <p class="ssfoot">
    A backfield splitting carries two ways still puts both backs into its
    opponents' number, so this measures how good a matchup a defense is,
    not what any one player would score against it. Ranks run from 1, the
    easiest. Only games not yet played are counted, so the table shrinks as
    the season goes and empties once it ends. Updated {built:%B %-d, %Y}.
  </p>
{seo.faq_html(seo_faqs.SOS)}{seo.related_html('strength-of-schedule')}
</main>

<script>
const SOS = {js_rows};
const SEASON = {season};
const LABEL = {{ppr: "PPR", half: "Half PPR", std: "Standard"}};
let weeks = "all", pos = "RB", fmt = "ppr";

function windowed(r){{
  // A team's remaining games inside the selected weeks.
  //
  // "Next 4" counts from the first unplayed week across the league rather
  // than each team's own, so a team on a bye is not quietly given an extra
  // game that everybody else does not get.
  if(weeks === "playoffs") return r.sched.filter(g => g.w >= 15 && g.w <= 17);
  if(weeks === "next4"){{
    const first = Math.min(...SOS.flatMap(x => x.sched.map(g => g.w)));
    return r.sched.filter(g => g.w < first + 4);
  }}
  return r.sched;
}}

function inWindow(w){{
  if(weeks === "playoffs") return w >= 15 && w <= 17;
  if(weeks === "next4"){{
    const first = Math.min(...SOS.flatMap(x => x.sched.map(g => g.w)));
    return w >= first && w < first + 4;
  }}
  return true;
}}

function avg(games, key){{
  const v = games.map(g => g[key]).filter(x => x !== null && x !== undefined);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}}

// Which key a position uses under the selected scoring rule. A defense
// that gives up catches looks very different in PPR and standard, so the
// whole board has to move when the format does.
function pkey(p){{ return p === "wp" ? "wp" : p + "_" + fmt; }}

function shade(rank, total){{
  if(!rank) return "";
  if(rank <= total * 0.16) return "e1";
  if(rank <= total * 0.33) return "e2";
  if(rank > total * 0.84) return "h1";
  if(rank > total * 0.67) return "h2";
  return "";
}}

function rows(){{
  const out = [];
  for(const r of SOS){{
    const g = windowed(r);
    if(!g.length) continue;
    const row = {{team: r.team, games: g.length,
                 home: g.filter(x => x.h).length, wp: avg(g, "wp"),
                 byes: (r.byes || []).filter(w => inWindow(w)),
                 sched: g}};
    for(const p of ["QB","RB","WR","TE"]) row[p] = avg(g, pkey(p));
    out.push(row);
  }}
  return out;
}}

function rankAll(list){{
  // Every column ranked, every time, because the shading has to mean the
  // same thing whichever column you sorted by. A green cell is a soft
  // matchup for that position, not a consequence of the current sort.
  const ranks = {{}};
  const cols = [["wp", false], ["QB", true], ["RB", true],
                ["WR", true], ["TE", true]];
  for(const [key, easiestIsHigh] of cols){{
    const v = list.filter(r => r[key] !== null)
                  .sort((a, b) => easiestIsHigh ? b[key] - a[key]
                                                : a[key] - b[key]);
    ranks[key] = new Map(v.map((r, i) => [r.team, i + 1]));
  }}
  return ranks;
}}

function cell(v, digits){{
  return v === null || v === undefined ? "\u2014" : v.toFixed(digits);
}}

function draw(){{
  const list = rows();
  const ranks = rankAll(list);
  const n = list.length;

  // Sorted by whichever column is selected. Opponent record sorts ascending
  // -- a weak schedule is a low win percentage -- and every position sorts
  // descending, because a defense that gives up points is a good matchup.
  const key = pos === "RECORD" ? "wp" : pos;
  const asc = pos === "RECORD";
  const sorted = list.filter(r => r[key] !== null)
    .sort((a, b) => asc ? a[key] - b[key] : b[key] - a[key])
    .concat(list.filter(r => r[key] === null));

  document.getElementById("sstbody").innerHTML = sorted.map((r, i) => {{
    const c = k => shade(ranks[k].get(r.team), n);
    const em = k => k === key ? " ssel" : "";
    // A three-game four-week stretch is not a four-game one, so the bye is
    // on the row rather than hidden inside an average.
    const bye = r.byes && r.byes.length
      ? `<span class="ssbye">Bye ${{r.byes.join(", ")}}</span>` : "";
    const gm = weeks === "all" ? ""
      : `<span class="ssgm">${{r.games}}g</span>`;
    return `<tr class="tr" data-team="${{r.team}}">
      <td class="l ssrk c-rk">${{i + 1}}</td>
      <td class="l sstm c-nm">${{r.team}}${{gm}}${{bye}}</td>
      <td class="ssv ${{c("wp")}}${{em("wp")}}">${{cell(r.wp, 3)}}</td>
      <td class="ssv ${{c("QB")}}${{em("QB")}}">${{cell(r.QB, 1)}}</td>
      <td class="ssv ${{c("RB")}}${{em("RB")}}">${{cell(r.RB, 1)}}</td>
      <td class="ssv ${{c("WR")}}${{em("WR")}}">${{cell(r.WR, 1)}}</td>
      <td class="ssv ${{c("TE")}}${{em("TE")}}">${{cell(r.TE, 1)}}</td>
    </tr>`;
  }}).join("");

  document.querySelectorAll("tr.tr").forEach(tr =>
    tr.addEventListener("click", () => expand(tr, sorted.find(
      x => x.team === tr.dataset.team))));
}}

// The schedule behind the ranking.
//
// The table gives a team an average and a rank; this says which weeks
// produced it. A reader deciding whether to start somebody in week 3 is
// not helped by a season average, and "every team, week by week" should
// mean something.
const LABELS = [[0.16, "Very favourable", "e1"], [0.33, "Favourable", "e2"],
                [0.67, "Average", ""], [0.84, "Tough", "h2"],
                [1.01, "Very tough", "h1"]];

function difficulty(v, pos){{
  // Ranked against every other defense in the league on the same measure,
  // so "favourable" means favourable relative to what else is out there
  // rather than to an arbitrary number.
  const all = SOS.flatMap(r => r.sched.map(g => g[pkey(pos)]))
                 .filter(x => x !== null && x !== undefined)
                 .sort((a, b) => b - a);
  if(!all.length || v === null || v === undefined) return ["", ""];
  const idx = all.findIndex(x => x <= v);
  const pct = (idx < 0 ? all.length : idx) / all.length;
  for(const [lim, label, cls] of LABELS)
    if(pct <= lim) return [label, cls];
  return ["Average", ""];
}}

function expand(tr, r){{
  const next = tr.nextElementSibling;
  if(next && next.classList.contains("ssx")){{ next.remove(); return; }}
  document.querySelectorAll(".ssx").forEach(x => x.remove());
  if(!r) return;

  const isRec = pos === "RECORD";
  const cells = r.sched.map(g => {{
    const v = isRec ? g.wp : g[pkey(pos)];
    const [label, cls] = isRec
      ? [g.wp === null ? "" : (g.wp < 0.45 ? "Favourable"
          : g.wp > 0.55 ? "Tough" : "Average"),
         g.wp === null ? "" : (g.wp < 0.45 ? "e2" : g.wp > 0.55 ? "h2" : "")]
      : difficulty(v, pos);
    return `<div class="ssw">
      <span class="sswk">Wk ${{g.w}}</span>
      <b>${{g.h ? "" : "@"}}${{g.o}}</b>
      <span class="sswd ${{cls}}">${{label}}</span></div>`;
  }});
  (r.byes || []).forEach(w => cells.push(
    `<div class="ssw bye"><span class="sswk">Wk ${{w}}</span>
      <b>Bye</b><span class="sswd"></span></div>`));

  const row = document.createElement("tr");
  row.className = "ssx";
  const what = isRec ? "opponent record" : pos + ", " + LABEL[fmt];
  row.innerHTML = `<td colspan="7"><div class="ssxin">
    <p class="ssxh">${{r.team}} &middot; ${{what}}</p>
    <div class="ssxgrid">${{cells.join("")}}</div></div></td>`;
  tr.after(row);
}}

document.querySelectorAll("[data-s]").forEach(b =>
  b.addEventListener("click", () => {{
    fmt = b.dataset.s;
    document.querySelectorAll("[data-s]").forEach(x =>
      x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    draw();
  }}));

document.querySelectorAll("[data-w]").forEach(b =>
  b.addEventListener("click", () => {{
    weeks = b.dataset.w;
    document.querySelectorAll("[data-w]").forEach(x =>
      x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    draw();
  }}));
document.querySelectorAll("[data-p]").forEach(b =>
  b.addEventListener("click", () => {{
    pos = b.dataset.p;
    document.querySelectorAll("[data-p]").forEach(x =>
      x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    draw();
  }}));
draw();
</script>"""
    return body, css, header, footer, built


def add_to_sitemap(url):
    sm = SITE / "sitemap.xml"
    if not sm.exists():
        return False
    text = sm.read_text()
    if url in text:
        return False
    today = eastern_now().strftime("%Y-%m-%d")
    entry = (f"  <url><loc>{url}</loc><lastmod>{today}</lastmod>"
             f"<changefreq>weekly</changefreq><priority>0.8</priority></url>\n")
    sm.write_text(text.replace("</urlset>", entry + "</urlset>"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="site/data/sos.json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    src = ROOT / args.data
    if not src.exists():
        sys.exit(f"  no {args.data}. Run:\n"
                 f"    python3 scripts/import_schedule.py\n"
                 f"    python3 scripts/schedule_strength.py")
    data = json.loads(src.read_text())

    css, header, footer = site_chrome()
    body, css, header, footer, built = build_html(data, css, header, footer)

    season = data["season"]
    total = len(data["rows"])
    title = f"{season} NFL Fantasy Strength of Schedule | LineupBeat"
    desc = (f"Remaining fantasy strength of schedule for all {total} NFL "
            f"teams, by position and scoring format. Updates as games are "
            f"played.")

    schema = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": f"{season} NFL Strength of Schedule",
        "description": desc,
        "url": f"https://lineupbeat.com/{SPORT}/strength-of-schedule/",
        "dateModified": built.strftime("%Y-%m-%d"),
        "creator": {"@type": "Organization", "name": "LineupBeat"},
        **seo.dataset_extras(temporal=str(season)),
        "variableMeasured": ["Opponent win percentage",
                             "Fantasy points allowed per game by position"],
    }
    crumbs = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "LineupBeat",
             "item": "https://lineupbeat.com/"},
            {"@type": "ListItem", "position": 2, "name": "Fantasy data",
             "item": f"https://lineupbeat.com/{SPORT}/data/"},
            {"@type": "ListItem", "position": 3,
             "name": "Strength of schedule",
             "item": f"https://lineupbeat.com/{SPORT}/strength-of-schedule/"},
        ],
    }

    # One graph rather than loose blocks: these are facets of one page,
    # and saying so lets a crawler connect the dataset to the site that
    # publishes it and to the questions it answers.
    ldjson = seo.graph(
        {k: v for k, v in schema.items() if k != "@context"},
        {k: v for k, v in crumbs.items() if k != "@context"},
        seo.faq_schema(seo_faqs.SOS),
        seo.ORGANISATION,
        globals().get("_itemlist"))

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
      href="https://lineupbeat.com/{SPORT}/strength-of-schedule/">
{seo.social_meta(title, desc, f"{seo.SITE_URL}/{SPORT}/strength-of-schedule/")}
<script type="application/ld+json">{ldjson}</script>
<style>{css}{PAGE_CSS}{seo.CRUMB_CSS}{seo.SCROLLTABLE_CSS}{seo.RELATED_CSS}{seo.TEAMS_CSS}{seo.TEAMS_CSS}{seo.BYLINE_CSS}</style>
</head>
<body>
{header}
{body}
{footer}
{seo.TRACKING}
</body>
</html>"""

    out = (Path(args.out) if args.out
           else SITE / SPORT / "strength-of-schedule" / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(seo.check_page(page, str(out)))
    print(f"\n  wrote {out.relative_to(ROOT)}  ({len(page):,} bytes)")
    print(f"  {total} teams, "
          + (f"{data['weeks_played']} weeks played"
             if data["weeks_played"] else "season not started"))
    if add_to_sitemap(
            f"https://lineupbeat.com/{SPORT}/strength-of-schedule/"):
        print(f"  added to sitemap.xml")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
