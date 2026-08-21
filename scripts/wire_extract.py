#!/usr/bin/env python3
"""Extract reviewable evidence from stored articles and transcripts.

    python3 scripts/wire_extract.py                 # everything captured
    python3 scripts/wire_extract.py --only pewter
    python3 scripts/wire_extract.py --dry-run
    python3 scripts/wire_extract.py --show           # what came out

Dark launch. Every row lands in wire_evidence with review_status PENDING and
the only way out is a human in review_wire.py. This script cannot write to
data/wire_publications.json and there is a test asserting it never does.

It reads what has already been captured -- no fetching, no model calls, no
fantasy data. Classification is deterministic and deliberately timid: an
over-cautious label costs a reviewer a moment, an over-confident one costs a
published mistake.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire import evidence as ev
from wire import players as pl
from wire import registry as artreg
from wire import youtube as yt
from wire.store import WireStore


def source_context(store, source_id: str) -> dict:
    """Who the source is, and whether we can trust the voice in it."""
    for s in artreg.load():
        if s.source_id == source_id:
            return {"type": "article", "name": s.source_name,
                    "author": s.reporter_name, "teams": s.teams,
                    "reporter_voice": True, "auto_captions": False,
                    "multi_speaker": False, "channel_id": ""}
    chans, _ = yt.load()
    for c in chans:
        if c.source_id == source_id:
            return {"type": "youtube", "name": c.source_name,
                    "author": ", ".join(c.approved_reporters),
                    "teams": [c.team], "reporter_voice": False,
                    "auto_captions": True, "multi_speaker": False,
                    "channel_id": c.channel_id}
    return {"type": "unknown", "name": source_id, "author": "", "teams": [],
            "reporter_voice": False, "auto_captions": False,
            "multi_speaker": True, "channel_id": ""}


def spans_from_article(item) -> list[tuple[str, str, float | None, float | None]]:
    """(location, text, start, end) per candidate passage."""
    sents = ev.sentences(item["raw_text"] or "")
    out = []
    for i in range(len(sents)):
        out.append((f"sentence_{i + 1}", ev.window(sents, i), None, None))
    return out


def spans_from_transcript(store, video_id: str
                          ) -> list[tuple[str, str, float | None, float | None]]:
    tr = store.cached_transcript(video_id)
    if not tr:
        return []
    spans = yt.evidence_spans(video_id, tr["segments"])
    return [(f"t{int(s['start_seconds'])}", s["text"],
             s["start_seconds"], s["end_seconds"]) for s in spans]


def extract_item(store, item, reg, ctx, cfg, dry=False) -> dict:
    """One captured source becomes zero or more evidence candidates."""
    stats = {"spans": 0, "with_players": 0, "candidates": 0, "new": 0,
             "context_only": 0, "unresolved": 0}
    team = (ctx["teams"] or [""])[0]
    video_id = ""
    if ctx["type"] == "youtube":
        video_id = (item["canonical_url"] or "").split("v=")[-1].split("&")[0]
        spans = spans_from_transcript(store, video_id)
    else:
        spans = spans_from_article(item)

    for location, text, start, end in spans:
        stats["spans"] += 1
        if len(text) < 60:
            continue
        named = ev.find_players(text, reg, team)
        if not named:
            continue                     # a passage naming nobody is not evidence
        stats["with_players"] += 1

        klass, conf, why = ev.classify(
            text,
            reporter_voice=ctx["reporter_voice"],
            auto_captions=ctx["auto_captions"],
            multi_speaker=ctx["multi_speaker"])
        gid = ev.group_id(item["source_item_id"], location, text)

        for name, hits, how in named:
            player = hits[0] if len(hits) == 1 else None
            exclusion = ""
            if player is not None and player.context_only:
                # A lineman is team context. The claim is kept and linked to
                # nobody, so it can inform a reviewer without becoming a
                # fantasy-player card.
                exclusion = "offensive line: team context, not a fantasy player"
                stats["context_only"] += 1
            elif player is not None and not player.fantasy_candidate:
                exclusion = f"{player.position} is not a fantasy position"
            elif player is None:
                exclusion = (f"{len(hits)} registry matches" if hits
                             else "no registry match")
                stats["unresolved"] += 1

            rec = {
                "candidate_id": ev.candidate_id(
                    gid, player.player_id if player else "", name),
                "evidence_group_id": gid,
                "source_type": ctx["type"],
                "source_id": item["source_id"],
                "source_url": item["canonical_url"],
                "source_title": item["headline"],
                "source_author_or_channel": ctx["channel_id"] or ctx["author"],
                "published_at": item["published_at"],
                "video_id": video_id,
                "start_seconds": start, "end_seconds": end,
                "location": location,
                "evidence_text": text[:1200],
                "evidence_class": klass,
                "classification_confidence": conf,
                "classification_reasons": why,
                # Identity resolution is scored separately from the claim.
                # A confident classification of an unidentified player is
                # still not publishable, and one number would hide that.
                "player_id": player.player_id if player else "",
                "player_name": player.full_name if player else name,
                "team": player.team if player else team,
                "position": player.position if player else "",
                "resolution_method": how,
                "resolution_confidence": (
                    0.95 if how == "stable_id"
                    else 0.85 if how == "name_team_position" and player
                    else 0.0),
                "registry_version": reg.version,
                "registry_hash": cfg.get("source_sha256", ""),
                "review_status": ev.PENDING,
                "exclusion_reason": exclusion,
            }
            stats["candidates"] += 1
            if not dry and store.upsert_evidence(rec):
                stats["new"] += 1
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="substring on source_id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    store = WireStore()
    reg = pl.load()
    if not reg.players:
        sys.exit("  no player registry; run scripts/wire_players_refresh.py")
    cfg = json.loads(pl.REGISTRY.read_text())

    if args.show:
        rows = store.evidence(status=ev.PENDING)
        print(f"  {len(rows)} pending evidence candidate(s)")
        for r in rows[:args.limit]:
            who = (f"{r['player_name']} ({r['team']} {r['position']})"
                   if r["player_id"] else f"{r['player_name']} [unresolved]")
            ts = (f" @{int(r['start_seconds'])}s"
                  if r["start_seconds"] is not None else "")
            print(f"\n  {r['evidence_class']:<22}{r['classification_confidence']:.2f}"
                  f"  {who}{ts}")
            print(f"    {r['source_title'][:70]}")
            print(f"    {r['evidence_text'][:150]}")
            if r["exclusion_reason"]:
                print(f"    excluded: {r['exclusion_reason']}")
        return 0

    items = store.conn.execute(
        "SELECT * FROM wire_source_items WHERE extraction_status = 'COMPLETE'"
    ).fetchall()
    if args.only:
        items = [i for i in items if args.only.lower() in i["source_id"].lower()]
    print(f"  {len(items)} captured source(s), registry {reg.version}")

    total = {"spans": 0, "with_players": 0, "candidates": 0, "new": 0,
             "context_only": 0, "unresolved": 0}
    for item in items:
        ctx = source_context(store, item["source_id"])
        st = extract_item(store, dict(item), reg, ctx, cfg, dry=args.dry_run)
        for k in total:
            total[k] += st[k]
        if st["candidates"]:
            print(f"    {item['source_id'][:28]:<29}{st['candidates']:>3} "
                  f"candidates from {st['with_players']}/{st['spans']} spans"
                  f"  {item['headline'][:34]}")

    print(f"\n  {total['spans']} spans, {total['with_players']} naming a player")
    print(f"  {total['candidates']} candidates "
          f"({total['new']} new, {total['candidates'] - total['new']} updated)")
    print(f"  {total['unresolved']} unresolved names, "
          f"{total['context_only']} offensive-line context")
    if args.dry_run:
        print("  --dry-run, nothing written")
    else:
        print("  all PENDING; nothing published, nothing can be")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
