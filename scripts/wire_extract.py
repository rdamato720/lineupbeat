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
from wire import segment as seg
from wire import si as sicfg
from wire import youtube as yt
from wire.store import WireStore


_SI_AUTHORS: dict = {}


def _si_authors() -> dict:
    global _SI_AUTHORS
    if not _SI_AUTHORS:
        _SI_AUTHORS = sicfg.load_authors()
    return _SI_AUTHORS


def _series_ok(src, headline: str) -> bool:
    from wire import nflteam as _nt
    return _nt.series_ok(headline, src.qualifying_series)


def source_context(store, source_id: str, item: dict | None = None) -> dict:
    """Who the source is, and whether we can trust the voice in it."""
    item = item or {}
    for s in artreg.load():
        if s.source_id == source_id:
            if s.paid:
                # The third of three refusals, and the one that matters most:
                # even if a body somehow reached the store, it produces no
                # span here. Discovery metadata is not evidence and a
                # headline is not a claim.
                return {"type": "paid", "name": s.source_name, "author": "",
                        "teams": s.teams, "reporter_voice": False,
                        "auto_captions": False, "multi_speaker": True,
                        "channel_id": "", "paid": True, "si": False,
                        "refuse": artreg.PAID_LABEL,
                        "ownership": s.source_ownership}
            # A named reporter is a verified voice; a multi-author
            # publication is not. Registering Steelers Depot did not
            # establish that every byline on it attends practice, and
            # granting reporter_voice by source would have handed its
            # aggregators the firsthand status that SI authors have to earn
            # one at a time. Same standard, both routes.
            named = bool((s.reporter_name or "").strip()) and \
                "staff" not in s.reporter_name.lower()
            if s.source_class == artreg.OFFICIAL_TEAM_SITE:
                # A club writer is a firsthand voice only when he has been
                # approved by name AND the article is one of his approved
                # series. Naming the reporter in the config is not the
                # approval -- that is the mistake this rule exists to stop:
                # Jim Wyatt produced firsthand spans from a source where he
                # is deliberately unapproved, purely because his name was in
                # the yaml.
                named = (s.evidence_access == "TEAM_EMPLOYED_FIRSTHAND"
                         and named
                         and _series_ok(s, item.get("headline", "")))
            return {"type": "article", "name": s.source_name,
                    "author": s.reporter_name, "teams": s.teams,
                    "reporter_voice": named, "auto_captions": False,
                    "multi_speaker": False, "channel_id": "",
                    "paid": False, "si": s.adapter == artreg.SI_TEAM_PAGE,
                    "refuse": "", "ownership": s.source_ownership}
    chans, _ = yt.load()
    for c in chans:
        if c.source_id == source_id:
            return {"type": "youtube", "paid": False, "si": False, "refuse": "",
                    "ownership": artreg.INDEPENDENT, "name": c.source_name,
                    "author": ", ".join(c.approved_reporters),
                    "teams": [c.team], "reporter_voice": False,
                    "auto_captions": True, "multi_speaker": False,
                    "channel_id": c.channel_id}
    return {"type": "unknown", "name": source_id, "author": "", "teams": [],
            "reporter_voice": False, "auto_captions": False,
            "multi_speaker": True, "channel_id": "", "paid": False,
            "si": False, "refuse": "", "ownership": artreg.INDEPENDENT}


def spans_from_article(item) -> list[tuple[str, str, float | None, float | None]]:
    """(location, text, start, end) per candidate passage.

    Windows never cross a structural boundary. Sentence windowing alone
    merged a quotation, a section heading and the next paragraph into one
    span, which is how Josh Allen's words about Dalton Kincaid were filed as
    Keon Coleman's account of his own role.
    """
    return [(sp["location"], sp["text"], None, None)
            for sp in seg.spans(item["raw_text"] or "")]


def spans_from_transcript(store, video_id: str
                          ) -> list[tuple[str, str, float | None, float | None]]:
    tr = store.cached_transcript(video_id)
    if not tr:
        return []
    spans = yt.evidence_spans(video_id, tr["segments"])
    return [(f"t{int(s['start_seconds'])}", s["text"],
             s["start_seconds"], s["end_seconds"]) for s in spans]


