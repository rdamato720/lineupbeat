#!/usr/bin/env python3
"""Build /college-fantasy-football/projections/.

    python3 scripts/build_college_projections.py

Reads two files from the active release and nothing else. Projections and
fantasy points are not recomputed here: a second implementation of the
scoring rules is one nobody validates, and it will disagree with the
frozen one eventually.

The manifest SHA is checked before anything is written. A release that
does not match the pinned hash is not the release this page was reviewed
against, so the build stops rather than publishing it.
"""
import html, json, pathlib, sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = SITE / "college-fantasy-football" / "projections"
EXPECTED_SHA = "4c2f35fec2eaa3d43d2e18a2956d3118a20a17805ff1d4c74989a5cc069d6eb0"

import hashlib
cfg = json.loads((ROOT / "data/college/config.json").read_text())
REL = ROOT / "data/college" / cfg["activeCollegeProjectionVersion"]
man_bytes = (REL / "manifest.json").read_bytes()
sha = hashlib.sha256(man_bytes).hexdigest()
if sha != EXPECTED_SHA:
    sys.exit(f"  manifest SHA mismatch\n    found    {sha}\n"
             f"    expected {EXPECTED_SHA}\n"
             f"  refusing to publish a release this page was not reviewed "
             f"against.")
MAN = json.loads(man_bytes)
DATA = json.loads((REL / "college_site_projections_2026.json").read_text())
print(f"  release {cfg['activeCollegeProjectionVersion']}, SHA verified")

P = DATA["players"]
EXPECT = {"players": 2351, "teams": 68, "hybrids": 22}
got = {"players": len(P), "teams": len(DATA["teams"]),
       "hybrids": sum(1 for x in P if x.get("hybridRole"))}
if got != EXPECT:
    sys.exit(f"  publication totals differ: {got} against {EXPECT}")
print(f"  {got['players']} players, {got['teams']} teams, "
      f"{got['hybrids']} disclosed hybrids")

e = lambda s: html.escape(str(s), quote=True)

# Columns per position. Targets are deliberately absent: receptions are
# allocated directly by the frozen models and no target layer exists, so
# a Targets column could only be invented here.
# Points sits immediately after Team, not at the end.
#
# It was last, which on a phone put the number every one of these rankings
# is built on past the right edge of the screen: a reader had to swipe the
# full width of the stat line to reach the figure that explains the order
# he was already looking at. The NFL board carries the same order for the
# same reason.
#
# After it, what the position is paid for, then what explains it, then the
# accounting. The rushing columns stay on every position, including the
# receivers: the hybrid role is frequently the whole reason a tight end
# ranks where he does, and there is a badge in the name cell that reads as
# a bug without the carries beside it.
COLS = {
    "QB": [("rank", "Rank"), ("name", "Player"), ("team", "Team"),
           ("pts", "Points"),
           ("passYds", "Pass Yds"), ("passTd", "Pass TD"),
           ("rushAtt", "Carries"), ("rushYds", "Rush Yds"),
           ("rushTd", "Rush TD"),
           ("passAtt", "Pass Att"), ("comp", "Comp"), ("int", "INT")],
    "RB": [("rank", "Rank"), ("name", "Player"), ("team", "Team"),
           ("pts", "Points"),
           ("rushAtt", "Carries"), ("rushYds", "Rush Yds"),
           ("rushTd", "Rush TD"), ("rec", "Rec"), ("recYds", "Rec Yds"),
           ("recTd", "Rec TD")],
    "WR": [("rank", "Rank"), ("name", "Player"), ("team", "Team"),
           ("pts", "Points"),
           ("rec", "Rec"), ("recYds", "Rec Yds"), ("recTd", "Rec TD"),
           ("rushAtt", "Carries"), ("rushYds", "Rush Yds"),
           ("rushTd", "Rush TD")],
}
COLS["TE"] = COLS["WR"]
NUMERIC = {"rank", "passAtt", "comp", "passYds", "passTd", "int", "rushAtt",
           "rushYds", "rushTd", "rec", "recYds", "recTd", "pts"}
LABEL = {"QB": "Quarterbacks", "RB": "Running backs",
         "WR": "Wide receivers", "TE": "Tight ends"}
print("  columns set; no Targets column, none exists in the artifact")


import re
sys.path.insert(0, str(ROOT / "scripts"))
import seo
from college_team_logos import CSS as COLLEGE_LOGO_CSS, logo_html

