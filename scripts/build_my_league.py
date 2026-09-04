#!/usr/bin/env python3
"""Build the public, indexable My League product landing page."""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import seo


OUT = ROOT / "site/my-league/index.html"


def append_sitemap() -> None:
    path = ROOT / "site/sitemap.xml"
    if not path.exists():
        return
    text = path.read_text()
    if "/my-league/" in text:
        return
    origin_match = re.search(r"<loc>(https://[^/<]+)", text)
    origin = origin_match.group(1) if origin_match else "https://lineupbeat.com"
    url = html.escape(origin + "/my-league/")
    path.write_text(text.replace("</urlset>", f"<url><loc>{url}</loc></url></urlset>"))


def structured_data() -> str:
    faq = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": "What is LineupBeat My League?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": "My League turns an ESPN, Yahoo, or CBS fantasy football league archive into all-time standings, trophy history, records, manager pages, season summaries, and a shareable view-only page.",
                },
            },
            {
                "@type": "Question",
                "name": "Which fantasy football platforms can connect?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": "ESPN and Yahoo can collect connected seasons automatically. CBS history is added one visible season at a time from its History area with the browser connector.",
                },
            },
            {
                "@type": "Question",
                "name": "Is my fantasy league history private?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": "Yes. The imported archive stays in the browser by default. A commissioner can explicitly create an unlisted or public view-only link when the league is ready to share.",
                },
            },
        ],
    }
    return json.dumps(faq, separators=(",", ":"), ensure_ascii=False)


