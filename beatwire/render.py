"""Output. Terminal, JSON, and a static HTML feed.

The HTML target assumes the same deploy shape you already use: build a static
file, push it, let a scheduled job refresh it. No server to run.
"""

from __future__ import annotations

import html
import json
from datetime import datetime

BADGE = {3: "ACT", 2: "WATCH", 1: "NOTE", 0: "NOISE"}


def to_terminal(rows: list[dict]) -> str:
    if not rows:
        return "No nuggets."
    out = []
    for r in rows:
        srcs = ", ".join(a["source_name"] for a in r["attributions"])
        corr = f" (+{r['corroborations'] - 1} more)" if r["corroborations"] > 1 else ""
        out.append(
            f"[{BADGE.get(r['actionability'], '?'):<5}] "
            f"{r['player_name']} ({r['team']}, {r['category']})\n"
            f"         {r['claim']}\n"
            f"         via {srcs}{corr}  conf={r['confidence']}"
        )
    return "\n\n".join(out)


def to_json(rows: list[dict]) -> str:
    return json.dumps(rows, indent=2)


def to_html(rows: list[dict], title: str = "Beat feed") -> str:
    cards = []
    for r in rows:
        links = " · ".join(
            f'<a href="{html.escape(a["url"])}">{html.escape(a["source_name"])}</a>'
            for a in r["attributions"]
        )
        cards.append(f"""
    <article class="n a{r['actionability']}">
      <header>
        <span class="badge">{BADGE.get(r['actionability'], '?')}</span>
        <strong>{html.escape(r['player_name'])}</strong>
        <span class="meta">{html.escape(r['team'])} · {html.escape(r['category'])}</span>
      </header>
      <p>{html.escape(r['claim'])}</p>
      <footer>{links}</footer>
    </article>""")

    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ --bg:#0f1115; --fg:#e8eaed; --dim:#8b93a1; --line:#232733; }}
  body {{ background:var(--bg); color:var(--fg); font:15px/1.5 ui-sans-serif,system-ui,sans-serif;
         max-width:44rem; margin:0 auto; padding:2rem 1rem; }}
  h1 {{ font-size:1.1rem; letter-spacing:.04em; text-transform:uppercase; color:var(--dim); }}
  .n {{ border-top:1px solid var(--line); padding:1rem 0; }}
  .n header {{ display:flex; gap:.5rem; align-items:baseline; margin-bottom:.35rem; }}
  .badge {{ font-size:.65rem; letter-spacing:.08em; padding:.15rem .4rem; border-radius:3px;
            background:var(--line); color:var(--dim); }}
  .a3 .badge {{ background:#7f1d1d; color:#fecaca; }}
  .a2 .badge {{ background:#78350f; color:#fde68a; }}
  .meta {{ color:var(--dim); font-size:.85rem; }}
  .n p {{ margin:.2rem 0 .4rem; }}
  footer, footer a {{ color:var(--dim); font-size:.8rem; }}
</style>
<h1>{html.escape(title)}</h1>
{''.join(cards)}
<p class="meta">Updated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
"""
