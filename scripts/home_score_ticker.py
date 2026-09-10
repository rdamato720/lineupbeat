"""Homepage-only score strip. Live scores are separate from fantasy inputs."""
from pathlib import Path


def render():
    script = (Path(__file__).resolve().parents[1] / 'assets/score-ticker.js').read_text()
    return '''<style>
.lb-scores{background:#101713;color:#f5f7f2;border-bottom:1px solid #334035;font-family:Arial,sans-serif}
.lb-scores-inner{max-width:1280px;margin:auto;padding:12px 24px;display:flex;gap:20px;align-items:center}
.lb-scores-controls{flex:0 0 175px}.lb-scores-top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px}
.lb-scores-top strong{font-family:'Barlow Condensed',Arial,sans-serif;font-size:1.125rem;letter-spacing:.08em;text-transform:uppercase}
.lb-score-buttons{display:flex;gap:4px}.lb-scores button{font:inherit;cursor:pointer;color:inherit;background:transparent;border:1px solid #637363;border-radius:4px;min-height:32px;padding:5px 10px}
.lb-score-buttons button[aria-pressed=true]{background:#c6f53c;border-color:#c6f53c;color:#101713;font-weight:700}
.lb-scores button:focus-visible,.lb-scores a:focus-visible,.lb-score-track:focus-visible{outline:2px solid #c6f53c;outline-offset:3px}
.lb-score-meta{font-size:.75rem;color:#b9c6ba;margin-top:8px;line-height:1.4}.lb-score-meta a{color:inherit;text-decoration:underline}
.lb-score-view{min-width:0;flex:1}.lb-score-track{display:flex;gap:0;overflow-x:auto;overscroll-behavior-x:contain;scroll-snap-type:x proximity;scrollbar-width:thin;scrollbar-color:#66765c #101713;padding:3px 0 7px}
.lb-score-card{flex:0 0 166px;scroll-snap-align:start;padding:0 16px;border-left:1px solid #334035;color:inherit;text-decoration:none;font-size:.875rem}
.lb-score-card:hover{background:#1b251c}.lb-score-state{display:block;font-size:.75rem;color:#b9c6ba;white-space:nowrap;margin-bottom:6px;min-height:16px}.lb-score-card.is-live .lb-score-state{color:#c6f53c;font-weight:700}
.lb-score-team{display:flex;gap:7px;align-items:center;min-height:24px}.lb-score-team img{width:21px;height:21px;object-fit:contain}.lb-score-team span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lb-score-team b{margin-left:auto;font-variant-numeric:tabular-nums}.lb-score-team.is-winner{font-weight:700;color:#c6f53c}
.lb-score-message{font-size:.875rem;margin:16px}.lb-score-nav{display:flex;gap:5px;flex:0 0 auto}.lb-score-nav button{font-size:1.125rem;min-width:34px;min-height:40px}.lb-score-nav button:disabled{opacity:.35;cursor:default}
.lb-scores noscript{display:block;padding:12px 24px}.lb-scores noscript a{color:#c6f53c}
@media(max-width:650px){.lb-scores-inner{display:block;padding:10px 16px}.lb-scores-controls{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px}.lb-scores-top{margin:0;gap:14px}.lb-score-meta{margin:0;max-width:150px;text-align:right}.lb-score-view{margin-top:10px}.lb-score-card{flex-basis:150px;padding:0 12px}.lb-score-nav{display:none}}
</style>
<section class="lb-scores" id="lb-scores" aria-label="Football scores">
<div class="lb-scores-inner"><div class="lb-scores-controls"><div><div class="lb-scores-top"><strong>Scores</strong></div><div class="lb-score-buttons" role="group" aria-label="Scoreboard sport"><button type="button" data-score-sport="nfl" aria-pressed="true">NFL</button><button type="button" data-score-sport="college" aria-pressed="false">College</button></div></div><div class="lb-score-meta"><span id="lb-score-status" role="status">Loading scores…</span><br><a id="lb-score-source" href="https://therundown.io" target="_blank" rel="noopener noreferrer">Data provided by TheRundown</a></div></div>
<div class="lb-score-view"><div class="lb-score-track" id="lb-score-track" tabindex="0" role="region" aria-label="NFL games; scroll for more"><p class="lb-score-message">Loading this week’s games…</p></div></div>
<div class="lb-score-nav"><button type="button" data-score-direction="-1" aria-label="Previous games" disabled>‹</button><button type="button" data-score-direction="1" aria-label="Next games" disabled>›</button></div></div>
<noscript>Enable JavaScript to view football scores.</noscript>
</section><script>''' + script + '</script>'