POSITIONS = ["QB", "RB", "WR", "TE"]
BASE = "/college-fantasy-football/projections/"
DESCRIPTIONS = {
    None: ("2026 college fantasy football projections for 2,351 players "
           "across 68 teams. View Yahoo-scoring rankings and complete "
           "projected stat lines."),
    "QB": ("2026 college fantasy quarterback projections using Yahoo "
           "scoring. Compare passing, rushing and fantasy-point "
           "projections for 361 quarterbacks."),
    "RB": ("2026 college fantasy running back projections using Yahoo "
           "scoring. Compare carries, rushing production, receptions and "
           "fantasy points for 493 backs."),
    "WR": ("2026 college fantasy wide receiver projections using Yahoo "
           "scoring. Compare receptions, receiving production, rushing "
           "roles and fantasy points."),
    "TE": ("2026 college fantasy tight end projections using Yahoo "
           "scoring, including receptions, receiving production, hybrid "
           "rushing roles and fantasy points."),
}
INTROS = {
    "QB": ("Quarterback value here comes from both arms and legs: passing "
           "and rushing efficiency each carry calibrated player-history "
           "adjustments, and the rushing columns often separate two "
           "quarterbacks with similar passing lines."),
    "RB": ("Backfield share is projected room by room, splitting each "
           "team's carries between the backs who returned and those who "
           "arrived. A final room-concentration calibration moves the "
           "aggregate top-two carry share into the historical 79-81% band "
           "while preserving every team rushing budget. Rushing efficiency "
           "carries a calibrated adjustment; receptions come from the same "
           "team allocation."),
    "WR": ("Receivers are separated by projected opportunity rather than "
           "by receiving efficiency, which is anchored to team rates. "
           "Several receivers take enough carries that rushing decides "
           "part of their value, and those columns are shown."),
    "TE": ("Tight end value splits between receiving volume and, for a "
           "few players, real rushing work. One tight end's ranking rests "
           "mostly on his carries, which is why the rushing columns "
           "appear alongside the receiving line."),
}
TITLES = {
    None: "2026 College Fantasy Football Projections",
    "QB": "2026 College Fantasy Quarterback Projections",
    "RB": "2026 College Fantasy Running Back Projections",
    "WR": "2026 College Fantasy Wide Receiver Projections",
    "TE": "2026 College Fantasy Tight End Projections",
}


def chrome():
    """Site CSS and footer. The nav comes from seo.site_nav()."""
    src = (SITE / "template.html").read_text()
    css = re.search(r"<style>(.*?)</style>", src, re.S)
    return (css.group(1) if css else ""), seo.site_nav("projections", "college"), seo.site_footer()


def longdate(iso):
    """August 18, 2026, as the NFL pages write it."""
    from datetime import datetime
    return datetime.fromisoformat(iso[:10]).strftime("%B %-d, %Y")


def fmt(v, key):
    if key not in NUMERIC:
        return e(v)
    if isinstance(v, float) and key in ("passTd", "int", "rushTd", "recTd",
                                        "rec", "pts"):
        return f"{v:,.1f}"
    return f"{v:,.0f}" if isinstance(v, (int, float)) else e(v)


def table(pos, limit=None):
    cols = COLS[pos]
    sel = sorted((p for p in P if p["pos"] == pos), key=lambda x: x["rank"])
    if limit:
        sel = sel[:limit]
    head = "".join(
        f'<th data-k="{k}" class="{"num" if k in NUMERIC else ""}">{e(l)}</th>'
        for k, l in cols)
    body = []
    for p in sel:
        cells = []
        for k, _ in cols:
            v = p.get(k, 0)
            cls = "num" if k in NUMERIC else ""
            if k == "name":
                tag = ""
                if p.get("hybridRole"):
                    # Without this a tight end ranked first on seventeen
                    # catches reads as a bug rather than as a runner.
                    tag = (' <span class="hyb" title="Rushing production '
                           'materially affects this ranking">Hybrid rushing '
                           'role</span>')
                cells.append(f'<td class="{cls} pname">{e(v)}{tag}</td>')
            elif k == "team":
                cells.append(f'<td class="team"><span class="college-team-cell">'
                             f'{logo_html(p["teamId"], p["team"])}<span>{e(v)}</span></span></td>')
            else:
                cells.append(f'<td class="{cls}">{fmt(v, k)}</td>')
        body.append(f'<tr data-team="{e(p["team"])}" '
                    f'data-name="{e(p["name"].lower())}">'
                    + "".join(cells) + "</tr>")
    return (f'<table class="ctab" id="tab-{pos}"><thead><tr>{head}</tr>'
            f'</thead><tbody>{"".join(body)}</tbody></table>'), len(sel)


