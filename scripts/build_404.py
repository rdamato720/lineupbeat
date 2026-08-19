#!/usr/bin/env python3
import pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import seo

SITE = pathlib.Path(__file__).resolve().parent.parent / "site"
src = (SITE / "template.html").read_text()
css = re.search(r"<style>(.*?)</style>", src, re.S).group(1)
foot = re.search(r"<footer.*?</footer>", src, re.S).group(0)

# Without this, an unmatched path falls through to the homepage: the URL
# stays, the front page renders under it, and a crawler reads a soft 404.
# /nfl/stephen-carlson/ and /nfl/wire/ were both this, not separate bugs.
html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Page not found | LineupBeat</title>
<meta name="robots" content="noindex, follow">
<style>{css}{seo.UI_CSS}
.nf{{max-width:44rem;margin:0 auto;padding:4rem 1rem 6rem;text-align:center}}
.nf h1{{font-size:2.1rem;margin:0 0 .6rem}}
.nf p{{color:var(--quiet);font-size:.95rem;line-height:1.6;margin:0 auto 1.6rem;
  max-width:34rem}}
.nf .links{{display:flex;gap:.6rem;flex-wrap:wrap;justify-content:center}}
.nf .links a{{font-family:var(--agate);text-transform:uppercase;
  letter-spacing:.1em;font-size:.74rem;font-weight:600;padding:.5rem .95rem;
  border:1px solid var(--rule);border-radius:999px;color:var(--quiet);
  text-decoration:none}}
.nf .links a:hover{{color:var(--ink);border-color:var(--signal)}}
</style>
</head><body>
{seo.site_nav()}
<main class="nf">
  <h1>That page isn't here</h1>
  <p>The address may be mistyped, or the player may not have a page yet.
     Pages are written for players with beat reports or a projection, so a
     deep roster name can be on the site without having one of his own.</p>
  <div class="links">
    <a href="/">The Wire</a>
    <a href="/nfl/data/">Fantasy Data</a>
    <a href="/nfl/projections/">NFL projections</a>
    <a href="/college-fantasy-football/projections/">College projections</a>
  </div>
</main>
{foot}
</body></html>"""
(SITE / "404.html").write_text(html)
print(f"  wrote site/404.html ({len(html):,} bytes)")
