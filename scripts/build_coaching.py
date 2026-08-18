#!/usr/bin/env python3
"""Build the offensive coaching page from data/coaching.csv.

    python3 scripts/build_coaching.py

WHAT THIS PUBLISHES AND WHAT IT DOES NOT

Public: head coach, coordinator, primary play caller, whether that caller is
new, the draft signal, the positions to target or treat with caution, and
the football reasoning.

Internal: the per-position scores and the multiplier they map to. A reader
does not need to be told that Justin Herbert received a 1.030 coefficient.
He needs to know that McDaniel's history creates additional upside, and to
see the projection that already accounts for it.

THE PLAY CALLER IS THE FIELD THAT MATTERS

Not the coordinator. Buffalo has Pete Carmichael Jr. as OC and Joe Brady
still calls the plays; Chicago has Press Taylor as OC and Ben Johnson still
calls them. A page built around the coordinator title would report a change
where there is none, and miss one where there is.

So "new caller" means a different primary caller from whoever finished 2025
calling that offense, which is seventeen teams -- not the number of teams
that hired a coordinator.

WHY THE MULTIPLIER IS NOT APPLIED HERE

The projections are built by hand, and the spec is explicit that the
adjustment is applied once. If the reasoning is already in the projected
touches and targets, multiplying again would double-count it. So this page
publishes the context and leaves the numbers alone; applying the multiplier
is a decision about a particular workbook, not something a page builder
should assume.
"""

from __future__ import annotations

import argparse
import csv
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

MULTIPLIER = {"+2": 1.030, "+1": 1.015, "0": 1.000,
              "-1": 0.985, "-2": 0.970}

SIGNAL_ORDER = ["Strong Target", "Target", "Selective Target",
                "No New Coaching Edge", "Caution"]

SIGNAL_MEANING = {
    "Strong Target": "Coaching creates meaningful additional upside. At "
                     "comparable ADP, actively favour these players.",
    "Target": "Coaching is a legitimate positive tiebreaker. At comparable "
              "ADP, favour these players over similarly projected "
              "alternatives.",
    "Selective Target": "The change looks favourable to specific positions "
                        "rather than the whole offense. Only the positions "
                        "listed.",
    "No New Coaching Edge": "No meaningful new 2026 coaching reason to move "
                            "players either way. It does not mean avoid the "
                            "team.",
    "Caution": "Coaching creates a modest additional downside. A tiebreaker "
               "between players at similar ADP, never an automatic fade.",
}

TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos",
    "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots",
    "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers", "SF": "San Francisco 49ers",
    "SEA": "Seattle Seahawks", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def slug(s):
    s = re.sub(r"[^\w\s-]", "", (s or "").lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")


PAGE_CSS = """
.topbar .logo,.topbar .vbtn{text-decoration:none}
.topbar .vbtn:hover{text-decoration:none; color:var(--ink)}
.vbtn[aria-current="page"]{color:#0A0C08; background:var(--signal);
  border-color:var(--signal)}

/* ---- coaching ----
   A card per team, because the useful unit is one offense: who calls it,
   whether that changed, and what it means. A table of scores would publish
   the internal numbers and bury the reasoning. */
.cowrap{max-width:1080px; margin:0 auto; padding:0 1rem 4rem}
.cohead{margin:1.6rem 0 .4rem}
.cohead h1{font-size:1.7rem; margin:0; letter-spacing:-.01em;
  font-family:var(--text)}
.cosub{color:var(--quiet); font-size:.86rem; margin:.4rem 0 0; max-width:70ch;
  line-height:1.55}
.codate{display:inline-block; margin-left:.5rem; font-family:var(--agate);
  text-transform:uppercase; letter-spacing:.06em; font-size:.7rem;
  color:var(--signal); border:1px solid var(--rule); border-radius:999px;
  padding:.1rem .5rem; vertical-align:.05em}
.cocards{display:grid; grid-template-columns:repeat(3, 1fr); gap:.7rem;
  margin:1.2rem 0 0}
@media (max-width:820px){ .cocards{grid-template-columns:1fr} }
.cocard{background:var(--card); border:1px solid var(--rule);
  border-radius:8px; padding:.75rem .9rem}
.cocard p{margin:.3rem 0 0; font-size:.84rem; line-height:1.45;
  color:var(--quiet)}
.cocard p b{color:var(--ink)}
.cok{font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em;
  font-size:.68rem; color:var(--signal)}

.coctl{display:flex; gap:.3rem; flex-wrap:wrap; margin:1.6rem 0 1rem}
.cotab{font-family:var(--agate); text-transform:uppercase;
  background:transparent; border:1px solid var(--rule); color:var(--quiet);
  font-size:.76rem; padding:.32rem .75rem; border-radius:999px;
  cursor:pointer; letter-spacing:.04em}
.cotab:hover{color:var(--ink); border-color:var(--ink)}
.cotab[aria-pressed="true"]{background:var(--signal); border-color:var(--signal);
  color:#0b0f0a; font-weight:600}

.cogrid{display:grid; grid-template-columns:repeat(2, 1fr); gap:.9rem}
@media (max-width:860px){ .cogrid{grid-template-columns:1fr} }
.team{background:var(--card); border:1px solid var(--rule); border-radius:10px;
  padding:1rem 1.1rem; border-left:3px solid var(--rule)}
.team.s-strong{border-left-color:#8BE04E}
.team.s-target{border-left-color:#B9DE7E}
.team.s-selective{border-left-color:var(--standing)}
.team.s-caution{border-left-color:#FF6B4A}
.tmhead{display:flex; align-items:center; gap:.55rem; flex-wrap:wrap}
/* The mark, so a reader finds his team by shape rather than by reading
   thirty-two names. Hidden on failure rather than showing a broken image:
   an alt box in a card header is worse than no logo at all. */
.tmlogo{width:1.6rem; height:1.6rem; object-fit:contain; flex:none}
.tmname{font-family:var(--agate); text-transform:uppercase; letter-spacing:.04em;
  font-size:1rem; font-weight:600; margin:0}
.tmsig{font-family:var(--agate); text-transform:uppercase; font-size:.64rem;
  letter-spacing:.07em; border:1px solid var(--rule); border-radius:999px;
  padding:.1rem .5rem; color:var(--quiet)}
.tmsig.s-strong,.tmsig.s-target{color:#8BE04E; border-color:#8BE04E}
.tmsig.s-selective{color:var(--standing); border-color:var(--standing)}
.tmnew{font-family:var(--agate); text-transform:uppercase; font-size:.62rem;
  letter-spacing:.07em; color:var(--signal)}
.tmstaff{margin:.6rem 0 0; font-size:.82rem; line-height:1.5; color:var(--quiet)}
.tmstaff b{color:var(--ink); font-weight:600}
.tmpos{display:flex; gap:.35rem; flex-wrap:wrap; margin:.6rem 0 0}
.pchip{font-family:var(--data); font-size:.66rem; letter-spacing:.04em;
  border:1px solid var(--rule); border-radius:4px; padding:.1rem .4rem;
  color:var(--quiet)}
.pchip.up{color:#8BE04E; border-color:#8BE04E}
.pchip.down{color:#FF6B4A; border-color:#FF6B4A}
.tmwhy{margin:.7rem 0 0; font-size:.84rem; line-height:1.55; color:var(--ink)}
.cocount{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.06em; font-size:.68rem; color:var(--quiet);
  margin:0 0 .8rem}

/* Touch targets on a phone.
   These pills are ~30px tall, which is fine for a cursor and small for a
   thumb -- the platform guidance is 44. Padding rather than height, so the
   text stays where it is and only the box a finger can hit grows. */
@media (max-width:760px){
  .cotab{min-height:44px; display:inline-flex; align-items:center;
    padding-top:.5rem; padding-bottom:.5rem}
}
.coempty{color:var(--quiet); padding:1.2rem 0; font-size:.86rem}
.comethod{margin:2.6rem 0 0; border-top:1px solid var(--rule);
  padding-top:1.4rem}
.coh2{font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em;
  font-size:.78rem; color:var(--quiet); margin:0 0 .7rem}
.colede{font-size:.92rem; line-height:1.6; color:var(--ink); max-width:74ch;
  margin:0 0 1.2rem}
.colede b{color:var(--signal); font-weight:600}
.cosig{margin:0; display:grid; gap:.7rem; max-width:74ch}
.cosig dt{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.06em; font-size:.7rem; color:var(--ink)}
.cosig dd{margin:.15rem 0 0; font-size:.84rem; line-height:1.5;
  color:var(--quiet)}
.cosig dd b{color:var(--ink)}
.cofoot{color:var(--quiet); font-size:.78rem; margin:1.4rem 0 0; max-width:74ch;
  line-height:1.55}
.cofoot b{color:var(--ink)}
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


def sig_class(signal):
    s = (signal or "").lower()
    if "strong" in s:
        return "s-strong"
    if "selective" in s:
        return "s-selective"
    if "caution" in s:
        return "s-caution"
    if "target" in s:
        return "s-target"
    return ""


def team_card(r):
    sc = sig_class(r["coaching_draft_signal"])
    name = TEAM_NAMES.get(r["team"], r["team"])
    new = (r["new_play_caller"] or "").strip().lower() == "yes"

    caller = r["primary_play_caller"]
    oc = r["offensive_coordinator"]
    hc = r["head_coach"]

    # Who calls it, said once and clearly. When the caller is also the head
    # coach or the coordinator, repeating the name three times reads as an
    # error rather than as a fact about the staff.
    lines = [f'<b>{esc(caller)}</b> calls the offense']
    if caller != hc:
        lines.append(f'{esc(hc)} is head coach')
    if oc and oc != caller:
        lines.append(f'{esc(oc)} is coordinator')
    staff = ", ".join(lines) + "."

    targets = [p.strip() for p in (r["positions_to_target"] or "").split(",")
               if p.strip() and "none" not in p.lower()]
    cautions = [p.strip() for p in (r["positions_to_caution"] or "").split(",")
                if p.strip() and "none" not in p.lower()]

    chips = "".join(f'<span class="pchip up">{esc(p)}</span>' for p in targets)
    chips += "".join(f'<span class="pchip down">{esc(p)} caution</span>'
                     for p in cautions)

    # Per-position scores on the element, so a filter cannot disagree with
    # the card. "Worth targeting" used to match any signal containing the
    # word, which put a Selective Target team in front of somebody looking
    # at every position -- Detroit is a TE signal and nothing else, and a
    # filter implying otherwise is the exact overreach the spec warns about.
    pos_data = " ".join(
        f'data-{x}="{esc((r[f"{x}_coaching_score"] or "0").strip())}"'
        for x in ("qb", "rb", "wr", "te"))

    return f'''<article class="team {sc}"
      data-signal="{esc(r["coaching_draft_signal"])}"
      data-new="{"yes" if new else "no"}"
      {pos_data}
      data-target="{esc(",".join(targets))}"
      data-caution="{esc(",".join(cautions))}">
  <div class="tmhead">
    <img class="tmlogo" loading="lazy" alt=""
         src="https://a.espncdn.com/i/teamlogos/nfl/500/{r["team"].lower()}.png"
         onerror="this.style.display='none'">
    <h2 class="tmname">{esc(name)}</h2>
    <span class="tmsig {sc}">{esc(r["coaching_draft_signal"])}</span>
    {'<span class="tmnew">New caller</span>' if new else ''}
  </div>
  <p class="tmstaff">{staff}</p>
  {f'<div class="tmpos">{chips}</div>' if chips else ''}
  <p class="tmwhy">{esc(r["fantasy_rationale"])}</p>
</article>'''


def build_html(rows, css, header, footer, verified):
    built = eastern_now()
    new_callers = [r for r in rows
                   if (r["new_play_caller"] or "").strip().lower() == "yes"]

    order = {s: i for i, s in enumerate(SIGNAL_ORDER)}
    rows = sorted(rows, key=lambda r: (order.get(r["coaching_draft_signal"], 9),
                                       TEAM_NAMES.get(r["team"], r["team"])))
    cards = "\n".join(team_card(r) for r in rows)

    body = f"""<main class="cowrap">
  <nav class="crumbs" aria-label="Breadcrumb">
    <a href="/">LineupBeat</a><span>/</span>
    <a href="/{SPORT}/data/">Fantasy data</a><span>/</span>
    <b>Coaching</b></nav>

  <div class="cohead">
    <h1>2026 Offensive Coaching</h1>
    <p class="cosub">Who actually calls each offense, whether that changed,
      and what it means when you are choosing between players at a similar
      price.
      <span class="codate">Reviewed {esc(verified)}</span></p>
  </div>

{seo.byline_html(built, method="Staff and play-caller research, reviewed by hand")}
  <div class="cocards">
    <div class="cocard">
      <span class="cok">The play caller</span>
      <p>Not the coordinator. Buffalo's coordinator is Pete Carmichael Jr.
         and <b>Joe Brady still calls the plays</b>, so nothing changed
         there.</p>
    </div>
    <div class="cocard">
      <span class="cok">{len(new_callers)} new callers</span>
      <p>Teams whose primary caller is different from whoever finished 2025
         calling that offense. <b>That is the change that matters.</b></p>
    </div>
    <div class="cocard">
      <span class="cok">A tiebreaker</span>
      <p>Role, talent, touches and targets matter far more.
         <b>Use this between players at comparable ADP</b>, not to reach.</p>
    </div>
  </div>

  <div class="coctl" role="group" aria-label="Filter">
    <button class="cotab" data-f="all" aria-pressed="true">All 32</button>
    <button class="cotab" data-f="new" aria-pressed="false">New caller</button>
    <button class="cotab" data-f="target" aria-pressed="false">Worth targeting</button>
    {''.join(f'<button class="cotab" data-p="{p}" aria-pressed="false">{p}</button>'
             for p in ("QB", "RB", "WR", "TE"))}
  </div>

  <p class="cocount" id="cocount">32 of 32</p>

  <div class="cogrid" id="cogrid">
{cards}
  </div>
  <p class="coempty" id="coempty" hidden>Nothing matches that filter.</p>

  <section class="comethod">
    <h2 class="coh2">How to use this</h2>

    <p class="colede">Coaching is a tiebreaker, not the foundation of a
      fantasy ranking. A <b>Target</b> or <b>Strong Target</b> designation
      means we prefer players from that offense at comparable projection
      and ADP. It does not mean reaching significantly ahead of market
      value.</p>

    <dl class="cosig">
      <div><dt>Strong Target</dt>
        <dd>Coaching creates meaningful additional upside. At comparable
            ADP, actively favour the positions listed.</dd></div>
      <div><dt>Target</dt>
        <dd>A legitimate positive tiebreaker over similarly projected
            alternatives.</dd></div>
      <div><dt>Selective Target</dt>
        <dd>Favourable to <b>specific positions only</b>, not the whole
            offense. Detroit is a tight end signal; it is not a reason to
            move a Lions receiver.</dd></div>
      <div><dt>No New Coaching Edge</dt>
        <dd>There is no new 2026 coaching change giving an additional
            reason to move these players either way. It is <b>not</b> a
            negative. Cincinnati, Kansas City, San Francisco and Minnesota
            all carry this label and all contain excellent picks, because
            their play-calling environment simply did not change.</dd></div>
      <div><dt>Caution</dt>
        <dd>A modest additional downside for the positions listed. Never an
            automatic fade, and only a tiebreaker between players at
            similar ADP.</dd></div>
    </dl>

    <p class="cofoot">
      The coaches and play callers are facts. The fantasy impact is an
      informed projection, and a small one: coaching moves a projection by
      at most three percent either way, and never overrides an obvious
      difference in role or talent. First-time callers are treated
      conservatively. Once real 2026 usage exists, touches, targets, routes
      and red-zone work matter more than any preseason read on a staff.
    </p>
    <p class="cofoot"><b>Last reviewed: {esc(verified)}.</b> Play-calling
      responsibilities can change during a season, including mid-year. If a
      team changes its primary caller, this page is updated and the review
      date moves with it.</p>
  </section>
{seo.faq_html(seo_faqs.COACHING)}{seo.related_html('coaching')}
</main>

<script>
let filter = "all", posFilter = null;

function apply(){{
  let shown = 0;
  document.querySelectorAll(".team").forEach(el => {{
    let ok = true;
    if(filter === "new") ok = el.dataset.new === "yes";
    if(filter === "target"){{
      // At least one position with a positive score. A Selective Target
      // team qualifies for the position it favors and not for the rest.
      ok = ["qb","rb","wr","te"].some(p =>
        parseInt(el.dataset[p] || "0", 10) > 0);
    }}
    if(ok && posFilter){{
      const p = posFilter.toLowerCase();
      ok = parseInt(el.dataset[p] || "0", 10) > 0;
    }}
    el.hidden = !ok;
    if(ok) shown++;
  }});
  document.getElementById("coempty").hidden = shown > 0;
  const n = document.getElementById("cocount");
  if(n) n.textContent = posFilter
    ? `${{shown}} team${{shown === 1 ? "" : "s"}} where coaching favors `
      + `${{posFilter}}`
    : `${{shown}} of 32`;
}}

document.querySelectorAll("[data-f]").forEach(b =>
  b.addEventListener("click", () => {{
    filter = b.dataset.f; posFilter = null;
    document.querySelectorAll("[data-f]").forEach(x =>
      x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    document.querySelectorAll("[data-p]").forEach(x =>
      x.setAttribute("aria-pressed", "false"));
    apply();
  }}));

document.querySelectorAll("[data-p]").forEach(b =>
  b.addEventListener("click", () => {{
    // A position filter answers "who should I target at this position",
    // which is a question about targets, so it implies that filter rather
    // than combining awkwardly with "all".
    posFilter = posFilter === b.dataset.p ? null : b.dataset.p;
    filter = posFilter ? "target" : "all";
    document.querySelectorAll("[data-p]").forEach(x =>
      x.setAttribute("aria-pressed",
                     x.dataset.p === posFilter ? "true" : "false"));
    document.querySelectorAll("[data-f]").forEach(x =>
      x.setAttribute("aria-pressed",
                     x.dataset.f === filter && !posFilter ? "true" : "false"));
    apply();
  }}));

apply();
</script>"""
    return body, built


def add_to_sitemap(url):
    sm = SITE / "sitemap.xml"
    if not sm.exists():
        return False
    text = sm.read_text()
    if url in text:
        return False
    today = eastern_now().strftime("%Y-%m-%d")
    entry = (f"  <url><loc>{url}</loc><lastmod>{today}</lastmod>"
             f"<changefreq>monthly</changefreq><priority>0.7</priority></url>\n")
    sm.write_text(text.replace("</urlset>", entry + "</urlset>"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/coaching.csv")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    src = ROOT / args.data
    if not src.exists():
        sys.exit(f"  no {args.data}")
    rows = list(csv.DictReader(src.open()))
    if not rows:
        sys.exit(f"  {args.data} is empty")

    # Say what is odd before publishing it.
    problems = []
    seen = set()
    for r in rows:
        t = r["team"]
        if t in seen:
            problems.append(f"{t} appears twice")
        seen.add(t)
        if t not in TEAM_NAMES:
            problems.append(f"{t} is not a team code")
        for p in ("qb", "rb", "wr", "te"):
            v = (r[f"{p}_coaching_score"] or "").strip()
            if v not in MULTIPLIER:
                problems.append(f"{t} {p.upper()} score {v!r} is not on the scale")
        if r["coaching_draft_signal"] not in SIGNAL_MEANING:
            problems.append(f"{t} signal {r['coaching_draft_signal']!r} "
                            f"is not one of the five")
    missing = sorted(set(TEAM_NAMES) - seen)
    if missing:
        problems.append(f"missing: {', '.join(missing)}")

    print(f"\n  {len(rows)} teams")
    if problems:
        print(f"\n  {len(problems)} thing(s) worth a look:")
        for p in problems[:8]:
            print(f"    {p}")
        print(f"  None of these stops the page being built.")

    verified = rows[0].get("last_verified_date", "")
    try:
        verified = datetime.strptime(verified, "%Y-%m-%d").strftime("%B %-d, %Y")
    except ValueError:
        verified = verified or "recently"

    css, header, footer = site_chrome()
    body, built = build_html(rows, css, header, footer, verified)

    newc = sum(1 for r in rows
               if (r["new_play_caller"] or "").strip().lower() == "yes")
    title = "2026 NFL Offensive Coaching and Fantasy Impact | LineupBeat"
    desc = (f"Who calls plays for all 32 NFL offenses in 2026, which "
            f"{newc} teams changed callers, and what it means for drafting "
            f"quarterbacks, backs, receivers and tight ends.")

    schema = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": "2026 NFL Offensive Coaching",
        "description": desc,
        "url": f"https://lineupbeat.com/{SPORT}/coaching/",
        "dateModified": built.strftime("%Y-%m-%d"),
        "creator": {"@type": "Organization", "name": "LineupBeat"},
        **seo.dataset_extras(temporal="2026"),
    }
    crumbs = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "LineupBeat",
             "item": "https://lineupbeat.com/"},
            {"@type": "ListItem", "position": 2, "name": "Fantasy data",
             "item": f"https://lineupbeat.com/{SPORT}/data/"},
            {"@type": "ListItem", "position": 3, "name": "Coaching",
             "item": f"https://lineupbeat.com/{SPORT}/coaching/"}]}

    # One graph rather than loose blocks: these are facets of one page,
    # and saying so lets a crawler connect the dataset to the site that
    # publishes it and to the questions it answers.
    ldjson = seo.graph(
        {k: v for k, v in schema.items() if k != "@context"},
        {k: v for k, v in crumbs.items() if k != "@context"},
        seo.faq_schema(seo_faqs.COACHING),
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
<link rel="canonical" href="https://lineupbeat.com/{SPORT}/coaching/">
{seo.social_meta(title, desc, f"https://lineupbeat.com/{SPORT}/coaching/")}
<script type="application/ld+json">{ldjson}</script>
<style>{css}{PAGE_CSS}{seo.CRUMB_CSS}{seo.RELATED_CSS}{seo.TEAMS_CSS}{seo.BYLINE_CSS}</style>
</head>
<body>
{header}
{body}
{footer}
{seo.TEAMS_JS}{seo.TRACKING}
</body>
</html>"""

    out = (Path(args.out) if args.out
           else SITE / SPORT / "coaching" / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(seo.check_page(page, str(out)))
    print(f"\n  wrote {out.relative_to(ROOT)}  ({len(page):,} bytes)")
    print(f"  {newc} new play callers")
    if add_to_sitemap(f"https://lineupbeat.com/{SPORT}/coaching/"):
        print(f"  added to sitemap.xml")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