def tabs(active):
    out = []
    for p in [None] + POSITIONS:
        href = BASE if p is None else f"{BASE}{p.lower()}/"
        lab = "All" if p is None else p
        cur = ' aria-current="page"' if p == active else ""
        out.append(f'<a class="ctab-link" href="{href}"{cur}>{e(lab)}</a>')
    return '<nav class="ctabs">' + "".join(out) + "</nav>"


print("  chrome, nav and table renderer ready")


CSS = COLLEGE_LOGO_CSS + """
.cwrap{max-width:var(--content-wide-table);margin:0 auto;padding:1.2rem 1rem 3rem}
.chero h1{font-size:clamp(2.4rem,5vw,4.3rem);line-height:1.02;margin:0 0 .7rem;letter-spacing:-.035em}
.chero p.lede{color:var(--quiet);font-size:.95rem;line-height:1.55;
  max-width:52rem;margin:0 0 .3rem}
.cmeta{font-family:var(--agate);text-transform:uppercase;letter-spacing:.08em;
  font-size:.7rem;color:var(--quiet);margin:.6rem 0 0}
.ctabs{display:flex;gap:.4rem;flex-wrap:wrap;margin:1.1rem 0 .7rem}
.ctab-link{font-family:var(--agate);text-transform:uppercase;
  letter-spacing:.1em;font-size:.74rem;font-weight:600;padding:.4rem .85rem;
  border:1px solid var(--rule);border-radius:999px;color:var(--quiet);
  text-decoration:none}
.ctab-link:hover{color:var(--ink);border-color:var(--signal)}
.ctab-link[aria-current=page]{color:#0A0C08;background:var(--signal);
  border-color:var(--signal)}
.cctl{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;
  margin:0 0 .7rem}
.cctl input,.cctl select{background:var(--card);border:1px solid var(--rule);
  color:var(--ink);border-radius:8px;padding:.45rem .7rem;font-size:.9rem}
.cctl input{min-width:14rem}
.cnote{background:var(--card);border:1px solid var(--rule);border-radius:10px;
  padding:.7rem .85rem;font-size:.8rem;line-height:1.5;color:var(--quiet);
  margin:0 0 1rem}
.cnote strong{color:var(--ink)}
.ctabwrap{overflow-x:auto;-webkit-overflow-scrolling:touch;
  overscroll-behavior-x:contain;
  border:1px solid var(--rule);border-radius:10px}
.ctabwrap:focus-visible{outline:2px solid var(--signal);outline-offset:2px}
/* border-collapse:separate is required, not a preference. With collapse
   the browser owns the borders and drops them from a sticky cell, so the
   pinned rank and player columns lost their rules and floated over the
   rows they belonged to. The lines are inset shadows on the cells now,
   which travel with them. */
table.ctab{width:100%;border-collapse:separate;border-spacing:0;
  font-size:.86rem}
table.ctab th,table.ctab td{padding:.5rem .6rem;text-align:left;
  box-shadow:inset 0 -1px 0 var(--rule);white-space:nowrap}
table.ctab th{font-family:var(--agate);text-transform:uppercase;
  letter-spacing:.07em;font-size:.68rem;color:var(--quiet);
  background:var(--card);position:sticky;top:0;cursor:pointer;user-select:none}
table.ctab th:hover{color:var(--ink)}
table.ctab td.num,table.ctab th.num{text-align:right}
table.ctab tbody tr:hover{background:rgba(255,255,255,.03)}
/* Rank and player stay put while the stat line scrolls: on a phone the
   columns run past the edge and a row of numbers with no name attached
   is unreadable. */
table.ctab th:nth-child(1),table.ctab td:nth-child(1){position:sticky;left:0;
  background:var(--card);z-index:2}
table.ctab th:nth-child(2),table.ctab td:nth-child(2){position:sticky;
  left:3.2rem;background:var(--card);z-index:2;
  box-shadow:inset 0 -1px 0 var(--rule),inset -1px 0 0 var(--rule)}
table.ctab thead th:nth-child(1),table.ctab thead th:nth-child(2){z-index:3}
/* Points is the number the ranking is built on; the stat line beside it
   is deliberately quieter. */
table.ctab td:nth-child(4){color:var(--signal);font-weight:600}
/* 13px floor. Below that a stat line is unreadable at arm's length, and
   shrinking the type to fit more columns is the mistake the sideways
   scroll exists to avoid. */
@media(max-width:900px){table.ctab{font-size:.85rem}}
.hyb{font-family:var(--agate);text-transform:uppercase;letter-spacing:.06em;
  font-size:.6rem;color:var(--signal);border:1px solid var(--signal);
  border-radius:999px;padding:.05rem .4rem;margin-left:.4rem}
.cmore{display:inline-block;margin:.7rem 0 0;font-family:var(--agate);
  text-transform:uppercase;letter-spacing:.08em;font-size:.72rem;
  color:var(--signal);text-decoration:none}
.cmore:hover{text-decoration:underline}
.cposh{font-size:1.5rem;margin:1.6rem 0 .6rem}
.ccount{font-family:var(--agate);text-transform:uppercase;font-size:.62rem;
  letter-spacing:.08em;color:var(--quiet);margin-left:.5rem}
.fcollege h3{font-family:var(--agate);text-transform:uppercase;
  letter-spacing:.08em;font-size:.72rem;margin:0 0 .3rem}
.fcollege p{font-size:.78rem;line-height:1.5;color:var(--quiet);margin:0}
.cintro{color:var(--quiet);font-size:.88rem;line-height:1.6;
  max-width:52rem;margin:.6rem 0 0}
.cweekly{display:inline-flex;margin:.8rem 0 0;padding:.5rem .85rem;
  border-radius:999px;background:var(--signal);color:#081006;
  font-family:var(--agate);font-size:.72rem;font-weight:700;
  text-transform:uppercase;letter-spacing:.07em;text-decoration:none}
.cfaq{margin:var(--gap-section) 0 0}
.cfaq h2{font-size:1.15rem;margin:0 0 .7rem}
.cfaq details{border-bottom:1px solid var(--rule);padding:.6rem 0}
.cfaq summary{cursor:pointer;font-weight:600;font-size:.92rem}
.cfaq p{color:var(--quiet);font-size:.86rem;line-height:1.6;margin:.5rem 0 0}
@media(max-width:640px){.chero h1{font-size:2rem}
  table.ctab th:nth-child(2),table.ctab td:nth-child(2){left:2.8rem}}
"""

