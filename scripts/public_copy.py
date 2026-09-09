"""Keep engineering notes out of public page copy without changing data."""
from __future__ import annotations
import re
from html.parser import HTMLParser
from pathlib import Path

SEASON_NOTE = '<section class="v15method" id="methodology"><h2>About these projections</h2><p>These are full-season estimates, last updated August 30, 2026. For this week’s matchups and injury status, see <a href="/nfl/week-1/projections/">Week 1 projections</a>.</p></section>'
# Exact historical template copy only; never rewrite news or numerical data.
REPLACEMENTS = {
    'Trusted current set: 424 projected players; 81 evidence holds': '424 players · Season projection coverage',
    '2026 season · trusted current set': '2026 season',
    '2026 season · evidence hold': '2026 season',
    'Season projection withheld': 'Season projection unavailable',
    'See trusted-set coverage': 'See projection coverage',
    'Projection values reviewed August 30 · current roster matched September 2': 'Season projections updated August 30, 2026',
    ' · current active exact match': '',
    'Season source: trusted current set · September 2, 2026': '2026 season projections',
    'Reviewed August 30 stat line; current injury adjustments are unavailable.': '',
    'Descending projected points; stable player ID breaks ties.': 'Ranked by projected season points.',
    '<h3>Validated data</h3>': '<h3>Rankings &amp; projections</h3>',
    'NFL and College fantasy projections, comparisons, decision boundaries, rankings, and accountable recommendations.': 'NFL and College fantasy rankings, projections, and player comparisons.',
    'Methodology &amp; accountability': 'About LineupBeat',
    'How Lineup Beat makes and preserves decisions': 'How LineupBeat works',
    'No additional validated decision context is available; the projection panel above remains the current Lineup Beat view.': 'See the season projection above and visit the Week 1 boards for weekly matchups.',
    'Team Decision Board · validated player profiles and projection context': 'Team Decision Board · player profiles and fantasy projections',
    'The historical database needed for this page is not included in this offline development release.': 'This tool is currently unavailable. Explore the Week 1 rankings and projections for current matchups.',
    '<p class="eyebrow">Development preview</p>': '<p class="eyebrow">Fantasy tools</p>',
    'View the validated 2026 season projections': 'View the 2026 season projections',
}
PROHIBITED = re.compile(
    r'published only after exact current active identity|projection values retained from the reviewed|'
    r'current injury reporting is not incorporated|no v1\.5 fallback|experimental v1\.5|'
    r'private quality-control benchmark|trusted current set|evidence holds?|'
    r'current active exact match|stable player ID breaks ties|'
    r'qualifying out-of-sample validation|deployed formula|'
    r'development preview|offline development release|'
    r'page build: current release|frozen team(?:-level| totals)|'
    r'twelve decimal places|reconciliation checks|no target layer has been built|'
    r'identity-resolved players|exact current name, team and position match|'
    r'published only when validated profile data', re.I)

class ReaderText(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip=0; self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style','svg'}: self.skip+=1
        attrs=dict(attrs)
        if tag=='meta' and (attrs.get('name')=='description' or attrs.get('property') in {'og:title','og:description'}):
            self.parts.append(attrs.get('content',''))
    def handle_endtag(self, tag):
        if tag in {'script','style','svg'} and self.skip: self.skip-=1
    def handle_data(self, text):
        if not self.skip: self.parts.append(text)

def visible_text(page):
    parser=ReaderText();parser.feed(page)
    return ' '.join(' '.join(parser.parts).split())

def clean_page(page):
    page=re.sub(r'<section class="v15method" id="methodology">.*?</section>',
                lambda m:SEASON_NOTE if 'experimental v1.5' in m[0] else m[0],page,flags=re.S)
    page=re.sub(r'<p class="notice">Published only after exact current active identity.*?</p>', '',page,flags=re.S)
    page=re.sub(r'<p>([^<]+) is on the current [^<]+ roster, but does not have one exact current name, team and position match in the reviewed projection baseline\. Lineup Beat will not fill that gap with a lower-confidence estimate\.</p>',
                lambda m:f'<p>A season projection is not yet available for {m[1]}.</p>',page)
    for old,new in REPLACEMENTS.items(): page=page.replace(old,new)
    return page

def audit(root: Path):
    failures=[];count=0
    for path in sorted(root.rglob('*.html')):
        if path.name=='template.html':continue  # source template, not a public route
        count+=1
        match=PROHIBITED.search(visible_text(path.read_text(errors='replace')))
        if match:failures.append(f'{path.relative_to(root)}: {match[0]}')
    return count,failures
