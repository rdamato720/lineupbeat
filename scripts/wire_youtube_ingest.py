#!/usr/bin/env python3
"""YouTube ingestion for the Wire. Local, budgeted, dark launch.

    python3 scripts/wire_youtube_ingest.py            # take today's next slot
    python3 scripts/wire_youtube_ingest.py --plan     # what it would do, no requests
    python3 scripts/wire_youtube_ingest.py --url ...  # one video, same budget
    python3 scripts/wire_youtube_ingest.py --status   # budget and cooldown

Run on a laptop. Captions are rate-limited by address and the ceiling is low:
thirty requests worked, roughly forty in an hour earned an IpBlocked that
outlasted several minutes. So this does not poll. It takes at most five
transcripts a day, one per channel, forty-five minutes apart, and stops
entirely for a day the moment YouTube says no.

Discovery is separate from that budget. Titles, ids, publication times and
durations cost nothing against the caption limit, so eligibility is decided
before a single transcript is requested.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import youtube, ytapi
from wire.store import WireStore, now


def iso(dt) -> str:
    return dt.replace(microsecond=0).isoformat()


def both_zones(utc_iso: str) -> str:
    """A timestamp in UTC and in the reader's own clock.

    The records are UTC because they have to be comparable across machines,
    but "until 01:12 UTC" is not a time anybody waits for. Both, always.
    """
    if not utc_iso:
        return "never"
    try:
        dt = datetime.fromisoformat(utc_iso)
    except (TypeError, ValueError):
        return utc_iso
    local = dt.astimezone()
    return (f"{dt.strftime('%Y-%m-%d %H:%M')} UTC "
            f"({local.strftime('%H:%M %Z')} local)")


def budget_state(store) -> dict:
    """What the day has left, and whether YouTube is speaking to us."""
    used = store.requests_today()
    last = store.last_request_at()
    cooling = store.cooldown_until()
    blocked_until = None
    if cooling and cooling > now():
        blocked_until = cooling
    wait_minutes = 0
    if last:
        nxt = (datetime.fromisoformat(last)
               + timedelta(minutes=youtube.MIN_MINUTES_BETWEEN))
        if iso(nxt) > now():
            wait_minutes = max(
                1, int((nxt - datetime.now(timezone.utc)).total_seconds() // 60))
    return {"used": len(used),
            "remaining": max(0, youtube.MAX_REQUESTS_PER_DAY - len(used)),
            "last": last, "wait_minutes": wait_minutes,
            "blocked_until": blocked_until,
            "channels_done": store.channels_done_today()}


def may_request(state: dict) -> tuple[bool, str]:
    if state["blocked_until"]:
        return False, (f"YouTube returned IpBlocked; no transcript requests "
                       f"until {state['blocked_until']}")
    if state["remaining"] <= 0:
        return False, (f"the day's {youtube.MAX_REQUESTS_PER_DAY} transcript "
                       f"requests are spent")
    if state["wait_minutes"]:
        return False, (f"{state['wait_minutes']} min until the next slot "
                       f"({youtube.MIN_MINUTES_BETWEEN} min apart)")
    return True, ""


def assess(store, ch, v, rules) -> tuple[bool, str, list[str]]:
    """Eligible or not, and every reason either way. Requests no transcript."""
    reasons = []
    if not youtube.owner_matches(ch, v):
        reasons.append(f"owner {v.get('channel_id')!r} is not {ch.channel_id}")
        return False, "", reasons
    if store.cached_transcript(v["video_id"]):
        reasons.append("transcript already cached")
        return False, "", reasons

    secs = v.get("duration_seconds")
    ok, mode, why = youtube.eligible(ch, v, rules, seconds=secs)
    if not ok:
        reasons.append(why)
        return False, mode, reasons
    if secs is None:
        # RSS gives no length. A video whose duration cannot be established
        # safely never spends one of five daily transcript requests.
        reasons.append("duration unknown (RSS discovery); needs the Data API")
        return False, mode, reasons

    reasons.append(f"single voice, {secs // 60}m, owner verified")
    return True, mode, reasons


def pick(store, channels, rules, verbose=True) -> list[tuple]:
    """One eligible video per channel. Metadata only -- no captions touched.

    Everything discovered is recorded, eligible or not, with the reasons.
    A run that finds nothing worth transcribing still leaves an account of
    what it looked at and why it passed.
    """
    done = store.channels_done_today()
    plan = []
    for ch in channels:
        vids, method, note = youtube.discover(ch, limit=8)
        if verbose and note:
            print(f"  {ch.team:<4}{note[:74]}")
        chosen = None
        for v in vids:
            ok, mode, reasons = assess(store, ch, v, rules)
            store.record_discovery({
                "video_id": v["video_id"], "channel_id": v.get("channel_id", ""),
                "channel_name": v.get("channel_name", ch.source_name),
                "source_id": ch.source_id, "canonical_url": v["url"],
                "title": v["title"], "description": v.get("description", ""),
                "published_at": v.get("published_at", ""),
                "duration_seconds": v.get("duration_seconds"),
                "discovery_method": method, "eligible": ok,
                "speaker_mode": mode, "reasons": reasons})
            if ok and chosen is None and ch.channel_id not in done:
                chosen = (ch, v, mode, v.get("duration_seconds"))
        if ch.channel_id in done:
            if verbose:
                print(f"  {ch.team:<4}{ch.source_name[:26]:<27} already had its "
                      f"video today")
            continue
        if chosen:
            plan.append(chosen)
            if verbose:
                _, v, mode, secs = chosen
                print(f"  {ch.team:<4}{v['title'][:40]:<41} "
                      f"{mode} {secs // 60}m -> eligible")
        elif verbose:
            print(f"  {ch.team:<4}{ch.source_name[:26]:<27} nothing eligible "
                  f"({len(vids)} seen via {method})")
    return plan


def candidate_from(ch, video, tr, spans, mode, secs) -> dict:
    """The reviewable record. No span claims to know who is speaking."""
    return {
        "kind": "youtube",
        "source_id": ch.source_id, "source_name": ch.source_name,
        "channel_id": ch.channel_id, "teams": [ch.team],
        "approved_reporters": ch.approved_reporters,
        "classification": ch.classification,
        "attends_practice": ch.attends_practice,
        "reporting_type": ("FIRSTHAND_PRACTICE" if ch.attends_practice
                           else "ANALYSIS"),
        "trust_tier": 1,
        "video_id": video["video_id"], "canonical_url": video["url"],
        "headline": video["title"],
        "description": video.get("description", "")[:600],
        "published_at": video["published_at"],
        "duration_seconds": secs,
        "original_language": tr["language"],
        "transcript_source": tr["transcript_source"],
        "transcript_chars": tr["chars"],
        "speaker_mode": mode,
        "readiness": youtube.readiness(ch, mode),
        "content_sha256": hashlib.sha256(
            "".join(s["text"] for s in spans).encode()).hexdigest(),
        "evidence_spans": spans[:60],
        "excerpt": " ".join(s["text"] for s in spans[:4])[:600],
        "facts": [], "fantasy_relevance": "", "wire_label": "",
        "publication_confidence": None,
        "review_notes": (
            "Auto-generated captions: verify names, negation and numbers "
            "against the video before publishing."
            if tr["transcript_source"] == "AUTO_CAPTIONS" else ""),
    }


def take_one(store, ch, video, mode, secs, rules) -> str:
    """Spend one request. Every outcome is logged, including refusals."""
    cached = store.cached_transcript(video["video_id"])
    if cached:
        return "already cached; no request made"

    tr = youtube.fetch_transcript(video["video_id"], ch.transcript_languages)
    if not tr["ok"]:
        store.log_request(video["video_id"], "FAILED", tr["error"][:120])
        if "IpBlocked" in tr["error"] or "TooManyRequests" in tr["error"]:
            # Stop the whole thing for a day rather than hammering an address
            # that has just said no. Retrying is what turns a rate limit into
            # a longer ban.
            until = iso(datetime.now(timezone.utc)
                        + timedelta(hours=youtube.COOLDOWN_HOURS_AFTER_BLOCK))
            store.set_cooldown(until, tr["error"][:120])
            return f"IpBlocked -- all transcript requests paused until {until}"
        return f"no transcript ({tr['error'][:60]})"

    store.log_request(video["video_id"], "OK", tr["transcript_source"])
    store.save_transcript(video["video_id"], ch.channel_id, tr)

    if tr["chars"] < rules.min_transcript_chars:
        return (f"transcript cached but only {tr['chars']} chars "
                f"-> no candidate")

    spans = youtube.evidence_spans(video["video_id"], tr["segments"])
    payload = candidate_from(ch, video, tr, spans, mode, secs)
    art_like = type("A", (), {
        "source_id": ch.source_id, "canonical_url": video["url"],
        "headline": video["title"], "author": ", ".join(ch.approved_reporters),
        "published_at": video["published_at"], "retrieved_at": now(),
        "original_language": tr["language"],
        "raw_text": " ".join(s["text"] for s in tr["segments"]),
        "content_sha256": payload["content_sha256"],
        "extraction_status": "COMPLETE", "http_status": 200,
        "note": f"{tr['transcript_source']} transcript"})()
    item_id = store.save_item(art_like)
    store.add_candidate(payload["content_sha256"][:16], item_id, ch.source_id,
                        payload,
                        hashlib.sha256(video["url"].encode()).hexdigest()[:20])
    return (f"candidate {payload['content_sha256'][:12]} "
            f"({tr['chars']:,} chars, {len(spans)} spans)")


def report(store, channels, days: int = 14) -> int:
    """The pilot report, from the records rather than from memory.

    The metric that matters is eligible substantial videos per day, not
    whether the day's five requests were spent. Unused capacity is a finding,
    not a failure: retrieving weak content to fill the allowance is the thing
    this pipeline is built to avoid.
    """
    since = iso(datetime.now(timezone.utc) - timedelta(days=days))
    disc = [d for d in store.discovered() if (d["last_seen_at"] or "") >= since]
    by_ch = {}
    for d in disc:
        by_ch.setdefault(d["channel_id"], []).append(d)

    print(f"  YouTube pilot, last {days} days\n")
    print(f"  {'team':<5}{'channel':<26}{'seen':>6}{'eligible':>10}"
          f"{'cached':>8}  top exclusion")
    print("  " + "-" * 78)
    for ch in channels:
        rows = by_ch.get(ch.channel_id, [])
        elig = [r for r in rows if r["eligible"]]
        cached = sum(1 for r in rows
                     if store.cached_transcript(r["video_id"]))
        why = Counter(r["reasons"][0] for r in rows
                      if not r["eligible"] and r["reasons"])
        top = why.most_common(1)[0][0][:34] if why else ""
        print(f"  {ch.team:<5}{ch.source_name[:25]:<26}{len(rows):>6}"
              f"{len(elig):>10}{cached:>8}  {top}")

    log = store.conn.execute(
        "SELECT outcome, detail, requested_at FROM wire_transcript_log "
        "WHERE requested_at >= ?", (since,)).fetchall()
    ok = sum(1 for r in log if r["outcome"] == "OK")
    failed = [r for r in log if r["outcome"] != "OK"]
    blocks = [r for r in failed if "IpBlocked" in (r["detail"] or "")]
    caption_off = [r for r in failed
                   if "Disabled" in (r["detail"] or "")
                   or "no caption" in (r["detail"] or "")]

    days_seen = {(d["last_seen_at"] or "")[:10] for d in disc if d["last_seen_at"]}
    elig_all = [d for d in disc if d["eligible"]]
    elig_days = Counter((d["last_seen_at"] or "")[:10] for d in elig_all)
    zero_days = len(days_seen) - len(elig_days)
    capacity = max(1, len(days_seen)) * youtube.MAX_REQUESTS_PER_DAY

    cands = store.candidates("EDITORIAL_REVIEW")
    yt_cands = [c for c in cands
                if '"kind": "youtube"' in (c["payload"] or "")]

    print(f"\n  transcript attempts   {len(log)}  "
          f"({ok} ok, {len(failed)} failed)")
    print(f"  caption-disabled      {len(caption_off)}")
    print(f"  ip blocks             {len(blocks)}"
          f"  ({len(blocks) * youtube.COOLDOWN_HOURS_AFTER_BLOCK}h cooldown)")
    print(f"  eligible videos       {len(elig_all)} over "
          f"{len(days_seen)} active day(s)"
          + (f", {len(elig_all) / len(days_seen):.1f}/day"
             if days_seen else ""))
    print(f"  days with none        {max(0, zero_days)}")
    print(f"  unused capacity       {max(0, capacity - len(log))} of {capacity}")
    print(f"  candidates pending    {len(yt_cands)} youtube, "
          f"{len(cands) - len(yt_cands)} article")
    teams = {d["source_id"] for d in elig_all}
    print(f"  channels producing    {len(teams)} of {len(channels)}")
    print("\n  Approval, edit and rejection counts come from the review log "
          "once\n  items have been through review_wire.py.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true",
                    help="show what is eligible; request no transcripts")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--report", action="store_true",
                    help="the 14-day pilot report, from the stored records")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--url", help="one video, still counted against the budget")
    ap.add_argument("--all-slots", action="store_true",
                    help="keep taking slots until the day's budget is spent")
    args = ap.parse_args()

    channels, rules = youtube.load()
    bad = youtube.problems(channels)
    if bad:
        for b in bad:
            print(f"  registry: {b}")
        sys.exit("  youtube registry is invalid; nothing ingested")

    store = WireStore()
    state = budget_state(store)
    pool = [c for c in channels if c.pollable]

    print(f"  transcript budget: {state['used']}/{youtube.MAX_REQUESTS_PER_DAY} "
          f"caption requests used today, {state['remaining']} left"
          + (f", next slot in {state['wait_minutes']} min"
             if state["wait_minutes"] else ""))
    if state["blocked_until"]:
        print(f"  COOLDOWN ACTIVE   no caption requests until "
              f"{both_zones(state['blocked_until'])}")
    print(f"  {len(pool)} channel(s) active, "
          f"{len(channels) - len(pool)} disabled or blocked")
    if args.report:
        return report(store, [c for c in channels if c.pollable], args.days)

    # Paused in production. Discovery and the read-only views still work --
    # seeing what would happen costs nothing -- but nothing may spend a
    # caption request while the registry says the pilot is stopped.
    if getattr(rules, "paused", False) and not (args.status or args.plan
                                                or args.report):
        print("  the YouTube pilot is PAUSED in sources/wire_youtube.yaml")
        print("  no transcript will be requested; --plan and --status still work")
        return 0

    if args.status:
        cd = store.cooldown_until()
        cached = store.conn.execute(
            "SELECT COUNT(*) c FROM wire_transcripts").fetchone()["c"]
        pend = store.candidates("EDITORIAL_REVIEW")
        yt_pend = [c for c in pend if '"kind": "youtube"' in (c["payload"] or "")]
        disc = store.discovered()
        elig = [d for d in disc if d["eligible"]]
        today = now()[:10]
        elig_today = [d for d in elig if (d["last_seen_at"] or "")[:10] == today]
        done = state["channels_done"]

        print("\n  METADATA  (YouTube Data API quota; never touches the "
              "transcript budget)")
        print(f"    discovery route   "
              + ("YOUTUBE_DATA_API" if ytapi.available()
                 else "YOUTUBE_RSS (no YOUTUBE_API_KEY set)"))
        print(f"    api calls, this run {ytapi.calls_made()}")
        print(f"    videos recorded   {len(disc)}")
        print(f"    last discovery    {both_zones(store.last_discovery_at())}")

        # Stored, selectable and selected are three different numbers and
        # have been mistaken for each other. A channel can have two eligible
        # videos and still offer only one, because the per-channel cap is a
        # day rate, not a queue.
        by_chan: dict = {}
        for d in elig_today:
            by_chan.setdefault(d["channel_id"], []).append(d)
        selectable = [c for c in by_chan if c not in done]
        attempts = store.conn.execute(
            "SELECT l.outcome, l.video_id, d.channel_name "
            "FROM wire_transcript_log l "
            "LEFT JOIN wire_discovery d ON d.video_id = l.video_id "
            "WHERE substr(l.requested_at,1,10) = ?", (today,)).fetchall()

        print("\n  ELIGIBILITY")
        print(f"    eligible stored   {len(elig)}  (all time)")
        print(f"    eligible today    {len(elig_today)} across "
              f"{len(by_chan)} channel(s)")
        print(f"    selectable now    {len(selectable)}  (cap of "
              f"{youtube.MAX_VIDEOS_PER_CHANNEL_PER_DAY} per channel per day; "
              f"extra eligible videos wait for tomorrow)")
        print(f"    attempted today   {len(attempts)}"
              + (f"  ({', '.join(f'{a[2] or a[1]}: {a[0]}' for a in attempts)})"
                 if attempts else ""))
        print(f"    captured today    {len(done)}"
              f"  ({', '.join(sorted(done)) if done else 'none'})")

        print("\n  TRANSCRIPT BUDGET  (caption endpoint; the scarce one)")
        print(f"    caption requests  {state['used']}/"
              f"{youtube.MAX_REQUESTS_PER_DAY} today, "
              f"{state['remaining']} left")
        print(f"    last request      {both_zones(state['last'])}")
        print(f"    next slot         "
              + ("blocked by cooldown" if state["blocked_until"]
                 else f"in {state['wait_minutes']} min" if state["wait_minutes"]
                 else "now" if state["remaining"] else "not today"))
        print(f"    cooldown          "
              + (f"ACTIVE until {both_zones(cd)}" if state["blocked_until"]
                 else f"none" + (f" (last expired {both_zones(cd)})" if cd else "")))
        print(f"    transcripts cached {cached}")

        print("\n  REVIEW QUEUE")
        print(f"    youtube candidates {len(yt_pend)}")
        print(f"    article candidates {len(pend) - len(yt_pend)}")

        ok, why = may_request(state)
        print("\n  NEXT SAFE COMMAND")
        if state["blocked_until"]:
            print("    cooldown active; use --plan or --status only")
            print("    wire_youtube_ingest.py --plan")
        elif not ok:
            print(f"    {why}")
            print("    wire_youtube_ingest.py --plan")
        elif elig_today:
            print("    wire_youtube_ingest.py        (takes one slot)")
        else:
            print("    nothing eligible; wire_youtube_ingest.py --plan")
        return 0

    if args.url:
        vid = args.url.split("v=")[-1].split("&")[0]
        ch = next((c for c in pool if True), None)
        owner = None
        for c in pool:
            vids, _ = youtube.uploads(c, limit=15)
            if any(v["video_id"] == vid for v in vids):
                owner = c
                video = next(v for v in vids if v["video_id"] == vid)
                break
        if owner is None:
            sys.exit(f"  {vid} is not a recent upload of any approved channel")
        ok, why = may_request(state)
        if not ok:
            sys.exit(f"  {why}")
        secs, _ = youtube.duration_seconds(vid)
        mode = youtube.speaker_mode(video["title"], rules)
        print(f"  manual: {owner.team} {video['title'][:52]}")
        print(f"    {take_one(store, owner, video, mode, secs, rules)}")
        return 0

    plan = pick(store, pool, rules, verbose=True)
    if not plan:
        print("\n  nothing eligible today")
        return 0
    print(f"\n  {len(plan)} eligible; budget allows {state['remaining']}")

    if args.plan:
        print("  --plan, no transcript requested")
        return 0

    taken = 0
    for ch, video, mode, secs in plan:
        state = budget_state(store)
        ok, why = may_request(state)
        if not ok:
            print(f"\n  stopping: {why}")
            break
        print(f"\n  {ch.team} {video['title'][:56]}")
        result = take_one(store, ch, video, mode, secs, rules)
        print(f"    {result}")
        taken += 1
        if "IpBlocked" in result:
            break
        if not args.all_slots:
            print(f"    one slot per run; "
                  f"{youtube.MIN_MINUTES_BETWEEN} min until the next")
            break

    n, changed = store.export_publications()
    print(f"\n  {taken} request(s) made. {n} published items"
          f"{' (file updated)' if changed else ''}")
    print("  nothing was published; candidates await review_wire.py")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
