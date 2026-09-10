"""Bounded access check: free quota lookup plus two score requests (4 credits)."""
import json
import os
import urllib.request
import urllib.error

key = os.environ.get('THE_ODDS_API_KEY')
if not key:
    raise SystemExit('THE_ODDS_API_KEY is missing; zero requests made')

def get(path):
    request = urllib.request.Request('https://api.the-odds-api.com/v4/' + path + ('&' if '?' in path else '?') + 'apiKey=' + key)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
            remaining = response.headers.get('x-requests-remaining')
            used = response.headers.get('x-requests-used')
            print(json.dumps({'endpoint': path, 'remaining': remaining, 'used': used, 'items': len(data)}))
            return data, int(remaining) if remaining is not None else None
    except Exception:
        raise SystemExit('Provider check failed; URL and provider error suppressed to protect credentials') from None

_, remaining = get('sports')
if remaining is None or remaining < 1004:
    raise SystemExit('Insufficient verified quota: preserving at least 1,000 credits for existing features')
for sport in ('americanfootball_nfl', 'americanfootball_ncaaf'):
    games, _ = get(f'sports/{sport}/scores?daysFrom=3')
    if not isinstance(games, list) or any(not all(k in g for k in ('id', 'home_team', 'away_team', 'commence_time', 'completed')) for g in games):
        raise SystemExit('Unexpected score schema')
    print(json.dumps({'sport': sport, 'games': len(games), 'with_scores': sum(bool(g.get('scores')) for g in games), 'completed': sum(g['completed'] for g in games)}))