FAQ = [
    ("What scoring do these projections use?",
     "Yahoo scoring rules. Positions are a separate matter: they come from "
     "school roster listings and have not been checked against Yahoo or any "
     "other platform's eligibility."),
    ("How are the projections built?",
     DATA["methodology"]),
    ("Why do some receivers and tight ends show carries?",
     "Twenty-two players take enough carries that rushing materially "
     "decides their fantasy value. One tight end's ranking rests mostly on "
     "his rushing. Their carries and rushing yards are shown so the "
     "ranking explains itself rather than looking like an error."),
    ("Why are there no target projections?",
     "Receptions are allocated directly by the underlying models. No target "
     "layer has been built, so targets would have to be invented here, and "
     "an invented column would weaken numbers that are otherwise exact."),
    ("How accurate are the team totals?",
     "Player projections aggregate exactly to the corresponding frozen "
     "team totals, to twelve decimal places, across fifteen "
     "reconciliation checks."),
]


def _page(pos):
    css, header, footer = chrome()
    title = TITLES[pos]
    url = BASE if pos is None else f"{BASE}{pos.lower()}/"
    shown = POSITIONS if pos is None else [pos]
    tables, counts = [], 0
    for p in shown:
        # The overview shows the top of each room; the position pages
        # carry everyone. Search on this page follows suit and sends you
        # to the position page, which is where the rest of the players are.
        limit = 25 if pos is None else None
        t, n = table(p, limit)
        counts += n
        more = (f'<a class="cmore" href="{BASE}{p.lower()}/">View all '
                f'{LABEL[p].lower()} projections &rarr;</a>'
                if pos is None else "")
        tables.append(f'<section class="cpos" data-pos="{p}">'
                      + (f'<h2 class="cposh">{LABEL[p]}'
                         f'<span class="ccount">top 25</span></h2>'
                         if pos is None else "")
                      + seo.scroll_hint("the full stat line")
                      + f'<div class="ctabwrap" tabindex="0" role="region" '
                        f'aria-label="{e(LABEL[p])} projections">{t}</div>'
                        f'{more}</section>')
    teams = sorted({p["team"] for p in P})
    desc = DESCRIPTIONS[pos]
    canon = f"https://lineupbeat.com{url}"
    trail = ([("LineupBeat", "/"), ("College Fantasy Football", None),
              ("Projections", BASE)] if pos is None else
             [("LineupBeat", "/"),
              ("College Fantasy Football Projections",
               BASE),
              (LABEL[pos], url)])
    crumbs = ('<nav class="crumbs" aria-label="Breadcrumb">'
              + ' <span aria-hidden="true">/</span> '.join(
                  (f'<a href="{h}">{e(t)}</a>' if h and t != TITLES[pos]
                   else f'<span>{e(t)}</span>') for t, h in trail)
              + '</nav>')
    dataset = ""
    if pos is None:
        # Described as what it is: projections built by LineupBeat that use
        # Yahoo scoring rules. Not Yahoo data, and not platform eligibility.
        dataset = json.dumps({
            "@context": "https://schema.org", "@type": "Dataset",
            "name": "2026 College Fantasy Football Projections",
            "description": DESCRIPTIONS[None],
            "url": canon,
            "dateModified": DATA["generatedAt"][:10],
            "temporalCoverage": "2026",
            "creator": {"@type": "Organization", "name": "LineupBeat"},
            "isAccessibleForFree": True,
            "creditText": "LineupBeat",
            "inLanguage": "en-US",
            "spatialCoverage": "United States",
            "measurementTechnique": DATA["methodology"],
            "variableMeasured": [
                {"@type": "PropertyValue", "name": n_, "description": d_}
                for n_, d_ in (
                    ("Fantasy points",
                     "Full-season projection under Yahoo scoring rules"),
                    ("Pass attempts, completions, yards, touchdowns, "
                     "interceptions",
                     "Quarterback passing, with calibrated player-history "
                     "efficiency"),
                    ("Carries, rushing yards, rushing touchdowns",
                     "Rushing for all positions; quarterback and running "
                     "back efficiency carry calibrated adjustments"),
                    ("Receptions, receiving yards, receiving touchdowns",
                     "Receiving, differentiated by projected opportunity "
                     "with efficiency anchored to team rates"),
                    ("Position",
                     "School roster listing sourced through CFBD, which may "
                     "differ from fantasy-platform eligibility"))],
            "size": f"{len(P):,} players across {len(DATA['teams'])} teams",
        }, separators=(",", ":"))
    crumb_schema = seo.breadcrumbs(
        [(t, h) for t, h in trail if h] or [("LineupBeat", "/")])
    faq = "".join(
        f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>"
        for q, a in FAQ)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} | LineupBeat</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{canon}">
{seo.social_meta(title + " | LineupBeat", desc, canon)}
<script type="application/ld+json">{crumb_schema}</script>\n{f'<script type="application/ld+json">{dataset}</script>' if dataset else ''}
<style>{css}{seo.CRUMB_CSS}{seo.UI_CSS}{seo.SCROLLTABLE_CSS}{CSS}</style>
</head><body>
{header}
<main class="cwrap">
  {crumbs}
  <div class="chero">
    <h1>{e(title)}</h1>
    <p class="lede">Full-season projections for {len(P):,} players across
      {len(DATA['teams'])} teams, using Yahoo scoring. Every player's
      projected stat line is shown behind the ranking.</p>
    {f'<p class="cintro">{e(INTROS[pos])}</p>' if pos else ''}
    <p class="cmeta">Updated {longdate(DATA['generatedAt'])} &middot;
      Model {DATA['modelVersion']}</p>
    <a class="cweekly" href="/college-fantasy-football/week-1/">View Week 1 projections &amp; rankings &rarr;</a>
  </div>
  {tabs(pos)}
  <div class="cctl">
    <input id="csearch" type="search" placeholder="Search players"
           aria-label="Search players">
    <select id="cteam" aria-label="Filter by team">
      <option value="">All teams</option>
      {"".join(f'<option>{e(t)}</option>' for t in teams)}
    </select>
  </div>
  <p class="cnote"><strong>Positions</strong> reflect school roster listings
    sourced through CFBD and may differ from eligibility on Yahoo or other
    fantasy platforms. Fantasy points use Yahoo scoring rules.</p>
  {"".join(tables)}
  <section class="cfaq"><h2>About these projections</h2>{faq}</section>
