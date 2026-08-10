#!/usr/bin/env python3
"""Shared SEO parts for the fantasy data pages.

Imported by build_projections, build_draft_value, build_coaching and
build_sos so the four pages carry the same structures rather than four
slightly different attempts at them.

WHY THIS EXISTS

A data page is mostly a table, and a table is close to invisible to a
search engine: it has the numbers and none of the questions somebody typed
to find them. The durability page already carried an FAQ block and picked
up traffic the others do not, and the difference is that it answers in
sentences.

So each page gets three things it did not have:

  FAQPage      the questions people actually search, answered in prose
  ItemList     the ranking, declared as a ranking
  cross-links  so the four pages stop being four islands

WHAT IT DELIBERATELY DOES NOT DO

Keyword stuffing, hidden text, or an FAQ written for a crawler rather than
a reader. Every answer here is one somebody would want if they asked the
question out loud, which is also the only kind of answer that survives a
search engine deciding it dislikes the pattern.
"""

from __future__ import annotations

import html
import json

SITE_URL = "https://lineupbeat.com"
SPORT = "nfl"


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


# The other data pages, for the cross-link strip. Each page filters itself
# out, so one list serves all of them.
DATA_PAGES = [
    ("projections", f"/{SPORT}/projections/", "Projections",
     "Full-season points in three scoring formats, with the stat line "
     "behind each number."),
    ("draft-value", f"/{SPORT}/draft-value/", "ADP & draft value",
     "Where the market is drafting each player against where we rank him."),
    ("coaching", f"/{SPORT}/coaching/", "Offensive coaching",
     "Who actually calls each offence and which positions that favours."),
    ("strength-of-schedule", f"/{SPORT}/strength-of-schedule/",
     "Strength of schedule",
     "Opponent record and fantasy points allowed, by position."),
    ("durability", f"/{SPORT}/durability/", "Durability",
     "How many games each player has really given since 2018."),
]


def related_html(current: str) -> str:
    """A strip of links to the other data pages.

    Four pages that never mention each other are four pages a reader leaves
    after one visit, and four pages a crawler treats as unrelated. Naming
    what is behind each link is the difference between navigation and a
    list of words.
    """
    items = [x for x in DATA_PAGES if x[0] != current]
    cards = "".join(
        f'<a class="relcard" href="{href}">'
        f'<h3>{esc(title)}</h3><p>{esc(blurb)}</p></a>'
        for _k, href, title, blurb in items)
    return (f'\n  <section class="related">\n'
            f'    <h2 class="relh">More fantasy data</h2>\n'
            f'    <div class="relgrid">{cards}</div>\n'
            f'  </section>\n')


RELATED_CSS = """
/* ---- related pages ----
   Four data pages that never mention each other are four pages a reader
   leaves after one visit. */
.related{margin:2.6rem 0 0; border-top:1px solid var(--rule);
  padding-top:1.4rem}
.relh{font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em;
  font-size:.78rem; color:var(--quiet); margin:0 0 .8rem}
.relgrid{display:grid; grid-template-columns:repeat(4, 1fr); gap:.7rem}
@media (max-width:900px){ .relgrid{grid-template-columns:repeat(2, 1fr)} }
@media (max-width:560px){ .relgrid{grid-template-columns:1fr} }
.relcard{display:block; background:var(--card); border:1px solid var(--rule);
  border-radius:8px; padding:.75rem .85rem; text-decoration:none}
.relcard:hover{border-color:var(--signal)}
.relcard h3{font-family:var(--agate); text-transform:uppercase;
  letter-spacing:.05em; font-size:.72rem; color:var(--ink); margin:0}
.relcard:hover h3{color:var(--signal)}
.relcard p{margin:.3rem 0 0; font-size:.78rem; line-height:1.45;
  color:var(--quiet)}

/* ---- FAQ ----
   Prose, because a table has the numbers and none of the questions
   somebody typed to find them. */
.faq{margin:2.2rem 0 0}
.faqh{font-family:var(--agate); text-transform:uppercase; letter-spacing:.07em;
  font-size:.78rem; color:var(--quiet); margin:0 0 .9rem}
.faq details{border-bottom:1px solid var(--rule); padding:.7rem 0}
.faq summary{font-size:.92rem; color:var(--ink); cursor:pointer;
  list-style:none; font-weight:500}
.faq summary::-webkit-details-marker{display:none}
.faq summary::before{content:"+"; color:var(--signal); margin-right:.5rem;
  font-family:var(--data)}
.faq details[open] summary::before{content:"\\2212"}
.faq p{margin:.5rem 0 0 1.1rem; font-size:.86rem; line-height:1.6;
  color:var(--quiet); max-width:74ch}
.faq p b{color:var(--ink)}
"""


def faq_html(pairs) -> str:
    """Visible FAQ. Open by default is wrong; findable is not.

    The answers are in the markup whether or not the details element is
    open, so a crawler reads all of them while a reader sees a tidy list.
    That is the honest version of the pattern -- the content is genuinely
    there, not injected after a click.
    """
    items = "".join(
        f"<details><summary>{esc(q)}</summary><p>{a}</p></details>"
        for q, a in pairs)
    return (f'\n  <section class="faq">\n'
            f'    <h2 class="faqh">Common questions</h2>\n'
            f'    {items}\n  </section>\n')


def faq_schema(pairs):
    """FAQPage, matching the visible text exactly.

    Schema that does not match what is on the page is the thing search
    engines penalise, so both come from the same list.
    """
    import re
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer",
                                "text": re.sub(r"<[^>]+>", "", a)}}
            for q, a in pairs],
    }


def itemlist_schema(name, url, items):
    """A ranking, declared as one.

    items: [(position, name, url_or_None)]
    """
    return {
        "@type": "ItemList",
        "name": name,
        "url": url,
        "numberOfItems": len(items),
        "itemListOrder": "https://schema.org/ItemListOrderDescending",
        "itemListElement": [
            {"@type": "ListItem", "position": pos, "name": nm,
             **({"url": SITE_URL + u} if u else {})}
            for pos, nm, u in items],
    }


def breadcrumbs(trail):
    """trail: [(name, path_or_None)]"""
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": nm,
             "item": SITE_URL + (path or "/")}
            for i, (nm, path) in enumerate(trail)],
    }


ORGANISATION = {
    "@type": "Organization",
    "@id": f"{SITE_URL}/#org",
    "name": "LineupBeat",
    "url": SITE_URL + "/",
    "description": ("Local NFL beat reporting matched to fantasy relevant "
                    "players, with projections, ADP, coaching and schedule "
                    "data."),
}


def graph(*nodes):
    """One @graph rather than several loose blocks.

    Separate script tags describe separate things; a graph says they are
    the same page seen from different angles, which is what they are.
    """
    return json.dumps({"@context": "https://schema.org",
                       "@graph": [n for n in nodes if n]},
                      separators=(",", ":"))
