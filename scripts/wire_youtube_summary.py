#!/usr/bin/env python3
"""A YouTube pilot status block for the CI job summary.

    python3 scripts/wire_youtube_summary.py >> "$GITHUB_STEP_SUMMARY"

Reports only. It requests nothing, spends nothing, and touches neither the
Data API quota nor the caption budget -- every figure below is read from the
database that discovery already wrote.

Why the workflow runs discovery but never a transcript: a caption request is
rate-limited by IP, and roughly forty in an hour earns an IpBlocked that
lasts a day. A CI runner has an IP shared with everybody else on it, so the
transcript half of the pilot stays on a laptop. Discovery -- titles, ids and
durations -- is a different endpoint with a different limit, costs one quota
unit a channel, and is safe to run here.

Nothing in this file may fail the job. A YouTube outage is not a reason for
the website to stop publishing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    out = ["### The Wire — YouTube pilot", ""]
    try:
        import importlib.util as _u
        from wire import youtube, ytapi
        from wire.store import WireStore

        # budget_state lives in the ingest CLI, which is also the only thing
        # allowed to spend a request. Imported, never invoked.
        _sp = _u.spec_from_file_location(
            "_yt_ingest", Path(__file__).resolve().parent / "wire_youtube_ingest.py")
        _m = _u.module_from_spec(_sp)
        _sp.loader.exec_module(_m)

        store = WireStore()
        state = _m.budget_state(store)
        disc = store.discovered()
        elig = [d for d in disc if d["eligible"]]
        cached = store.conn.execute(
            "SELECT COUNT(*) c FROM wire_transcripts").fetchone()["c"]
        reg = youtube.load()
        active = len([c for c in reg
                      if getattr(c, "enabled", True)]) or len(reg)

        pend = store.candidates("EDITORIAL_REVIEW")
        yt_pend = [c for c in pend if '"kind": "youtube"' in (c["payload"] or "")]
        yt_ev = store.conn.execute(
            "SELECT COUNT(*) c FROM wire_evidence WHERE source_url LIKE "
            "'%youtube.com%' OR source_url LIKE '%youtu.be%'").fetchone()["c"]

        cd = store.cooldown_until()
        cool = (f"ACTIVE until {cd}" if state.get("blocked_until")
                else "none")
        route = ("YOUTUBE_DATA_API" if ytapi.available()
                 else "YOUTUBE_RSS (no key in this environment)")

        rows = [
            ("Active channels", active),
            ("Videos discovered", len(disc)),
            ("Eligible videos", len(elig)),
            ("Transcript requests today",
             f"{state['used']} used, {state['remaining']} remaining "
             f"of {youtube.MAX_REQUESTS_PER_DAY}"),
            ("Cached transcripts", f"{cached} (a cached transcript never "
                                   f"costs a request)"),
            ("Cooldown", cool),
            ("YouTube evidence candidates", yt_ev),
            ("YouTube publications awaiting review", len(yt_pend)),
            ("Discovery route", route),
        ]
        out.append("| | |")
        out.append("|---|---|")
        out += [f"| {k} | {v} |" for k, v in rows]
        out += ["", "_Discovery runs here; transcripts are requested only on "
                    "a laptop, because the caption endpoint blocks by IP._"]
    except Exception as e:                      # never fail the job
        out.append(f"Status unavailable: `{type(e).__name__}`. "
                   f"The website pipeline is unaffected.")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