</main>
{footer}
<script>
(function(){{
  var s=document.getElementById('csearch'),t=document.getElementById('cteam');
  function apply(){{
    var q=(s.value||'').toLowerCase(),tm=t.value;
    document.querySelectorAll('table.ctab tbody tr').forEach(function(r){{
      var ok=(!q||r.dataset.name.indexOf(q)>-1)&&(!tm||r.dataset.team===tm);
      r.style.display=ok?'':'none';
    }});
  }}
  s.addEventListener('input',apply); t.addEventListener('change',apply);
  document.querySelectorAll('table.ctab th').forEach(function(th){{
    th.addEventListener('click',function(){{
      var tb=th.closest('table'),i=[].indexOf.call(th.parentNode.children,th),
          num=th.classList.contains('num'),
          asc=tb.dataset.sc==i&&tb.dataset.sd!='asc';
      var rows=[].slice.call(tb.tBodies[0].rows);
      rows.sort(function(a,b){{
        var x=a.cells[i].textContent.replace(/[,]/g,''),
            y=b.cells[i].textContent.replace(/[,]/g,'');
        if(num){{x=parseFloat(x)||0;y=parseFloat(y)||0;}}
        return (x<y?-1:x>y?1:0)*(asc?1:-1);
      }});
      rows.forEach(function(r){{tb.tBodies[0].appendChild(r);}});
      tb.dataset.sc=i; tb.dataset.sd=asc?'asc':'desc';
    }});
  }});
}})();
</script>
</body></html>"""


def page(pos):
    """Search the full player pool while keeping the overview concise."""
    document = _page(pos)
    index = ([{"name": player["name"], "team": player["team"],
               "pos": player["pos"]} for player in P]
             if pos is None else [])
    payload = json.dumps(index, separators=(",", ":")).replace("</", "<\\/")
    if pos is None:
        options = "".join(
            f'<option value="{e(player["name"])}" '
            f'label="{e(player["team"])} · {e(player["pos"])}"></option>'
            for player in P)
        document = document.replace(
            '<input id="csearch" type="search" placeholder="Search players"\n'
            '           aria-label="Search players">',
            '<input id="csearch" type="search" list="college-season-player-list" '
            'placeholder="Search all 2,351 players" aria-label="Search all players">'
            f'<datalist id="college-season-player-list">{options}</datalist>', 1)
    script = f'''<script>(()=>{{
const overview={str(pos is None).lower()},index={payload},input=document.getElementById('csearch');
const norm=value=>String(value||'').trim().toLowerCase();
function match(){{const query=norm(input.value);if(!query)return null;return index.find(p=>norm(p.name)===query)||index.find(p=>norm(p.name).startsWith(query))||index.find(p=>norm(p.name).includes(query));}}
function openMatch(){{if(!overview)return;const hit=match();if(hit)location.href='/college-fantasy-football/projections/'+hit.pos.toLowerCase()+'/?q='+encodeURIComponent(hit.name);}}
input.addEventListener('change',()=>{{const hit=match();if(hit&&norm(hit.name)===norm(input.value))openMatch();}});
input.addEventListener('keydown',event=>{{if(event.key==='Enter'&&overview){{event.preventDefault();openMatch();}}}});
const query=new URLSearchParams(location.search).get('q');if(query&&!overview){{input.value=query;input.dispatchEvent(new Event('input'));}}
}})()</script>'''
    return document.replace('</body>', script + '</body>', 1)


written = []
for pos in [None] + POSITIONS:
    d = OUT if pos is None else OUT / pos.lower()
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(page(pos))
    written.append((str((d / "index.html").relative_to(SITE)),
                    (d / "index.html").stat().st_size))

# The sitemap is written from scratch by build_pages.py, which runs
# last. Appending here would be silently discarded, so the college
# URLs are declared there instead.

print(f"\n  PAGES\n")
for path, size in written:
    print(f"    {size:>9,}  /{path.rsplit('/index.html')[0]}/")
print(f"\n  release {MAN['version']}, QA {MAN['qa_status']}, "
      f"{MAN['reconciliation_gates']} gates")

# The scheduled workflow already calls this reviewed college entry point.
# Build the separately versioned Week 1 release without recomputing it here.
import build_college_week1  # noqa: E402,F401
