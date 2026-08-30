# Private odds inputs

Sportsbook data is a private calibration signal for weekly NFL and college
football projections. It is not a reader-facing product and is never copied
into `site/`.

`scripts/odds_inputs.py` collects multi-book consensus game totals, spreads,
vig-free win probabilities, implied team totals and selected player props.
The normalized quotes, consensus values, source timestamps and quality labels
live only in the ignored `beatwire.db` runtime database.

The projection contract is intentionally conservative:

- a market is a calibration input, never the projection by itself;
- three books with a tight consensus is high quality, two books is medium,
  and one book is low;
- game totals and spreads describe scoring environment and game script;
- props may calibrate the matching player statistic only;
- unavailable props do not lower a player;
- public builders do not query the odds tables;
- a projection job must record the snapshot id and its adjustment separately
  before any validated projection snapshot can publish.

## Runtime

The key is the GitHub Actions secret `THE_ODDS_API_KEY`. A normal refresh asks
for featured NFL and NCAAF lines at most once every 20 hours. Player props are
opt-in on a manual workflow run because they are queried once per event and
consume substantially more API credits.

```bash
python scripts/odds_inputs.py --sports nfl,ncaaf
python scripts/odds_inputs.py --sports nfl,ncaaf --include-props
python scripts/odds_inputs.py --report
```

The collector reserves API credits, limits prop games per sport and reports
provider quota headers without printing the secret. Missing credentials skip
the private refresh and do not stop the public site build.