def build_page() -> str:
    styles = r"""
    :root{--ml-bg:#080c0b;--ml-panel:#101613;--ml-panel2:#0c110f;--ml-line:#29332e;--ml-muted:#aeb7b0}
    body{margin:0;background:radial-gradient(circle at 50% 11rem,rgba(35,51,43,.54),transparent 35rem),var(--ml-bg);color:var(--ink);font-family:var(--text)}
    body::before{content:"";position:absolute;z-index:-1;inset:3.8rem 0 auto;height:46rem;background-image:linear-gradient(rgba(255,255,255,.022) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.022) 1px,transparent 1px);background-size:72px 72px;mask-image:linear-gradient(to bottom,#000,transparent 92%);pointer-events:none}
    .ml-shell{max-width:74rem;margin:0 auto;padding:clamp(1.6rem,4vw,4rem) 1.25rem 5rem}
    .ml-hero{position:relative;display:grid;grid-template-columns:minmax(0,1.3fr) minmax(18rem,.7fr);gap:clamp(2rem,6vw,6rem);align-items:end;padding:clamp(2rem,5vw,5.5rem) 0 3rem;border-bottom:1px solid var(--ml-line)}
    .ml-kicker,.ml-eyebrow{display:block;margin:0 0 .8rem;color:var(--signal);font:800 .78rem/1.2 var(--agate);letter-spacing:.13em;text-transform:uppercase}
    .ml-hero h1{max-width:12ch;margin:0;font:700 clamp(3.2rem,8vw,6.9rem)/.88 var(--text);letter-spacing:-.055em}
    .ml-hero p{max-width:38rem;margin:1.4rem 0 0;color:#c4cac5;font:400 1.08rem/1.65 var(--agate)}
    .ml-actions{display:flex;flex-wrap:wrap;gap:.75rem;margin-top:1.8rem}
    .ml-button{display:inline-flex;align-items:center;justify-content:center;min-height:3.25rem;padding:.8rem 1.2rem;border:1px solid var(--signal);background:var(--signal);color:#071008;text-decoration:none;font:800 .82rem/1 var(--agate);letter-spacing:.05em;text-transform:uppercase}
    .ml-button.secondary{border-color:#52605a;background:transparent;color:var(--ink)}
    .ml-proof{display:grid;gap:.7rem;margin:0}
    .ml-proof div{display:grid;grid-template-columns:4.8rem 1fr;gap:1rem;align-items:center;padding:1rem;border:1px solid var(--ml-line);background:#0b100ed9}
    .ml-proof dt{color:var(--signal);font:800 1.35rem/1 var(--data)}.ml-proof dd{margin:0;color:var(--ml-muted);font:700 .78rem/1.35 var(--agate);letter-spacing:.04em;text-transform:uppercase}
    .ml-section{padding:clamp(3rem,7vw,5.5rem) 0;border-bottom:1px solid var(--ml-line)}
    .ml-section-head{display:grid;grid-template-columns:minmax(0,.75fr) minmax(18rem,1.25fr);gap:2rem;align-items:end;margin-bottom:1.6rem}
    .ml-section h2{max-width:14ch;margin:0;font:700 clamp(2.1rem,5vw,4rem)/.96 var(--text);letter-spacing:-.04em}.ml-section-head>p{max-width:42rem;margin:0;color:var(--ml-muted);font:400 1rem/1.6 var(--agate)}
    .ml-feature-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:.75rem}
    .ml-card{min-width:0;padding:1.25rem;border:1px solid var(--ml-line);border-top:3px solid var(--signal);background:linear-gradient(145deg,var(--ml-panel),var(--ml-panel2))}
    .ml-card span{color:#67716b;font:800 1.8rem/1 var(--data)}.ml-card h3{margin:2rem 0 .4rem;font:700 1.25rem/1.1 var(--agate)}.ml-card p{margin:0;color:var(--ml-muted);font:400 .92rem/1.5 var(--agate)}
    .ml-steps{counter-reset:steps;display:grid;border-top:1px solid var(--ml-line)}
    .ml-step{counter-increment:steps;display:grid;grid-template-columns:3.5rem minmax(10rem,.55fr) minmax(0,1.45fr);gap:1.25rem;align-items:center;padding:1.2rem .25rem;border-bottom:1px solid var(--ml-line)}
    .ml-step::before{content:"0" counter(steps);color:var(--signal);font:800 1rem/1 var(--data)}.ml-step h3{margin:0;font:700 1.08rem/1.2 var(--agate)}.ml-step p{margin:0;color:var(--ml-muted);font:400 .92rem/1.5 var(--agate)}
    .ml-platforms{display:grid;grid-template-columns:1.2fr 1fr 1fr;gap:.75rem}.ml-platform{padding:1.25rem;border:1px solid var(--ml-line);background:var(--ml-panel2)}.ml-platform.live{border-top:3px solid var(--signal)}
    .ml-platform small{color:var(--signal);font:800 .72rem/1 var(--agate);letter-spacing:.1em;text-transform:uppercase}.ml-platform h3{margin:1.4rem 0 .35rem;font:700 1.5rem/1 var(--agate)}.ml-platform p{margin:0;color:var(--ml-muted);font:400 .92rem/1.5 var(--agate)}
    .ml-privacy{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2rem;align-items:center;padding:clamp(1.5rem,4vw,2.5rem);border:1px solid #45534c;background:radial-gradient(circle at 100% 0,#c6f53c15,transparent 35%),var(--ml-panel)}.ml-privacy h2{max-width:18ch}.ml-privacy p{max-width:45rem;margin:.8rem 0 0;color:var(--ml-muted);font:400 1rem/1.6 var(--agate)}
    .ml-faq{display:grid;grid-template-columns:.6fr 1.4fr;gap:2rem}.ml-faq-list{border-top:1px solid var(--ml-line)}.ml-faq details{padding:1rem 0;border-bottom:1px solid var(--ml-line)}.ml-faq summary{cursor:pointer;color:var(--ink);font:700 1rem/1.3 var(--agate)}.ml-faq details p{max-width:46rem;margin:.7rem 0 0;color:var(--ml-muted);font:400 .92rem/1.55 var(--agate)}
    @media(max-width:900px){.ml-hero,.ml-section-head,.ml-faq{grid-template-columns:1fr}.ml-proof{grid-template-columns:repeat(3,1fr)}.ml-proof div{display:block}.ml-proof dt{margin-bottom:.45rem}.ml-feature-grid{grid-template-columns:1fr 1fr}.ml-platforms{grid-template-columns:1fr}.ml-privacy{grid-template-columns:1fr}.ml-step{grid-template-columns:2.5rem 1fr}.ml-step p{grid-column:2}}
    @media(max-width:600px){.ml-shell{padding-inline:1rem}.ml-hero{padding-top:2.5rem}.ml-hero h1{font-size:clamp(3rem,16vw,4.6rem)}.ml-proof{grid-template-columns:1fr}.ml-feature-grid{grid-template-columns:1fr}.ml-card h3{margin-top:1.2rem}.ml-actions,.ml-button{width:100%}.ml-step{gap:.75rem}.ml-section{padding:3.25rem 0}}
    """
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Fantasy Football League History &amp; Record Book | LineupBeat</title>
    <meta name="description" content="Turn your ESPN, Yahoo, or CBS fantasy football league history into all-time standings, records, trophies, manager pages, and one shareable league record book.">
    <meta name="robots" content="index,follow"><link rel="canonical" href="https://lineupbeat.com/my-league/">
    <script type="application/ld+json">{structured_data()}</script>
    <style>{seo.SHELL_CSS}{seo.TEAMS_CSS}{seo.NAV_CSS}{styles}</style></head><body>
    {seo.site_nav('league_history', 'nfl')}
    <main class="ml-shell">
      <section class="ml-hero"><div><span class="ml-kicker">My League</span><h1>Your league has a history. Keep all of it.</h1><p>Turn ESPN, Yahoo, or CBS fantasy football seasons into a living league record book—complete with all-time standings, trophies, rivalries, records, and every team name your league has used.</p><div class="ml-actions"><a class="ml-button" href="/league-history/">Connect your league</a><a class="ml-button secondary" href="#how-it-works">See how it works</a></div></div>
      <dl class="ml-proof"><div><dt>Every</dt><dd>available season</dd></div><div><dt>All</dt><dd>historical team names</dd></div><div><dt>One</dt><dd>permanent share link</dd></div></dl></section>

      <section class="ml-section"><div class="ml-section-head"><div><span class="ml-eyebrow">Your record book</span><h2>More than a list of champions.</h2></div><p>My League rebuilds the story your fantasy platform leaves scattered across individual seasons. Manager identities follow the person, while team names stay attached to the seasons where they were used.</p></div><div class="ml-feature-grid">
        <article class="ml-card"><span>01</span><h3>All-time standings</h3><p>Career records, win percentage, scoring, and titles across every season.</p></article>
        <article class="ml-card"><span>02</span><h3>Trophy case</h3><p>Championships, scoring crowns, runner-up finishes, and season winners.</p></article>
        <article class="ml-card"><span>03</span><h3>League records</h3><p>Best weeks, biggest blowouts, closest games, streaks, and head-to-head results.</p></article>
        <article class="ml-card"><span>04</span><h3>Manager pages</h3><p>Every season, team name, matchup, high, low, and rivalry in one career view.</p></article>
      </div></section>

      <section class="ml-section" id="how-it-works"><div class="ml-section-head"><div><span class="ml-eyebrow">How to connect</span><h2>One archive. One quick review.</h2></div><p>Choose ESPN, Yahoo, or CBS. LineupBeat keeps the private record book on your device until you decide to share it.</p></div><div class="ml-steps">
        <article class="ml-step"><h3>Choose your platform</h3><p>Use the browser connector for ESPN or CBS, or authorize read-only access to Yahoo Fantasy Football.</p></article>
        <article class="ml-step"><h3>Select your league</h3><p>LineupBeat finds the connected seasons and imports the complete matchup history.</p></article>
        <article class="ml-step"><h3>Match manager names</h3><p>Confirm only possible duplicates, such as nicknames or changed platform accounts.</p></article>
        <article class="ml-step"><h3>Review the record book</h3><p>Every historical team remains in its original season, including teams that left the league.</p></article>
        <article class="ml-step"><h3>Share when ready</h3><p>Create one permanent, view-only link for league mates. Future imports update that same page.</p></article>
      </div><div class="ml-actions"><a class="ml-button" href="/league-history/">Connect a league</a><a class="ml-button secondary" href="/my-team/extension/">Browser connector</a></div></section>

      <section class="ml-section"><div class="ml-section-head"><div><span class="ml-eyebrow">Platform support</span><h2>What connects today.</h2></div><p>League-history support and roster support are different features. This page makes that boundary explicit.</p></div><div class="ml-platforms">
        <article class="ml-platform live"><small>League history + My Team</small><h3>ESPN</h3><p>Import up to 25 available seasons and capture the current roster locally.</p></article>
        <article class="ml-platform live"><small>League history + My Team</small><h3>Yahoo</h3><p>Authorize your account to import connected seasons, plus capture a current roster locally.</p></article>
        <article class="ml-platform live"><small>League history + My Team</small><h3>CBS</h3><p>Capture the current roster and add visible CBS history seasons to one local archive.</p></article>
      </div></section>

      <section class="ml-section"><div class="ml-privacy"><div><span class="ml-eyebrow">Private by default</span><h2>Your league is not published unless you publish it.</h2><p>The private archive stays in your browser. LineupBeat never receives your provider password. Yahoo access stays in an encrypted, secure browser cookie and is not stored in the league-history database. Sharing creates a separate view-only page that you can unpublish.</p></div><a class="ml-button" href="/league-history/">Build your record book</a></div></section>

      <section class="ml-section ml-faq"><div><span class="ml-eyebrow">Common questions</span><h2>Before you connect.</h2></div><div class="ml-faq-list">
        <details><summary>What happens when an owner changes the team name?</summary><p>Each team name stays attached to its original season. The manager's career totals still follow the same person.</p></details>
        <details><summary>What happens when someone leaves the league?</summary><p>Their teams, seasons, records, and trophies remain part of the archive. Nothing historical disappears.</p></details>
        <details><summary>Can people outside the league see it?</summary><p>Not by default. An unlisted page is available only to people with the link. A commissioner may also choose public visibility.</p></details>
        <details><summary>Does LineupBeat need my provider password?</summary><p>No. ESPN and CBS use pages already open in your browser. Yahoo sends you through its own authorization page, so your password is never shared with LineupBeat.</p></details>
      </div></section>
    </main>{seo.site_footer()}</body></html>'''


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_page())
    append_sitemap()
    print(f"Built {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
