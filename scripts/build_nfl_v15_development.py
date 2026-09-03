#!/usr/bin/env python3
"""Complete offline development build with trusted-current season adapters.

Run in a disposable checkout. No provider requests or production data writes.
The deployment workflow calls this twice and compares every output byte.
"""
from __future__ import annotations
import argparse
import atexit
import importlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def run(name,*args):
    module=importlib.import_module(name)
    if hasattr(module,'eastern_now'):module.eastern_now=lambda:CUTOFF
    sys.argv=[name,*args]
    # A few established builders execute at import rather than exposing main.
    result=module.main() if hasattr(module,'main') else None
    if isinstance(result,int) and result:raise RuntimeError(f'{name} failed: {result}')

def deny_network(event,args):
    if event in ('socket.connect','socket.getaddrinfo'):raise RuntimeError('offline development build prohibits provider/network requests')

def build_trusted_half_ppr_rankings(model, formats, ranks):
    """Render the established rankings experience from trusted Half-PPR points.

    The format builder already restores the established PPR, Non-PPR,
    Superflex and dynasty boards. The canonical /nfl/rankings/ route is the
    Half-PPR board, so render that fifth view with the same table component
    instead of allowing the season-release projection table to replace it.
    """
    rows=[{
        'player_name':p['name'],
        'team':p['team'],
        'position':p['position'],
        'projected_points':p['formats']['half_ppr'],
    } for p in model['players']]
    # PPR is used only as the generic one-QB replacement-value calculation.
    # The development runner clears PPR adjustments and editorial ordering
    # before this call, so the retained values and order are Half-PPR only.
    records,replacement=formats.rank(rows,'ppr')
    for record in records:record['scoring_format']='half_ppr'
    css,header,footer=ranks.site_chrome()
    for pos in [None,*ranks.POSITIONS]:
        out=ROOT/'site/nfl/rankings'
        if pos:out/=pos.lower()
        out/='index.html';out.parent.mkdir(parents=True,exist_ok=True)
        page=ranks.render(records,pos,CUTOFF,replacement,css,header,footer)
        out.write_text(ranks.seo.check_page(page,str(out)))

def mark_trusted_ranking_pages():
    """Disclose trusted coverage without changing the established layout."""
    note=(
        '<p class="rkstatus"><a href="/nfl/projections/coverage/">'
        'Trusted current set: 424 projected players; 81 evidence holds'
        '</a></p>'
    )
    for page in (ROOT/'site/nfl/rankings').rglob('index.html'):
        text=page.read_text()
        if note not in text:text=text.replace('</header>',note+'</header>',1)
        page.write_text(text)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--development',action='store_true');args=parser.parse_args()
    if not args.development or os.environ.get('DEV_PROJECT')!='lineupbeat-dev':raise SystemExit('isolated development project required')
    os.environ['LINEUPBEAT_NFL_SEASON']='v1.6-trusted-current'
    sys.addaudithook(deny_network)
    import build_nfl_season_release as release
    global CUTOFF
    model,ranking=release.load();CUTOFF=datetime.fromisoformat(model['metadata']['cutoff_utc'].replace('Z','+00:00'))
    import dev_site
    feed=ROOT/'data/rollback/feed.before-replacement.json'
    db=ROOT/'audit/nfl-trusted-build.db';db.parent.mkdir(exist_ok=True)
    template=ROOT/'site/template.html';saved_template=ROOT/'audit/nfl-trusted-source-template.html'
    atexit.register(lambda: template.unlink(missing_ok=True))
    if template.exists():shutil.copyfile(template,saved_template)
    # Builders read site/template.html directly, so recreate the same pristine
    # input for every pass and remove it from the deployable artifact at end.
    shutil.copyfile(saved_template,template)
    dev_site.seed(feed,template,ROOT/'site')
    dev_site.hydrate_db(feed,db)
    release.context_pages()
    # Identity pages must exist before ranking renderers test whether to link.
    run('build_pages','--base','https://lineupbeat-dev.pages.dev','--db',str(db))
    # Season ranking consumers receive the frozen rankings. This does not
    # modify the production workbook, ranking JSON, or recommendation inputs.
    import build_ranking_formats as formats
    import build_rankings as ranks
    ranks.slugify=importlib.import_module('build_pages').slug
    ranks.eastern_now=lambda:CUTOFF
    formats.read_projection_formats=lambda _:{f:[{'player_name':p['name'],'team':p['team'],'position':p['position'],'projected_points':p['formats']['ppr' if f=='superflex' else f]} for p in model['players']] for f in formats.FORMATS}
    formats.source_updated=lambda _:CUTOFF
    def captured_ages(_):
        result={}
        for p in model['players']:
            if not p.get('birth_date'):continue
            birth=datetime.fromisoformat(p['birth_date'])
            age=CUTOFF.year-birth.year-((CUTOFF.month,CUTOFF.day)<(birth.month,birth.day))
            result[(p['name'],p['team'],p['position'])]=age
        return result
    formats.read_roster_ages=captured_ages
    formats.PPR_ADJUSTMENTS={};formats.NON_PPR_ADJUSTMENTS={};formats.PPR_ORDER=();formats.DYNASTY_EDITORIAL={}
    # Auxiliary formats use the same trusted population. The format builder's
    # lower floor is enabled only by this explicit development release.
    run('build_ranking_formats')
    build_trusted_half_ppr_rankings(model,formats,ranks)
    mark_trusted_ranking_pages()
    import build_comparison_tool as comparison
    superflex,_=formats.rank(formats.read_projection_formats(None)['superflex'],'superflex')
    comparison.rank_sets=lambda:{**release.legacy_rank_sets(),'superflex':superflex}
    comparison.slug=ranks.slugify
    original_roster=comparison.roster_data
    def current_roster():
        rows=original_roster()
        for p in model['players']:
            rows[p['canonical_slug']]={**rows.get(p['canonical_slug'],{}),'team':p['team'],'position':p['position'],'photo':release.photo(p)}
        return rows
    comparison.roster_data=current_roster
    import build_draft_value as draft
    draft.read_projections=lambda _:[{'name':p['name'],'pos':p['position'],'team':p['team'],'ppr':p['formats']['ppr'],'half':p['formats']['half_ppr'],'std':p['formats']['non_ppr']} for p in model['players']]
    run('build_pages','--base','https://lineupbeat-dev.pages.dev','--db',str(db))
    run('build_comparison_tool')
    for name in ('build_coaching','build_draft_value','build_college_projections','build_404'):
        run(name)
    run('build_wire','--base','https://lineupbeat-dev.pages.dev')
    run('wire_homepage_replacement','--apply')
    run('build_decision_room')
    run('build_pages','--base','https://lineupbeat-dev.pages.dev','--db',str(db))
    run('build_my_team')
    run('build_chrome_store_bundle')
    release.build()
    dev_site.protect(ROOT/'site','develop')
    # A source template is not a public route. Keep its pristine copy outside
    # the artifact, so protections cannot feed back into the next build.
    template.unlink(missing_ok=True)
    dev_site.verify(ROOT/'site')
    # Verification may restore the tracked template while resolving site
    # provenance. It is still build input, not a deployable route.
    template.unlink(missing_ok=True)
    if template.exists():raise RuntimeError('source template leaked into development artifact')
    print('Complete offline trusted-current development site built; no provider requests')

if __name__=='__main__':main()