def extract_item(store, item, reg, ctx, cfg, dry=False,
                 seen_claims=None, seen_reports=None) -> dict:
    """One captured source becomes zero or more evidence candidates."""
    stats = {"spans": 0, "with_players": 0, "candidates": 0, "new": 0,
             "context_only": 0, "unresolved": 0, "refused": 0,
             "superseded": 0, "not_relevant": 0, "duplicates": 0,
             "same_underlying_report": 0}
    live: set = set()
    seen_claims = {} if seen_claims is None else seen_claims
    seen_reports = {} if seen_reports is None else seen_reports
    # Per-article, so an overlap comparison stays cheap and only ever
    # compares spans that could actually be the same observation.
    claim_spans: list = []
    if ctx.get("refuse"):
        # No spans, no candidates, no partial credit.
        stats["refused"] = 1
        return stats
    team = (ctx["teams"] or [""])[0]

    reporter_voice = ctx["reporter_voice"]
    byline = (item.get("author") or "").strip() or ctx["author"]
    byline_class = ""
    if ctx.get("si"):
        # On SI the voice is a property of the byline, not the source. One
        # landing page carries a beat reporter's practice notebook and a
        # columnist's argument, and only the first is a firsthand voice.
        # An author nobody has classified is not one either.
        byline_class = sicfg.classify_author(byline, team, _si_authors())
        reporter_voice = byline_class == sicfg.FIRSTHAND_APPROVED
    video_id = ""
    if ctx["type"] == "youtube" and yt.load()[1].paused:
        # Paused in production. Cached transcripts are kept, and the code
        # that reads them is kept; what stops is turning them into evidence,
        # because the next thing evidence does is cost a Claude call.
        return []
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

        # Is this a current football development at all? Most of a team page
        # is not, and the publisher's own fantasy advice never is.
        irrelevant = ev.relevance(text)
        if irrelevant:
            stats["not_relevant"] += 1
            stats.setdefault("relevance_reasons", {})
            key = irrelevant.split("(")[0].strip()
            stats["relevance_reasons"][key] = \
                stats["relevance_reasons"].get(key, 0) + 1
            continue

        klass, conf, why = ev.classify(
            text,
            reporter_voice=reporter_voice,
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

            pid = player.player_id if player else name
            ckey = ev.claim_key(pid, text)
            dup = seen_claims.get(ckey, "")
            if not dup:
                # Only within the same segment. Windows overlap by
                # construction -- each span is a sentence plus its
                # neighbours -- so consecutive spans of one paragraph really
                # are one observation. Two different paragraphs are two
                # different observations, and comparing across the whole
                # article collapsed them: Shedeur Sanders splitting first-team
                # reps and Monken on resting starters became one report
                # because both name Sanders and share enough camp vocabulary
                # to clear 0.6.
                here = str(location).split("s")[0]
                for prev_text, prev_id, prev_pid, prev_loc in claim_spans:
                    if prev_pid != pid:
                        continue
                    if str(prev_loc).split("s")[0] != here:
                        continue
                    if ev.overlap(text, prev_text) >= 0.6:
                        dup = prev_id
                        break

            # A quotation belongs to whoever gave it -- but it is usually
            # ABOUT somebody else, and that somebody is the reason it matters.
            #
            # This used to demote any quotation the tagged player did not
            # personally speak, which threw away exactly the class the wire
            # exists to catch: a coach saying a player will be back Friday, a
            # coordinator describing a role. The original bug it was written
            # for is real -- a span quoting Hassan Haskins was filed as a
            # quotation from Jam Miller, who is merely named nearby -- so the
            # guard stays, narrowed to what it was for: the player has to be
            # named in the span, and whoever actually spoke is recorded and
            # travels with the record. Whether the quote supports a claim
            # about this player is a question of meaning, which is the
            # semantic layer's job, not a regex's.
            klass_here, conf_here, why_here = klass, conf, list(why)
            speaker = ""
            if klass_here == ev.DIRECT_QUOTATION:
                who = player.full_name if player else name
                speaker = ev.named_speaker(text)
                mentioned = pl.norm(who).split()[-1:] and (
                    pl.norm(who).split()[-1] in pl.norm(text).split())
                if not speaker:
                    klass_here, conf_here = ev.UNCERTAIN, 0.3
                    why_here = ["quoted words with no named speaker"] + why_here
                elif not mentioned:
                    klass_here, conf_here = ev.UNCERTAIN, 0.3
                    why_here = [f"quoted words and {who!r} is not named in "
                                f"the span"] + why_here
                elif not ev.is_speaker(who, text):
                    why_here = [f"quotation from {speaker!r} about "
                                f"{who!r}"] + why_here

            origin = ev.origin_of(text)
            urid = ev.underlying_report_id(origin, text)
            if urid and not dup:
                # Two rewrites of one original are one underlying report.
                prev = seen_reports.get(urid)
                if prev:
                    dup = prev
                    stats["same_underlying_report"] += 1
                else:
                    seen_reports[urid] = "pending"

            rec = {
                "quote_speaker": speaker if klass_here == ev.DIRECT_QUOTATION else "",
                "claim_key": ckey,
                **origin,
                "underlying_report_id": urid,
                "duplicate_of": dup,
                "candidate_id": ev.candidate_id(
                    gid, player.player_id if player else "", name),
                "evidence_group_id": gid,
                "source_type": ctx["type"],
                "source_id": item["source_id"],
                "source_url": item["canonical_url"],
                "source_title": item["headline"],
                # The byline, not the source. On SI the author is what
                # decides whether a span may read as firsthand, so a
                # candidate that does not name him cannot be reviewed.
                "source_author_or_channel": ctx["channel_id"] or byline,
                "published_at": item["published_at"],
                "video_id": video_id,
                "start_seconds": start, "end_seconds": end,
                "location": location,
                # The whole span, not a slice of it. Spans run to 1,720
                # characters and this was cut at 1,200, so a reviewer could
                # be shown a passage that does not contain the player it is
                # filed under -- the matcher had read the full span and was
                # right, but nothing on screen said so. Evidence a reviewer
                # cannot check is not evidence.
                "evidence_text": text,
                "evidence_class": klass_here,
                "classification_confidence": conf_here,
                "classification_reasons": (
                    why_here + [f"byline classified {byline_class}"]
                    if byline_class else why_here),
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
                "source_ownership": ctx.get("ownership", artreg.INDEPENDENT),
                "review_status": ev.PENDING,
                "exclusion_reason": exclusion,
            }
            if dup:
                stats["duplicates"] += 1
            else:
                seen_claims[ckey] = rec["candidate_id"]
                claim_spans.append((text, rec["candidate_id"], pid, location))
                if urid and seen_reports.get(urid) == "pending":
                    seen_reports[urid] = rec["candidate_id"]
            stats["candidates"] += 1
            live.add(rec["candidate_id"])
            if not dry and store.upsert_evidence(rec):
                stats["new"] += 1

    # Retire what this run no longer produces. Re-extraction used to only
    # add and update, so a candidate built from a superseded version of an
    # article outlived the text it came from: one row in the review sample
    # named Carlos Washington from a span that names Malik Washington, left
    # behind when the article was re-fetched and its span boundaries moved.
    # Superseded, never deleted, and a reviewer's decision is never touched.
    if not dry:
        stats["superseded"] = store.supersede_evidence(
            item["canonical_url"], live)
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
    items = items[:args.limit]
    print(f"  {len(items)} captured source(s), registry {reg.version}")

    total = {"spans": 0, "with_players": 0, "candidates": 0, "new": 0,
             "context_only": 0, "unresolved": 0, "refused": 0, "superseded": 0,
             "not_relevant": 0, "duplicates": 0, "same_underlying_report": 0}
    relevance_reasons: dict = {}
    # Rewrites that trace to one original report, across every source.
    seen_reports: dict = {}
    # Claims already seen in this run, so a syndicated story republished on
    # another team page links to the first copy instead of counting twice.
    seen_claims: dict = {}
    for item in items:
        ctx = source_context(store, item["source_id"], dict(item))
        st = extract_item(store, dict(item), reg, ctx, cfg, dry=args.dry_run,
                          seen_claims=seen_claims, seen_reports=seen_reports)
        for k in total:
            total[k] += st.get(k, 0)
        for k, v in (st.get("relevance_reasons") or {}).items():
            relevance_reasons[k] = relevance_reasons.get(k, 0) + v
        if st["candidates"]:
            print(f"    {item['source_id'][:28]:<29}{st['candidates']:>3} "
                  f"candidates from {st['with_players']}/{st['spans']} spans"
                  f"  {item['headline'][:34]}")

    print(f"\n  {total['spans']} spans, {total['with_players']} naming a player")
    print(f"  {total['candidates']} candidates "
          f"({total['new']} new, {total['candidates'] - total['new']} updated)")
    print(f"  {total['unresolved']} unresolved names, "
          f"{total['context_only']} offensive-line context")
    print(f"  {total['not_relevant']} span(s) refused as not a current "
          f"development")
    for k, v in sorted(relevance_reasons.items(), key=lambda x: -x[1]):
        print(f"      {v:>5}  {k}")
    print(f"  {total['duplicates']} duplicate claim(s) linked to a first copy"
          f" ({total['same_underlying_report']} of them rewrites of one "
          f"underlying report)")
    if total["superseded"]:
        print(f"  {total['superseded']} candidate(s) superseded "
              f"(the span that produced them no longer exists)")
    if total["refused"]:
        print(f"  {total['refused']} source(s) refused outright "
              f"(paid: no span may be built from them)")
    if args.dry_run:
        print("  --dry-run, nothing written")
    else:
        print("  all PENDING; nothing published, nothing can be")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
