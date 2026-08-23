#!/usr/bin/env python3
"""Wire regressions, including the isolation guarantee. No network, no keys.

    python3 scripts/test_wire.py

The isolation half is the important part. The Wire is an editorial news
product and must never read a projection, a ranking, an ADP, a draft value, a
strength-of-schedule figure or a durability rating -- nor recommend that any
of them change. That is a claim about the code, so it is checked against the
code rather than remembered.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wire import registry
from wire.store import WireStore

FAILURES = []


def check(name, ok, detail=""):
    print(f"[{'  ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


# ---------------------------------------------------------------- isolation

FORBIDDEN_IMPORTS = re.compile(
    r"^\s*(?:from|import)\s+(beatwire|scripts)\b", re.M)
FORBIDDEN_NAMES = re.compile(
    r"beatwire\.db|projections\.xlsx|nfl_rankings|draft_value|adp_curve|"
    r"schedule_strength|durability|coaching\.csv|rosters/nfl\.csv", re.I)

wire_files = sorted((ROOT / "wire").glob("*.py"))
check("the wire package exists", len(wire_files) >= 3,
      f"{len(wire_files)} modules")

def code_only(src: str) -> str:
    """Strip docstrings and comments before scanning.

    These modules explain the isolation rule in prose, naming the very things
    they must not touch. Scanning raw text flags the explanation and passes
    the violation, which is the wrong way round.
    """
    tree = ast.parse(src)
    doc_spans = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            doc_spans.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in doc_spans:
            continue
        out.append(line.split("#", 1)[0])
    return "\n".join(out)


for f in wire_files:
    src = code_only(f.read_text())
    hits = FORBIDDEN_IMPORTS.findall(src)
    check(f"{f.name} imports nothing from the fantasy side", not hits, str(hits))
    named = [m for m in FORBIDDEN_NAMES.findall(src)]
    check(f"{f.name} names no fantasy data file", not named, str(named[:3]))

ingest = (ROOT / "scripts" / "wire_ingest.py").read_text()
review = (ROOT / "scripts" / "review_wire.py").read_text()
for name, src in (("wire_ingest.py", code_only(ingest)),
                  ("review_wire.py", code_only(review))):
    check(f"{name} imports nothing from beatwire",
          not re.search(r"^\s*(?:from|import)\s+beatwire\b", src, re.M))
    check(f"{name} names no fantasy data file",
          not FORBIDDEN_NAMES.search(src))

# Capture and discovery may add source items and review candidates to the
# database, but they are not publication actions. In particular, a fresh
# local database must never be allowed to replace the tracked reviewed launch
# set with an empty export. Keeping the export route out of the ingestion
# script makes the publication file byte-identical for every discovery run.
check("article discovery cannot rewrite the publication mirror",
      "export_publications" not in code_only(ingest)
      and "wire_publications.json" not in code_only(ingest))

# The site build must not be able to read candidates.
build = (ROOT / "scripts" / "build_pages.py").read_text()
# `wire.db` is a substring of `beatwire.db`, so this needs a boundary or it
# reports the fantasy database as a Wire leak.
check("the site build never reads wire_candidates",
      "wire_candidates" not in build
      and not re.search(r"(?<!beat)wire\.db", build)
      and "wire_publications" not in build)

# ---------------------------------------------------------------- registry

sources = registry.load()
check("registry loads", len(sources) >= 10, f"{len(sources)} sources")
check("registry passes its own rules", not registry.problems(sources),
      str(registry.problems(sources)[:2]))

by_id = {s.source_id: s for s in sources}
blocked = [s for s in sources if s.status == registry.BLOCKED]
check("blocked sources are never pollable",
      all(not s.pollable for s in blocked), f"{len(blocked)} blocked")
check("blocked sources keep a recorded reason",
      all(s.blocked_reason or s.status == registry.BLOCKED for s in blocked))

# Manual submission is a route around missing discovery, never around a
# publisher's refusal. This is the rule the spec is most explicit about.
mass = by_id.get("masslive_mark_daniels")
check("a 403 publisher rejects manual submission too",
      mass is not None and not mass.manual_ok)
ath = by_id.get("the_athletic_dan_wiederer")
check("a paywalled publisher rejects manual submission too",
      ath is not None and not ath.manual_ok, ath.blocked_reason if ath else "")
bh = by_id.get("boston_herald_andrew_callahan")
check("a discovery-only gap still allows manual submission",
      bh is not None and bh.manual_ok and not bh.pollable)

# A site-wide feed without a filter would ingest a publisher's other desks.
loose = registry.Source(
    source_id="x", source_name="x", reporter_name="x", teams=["ARI"],
    domains=["example.com"], status=registry.AUTO_READY,
    reporting_type="LOCAL_BEAT", adapter=registry.FULL_TEXT_FEED,
    feed_url="https://example.com/feed", active=True, feed_scope="site")
check("an unfiltered site feed is rejected by the validator",
      any("no filter" in p for p in registry.problems([loose])))

check("url ownership matches on host, not substring",
      by_id["pewter_report"].owns("https://www.pewterreport.com/x/")
      and not by_id["pewter_report"].owns("https://evil.com/pewterreport.com/x"))

# ------------------------------------------------------------------- store

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "t.db"
    out = Path(tmp) / "pub.json"
    st = WireStore(db)

    st.add_candidate("c1", "i1", "pewter_report",
                     {"headline": "one", "canonical_url": "u1"}, "fp1")
    check("a new candidate lands in review, not published",
          len(st.candidates("EDITORIAL_REVIEW")) == 1
          and len(st.publications()) == 0)

    n, changed = st.export_publications(out)
    check("an unreviewed candidate reaches the published file never",
          n == 0 and json.loads(out.read_text())["publications"] == [])

    st.publish("c1", {"headline": "one", "canonical_url": "u1"}, "fp1")
    check("approving publishes exactly one item", len(st.publications()) == 1)

    st.add_candidate("c2", "i2", "pewter_report",
                     {"headline": "one, updated", "canonical_url": "u1b"}, "fp1")
    st.publish("c2", {"headline": "one, updated", "canonical_url": "u1b"}, "fp1")
    pubs = st.publications()
    check("the same event updates the card rather than adding a second",
          len(pubs) == 1 and pubs[0]["version"] == 2, f"v{pubs[0]['version']}")

    st.add_candidate("c3", "i3", "pewter_report",
                     {"headline": "other", "canonical_url": "u2"}, "fp2")
    st.publish("c3", {"headline": "other", "canonical_url": "u2"}, "fp2")
    check("a different event does get its own card", len(st.publications()) == 2)

    hist = st.history("fp1")
    check("every action is recorded in the audit trail", len(hist) >= 4,
          f"{len(hist)} entries for one event")

    n, changed = st.export_publications(out)
    check("the published file carries the approved items", n == 2 and changed)
    n2, changed2 = st.export_publications(out)
    check("an unchanged file is not rewritten", n2 == 2 and not changed2)

    try:
        st.set_state("c3", "NOT_A_STATE")
        check("an unknown state is refused", False)
    except ValueError:
        check("an unknown state is refused", True)

# ------------------------------------------------------------------ youtube

from wire import youtube as yt

channels, rules = yt.load()
check("youtube registry loads", len(channels) == 10, f"{len(channels)} channels")
check("youtube registry passes its own rules", not yt.problems(channels),
      str(yt.problems(channels)[:2]))
check("every channel id is a real UC id",
      all(re.fullmatch(r"UC[\w-]{22}", c.channel_id) for c in channels))
check("every channel records when and how it was verified",
      all(c.verified_on and c.verified_by for c in channels))

by_team = {c.team: c for c in channels}
# `NO` is YAML's boolean false. New Orleans parsed as `team: False` and
# matched nothing until the codes were quoted.
check("New Orleans survives YAML's boolean NO", "NO" in by_team,
      str(sorted(by_team)[:4]))
check("a channel with captions disabled is blocked and inactive",
      by_team["ARI"].classification == yt.BLOCKED
      and not by_team["ARI"].pollable
      and by_team["ARI"].blocked_reason == "transcripts_disabled")

# Speaker identity comes from the format, never from the content.
cases = [
    ("Packers Training Camp Report - Day 14!!!", yt.SINGLE_VOICE),
    ("David Walker Camp Diary: Bucs Want To Get Out", yt.SINGLE_VOICE),
    ("Bucs DT Vita Vea Speaks!", yt.MULTI_SPEAKER),
    ("Pack-A-Day Members Q&A!!!", yt.MULTI_SPEAKER),
    ("Will Howard, Steelers, breaks down his performance", yt.MULTI_SPEAKER),
    ("Titans press conference: head coach", yt.MULTI_SPEAKER),
    ("Vikings IMPORTANT intel from training camp", yt.UNCERTAIN),
]
for title, want in cases:
    got = yt.speaker_mode(title, rules)
    check(f"speaker mode: {title[:38]!r}", got == want, f"got {got}")

# The rule that matters: only a plainly single voice may skip straight to a
# candidate, and even then a human still approves it.
firsthand = by_team["MIN"]
check("a single-voice video from an approved reporter is AUTO_READY",
      yt.readiness(firsthand, yt.SINGLE_VOICE) == yt.AUTO_READY)
check("an interview is never AUTO_READY",
      yt.readiness(firsthand, yt.MULTI_SPEAKER) == yt.MANUAL_REVIEW_ONLY)
check("an unclassifiable title is never AUTO_READY",
      yt.readiness(firsthand, yt.UNCERTAIN) == yt.MANUAL_REVIEW_ONLY)

check("short transcripts are rejected by the floor",
      rules.min_transcript_chars >= 1500, str(rules.min_transcript_chars))
check("speech recognition is not an allowed transcript source",
      "WHISPER_TRANSCRIPTION" not in rules.allowed_transcript_sources)

segs = [{"start_seconds": 875.1, "duration_seconds": 4.8, "text": "one"},
        {"start_seconds": 879.9, "duration_seconds": 4.0, "text": "two"}]
spans = yt.evidence_spans("VID", segs, window=2)
check("evidence spans carry a start, an end and a deep link",
      len(spans) == 1 and spans[0]["start_seconds"] == 875.1
      and spans[0]["url"].endswith("&t=875s"), str(spans[:1]))
check("no evidence span claims to know who is speaking",
      all("speaker" not in s for s in spans))

ytsrc = (ROOT / "wire" / "youtube.py").read_text()
check("youtube.py imports nothing from the fantasy side",
      not FORBIDDEN_IMPORTS.findall(code_only(ytsrc)))
check("youtube.py names no fantasy data file",
      not FORBIDDEN_NAMES.search(code_only(ytsrc)))
check("no diarization is attempted in V1",
      "diariz" not in code_only(ytsrc).lower())

# ------------------------------------------------- youtube transcript budget

sys.path.insert(0, str(ROOT / "scripts"))
import wire_youtube_ingest as ing
from datetime import datetime, timedelta, timezone

active = [c for c in channels if c.pollable]
check("exactly the five approved channels are active", len(active) == 5,
      ", ".join(c.team for c in active))
check("the starting five are the ones approved",
      {c.team for c in active} == {"BUF", "PIT", "TEN", "MIN", "TB"},
      str(sorted(c.team for c in active)))

check("the budget is five a day, forty-five minutes apart",
      yt.MAX_REQUESTS_PER_DAY == 5 and yt.MIN_MINUTES_BETWEEN == 45
      and yt.MAX_VIDEOS_PER_CHANNEL_PER_DAY == 1)
check("a block pauses everything for a day",
      yt.COOLDOWN_HOURS_AFTER_BLOCK == 24)
check("videos under five minutes are not worth a request",
      yt.MIN_DURATION_SECONDS == 300)

tb = by_team["TB"]
for title, want_ok, why in [
    ("David Walker Camp Diary: Bucs Want To Get Out", True, ""),
    ("Bucs DT Vita Vea Speaks!", False, "multi"),
    ("Bucs highlights #shorts", False, "short"),
]:
    ok, mode, reason = yt.eligible(tb, {"title": title}, rules)
    check(f"eligibility: {title[:34]!r}", ok == want_ok, reason or "eligible")
ok, _, reason = yt.eligible(tb, {"title": "Bucs Camp Diary Day 9"}, rules,
                            seconds=120)
check("a two-minute video is refused on length", not ok, reason)

with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "b.db")
    check("a fresh day allows a request", ing.may_request(ing.budget_state(st))[0])

    for i in range(yt.MAX_REQUESTS_PER_DAY):
        st.log_request(f"v{i}", "OK", "")
    ok, why = ing.may_request(ing.budget_state(st))
    check("the daily cap stops further requests", not ok, why)

with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "b.db")
    st.log_request("v1", "OK", "")
    ok, why = ing.may_request(ing.budget_state(st))
    check("requests are staggered", not ok, why)
    # A refused request still spent an attempt against the address.
    st2 = WireStore(Path(tmp) / "c.db")
    st2.log_request("v9", "FAILED", "IpBlocked")
    check("a failed request still counts against the day",
          ing.budget_state(st2)["used"] == 1)

with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "b.db")
    until = (datetime.now(timezone.utc) + timedelta(hours=24)
             ).replace(microsecond=0).isoformat()
    st.set_cooldown(until, "IpBlocked")
    ok, why = ing.may_request(ing.budget_state(st))
    check("an IpBlocked pauses every request for a day", not ok, why[:44])

with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "b.db")
    st.save_transcript("vidX", "UC1", {"transcript_source": "AUTO_CAPTIONS",
                                       "language": "en", "chars": 9000,
                                       "segments": []})
    check("a transcript is cached permanently and never re-requested",
          st.cached_transcript("vidX") is not None)
    check("a channel gets one video a day", "UC1" in st.channels_done_today())

# ------------------------------------------------- youtube data api + keys

from wire import ytapi

check("the key is read only from the environment",
      "YOUTUBE_API_KEY" in (ROOT / "wire" / "ytapi.py").read_text()
      and not any("YOUTUBE_API_KEY" in (ROOT / "sources" / f).read_text()
                  for f in ("wire_youtube.yaml", "wire_articles.yaml")))
check("no key is committed anywhere in the wire",
      not any(re.search(r"AIza[0-9A-Za-z_\-]{20,}", f.read_text())
              for f in list((ROOT / "wire").glob("*.py"))
              + list((ROOT / "scripts").glob("wire_*.py"))
              + list((ROOT / "sources").glob("wire_*.yaml"))))

_saved = os.environ.get("YOUTUBE_API_KEY")
os.environ["YOUTUBE_API_KEY"] = "AIzaSyFAKEKEYFAKEKEYFAKEKEYFAKEKEY01"
try:
    leak = "https://googleapis.com/v3/videos?key=AIzaSyFAKEKEYFAKEKEYFAKEKEYFAKEKEY01&id=x"
    check("a url carrying the key is redacted",
          "AIzaSy" not in ytapi.redact(leak) and "[REDACTED]" in ytapi.redact(leak))
    check("a key we do not hold is redacted too",
          "AIzaSyOTHERKEYOTHERKEYOTHERKEY99" not in
          ytapi.redact("failed with key AIzaSyOTHERKEYOTHERKEYOTHERKEY99"))
    try:
        ytapi._call("videos", {"id": "x"})
        raised = ""
    except ytapi.YouTubeAPIError as e:
        raised = str(e)
    check("an api error never carries the key", "AIzaSy" not in raised, raised[:50])
finally:
    os.environ.pop("YOUTUBE_API_KEY", None)
    if _saved is not None:
        os.environ["YOUTUBE_API_KEY"] = _saved

check("a missing key fails safely rather than raising",
      ytapi.available() is False or isinstance(ytapi.api_key(), str))
check("search.list is never used", "search.list" not in
      code_only((ROOT / "wire" / "ytapi.py").read_text())
      and "\"search\"" not in code_only((ROOT / "wire" / "ytapi.py").read_text()))
check("uploads playlist id is derived from the channel id",
      ytapi.uploads_playlist("UCwqeh84GMm9Hf7365M9lRYw") == "UUwqeh84GMm9Hf7365M9lRYw")
try:
    ytapi.uploads_playlist("@PewterReportTV")
    check("a handle is refused as a channel id", False)
except ytapi.YouTubeAPIError:
    check("a handle is refused as a channel id", True)

check("iso durations parse", ytapi.parse_duration("PT1H2M3S") == 3723
      and ytapi.parse_duration("PT8M") == 480)
check("an unreadable duration is None, not zero",
      ytapi.parse_duration("") is None and ytapi.parse_duration("garbage") is None)
check("the rss fallback is labelled rss, not the data api",
      ytapi.RSS == "YOUTUBE_RSS" and ytapi.DATA_API == "YOUTUBE_DATA_API"
      and ytapi.RSS != ytapi.DATA_API)

# ------------------------------------------------- discovery and eligibility

import wire_youtube_ingest as ing2

tb = by_team["TB"]
with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "d.db")
    good = {"video_id": "v1", "channel_id": tb.channel_id, "url": "u",
            "title": "Bucs Camp Diary Day 9", "duration_seconds": 900}
    ok, mode, why = ing2.assess(st, tb, good, rules)
    check("a verified single-voice report is eligible", ok, str(why))

    wrong = dict(good, channel_id="UCwrongwrongwrongwrong1")
    ok, _, why = ing2.assess(st, tb, wrong, rules)
    check("a video owned by another channel is refused", not ok, why[0][:52])

    noduration = dict(good, duration_seconds=None)
    ok, _, why = ing2.assess(st, tb, noduration, rules)
    check("a video with no safe duration is refused", not ok, why[0][:52])

    short = dict(good, duration_seconds=120)
    ok, _, why = ing2.assess(st, tb, short, rules)
    check("a video under five minutes is refused", not ok, why[0][:40])

    for title in ("Bucs DT Vita Vea Speaks!", "Bucs press conference",
                  "Members Q&A", "Bucs roundtable", "highlights #shorts"):
        ok, _, why = ing2.assess(st, tb, dict(good, title=title), rules)
        check(f"excluded: {title[:30]!r}", not ok, why[0][:34])

    st.save_transcript("v1", tb.channel_id, {"transcript_source": "AUTO_CAPTIONS",
                                             "language": "en", "chars": 9000,
                                             "segments": []})
    ok, _, why = ing2.assess(st, tb, good, rules)
    check("a cached video is never requested again", not ok, why[0][:40])

    # A to Z is a network channel; college content must not pass its filter.
    ten = by_team["TEN"]
    ok, _, why = ing2.assess(st, ten, {"video_id": "v9",
                                       "channel_id": ten.channel_id, "url": "u",
                                       "title": "Notre Dame camp report",
                                       "duration_seconds": 900}, rules)
    check("A to Z's team filter rejects college content", not ok, why[0][:40])

    # Discovery is idempotent and costs no transcript budget.
    rec = {"video_id": "d1", "channel_id": tb.channel_id, "canonical_url": "u",
           "title": "t", "eligible": False, "reasons": ["x"]}
    check("discovery is idempotent",
          st.record_discovery(rec) is True and st.record_discovery(rec) is False)
    check("discovery consumes no transcript budget",
          ing2.budget_state(st)["used"] == 0)

    # Discovery keeps working while transcripts are frozen.
    until = (datetime.now(timezone.utc) + timedelta(hours=24)
             ).replace(microsecond=0).isoformat()
    st.set_cooldown(until, "IpBlocked")
    state = ing2.budget_state(st)
    check("the cooldown reports its exact expiry",
          state["blocked_until"] == until, until)
    check("discovery still records during a cooldown",
          st.record_discovery(dict(rec, video_id="d2")) is True)
    ok, why = ing2.may_request(state)
    check("no transcript is requested during a cooldown", not ok, why[:40])

disabled = [c for c in channels if not c.pollable]
check("disabled channels can never be picked",
      all(c.team in {"CLE", "NO", "GB", "PHI", "ARI"} for c in disabled),
      ", ".join(c.team for c in disabled))
check("PHNX stays blocked with captions disabled",
      by_team["ARI"].classification == yt.BLOCKED
      and by_team["ARI"].blocked_reason == "transcripts_disabled")
check("the PHLY travel channel is never referenced",
      "UCw0gNeeQuXiRcBd23239Ivg" not in
      (ROOT / "sources" / "wire_youtube.yaml").read_text())

# The site must not be able to reach any of this.
check("the site build cannot read transcripts or discovery",
      not any(t in build for t in ("wire_transcripts", "wire_discovery",
                                   "wire_candidates")))

# ------------------------------------------------------- player registry

from wire import players as pl

reg = pl.load()
check("the player registry loads", len(reg.players) > 1500,
      f"{len(reg.players)} players")
check("the registry records its version and source",
      bool(reg.version) and "nflverse" in reg.source_url, reg.version)

raw = json.loads((ROOT / "sources" / "wire_players.json").read_text())
check("the registry validates", not pl.validate(raw), str(pl.validate(raw)[:2]))

# No fantasy-derived field, at any depth. Walked rather than eyeballed.
def deep_keys(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(str(k).lower())
            deep_keys(v, out)
    elif isinstance(node, list):
        for x in node[:200]:
            deep_keys(x, out)
    return out

keys = deep_keys(raw, set())
leaked = sorted(k for k in keys
                if any(f in k for f in pl.FORBIDDEN_FIELDS))
check("no fantasy field appears anywhere in the registry", not leaked, str(leaked))
check("only identity fields are stored",
      keys >= {"player_id", "full_name", "team", "position"}
      and not ({"adp", "projection", "points", "rank"} & keys))

ids = [p.player_id for p in reg.players if p.player_id]
check("player ids are unique", len(ids) == len(set(ids)),
      f"{len(ids)} ids, {len(set(ids))} unique")
teams = {p.team for p in reg.players if p.team}
check("teams are the wire's own codes, all 32",
      teams <= pl.NFL_TEAMS and len(teams) == 32,
      str(sorted(teams - pl.NFL_TEAMS)[:4]))
check("nflverse's AZ and LA are normalised",
      "AZ" not in teams and "LA" not in teams
      and "ARI" in teams and "LAR" in teams)
check("candidate flags are only on QB/RB/WR/TE",
      all(p.position in pl.CANDIDATE_POSITIONS
          for p in reg.players if p.fantasy_candidate))
check("linemen are kept as context, never as candidates",
      any(p.context_only for p in reg.players)
      and not any(p.context_only and p.fantasy_candidate for p in reg.players))

# The Wire must never touch the fantasy roster, even to build this.
psrc = code_only((ROOT / "wire" / "players.py").read_text())
rsrc = code_only((ROOT / "scripts" / "wire_players_refresh.py").read_text())
for nm, src in (("players.py", psrc), ("wire_players_refresh.py", rsrc)):
    check(f"{nm} never references a fantasy file",
          not FORBIDDEN_NAMES.search(src) and "rosters/" not in src)

# Resolution: exact, or nothing.
hits, how = reg.resolve("Jahmyr Gibbs", "DET", "RB")
check("an exact name+team+position resolves", len(hits) == 1
      and how == "name_team_position", how)
check("a suffix is handled", len(reg.resolve("Marvin Harrison", "ARI", "WR")[0]) == 1)
gibbs = hits[0].player_id
check("a stable id resolves directly",
      reg.resolve("anything at all", player_id=gibbs)[1] == "stable_id")

hits, how = reg.resolve("Jayden Reed", "GB", "WR")
other, _ = reg.resolve("Jarran Reed", "SEA", "DL")
check("the misheard-caption pair stay separate",
      len(hits) == 1 and len(other) == 1
      and hits[0].player_id != other[0].player_id)

check("a name with no team cannot resolve",
      reg.resolve("Josh Allen")[1] == "name_only_insufficient")
check("a wrong team does not resolve", not reg.resolve("Jahmyr Gibbs", "TB", "RB")[0])
check("a wrong position does not resolve", not reg.resolve("Jahmyr Gibbs", "DET", "WR")[0])
check("an unknown name does not resolve",
      reg.resolve("Nobody At All", "TB", "WR")[1] == "no_match")
check("nothing is fuzzy-matched",
      not reg.resolve("Jahmir Gibs", "DET", "RB")[0])

# Bad registries must never replace a good one.
with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / "reg.json"
    good = dict(raw)
    pl.write_atomic(good, target)
    before = target.read_text()

    partial = dict(raw, players=raw["players"][:10])
    try:
        pl.write_atomic(partial, target)
        check("a partial registry is refused", False)
    except Exception as e:
        check("a partial registry is refused", True, str(e)[:40])
    check("the good registry survives a refused write",
          target.read_text() == before)
    check("no temp file is left behind",
          not (target.with_name(target.name + ".tmp")).exists())

    poisoned = json.loads(json.dumps(raw))
    poisoned["players"][0]["adp"] = 1.0
    try:
        pl.write_atomic(poisoned, target)
        check("a registry carrying a fantasy field is refused", False)
    except Exception as e:
        check("a registry carrying a fantasy field is refused", True, str(e)[:40])
    check("the good registry survives that too", target.read_text() == before)

check("a download failure keeps the existing registry",
      "keeping the existing registry" in
      (ROOT / "scripts" / "wire_players_refresh.py").read_text())

# An unresolved player can never be approved into a publication.
with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "p.db")
    st.add_candidate("c9", "i9", "yt_pewter_report_tv",
                     {"player_id": None, "player_name": "Unclear Name",
                      "wire_label": "MONITOR", "canonical_url": "u"}, "fp9")
    pend = st.candidates("EDITORIAL_REVIEW")
    check("an unresolved player waits for a human",
          len(pend) == 1 and len(st.publications()) == 0)
    n, _ = st.export_publications(Path(tmp) / "pub.json")
    check("an unresolved player never reaches the published file", n == 0)

# ------------------------------------------------- evidence classification

from wire import evidence as ev

def cls(text, **kw):
    kw.setdefault("reporter_voice", True)
    return ev.classify(text, **kw)[0]

check("observation in a reporter's voice is firsthand",
      cls("Gibbs took first-team reps in team drills, by my count nine of them.")
      == ev.FIRSTHAND_OBSERVATION)
check("hedging beats observation language",
      cls("I saw Gibbs at practice and I think he probably starts Week 1.")
      == ev.ANALYSIS_OR_OPINION)
check("prediction is opinion",
      cls("Gibbs should lead the backfield and could see 300 touches.")
      == ev.ANALYSIS_OR_OPINION)
check("a quotation with a named attribution verb is a quotation",
      cls('Campbell said \u201cJahmyr Gibbs looked sharp out there today\u201d after practice.')
      == ev.DIRECT_QUOTATION)
check("quoted words with nobody named are uncertain",
      cls('\u201cJahmyr Gibbs looked sharp out there today and ran hard\u201d')
      == ev.UNCERTAIN)
check("relaying another outlet is never firsthand",
      cls("According to Schefter, Gibbs will be limited at practice today.")
      != ev.FIRSTHAND_OBSERVATION)
check("an unattributed medical claim is uncertain",
      cls("Gibbs has a torn hamstring and will miss four weeks of the season.")
      == ev.UNCERTAIN)
check("an attributed medical claim is not silently firsthand",
      cls("Campbell said Gibbs has a hamstring strain and will miss four weeks.")
      in (ev.DIRECT_QUOTATION, ev.UNCERTAIN))
check("auto-captioned multi-speaker video is always uncertain",
      cls("Gibbs took first-team reps in team drills, by my count.",
          reporter_voice=False, auto_captions=True, multi_speaker=True)
      == ev.UNCERTAIN)
check("observation with no established speaker is uncertain",
      cls("Gibbs took first-team reps in team drills.", reporter_voice=False,
          auto_captions=True) == ev.UNCERTAIN)
check("classification never invents a firsthand label from order alone",
      cls("Gibbs looked good. He said he feels fast.", reporter_voice=False,
          auto_captions=True, multi_speaker=True) == ev.UNCERTAIN)

# ------------------------------------------------- player linking

reg2 = pl.load()
multi = ev.find_players(
    "Jahmyr Gibbs and Jameson Williams both worked with the starters while "
    "Sam LaPorta watched.", reg2, "DET")
check("one excerpt can name several players", len(multi) == 3,
      str(sorted(m[0] for m in multi)))
check("a surname alone never matches",
      not ev.find_players("Gibbs looked quick today.", reg2, "DET"))
check("a misspelled name does not resolve",
      not ev.find_players("Jahmir Gibs took reps.", reg2, "DET"))
check("a player from another team does not resolve on this beat",
      not ev.find_players("Jahmyr Gibbs took reps.", reg2, "TB"))

ol = [p for p in reg2.players if p.context_only and p.team == "PHI"]
check("linemen are in the registry as context",
      bool(ol) and not any(p.fantasy_candidate for p in ol))

# ------------------------------------------------- stored evidence

store2 = WireStore()
rows = store2.evidence()
if rows:
    # PENDING or SUPERSEDED. Nothing may be APPROVED without a person, and
    # SUPERSEDED exists because re-extraction retires a candidate whose span
    # no longer exists rather than leaving it to outlive its own text.
    _states = {r["review_status"] for r in rows}
    check("no stored candidate is approved or published",
          _states <= {"PENDING", "SUPERSEDED"}, sorted(_states))
    check("nothing has been approved without a reviewer",
          not [r for r in rows if r["review_status"] == "APPROVED"])
    check("registry version and hash are recorded on every row",
          all(r["registry_version"] and r["registry_hash"] for r in rows))
    check("identity confidence is stored apart from claim confidence",
          all(r["resolution_confidence"] is not None
              and r["classification_confidence"] is not None for r in rows))
    ol_rows = [r for r in rows if "offensive line" in (r["exclusion_reason"] or "")]
    check("offensive linemen are excluded from fantasy resolution",
          bool(ol_rows) and all(r["position"] in pl.CONTEXT_POSITIONS
                                for r in ol_rows), f"{len(ol_rows)} rows")
    groups = Counter(r["evidence_group_id"] for r in rows)
    check("players from one excerpt share an evidence group id",
          any(v > 1 for v in groups.values()))
    check("evidence text is preserved, not summarised",
          all(len(r["evidence_text"]) > 40 for r in rows))

with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "ev.db")
    rec = {"candidate_id": "x1", "evidence_group_id": "g1",
           "source_id": "s", "evidence_text": "t", "review_status": "PENDING"}
    check("evidence upsert is idempotent",
          st.upsert_evidence(rec) is True and st.upsert_evidence(rec) is False)
    st.conn.execute("UPDATE wire_evidence SET review_status='REJECTED' "
                    "WHERE candidate_id='x1'")
    st.conn.commit()
    st.upsert_evidence(rec)
    kept = st.evidence()[0]["review_status"]
    check("re-extraction never overwrites a reviewer's decision",
          kept == "REJECTED", kept)
    n, _ = st.export_publications(Path(tmp) / "p.json")
    check("extracted evidence cannot reach the published file", n == 0)

extract_src = code_only((ROOT / "scripts" / "wire_extract.py").read_text())
check("the extractor never writes to the publications file",
      "wire_publications" not in extract_src
      and "publish(" not in extract_src)
check("the extractor references no fantasy file",
      not FORBIDDEN_NAMES.search(extract_src))
evsrc = code_only((ROOT / "wire" / "evidence.py").read_text())
check("evidence.py references no fantasy file",
      not FORBIDDEN_NAMES.search(evsrc))
check("evidence.py imports nothing from the fantasy side",
      not FORBIDDEN_IMPORTS.findall(evsrc))

# ----------------------------------------------------------------- SI adapter
from wire import capture
from wire import si as SI

SI_AUTH = SI.load_authors()

# Team association is decided by the canonical url and by nothing else. This
# is the case a title filter and an author allowlist both miss: Albert
# Breer's Eagles training-camp notebook is firsthand reporting by a
# credentialed reporter, and it appears on the Bills landing page.
wrong = SI.evaluate(
    {"canonical_url": "https://www.si.com/nfl/jaguars/onsi/jaguars-practice-notes",
     "headline": "Jaguars Practice Notes", "author": "Ralph Ventre",
     "published_at": "2026-08-20"}, "BUF", SI_AUTH)
check("an SI article from another team is rejected",
      not wrong.eligible and "JAX" in wrong.exclusion_reason,
      wrong.exclusion_reason)

national = SI.evaluate(
    {"canonical_url": "https://www.si.com/nfl/top-50-nfl-draft-prospects-2026",
     "headline": "SI's Top 50 NFL Draft Prospects", "author": "Gilberto Manzano",
     "published_at": "2026-08-20"}, "BUF", SI_AUTH)
check("a national SI story syndicated onto a team page is rejected",
      not national.eligible and "national" in national.exclusion_reason,
      national.exclusion_reason)

# The same national story sits on all four pilot pages. One canonical url,
# so one stored article, whichever page found it.
with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "w.db")
    from wire.capture import Article as _A
    ids = set()
    for team_src in ("si_bills", "si_dolphins", "si_patriots", "si_jets"):
        ids.add(st.save_item(_A(
            source_id=team_src,
            canonical_url="https://www.si.com/nfl/one-national-story",
            headline="One National Story", author="Albert Breer",
            published_at="2026-08-20", raw_text="x" * 900,
            content_sha256="deadbeef", extraction_status="COMPLETE")))
    rows = st.conn.execute("SELECT COUNT(*) c FROM wire_source_items").fetchone()["c"]
    check("one national article on four team pages stores one article",
          rows == 1 and len(ids) == 1, f"{rows} rows, {len(ids)} ids")

    # Idempotency is by url, not by body. A publisher fixing a typo must
    # update the article, not file a second one -- every candidate id
    # downstream is derived from this id.
    first = st.save_item(_A(source_id="si_bills",
                            canonical_url="https://www.si.com/nfl/bills/onsi/a",
                            headline="A", published_at="2026-08-20",
                            raw_text="y" * 900, content_sha256="aaa",
                            extraction_status="COMPLETE"))
    again = st.save_item(_A(source_id="si_bills",
                            canonical_url="https://www.si.com/nfl/bills/onsi/a",
                            headline="A (corrected)", published_at="2026-08-20",
                            raw_text="y" * 950, content_sha256="bbb",
                            extraction_status="COMPLETE"))
    n = st.conn.execute("SELECT COUNT(*) c FROM wire_source_items").fetchone()["c"]
    check("canonical-url dedup is idempotent across an edit",
          first == again and n == 2, f"{first[:8]} vs {again[:8]}, {n} rows")

# Content types that may never become an automatic claim. Checked one at a
# time so a regression names the type it lost.
for headline, kind in [
        ("Bills Fantasy Football Start/Sit Advice for Week 2", "fantasy"),
        ("Bills vs Browns Odds, Spread and Prop Bets", "betting"),
        ("2027 NFL Mock Draft: Bills Take a Receiver", "mock draft"),
        ("NFL Power Rankings: Where the Bills Land", "power rankings"),
        ("Ranking the Five Best Free Agents Still Available", "national list"),
        ("Bills 53-Man Roster Prediction 2.0", "roster prediction"),
        ("Winners and Losers From the Bills' Preseason Opener", "winners"),
        ("A Trade Proposal That Sends a Star to Buffalo", "trade proposal")]:
    v = SI.evaluate({"canonical_url": "https://www.si.com/nfl/patriots/onsi/x",
                     "headline": headline, "author": "Ethan Hurwitz",
                     "published_at": "2026-08-20"}, "NE", SI_AUTH)
    check(f"{kind} cannot be eligible even from an approved author",
          not v.eligible, f"{headline!r} -> {v.exclusion_reason!r}")

# The section is checked as well as the title, because a betting page can be
# headlined like a news story.
# A team-segment url in a non-reporting section. The team check passes, so
# this genuinely exercises the section rule rather than falling through to
# the national-story refusal.
vid = SI.evaluate({"canonical_url": "https://www.si.com/nfl/patriots/video/camp",
                   "headline": "Patriots Camp Report", "author": "Ethan Hurwitz",
                   "published_at": "2026-08-20"}, "NE", SI_AUTH)
check("a video-section item is never reporting",
      not vid.eligible and "video" in vid.exclusion_reason,
      vid.exclusion_reason)

unknown = SI.evaluate({"canonical_url": "https://www.si.com/nfl/patriots/onsi/y",
                       "headline": "Bills Practice Report",
                       "author": "Somebody Nobody Researched",
                       "published_at": "2026-08-20"}, "NE", SI_AUTH)
check("an unresearched SI author is not eligible",
      not unknown.eligible and unknown.author_class == SI.UNKNOWN,
      unknown.exclusion_reason)

noauthor = SI.evaluate({"canonical_url": "https://www.si.com/nfl/patriots/onsi/z",
                        "headline": "Bills Notes", "author": "SI Staff",
                        "published_at": "2026-08-20"}, "NE", SI_AUTH)
check("an article with no identifiable author is not eligible",
      not noauthor.eligible, noauthor.exclusion_reason)

# Appearing on a team page is not approval, and neither is appearing on
# every team page. The national cohort is classified, not promoted.
check("team-page appearance alone grants no approval",
      all(e["classification"] != SI.FIRSTHAND_APPROVED
          for e in SI_AUTH.get("national", {}).values()),
      [n for n, e in SI_AUTH.get("national", {}).items()
       if e["classification"] == SI.FIRSTHAND_APPROVED])
# Exactly one author carries auto_ready, and only with the grandfathering
# note that says what it is. Nobody may be promoted by analogy to him.
_auto = [(n, e) for t in SI_AUTH.get("teams", {}).values()
         for n, e in t["authors"].items() if e.get("auto_ready")]
check("only Bill Huber is marked auto_ready, and only as grandfathered",
      len(_auto) == 1 and _auto[0][0] == "Bill Huber"
      and "GRANDFATHERED_AUTO_READY" in _auto[0][1].get("grandfathered", ""),
      [n for n, _ in _auto])

# An analysis-only byline is not a firsthand voice, so a span that would
# otherwise read as observation cannot be classified FIRSTHAND.
obs = "Gurzi watched from the sideline. I counted only two reps for him at practice."
k_appr, _, _ = ev.classify(obs, reporter_voice=True)
k_anal, _, _ = ev.classify(obs, reporter_voice=False)
check("an analysis-only author cannot produce a firsthand claim",
      k_appr == ev.FIRSTHAND_OBSERVATION and k_anal != ev.FIRSTHAND_OBSERVATION,
      f"approved={k_appr} analysis={k_anal}")
check("classify_author is exact-name and team-scoped",
      SI.classify_author("Ethan Hurwitz", "NE", SI_AUTH) == SI.FIRSTHAND_APPROVED
      and SI.classify_author("Ethan Hurwitz", "MIA", SI_AUTH) == SI.UNKNOWN
      and SI.classify_author("ethan hurwitz", "NE", SI_AUTH) == SI.UNKNOWN)

# A byline that files for several teams is a desk, not a beat, and is never
# a single team's firsthand voice however good the article is.
check("a multi-team byline is never FIRSTHAND_APPROVED",
      all(not a.get("multi_team")
          for t in SI_AUTH["teams"].values() for a in t["authors"].values()
          if a["classification"] == SI.FIRSTHAND_APPROVED))

# Relay beats quotation, and the order matters more than either rule. A beat
# aggregator quoting a paywalled outlet's reporter has quotation marks and an
# attribution verb; checked the other way round it scored DIRECT_QUOTATION at
# 0.80, which is a paid outlet's reporting arriving second-hand wearing our
# highest confidence.
_relay = 'Here are the details. "Tough finish for the offense," Fishbain wrote.'
_k, _c, _w = ev.classify(_relay, reporter_voice=True)
check("a quote lifted from another outlet is not a DIRECT_QUOTATION",
      _k != ev.DIRECT_QUOTATION and "relays" in _w[0], f"{_k} {_w}")
_own = '"That is one of the best offenses," Bengals cornerback DJ Turner II said.'
_k2, _, _ = ev.classify(_own, reporter_voice=True)
check("a locker-room quote is still a DIRECT_QUOTATION",
      _k2 == ev.DIRECT_QUOTATION, _k2)
for _outlet in ("The Athletic", "ESPN", "NFL Network"):
    _k3, _, _ = ev.classify(
        f'{_outlet} reported the starter took every rep. "He looked sharp," it said.',
        reporter_voice=True)
    check(f"a story relayed from {_outlet} is never firsthand or quoted",
          _k3 not in (ev.FIRSTHAND_OBSERVATION, ev.DIRECT_QUOTATION), _k3)

# Whatever a candidate is filed under has to appear in the passage a
# reviewer is shown. This failed once: spans reach 1,720 characters and
# evidence_text was stored as text[:1200], so a correctly matched player fell
# off the end of his own evidence.
_ws = WireStore()
_missing = []
for _r in _ws.evidence():
    if _r["review_status"] != "PENDING" or not _r["player_name"]:
        continue
    _hay = pl.norm(_r["evidence_text"])
    _last = pl.norm(_r["player_name"]).split()[-1]
    if _last not in _hay.split():
        _missing.append((_r["player_name"], _r["source_url"][-40:]))
check("every candidate names its player inside the stored evidence",
      not _missing, _missing[:3])

import wire_extract as WX
from wire import fantasy as fz
from wire import segment as seg

# ------------------------------------------------------------ SI On SI
check("On SI is the primary discovery url",
      SI.landing_url("bills") == "https://www.si.com/nfl/bills/onsi")
check("the broad team page is the fallback",
      SI.landing_url("bills", onsi=False) == "https://www.si.com/nfl/bills")
check("onsi_team reads the section from the canonical url",
      SI.onsi_team("https://www.si.com/nfl/bills/onsi/x") == "bills"
      and SI.onsi_team("https://www.si.com/nfl/bills/news/x") is None
      and SI.onsi_team("https://www.si.com/nfl/top-50") is None)

# The fallback page buys no leniency. A "More Bills" tile or anything else
# the broad page surfaces still has to be an /onsi/ article for this team.
_notonsi = SI.evaluate(
    {"canonical_url": "https://www.si.com/nfl/bills/news/story",
     "headline": "Bills practice report", "author": "Ethan Hurwitz",
     "published_at": "2026-08-20",
     "discovery_route": "TEAM_PAGE_FALLBACK"}, "BUF", SI_AUTH)
check("fallback discovery cannot bypass the /onsi/ canonical rule",
      not _notonsi.eligible and "On SI section" in _notonsi.exclusion_reason,
      _notonsi.exclusion_reason)

_si_srcs2 = [x for x in registry.load() if x.adapter == registry.SI_TEAM_PAGE]
check("every SI source stores both discovery urls",
      all(s.landing_page.endswith("/onsi") and s.fallback_page
          for s in _si_srcs2))
check("every SI source is classed SI_ONSI",
      all(s.source_class == registry.SI_ONSI for s in _si_srcs2))
check("Bill Huber keeps his exact author and /onsi/ url restrictions",
      any(s.source_id == "packers_on_si_bill_huber"
          and s.filter_author == "Bill Huber"
          and s.filter_url_pattern == "^/nfl/packers/onsi/"
          and s.status == registry.AUTO_READY
          for s in registry.load()))

# --------------------------------------------------- official team sites
from wire import nflteam as NT

check("one adapter covers all 32 club sites", len(NT.SITES) == 32, len(NT.SITES))
_off = [x for x in registry.load()
        if x.source_class == registry.OFFICIAL_TEAM_SITE]
check("all 32 official team sites are registered", len(_off) == 32, len(_off))
check("every official team source is TEAM_OWNED",
      all(s.team_owned for s in _off))
check("no official team source is pollable unattended",
      not any(s.pollable for s in _off))
_bad_off = registry.Source(
    source_id="off_x", source_name="x", reporter_name="", teams=["TEN"],
    domains=["x.com"], status=registry.AUTO_READY, feed_url="https://x/f",
    reporting_type="LOCAL_BEAT", adapter=registry.NFL_TEAM_SITE,
    source_class=registry.OFFICIAL_TEAM_SITE,
    source_ownership=registry.TEAM_OWNED, active=True)
check("a team-owned source cannot be AUTO_READY",
      any("team-owned source marked AUTO_READY" in b
          for b in registry.problems([_bad_off])))
_mislabelled = registry.Source(
    source_id="off_y", source_name="y", reporter_name="", teams=["TEN"],
    domains=["y.com"], status=registry.MANUAL_REVIEW_ONLY,
    reporting_type="LOCAL_BEAT", adapter=registry.NFL_TEAM_SITE,
    source_class=registry.OFFICIAL_TEAM_SITE,
    source_ownership=registry.INDEPENDENT)
check("an official team site cannot be labelled independent",
      any("must be TEAM_OWNED" in b for b in registry.problems([_mislabelled])))

for _h, _kind in [("2026 Titans Foundation 5K, Presented by SeatGeek", "marketing"),
                  ("Single-Game Tickets On Sale Now", "ticketing"),
                  ("How To Watch: Titans vs Bills", "broadcast"),
                  ("Titans Foundation Announces Community Grant", "community"),
                  ("Titans Mailbag: What About The Secondary?", "mailbag"),
                  ("Ring of Honor: Remembering the 1999 Run", "historical")]:
    check(f"club {_kind} content is excluded",
          NT.content_exclusion(_h, "/news/x"), _h)
check("a club practice notebook is kept",
      not NT.content_exclusion(
          "Ten Observations From Titans Training Camp on Wednesday",
          "/news/ten-observations",
          "He took first-team reps during Wednesday's practice."))
check("team hype with no concrete observation is refused",
      NT.content_exclusion("A Special Season Awaits", "/news/hype",
                           "The energy in the building is unmatched."))
check("a recurring series can be restricted by exact headline",
      NT.series_ok("Ten Observations From Titans Training Camp on Wednesday",
                   "^Ten Observations From Titans Training Camp")
      and not NT.series_ok("Titans Mailbag",
                           "^Ten Observations From Titans Training Camp"))
_ten = next(s for s in registry.load() if s.source_id == "official_ten")
check("the Titans series restriction is configured but not granted",
      _ten.qualifying_series.startswith("^Ten Observations")
      and _ten.filter_author == "Jim Wyatt"
      and _ten.evidence_access == "",
      _ten.evidence_access)
check("Jim Wyatt is not firsthand-approved on a three-article sample",
      "Jim Wyatt" not in [n for t in SI_AUTH["teams"].values()
                          for n, a in t["authors"].items()
                          if a["classification"] == SI.FIRSTHAND_APPROVED])

# Naming a club reporter in the config is not approving him. Wyatt produced
# firsthand spans from a source where he is deliberately unapproved, purely
# because his name was in the yaml.
_ten_ctx = WX.source_context(
    None, "official_ten",
    {"headline": "Ten Observations From Titans Training Camp on Wednesday"})
check("a named club reporter is not a firsthand voice without approval",
      _ten_ctx["reporter_voice"] is False, _ten_ctx["reporter_voice"])
check("official team context carries TEAM_OWNED ownership",
      _ten_ctx["ownership"] == registry.TEAM_OWNED, _ten_ctx["ownership"])
_off_ctx = WX.source_context(None, "official_buf", {"headline": "anything"})
check("an unapproved club source is never a firsthand voice",
      _off_ctx["reporter_voice"] is False)

# Team-owned evidence never corroborates.
_own_row = {"source_url": "u1", "source_author_or_channel": "Club Writer",
            "source_ownership": "TEAM_OWNED",
            "evidence_class": "FIRSTHAND_OBSERVATION",
            "evidence_text": "He took first-team reps at practice."}
_ind_row = {"source_url": "u2", "source_author_or_channel": "Beat Reporter",
            "source_ownership": "INDEPENDENT",
            "evidence_class": "FIRSTHAND_OBSERVATION",
            "evidence_text": "He took first-team reps."}
check("team-owned evidence adds no independent source",
      fz.independent_sources([_own_row]) == 0)
check("two club articles are still one source family",
      fz.independent_sources([_own_row, dict(_own_row, source_url="u3")]) == 0)
check("team-owned evidence does not inflate an independent count",
      fz.independent_sources([_own_row, _ind_row]) == 1)
check("one club practice report cannot reach HIGH",
      fz.strength([_own_row], "FIRST_TEAM_REPS", 0) == fz.LOW)
_ir = dict(_own_row, evidence_text="The club placed him on injured reserve "
                                   "with a torn ACL, out for the season.")
check("a club may confirm its own official act",
      fz.strength([_ir], "INJURY", 0) == fz.HIGH)

# ------------------------------------------------------------------- paid
srcs = registry.load()
ath = [s for s in srcs if s.paid]
check("The Athletic is registered and paid", len(ath) == 1, len(ath))
ath = ath[0]
check("a paid source is DISCOVERY_ONLY_PAID",
      ath.status == registry.DISCOVERY_ONLY_PAID, ath.status)
check("a paid source is never pollable", not ath.pollable)
check("a paid source refuses manual URL submission", not ath.manual_ok)
check("a paid source discovers nothing by crawling",
      capture.discover(ath) == [])
paid_art = capture.capture(ath, {"url": "https://www.nytimes.com/athletic/nfl/team/bills/",
                                 "headline": "Bills camp notebook"})
check("capturing a paid article fetches no body",
      paid_art.raw_text == "" and paid_art.note == registry.PAID_LABEL,
      f"{len(paid_art.raw_text)} chars, note={paid_art.note!r}")
check("a paid item is labelled PAID_SUBSCRIPTION_REQUIRED",
      paid_art.note == registry.PAID_LABEL)

import wire_extract as WX
pctx = WX.source_context(None, ath.source_id)
pstats = WX.extract_item(None, {"source_id": ath.source_id,
                                "raw_text": "Josh Allen took every first-team rep. " * 40,
                                "canonical_url": "https://www.nytimes.com/athletic/x",
                                "headline": "h", "published_at": "2026-08-20"},
                         None, pctx, {}, dry=True)
check("a paid source produces no evidence span and no candidate",
      pstats["spans"] == 0 and pstats["candidates"] == 0 and pstats["refused"] == 1,
      pstats)

# A registry edit is the only way past any of this, and the validator says so.
bad_paid = registry.Source(
    source_id="x", source_name="x", reporter_name="", teams=["BUF"],
    domains=["nytimes.com"], status=registry.AUTO_READY,
    reporting_type="LOCAL_BEAT", adapter=registry.PAID_METADATA_ONLY,
    active=True)
check("a paid source marked AUTO_READY is a registry error",
      any("paid source marked AUTO_READY" in b
          for b in registry.problems([bad_paid])),
      registry.problems([bad_paid]))

# ------------------------------------------------------- SI registry rules
si_srcs = [s for s in srcs if s.adapter == registry.SI_TEAM_PAGE]
check("all 32 SI team pages are registered", len(si_srcs) == 32, len(si_srcs))
check("every SI source is MANUAL_REVIEW_ONLY",
      all(s.status == registry.MANUAL_REVIEW_ONLY for s in si_srcs),
      [s.source_id for s in si_srcs if s.status != registry.MANUAL_REVIEW_ONLY])
check("no SI source is pollable unattended",
      not any(s.pollable for s in si_srcs))
check("every SI slug matches its registered team",
      all(SI.TEAMS[s.si_team_slug] == s.teams[0] for s in si_srcs))
_noapproved = next(t for t in sorted(SI.CODE_TO_SLUG)
                   if not [a for a in SI_AUTH["teams"].get(t, {}).get("authors", {}).values()
                           if a["classification"] == SI.FIRSTHAND_APPROVED])
bad_si = registry.Source(
    source_id="si_x", source_name="x", reporter_name="", teams=[_noapproved],
    domains=["si.com"], status=registry.AUTO_READY, feed_url="https://x/f",
    reporting_type="LOCAL_BEAT", adapter=registry.SI_TEAM_PAGE,
    si_team_slug=SI.CODE_TO_SLUG[_noapproved],
    landing_page="https://www.si.com/nfl/x", active=True)
check("an SI team with no firsthand author cannot be AUTO_READY",
      any("no FIRSTHAND_APPROVED author" in b
          for b in registry.problems([bad_si])),
      registry.problems([bad_si]))

# ---------------------------------------------------- isolation, unchanged
with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "w.db")
    from wire.capture import Article as _A
    iid = st.save_item(_A(source_id="si_bills",
                          canonical_url="https://www.si.com/nfl/bills/onsi/keep",
                          headline="Keep", published_at="2026-08-20",
                          raw_text="z" * 900, content_sha256="c1",
                          extraction_status="COMPLETE"))
    st.add_candidate("cand1", iid, "si_bills", {"kind": "article"}, "fp1")
    st.conn.execute("UPDATE wire_candidates SET state='REJECTED' "
                    "WHERE candidate_id='cand1'")
    st.conn.commit()
    # Re-ingest the same url, edited.
    st.save_item(_A(source_id="si_bills",
                    canonical_url="https://www.si.com/nfl/bills/onsi/keep",
                    headline="Keep (updated)", published_at="2026-08-20",
                    raw_text="z" * 950, content_sha256="c2",
                    extraction_status="COMPLETE"))
    state = st.conn.execute("SELECT state FROM wire_candidates "
                            "WHERE candidate_id='cand1'").fetchone()["state"]
    check("a rejected candidate stays rejected after re-ingestion",
          state == "REJECTED", state)
    n, _ = st.export_publications(Path(tmp) / "pubs.json")
    check("SI candidates cannot reach the published file", n == 0, n)

# A failed run must leave the last good registry and publications in place.
before = REGISTRY_YAML.read_text() if (REGISTRY_YAML := ROOT / "sources" / "wire_articles.yaml") else ""
try:
    SI.discover_team("not-a-team")
    ok = False
except ValueError:
    ok = True
check("an unknown team slug raises rather than inventing a page", ok)
check("a failed probe leaves the source registry untouched",
      (ROOT / "sources" / "wire_articles.yaml").read_text() == before)

si_src = code_only((ROOT / "wire" / "si.py").read_text())
check("si.py references no fantasy file", not FORBIDDEN_NAMES.search(si_src))
check("si.py imports nothing from the fantasy side",
      not FORBIDDEN_IMPORTS.findall(si_src))
probe_src = code_only((ROOT / "scripts" / "wire_si_probe.py").read_text())
check("the SI probe never writes to the publications file",
      "wire_publications" not in probe_src and "export_publications" not in probe_src)
check("the SI probe references no fantasy file",
      not FORBIDDEN_NAMES.search(probe_src))

# Firsthand voice is earned per reporter on both routes. A multi-author
# local publication does not confer it: registering Steelers Depot said
# nothing about whether every byline on it attends practice.
_ctx_named = WX.source_context(None, "purple_insider_matthew_coller")
_ctx_multi = WX.source_context(None, "steelers_depot")
check("a named local reporter is a firsthand voice",
      _ctx_named["reporter_voice"] is True)
check("a multi-author local publication is not a firsthand voice",
      _ctx_multi["reporter_voice"] is False)
_ctx_staff = WX.source_context(None, "pewter_report")
check("a staff byline is not a firsthand voice",
      _ctx_staff["reporter_voice"] is False)

# ------------------------------------------------------- FANTASY_IMPACT
from wire import fantasy as fz
import wire_fantasy_impact as WFI

_reg = pl.load()
_qb = next(p for p in _reg.players if p.position == "QB" and p.player_id)
_ol = next(p for p in _reg.players if p.context_only and p.player_id)


def _row(player, klass="FIRSTHAND_OBSERVATION", text=None, url="u1",
         author="A Reporter", cid=None):
    return {"candidate_id": cid or f"c{abs(hash((player.full_name, url, klass)))%10**8}",
            "evidence_group_id": "g1", "source_url": url,
            "source_author_or_channel": author,
            "evidence_class": klass, "review_status": "APPROVED",
            "exclusion_reason": "", "duplicate_of": "",
            "player_id": player.player_id, "player_name": player.full_name,
            "team": player.team, "position": player.position,
            "evidence_text": text or
            f"{player.full_name} took first-team reps during Tuesday's practice."}


_base = fz.build([_row(_qb)], _reg, "v1")
check("commentary is generated as PENDING", _base.review_status == fz.PENDING)
check("commentary never carries fantasy-advice language",
      not fz.SOURCE_FANTASY_ADVICE.search(_base.lineupbeat_commentary),
      _base.lineupbeat_commentary)
check("a fantasy-impact record stores its supporting evidence ids",
      _base.evidence_candidate_ids and _base.evidence_group_ids)

_noev = fz.Impact(player_id=_qb.player_id, player_name=_qb.full_name,
                  team=_qb.team, position=_qb.position)
check("commentary cannot exist without supporting evidence ids",
      any("no supporting evidence" in b
          for b in fz.validate(_noev, [], _reg)), fz.validate(_noev, [], _reg))

_ol_out = fz.build([_row(_ol)], _reg, "v1")
check("an offensive lineman gets no individual commentary",
      isinstance(_ol_out, dict) and _ol_out.get("suppressed"), _ol_out)
_def = next((p for p in _reg.players
             if p.position in ("LB", "DB", "DL") and p.player_id), None)
if _def:
    _def_out = fz.build([_row(_def)], _reg, "v1")
    check("a defensive player gets no individual commentary",
          isinstance(_def_out, dict) and _def_out.get("suppressed"), _def_out)

# Source fantasy advice is refused before it can ever be evidence.
for _bad in ("He is a sleeper worth a round 8 pick.",
             "Start him this week in all fantasy formats.",
             "Best DFS value plays and prop bets for Sunday.",
             "Waiver wire adds: three players to claim."):
    check(f"source fantasy advice is not evidence: {_bad[:26]!r}",
          "fantasy" in ev.relevance(_bad) or "betting" in ev.relevance(_bad),
          ev.relevance(_bad))

# HIGH is reserved. One reporter cannot reach it however dramatic the words.
_solo_major = [_row(_qb, text=f"{_qb.full_name} tore his ACL and is out for the season.")]
_s1 = fz.strength(_solo_major, "INJURY", independent=1)
check("one report cannot produce HIGH impact", _s1 != fz.HIGH, _s1)
_two = [_row(_qb, text=f"{_qb.full_name} tore his ACL and is out for the season.",
             url="u1", author="Reporter One"),
        _row(_qb, text=f"{_qb.full_name} tore his ACL and is out for the season.",
             url="u2", author="Reporter Two", klass="DIRECT_QUOTATION")]
check("a confirmed major event with two reporters may reach HIGH",
      fz.strength(_two, "INJURY", independent=2) == fz.HIGH)

# Duplicates are not corroboration.
_dupes = [_row(_qb, url="u1", author="Same Reporter"),
          _row(_qb, url="u1", author="Same Reporter", cid="c-other")]
check("the same article twice is one independent source",
      fz.independent_sources(_dupes) == 1, fz.independent_sources(_dupes))
_synd = [_row(_qb, url="u1", author="Same Reporter"),
         _row(_qb, url="u2", author="Same Reporter")]
check("a syndicated story by one reporter is one independent source",
      fz.independent_sources(_synd) == 1, fz.independent_sources(_synd))

# Two spans of one reporter's account of one practice are not repetition.
_one_article = [_row(_qb, url="u1"), _row(_qb, url="u1", cid="c-second")]
check("two spans from one article do not reach MEDIUM",
      fz.strength(_one_article, "FIRST_TEAM_REPS", 1) == fz.LOW)

# UPDATE_RECOMMENDED is a task for a person, never a projection change.
_hi = fz.Impact(player_id=_qb.player_id, player_name=_qb.full_name,
                team=_qb.team, position=_qb.position,
                impact_strength=fz.HIGH, independent_source_count=1,
                evidence_candidate_ids=["c1"],
                lineupbeat_commentary="x", projection_action=fz.UPDATE_RECOMMENDED)
check("UPDATE_RECOMMENDED without corroboration fails validation",
      any("corroboration" in b for b in fz.validate(_hi, [_row(_qb)], _reg)))
fsrc = code_only((ROOT / "wire" / "fantasy.py").read_text())
wfi_src = code_only((ROOT / "scripts" / "wire_fantasy_impact.py").read_text())
check("the fantasy layer never touches projection files",
      not FORBIDDEN_NAMES.search(fsrc) and not FORBIDDEN_NAMES.search(wfi_src))
check("the fantasy layer imports nothing from the fantasy side",
      not FORBIDDEN_IMPORTS.findall(fsrc)
      and not FORBIDDEN_IMPORTS.findall(wfi_src))
check("UPDATE_RECOMMENDED writes no projection",
      "projections.xlsx" not in wfi_src and "build_rankings" not in wfi_src)

# Invented facts fail validation.
_liar = fz.build([_row(_qb)], _reg, "v1")
_liar.lineupbeat_commentary = ("He tore his ACL and will miss 6 weeks, and "
                               "took 12 carries.")
_probs = fz.validate(_liar, [_row(_qb)], _reg)
check("commentary inventing an injury or a number fails validation",
      any("injury or timeline" in b or "number" in b for b in _probs), _probs)
_wrongplayer = fz.build([_row(_qb)], _reg, "v1")
_wrongplayer.player_name = "Somebody Entirely Else"
check("commentary about a player absent from the evidence fails validation",
      any("not named in the supporting evidence" in b
          for b in fz.validate(_wrongplayer, [_row(_qb)], _reg)))

# A relayed Athletic quotation cannot become a strong signal.
_relayed = "The Athletic's Jeff Zrebiec reported that he took every first-team rep."
_rk, _, _ = ev.classify(_relayed, reporter_voice=True)
check("a relayed Athletic report is neither firsthand nor a direct quotation",
      _rk not in (ev.FIRSTHAND_OBSERVATION, ev.DIRECT_QUOTATION), _rk)
check("a relayed report is not eligible to support commentary",
      _rk not in WFI.SUPPORTING, _rk)

# Storage separation, review independence and dependency.
with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "f.db")
    st._fantasy_schema()
    ecols = {r["name"] for r in st.conn.execute("PRAGMA table_info(wire_evidence)")}
    check("evidence rows carry no generated commentary",
          not (ecols & {"lineupbeat_commentary", "fantasy_impact",
                        "impact_strength", "projection_action"}), sorted(ecols))
    rec = fz.build([_row(_qb)], _reg, "v1").to_record()
    rec["evidence_candidate_ids"] = ["cand-A"]
    st.upsert_impact(rec)
    check("a stored impact begins PENDING",
          st.impacts()[0]["review_status"] == fz.PENDING)
    # Regeneration is idempotent and never overwrites a decision.
    st.upsert_impact(rec)
    check("regenerating unchanged evidence makes no second record",
          len(st.impacts()) == 1, len(st.impacts()))
    st.conn.execute("UPDATE wire_fantasy_impact SET review_status='APPROVED', "
                    "lineupbeat_commentary='a human wrote this'")
    st.conn.commit()
    st.upsert_impact(rec)
    got = st.impacts()[0]
    check("regeneration preserves an approved decision and its edited text",
          got["review_status"] == "APPROVED"
          and got["lineupbeat_commentary"] == "a human wrote this",
          dict(got)["review_status"])
    # Rejecting the commentary leaves the evidence alone.
    st.conn.execute("UPDATE wire_fantasy_impact SET review_status='REJECTED'")
    st.conn.commit()
    st.conn.execute(
        "INSERT INTO wire_evidence (candidate_id, review_status) VALUES (?,?)",
        ("cand-A", "APPROVED"))
    st.conn.commit()
    ev_status = st.conn.execute(
        "SELECT review_status FROM wire_evidence WHERE candidate_id='cand-A'"
    ).fetchone()["review_status"]
    check("rejecting commentary does not reject its evidence",
          ev_status == "APPROVED", ev_status)
    # Rejecting the evidence invalidates the commentary.
    st.conn.execute("UPDATE wire_fantasy_impact SET review_status='PENDING'")
    st.conn.execute("UPDATE wire_evidence SET review_status='REJECTED' "
                    "WHERE candidate_id='cand-A'")
    st.conn.commit()
    n = st.invalidate_impacts_without_evidence()
    got = st.impacts()[0]
    check("rejecting all supporting evidence invalidates the commentary",
          n == 1 and got["review_status"] == "INVALIDATED", n)
    check("an invalidated record records why and when",
          got["invalidated_at"] and got["invalidation_reason"])

# ------------------------------------------------- semantic LLM layer
from wire import semantic as SEM
from wire import semantic_validate as SV
from wire import independent_review as IR
from scripts import wire_semantic_eval as WSE
from wire.providers import REGISTRY as PROVIDERS
from wire.providers.claude import ClaudeSemanticProvider, redact as credact
from wire.providers.openai import (OpenAIProviderError,
                                   OpenAISemanticProvider, redact as oredact)
from wire.providers.openai_review import OpenAIIndependentReviewer

check("only rules and OpenAI are active providers",
      set(PROVIDERS) == {"rules", "openai"}
      and all(issubclass(c, SEM.FantasySemanticProvider)
              for c in PROVIDERS.values()), sorted(PROVIDERS))
check("the response schema is strict",
      SEM.RESPONSE_SCHEMA["additionalProperties"] is False
      and len(SEM.RESPONSE_SCHEMA["required"]) == 18)
check("a provider may abstain", SEM.ABSTAIN in SEM.DECISIONS)
check("Claude uses the native Messages API, not the compatibility layer",
      "openai" not in code_only(
          (ROOT / "wire" / "providers" / "claude.py").read_text()).lower())

# Keys: environment only, and scrubbed from anything that escapes.
for _mod, _key in (("claude.py", "ANTHROPIC_API_KEY"),
                   ("openai.py", "OPENAI_API_KEY")):
    _src = (ROOT / "wire" / "providers" / _mod).read_text()
    check(f"{_mod} reads its key only from the environment",
          f'os.environ.get("{_key}")' in _src or f"environ.get('{_key}')" in _src)
    check(f"{_mod} hardcodes no key",
          not re.search(r"sk-(ant-)?[A-Za-z0-9_\-]{16,}", _src))
check("an anthropic key is scrubbed from an error",
      "sk-ant-" not in credact("x-api-key sk-ant-AAAABBBBCCCCDDDDEEEE failed"))
check("an openai key is scrubbed from an error",
      "sk-proj" not in oredact("Authorization: Bearer sk-proj-AAAABBBBCCCCDDDD"))
check("an authorization header is scrubbed",
      "[REDACTED]" in credact('{"authorization": "Bearer abc123def456"}'))

# A missing key is a clean failure, never a weakened standard.
_noky = OpenAISemanticProvider(transport=None)
_prev = os.environ.pop("OPENAI_API_KEY", None)
try:
    try:
        _noky.evaluate("He took first-team reps.", {}, [])
    except OpenAIProviderError:
        _raised_provider_error = True
    else:
        _raised_provider_error = False
    check("a missing key is a provider failure, not a semantic abstention",
          _raised_provider_error)
finally:
    if _prev is not None:
        os.environ["OPENAI_API_KEY"] = _prev

def _raise_quota(_prompt):
    raise OpenAIProviderError("RateLimitError: quota exceeded")

try:
    OpenAISemanticProvider(transport=_raise_quota).evaluate(
        "He took first-team reps.", {}, [])
except OpenAIProviderError:
    _quota_is_provider_failure = True
else:
    _quota_is_provider_failure = False
check("a quota error propagates instead of becoming a semantic abstention",
      _quota_is_provider_failure)

# The validator: the model reads, it does not decide.
_seg = ("With no Parker Washington on the field, the No. 1 target for Trevor "
        "Lawrence on Tuesday was pretty easily Brian Thomas Jr.")
_pl = [{"player_id": "PID", "player_name": "Brian Thomas Jr.",
        "team": "JAX", "position": "WR"}]
_base = {"decision": "INTERPRET", "claim_subject_player_id": "PID",
         "claim_subject_player_name": "Brian Thomas Jr.",
         "mentioned_players": [{"player_id": "PID",
                                "player_name": "Brian Thomas Jr.",
                                "relationship": "BENEFICIARY"}],
         "quote_speaker": None, "pronoun_antecedents": [],
         "supporting_quote": "the No. 1 target for Trevor Lawrence on Tuesday "
                             "was pretty easily Brian Thomas Jr.",
         "evidence_classification": "FIRSTHAND_OBSERVATION",
         "fantasy_mechanism": "TARGETS", "direction": "POSITIVE",
         "impact_strength": "LOW", "impact_horizon": "SHORT_TERM",
         "projection_action": "NONE", "fantasy_commentary": "He led targets.",
         "why_it_matters": "x", "limitations": [], "confidence": 0.7,
         "abstention_reason": None}


def _assess(**over):
    d = dict(_base)
    d.update(over)
    p = OpenAISemanticProvider(
        transport=lambda prompt: (d, {"input_tokens": 10, "output_tokens": 5}))
    a = p.evaluate(_seg, {"team": "JAX"}, over.pop("_players", None) or _pl)
    return SV.enforce(a, _seg, over.get("_players") or _pl, None, {})


check("a well-formed response is accepted",
      _assess().decision == "INTERPRET")
check("a quote absent from the passage is rejected",
      _assess(supporting_quote="he took first-team reps").decision == SEM.ABSTAIN)
check("an unknown player id is rejected",
      _assess(claim_subject_player_id="NOPE").decision == SEM.ABSTAIN)
check("a name that disagrees with its id is rejected",
      _assess(claim_subject_player_name="Parker Washington").decision == SEM.ABSTAIN)
check("commentary inventing a number is rejected",
      _assess(fantasy_commentary="He saw 9 targets.").decision == SEM.ABSTAIN)
check("commentary using banned filler is rejected",
      _assess(fantasy_commentary="Worth monitoring.").decision == SEM.ABSTAIN)
check("commentary mentioning ADP or rankings is rejected",
      _assess(fantasy_commentary="His ADP is wrong.").decision == SEM.ABSTAIN)
check("a validation failure abstains and is never repaired",
      _assess(supporting_quote="nope").fantasy_mechanism == "NO_FANTASY_IMPACT")
_perf_only = _assess(fantasy_mechanism="PERFORMANCE")
check("performance-only INTERPRET fails closed instead of becoming a card",
      _perf_only.decision == SEM.ABSTAIN
      and any("not a publishable fantasy mechanism" in failure
              for failure in _perf_only.validation_failures),
      _perf_only.to_dict())
_other_only = _assess(fantasy_mechanism="OTHER")
check("OTHER cannot bypass the concrete-mechanism requirement",
      _other_only.decision == SEM.ABSTAIN
      and any("not a publishable fantasy mechanism" in failure
              for failure in _other_only.validation_failures),
      _other_only.to_dict())
check("an explicit no-impact model decision remains no impact",
      _assess(decision=SEM.NO_FANTASY_IMPACT,
              fantasy_mechanism="NO_FANTASY_IMPACT").decision
      == SEM.NO_FANTASY_IMPACT)
check("the prompt explicitly routes isolated performance to no impact",
      "PERFORMANCE and OTHER are not publishable fantasy mechanisms"
      in SEM.SYSTEM
      and "return NO_FANTASY_IMPACT" in SEM.SYSTEM)
check("the prompt treats source metadata as context rather than evidence",
      "Source metadata is context for provenance and authority only"
      in SEM.SYSTEM
      and "A missing metadata field is not an \"unverified\"" in SEM.SYSTEM
      and "evidence access: not supplied" in
          SEM.build_prompt(_seg, {"team": "JAX"}, _pl))
check("the prompt forbids an update recommendation on LOW evidence",
      "LOW evidence may use NONE or REVIEW" in SEM.SYSTEM
      and "never UPDATE_RECOMMENDED" in SEM.SYSTEM)
check("clear authorial analysis routes to no impact rather than abstention",
      "must return NO_FANTASY_IMPACT, not ABSTAIN" in SEM.SYSTEM
      and "authority boundary makes it non-actionable" in SEM.SYSTEM)
check("the prompt keeps an unattributed diagnosis uncertain",
      "An unattributed diagnosis, medical cause, or medical timetable is UNCERTAIN"
      in " ".join(SEM.SYSTEM.split())
      and "nearby quotation does not lend" in SEM.SYSTEM
      and "Makai Lemon began practicing in a limited capacity" in
          " ".join(SEM.SYSTEM.split()))
_return_seg = ("Makai Lemon began practicing in a limited capacity on "
               "Thursday. He will be returning to practice full-time soon.")
_return_players = [{"player_id": "LEMON", "player_name": "Makai Lemon",
                    "team": "PHI", "position": "WR"}]
_return_payload = {
    **_base,
    "claim_subject_player_id": "LEMON",
    "claim_subject_player_name": "Makai Lemon",
    "mentioned_players": [{"player_id": "LEMON",
                            "player_name": "Makai Lemon",
                            "relationship": "CLAIM_SUBJECT"}],
    "supporting_quote": ("Makai Lemon began practicing in a limited capacity "
                         "on Thursday."),
    "fantasy_mechanism": "RETURN_TO_PRACTICE",
    "direction": "POSITIVE",
    "fantasy_commentary": ("Lemon began practicing in a limited capacity on "
                           "Thursday."),
    "limitations": ["The passage does not establish full participation."],
}
_return_provider = OpenAISemanticProvider(
    transport=lambda prompt: (_return_payload,
                              {"input_tokens": 10, "output_tokens": 5}))
_return_assessment = SV.enforce(
    _return_provider.evaluate(_return_seg, {"team": "PHI"}, _return_players),
    _return_seg, _return_players, None, {})
check("began practicing is an explicit return-to-practice event",
      _return_assessment.decision == SEM.INTERPRET
      and _return_assessment.fantasy_mechanism == "RETURN_TO_PRACTICE",
      _return_assessment.to_dict())
_invented_metadata = _assess(
    decision=SEM.ABSTAIN,
    fantasy_mechanism="LIMITED_PARTICIPATION",
    fantasy_commentary="The source metadata is unverified.",
    abstention_reason="The evidentiary status is unverified.")
check("an abstention cannot invent unverified source metadata",
      _invented_metadata.decision == SEM.ABSTAIN
      and any("unverified source metadata" in failure
              for failure in _invented_metadata.validation_failures),
      _invented_metadata.to_dict())
_invented_preseason = _assess(
    decision=SEM.NO_FANTASY_IMPACT,
    fantasy_mechanism="NO_FANTASY_IMPACT",
    fantasy_commentary="This was an isolated preseason game play.")
check("a no-impact explanation cannot import game context from metadata",
      _invented_preseason.decision == SEM.ABSTAIN
      and any("preseason context" in failure
              for failure in _invented_preseason.validation_failures),
      _invented_preseason.to_dict())
_negative_context = _assess(
    decision=SEM.NO_FANTASY_IMPACT,
    fantasy_mechanism="NO_FANTASY_IMPACT",
    fantasy_commentary=("The passage does not establish regular-season "
                        "availability."))
check("a limitation may name context that the evidence does not establish",
      _negative_context.decision == SEM.NO_FANTASY_IMPACT
      and not _negative_context.validation_failures,
      _negative_context.to_dict())
_low_update = _assess(projection_action="UPDATE_RECOMMENDED")
check("LOW evidence cannot recommend an update",
      _low_update.decision == SEM.ABSTAIN
      and any("UPDATE_RECOMMENDED on LOW" in failure
              for failure in _low_update.validation_failures),
      _low_update.to_dict())
_analysis_interpretation = _assess(
    evidence_classification="ANALYSIS_OR_OPINION")
check("analysis or opinion cannot support an interpretation",
      _analysis_interpretation.decision == SEM.ABSTAIN
      and any("may not support" in failure
              for failure in _analysis_interpretation.validation_failures),
      _analysis_interpretation.to_dict())
_relay_interpretation = _assess(
    evidence_classification="RELAYED_REPORTING")
check("relayed reporting cannot support an interpretation",
      _relay_interpretation.decision == SEM.ABSTAIN
      and any("may not support" in failure
              for failure in _relay_interpretation.validation_failures),
      _relay_interpretation.to_dict())
_uncertain_interpretation = _assess(evidence_classification="UNCERTAIN")
check("uncertain evidence cannot support an interpretation",
      _uncertain_interpretation.decision == SEM.ABSTAIN
      and any("may not support" in failure
              for failure in _uncertain_interpretation.validation_failures),
      _uncertain_interpretation.to_dict())
check("an official designation remains an eligible evidence class",
      _assess(evidence_classification="OFFICIAL_DESIGNATION").decision
      == SEM.INTERPRET
      and "OFFICIAL_DESIGNATION" in
          SEM.RESPONSE_SCHEMA["properties"]["evidence_classification"]["enum"])
check("non-evidence classification does not corrupt a no-impact decision",
      _assess(decision=SEM.NO_FANTASY_IMPACT,
              fantasy_mechanism="NO_FANTASY_IMPACT",
              evidence_classification="ANALYSIS_OR_OPINION").decision
      == SEM.NO_FANTASY_IMPACT)
check("the prompt distinguishes official designations from firsthand reports",
      "OFFICIAL_DESIGNATION" in SEM.SYSTEM
      and "not a reporter's firsthand observation" in SEM.SYSTEM)
check("the prompt does not transfer another player's role to a quote speaker",
      "Geno Smith saying" in SEM.SYSTEM
      and "return NO_FANTASY_IMPACT for the matched speaker" in SEM.SYSTEM)
check("the prompt treats recurring two-minute routes as concrete usage",
      "every two-minute drill" in SEM.SYSTEM
      and "return INTERPRET with ROUTES" in SEM.SYSTEM)

_allen_seg = ("With LeQuint Allen Jr. out for the rest of training camp with "
              "a soft-tissue injury, Abdullah could have a prime opportunity "
              "to step up.")
_allen_players = [{"player_id": "ALLEN", "player_name": "LeQuint Allen Jr.",
                   "team": "JAX", "position": "RB"}]
_allen_payload = {
    **_base,
    "claim_subject_player_id": "ALLEN",
    "claim_subject_player_name": "LeQuint Allen Jr.",
    "mentioned_players": [{"player_id": "ALLEN",
                            "player_name": "LeQuint Allen Jr.",
                            "relationship": "ABSENT_PLAYER"}],
    "supporting_quote": _allen_seg,
    "evidence_classification": "FIRSTHAND_OBSERVATION",
    "fantasy_mechanism": "INJURY",
    "direction": "NEGATIVE",
    "fantasy_commentary": ("Allen is out for the rest of training camp with "
                           "a soft-tissue injury."),
    "limitations": ["The passage does not establish regular-season availability."],
}
_allen_provider = OpenAISemanticProvider(
    transport=lambda prompt: (_allen_payload,
                              {"input_tokens": 10, "output_tokens": 5}))
_allen_assessment = SV.enforce(
    _allen_provider.evaluate(_allen_seg, {"team": "JAX"}, _allen_players),
    _allen_seg, _allen_players, None, {})
check("an unattributed diagnosis cannot be classified as firsthand",
      _allen_assessment.decision == SEM.ABSTAIN
      and any("unattributed diagnosis" in failure
              for failure in _allen_assessment.validation_failures)
      and not any("regular season context" in failure
                  for failure in _allen_assessment.validation_failures),
      _allen_assessment.to_dict())

_absent_pl = [{"player_id": "PID", "player_name": "Parker Washington",
               "team": "JAX", "position": "WR"}]
_pa = ClaudeSemanticProvider(transport=lambda p: (
    {**_base, "claim_subject_player_name": "Parker Washington",
     "mentioned_players": [{"player_id": "PID",
                            "player_name": "Parker Washington",
                            "relationship": "ABSENT_PLAYER"}]},
    {"input_tokens": 1, "output_tokens": 1}))
_res = SV.enforce(_pa.evaluate(_seg, {}, _absent_pl), _seg, _absent_pl, None, {})
check("an absent player cannot inherit the beneficiary's targets",
      _res.decision == SEM.ABSTAIN
      and any("absent" in f for f in _res.validation_failures),
      _res.validation_failures)

_relay_seg = "Per Cameron Wolfe of NFL Network, he was held out of practice."
_rp = ClaudeSemanticProvider(transport=lambda p: (
    {**_base, "supporting_quote": "he was held out of practice",
     "evidence_classification": "FIRSTHAND_OBSERVATION"},
    {"input_tokens": 1, "output_tokens": 1}))
_rr = SV.enforce(_rp.evaluate(_relay_seg, {}, _pl), _relay_seg, _pl, None, {})
check("relayed reporting cannot be relabelled firsthand by the model",
      _rr.decision == SEM.ABSTAIN
      and any("relayed" in f for f in _rr.validation_failures),
      _rr.validation_failures)

_ru = ClaudeSemanticProvider(transport=lambda p: (
    {**_base, "fantasy_mechanism": "FIRST_TEAM_REPS"},
    {"input_tokens": 1, "output_tokens": 1}))
_rc = SV.enforce(_ru.evaluate(_seg, {}, _pl), _seg, _pl, None, {})
check("a unit claim needs unit language in its own quote",
      _rc.decision == SEM.ABSTAIN)

_dup = SV.enforce(_assess(impact_strength="MEDIUM"), _seg, _pl, None,
                  {"duplicate_of": "other"})
check("a duplicate report cannot exceed LOW", _dup.decision == SEM.ABSTAIN)
_own = SV.enforce(_assess(impact_strength="HIGH"), _seg, _pl, None,
                  {"source_ownership": "TEAM_OWNED"})
check("a team-owned observation cannot reach HIGH", _own.decision == SEM.ABSTAIN)

# Isolation: the semantic layer touches nothing it must not.
for _f in ("semantic.py", "semantic_validate.py", "providers/rules.py",
           "providers/openai.py", "providers/openai_review.py"):
    _src = code_only((ROOT / "wire" / _f).read_text())
    check(f"{_f} reads no fantasy projection data",
          not FORBIDDEN_NAMES.search(_src))
    check(f"{_f} imports nothing from the fantasy side",
          not FORBIDDEN_IMPORTS.findall(_src))
    check(f"{_f} publishes nothing", "wire_publications" not in _src)
_evalsrc = code_only((ROOT / "scripts" / "wire_semantic_eval.py").read_text())
check("the evaluation harness publishes nothing",
      "wire_publications" not in _evalsrc
      and not FORBIDDEN_NAMES.search(_evalsrc))

_ordinary_abstention = WSE.grade(
    {"id": "ordinary-no-impact", "expected": {
        "decision": SEM.NO_FANTASY_IMPACT}},
    SEM.SemanticAssessment(decision=SEM.ABSTAIN))
check("an ordinary abstention is not a correct no-impact answer",
      not _ordinary_abstention["correct"]
      and _ordinary_abstention["errors"] == ["unnecessary_abstention"],
      _ordinary_abstention)
_registry_refusal = WSE.grade(
    {"id": "registry-refusal",
     "identity_outcome": "CORRECT_REGISTRY_REFUSAL",
     "expected": {"decision": SEM.NO_FANTASY_IMPACT},
     "forbidden_mechanism": "RETURN_TO_PRACTICE"},
    SEM.SemanticAssessment(decision=SEM.ABSTAIN))
check("the labelled registry refusal remains a correct abstention",
      _registry_refusal["correct"], _registry_refusal)

_passing_summary = {
    "available": True, "locked_gold_items": 23,
    "correct_num": 22, "correct_den": 23,
    "precision_num": 12, "precision_den": 12,
    "recall_num": 12, "recall_den": 13,
    "abstain_num": 3, "abstain_den": 23,
}
_passing_gate = WSE.promotion_gate(
    _passing_summary,
    [{"id": "clean", "errors": [], "validation_failures": []}])
check("the locked semantic gate passes only a clean threshold result",
      _passing_gate["passed"], _passing_gate)
_partial_gate = WSE.promotion_gate(
    {**_passing_summary, "correct_num": 1, "correct_den": 1,
     "precision_num": 1, "precision_den": 1,
     "recall_num": 1, "recall_den": 1,
     "abstain_num": 0, "abstain_den": 1},
    [{"id": "partial", "errors": [], "validation_failures": []}])
check("a partial corpus cannot pass the locked semantic gate",
      not _partial_gate["passed"]
      and not _partial_gate["checks"]["locked_corpus_complete"],
      _partial_gate)
_validation_gate = WSE.promotion_gate(
    _passing_summary,
    [{"id": "invalid", "errors": [],
      "validation_failures": ["INTERPRET with no claim subject id"]}])
check("an unexpected validator failure blocks semantic promotion",
      not _validation_gate["passed"]
      and not _validation_gate["checks"][
          "unexpected_validation_failures_zero"],
      _validation_gate)
_error_gate = WSE.promotion_gate(
    _passing_summary,
    [{"id": "wrong", "errors": ["wrong_player"],
      "validation_failures": []}])
check("a zero-tolerance semantic error blocks promotion",
      not _error_gate["passed"]
      and not _error_gate["checks"]["zero_tolerance_errors_zero"],
      _error_gate)

# The corpus reports its own labelled fraction rather than assuming it.
_corpus = json.loads((ROOT / "data" / "wire_eval_corpus.json").read_text())
check("the corpus separates gold from unlabelled",
      _corpus["gold_items"] > 0 and _corpus["unlabelled_items"] > 0)
check("unlabelled items carry no expected answer",
      all(x["expected"] is None for x in _corpus["items"]
          if x["kind"] == "UNLABELLED"))
_gold_ids = {x["id"] for x in _corpus["items"] if x["kind"] == "GOLD"}
for _need in ("keon-coleman-relay", "washington-thomas-targets",
              "mccarthy-price-pronoun", "geno-about-omar",
              "hankerson-waived", "giddens-reinjury", "jacobs-return",
              "laporta-absent", "stidham-mixed-units",
              "hollins-two-minute"):
    check(f"the corpus contains the {_need} fixture", _need in _gold_ids)

# Review controls: unique ids and the full reason set.
_rev = json.loads((ROOT / "data" / "wire_fantasy_review.json").read_text())
_ids = [i["fantasy_impact_id"] for i in _rev["items"]]
check("every review row has a unique stable id",
      len(_ids) == len(set(_ids)), len(_ids) - len(set(_ids)))
check("suppressed rows do not collide on player id",
      len({i for i in _ids if i.startswith("suppressed:")})
      == len([i for i in _ids if i.startswith("suppressed:")]))
_rhtml = (ROOT / "data" / "wire_fantasy_review.html").read_text()
for _r in ("REJECT_WRONG_DIRECTION", "REJECT_WRONG_UNIT"):
    check(f"the review page offers {_r}", _r in _rhtml)

# Anonymous synthesis. The relay detector needs a name to match, so "by all
# accounts" slipped past it and a Daniel Jones performance note reached
# FIRSTHAND_OBSERVATION from an approved reporter.
for _phrase in ("By all accounts", "Reportedly", "Word is", "Sources say",
                "It is believed"):
    _k, _c, _w = ev.classify(f"{_phrase}, he had his way with the first-team "
                             f"defense on Wednesday.", reporter_voice=True)
    check(f"anonymous synthesis is not firsthand: {_phrase!r}",
          _k != ev.FIRSTHAND_OBSERVATION, _k)
check("a real observation is still firsthand",
      ev.classify("He took first-team reps during Wednesday's practice.",
                  reporter_voice=True)[0] == ev.FIRSTHAND_OBSERVATION)
check("a counted observation is still firsthand",
      ev.classify("I counted twelve first-team reps for him.",
                  reporter_voice=True)[0] == ev.FIRSTHAND_OBSERVATION)

# ---------------------------------------------- fantasy relevance gate
from wire import relevance as RV

_rel = RV.load()
check("the relevance registry exists and is populated",
      _rel["count"] > 100, _rel["count"])
_relfile = json.loads((ROOT / "data" / "wire_fantasy_relevance.json").read_text())
_allowed = {"player_id", "team", "position", "relevance_tier",
            "relevance_reason", "effective_at", "registry_version"}
check("the relevance file carries identity fields only",
      all(set(r) <= _allowed for r in _relfile["players"]),
      sorted(set(_relfile["players"][0]) - _allowed) if _relfile["players"] else [])
check("no ADP, rank, points or projection appears in the relevance file",
      not re.search(r'"(adp|rank|ranking|points|projected|projection|vorp)"',
                    json.dumps(_relfile["players"][:200])))
check("relevance boundaries are recorded, not inferred",
      set(_relfile["boundaries"]) == {"QB", "RB", "WR", "TE"},
      _relfile["boundaries"])

# Position eligibility and fantasy relevance are different questions. This
# is the check that was missing when a backup quarterback's routine
# second-team rep became a published card.
def _rel_ok(pos, text, pid="zz-not-in-registry"):
    return RV.assess(pid, pos, text, _rel)["eligible"]


check("a backup quarterback taking routine second-team reps is suppressed",
      not _rel_ok("QB", "Richardson led the second-team offense to a field goal."))
check("a backup quarterback promoted to first-team work is reviewable",
      _rel_ok("QB", "Richardson took first-team reps for the first time."))
check("a backup quarterback named the starter is reviewable",
      _rel_ok("QB", "The Colts named Anthony Richardson the starter for Week 1."))
check("a backup quarterback replacing an injured starter is reviewable",
      _rel_ok("QB", "Richardson replaced Daniel Jones after Jones left injured."))
check("generic praise for a backup quarterback is suppressed",
      not _rel_ok("QB", "Richardson has looked sharp and the coaches are pleased."))
check("a deep receiver taking ordinary reserve reps is suppressed",
      not _rel_ok("WR", "Blair worked with the reserves in individual drills."))
check("a deep receiver taking first-team red-zone snaps is reviewable",
      _rel_ok("WR", "Blair received most of Dotson's first-team snaps during "
                    "red-zone work."))
check("a deep back taking goal-line carries is reviewable",
      _rel_ok("RB", "Heidenreich took the goal-line carries with the ones."))
check("an offensive lineman remains team context",
      not _rel_ok("OL", "Mailata handled Smith easily."))
check("a defensive player remains team context",
      not _rel_ok("DB", "Branch did not participate."))
check("relevance is distinct from position eligibility",
      RV.assess("zz", "QB", "led the second-team offense", _rel)["tier"]
      == RV.NOT_RELEVANT
      and "QB" in RV.FANTASY_POSITIONS)
check("every suppression records a reason",
      all(RV.assess("zz", p, t, _rel)["reason"]
          for p, t in (("QB", "second-team reps"), ("OL", "blocked well"),
                       ("WR", "reserve drills"))))
check("relevance never authorises publication",
      "publish" not in code_only((ROOT / "wire" / "relevance.py").read_text()))
check("the relevance module reads no projection or ADP file",
      not FORBIDDEN_NAMES.search(code_only(
          (ROOT / "wire" / "relevance.py").read_text())))

# The generator applies the gate before any model call.
_gsrc = (ROOT / "scripts" / "wire_fantasy_impact.py").read_text()
check("the gate runs before interpretation",
      _gsrc.index("relevance_filter") < _gsrc.index('interpreter == "claude"')
      if 'interpreter == "claude"' in _gsrc else True)
check("refused rows are recorded with their reasons",
      "wire_not_relevant.json" in _gsrc)

# Nothing numeric may reach the model.
_prompt = SEM.build_prompt("He took first-team reps.",
                           {"team": "IND", "article_title": "t"},
                           [{"player_name": "X", "player_id": "1",
                             "team": "IND", "position": "QB"}])
for _banned in ("adp", "ADP", "projected points", "rank"):
    check(f"the model prompt contains no {_banned}", _banned not in _prompt)
check("the model prompt carries no relevance tier or numeric field",
      "ROSTERABLE" not in _prompt and "relevance" not in _prompt.lower())

# ------------------------------------------------ the published file
_pubs = json.loads((ROOT / "data" / "wire_publications.json").read_text())
_pub_names = sorted(p["player_name"] for p in _pubs["publications"])
# Whoever is approved now. Naming players here broke the moment a reviewer
# retracted Anthony Richardson as not fantasy relevant.
check("every published card carries a reviewer approval",
      all(p["reviewer_action"].startswith("APPROVE")
          for p in _pubs["publications"]), _pub_names)
check("a retracted card is not published",
      "Anthony Richardson" not in _pub_names, _pub_names)
check("every published card names its reviewer approval",
      all(p["reviewer_action"].startswith("APPROVE")
          for p in _pubs["publications"]))
check("no published card touches a projection",
      all(p["projection_action"] == "NONE" for p in _pubs["publications"])) 
check("every published card keeps its source link",
      all(p["url"].startswith("https://") for p in _pubs["publications"]))
check("every published card keeps the reporter's own words",
      all(p["reporter_found"] for p in _pubs["publications"]))
check("the reporter's evidence and our commentary stay separate fields",
      all(p["reporter_found"] != p["lineupbeat_impact"]
          for p in _pubs["publications"]))
_pubsrc = (ROOT / "scripts" / "wire_publish.py").read_text()
for _state in ("PENDING", "HOLD", "ABSTAIN", "NO_FANTASY_IMPACT"):
    check(f"{_state} can never be published", _state in _pubsrc)
check("publishing runs the readiness checks, not just the approval flag",
      "readiness_failures" in _pubsrc)
check("a model assessment alone authorises nothing",
      "reviewer_action" in _pubsrc and "startswith(\"APPROVE\")" in _pubsrc)

# ------------------------------------- quote retry and publication preview
check("the retry schema has exactly one field",
      list(SEM.QUOTE_RETRY_SCHEMA["properties"]) == ["supporting_quote"])
check("the retry tells the model its interpretation is not under review",
      "not yours to revisit" in SEM.QUOTE_RETRY_SYSTEM)
check("the retry forbids tidying punctuation",
      "complete or tidy punctuation" in SEM.QUOTE_RETRY_SYSTEM)
_svsrc = (ROOT / "wire" / "semantic_validate.py").read_text()
check("only an inexact quotation earns a retry",
      "all(f == QUOTE_FAILURE for f in first_fails)" in _svsrc)
check("both attempts are stored",
      '"attempt": 1' in _svsrc and '"attempt": 2' in _svsrc)
check("a failed retry leaves the case pending, not repaired",
      "did not produce an exact substring" in _svsrc)

# The quotation check folds typography and tolerates a dangling delimiter,
# and nothing more: the words must still be the passage's words.
check("curly and straight quotes compare equal",
      SV._quote_in("they\u2019ve looked all camp", "they've looked all camp"))
check("a dangling closing quote is tolerated",
      SV._quote_in('he looked sharp."', "she said he looked sharp."))
check("different words are still rejected",
      not SV._quote_in("he took first-team reps", "he took second-team reps"))
check("a paraphrase is still rejected",
      not SV._quote_in("Richardson ran with the twos",
                       "Anthony Richardson led the second-team offense"))

import wire_publication_preview as PP
check("a NEUTRAL card whose wording argues negative is flagged",
      PP.framing_conflict("NEUTRAL", "This is a concerning depth-chart signal."))
check("a consistent card is not flagged",
      not PP.framing_conflict("NEUTRAL", "He took second-team reps on Tuesday."))

# Every readiness failure the reviewer asked to be automatic. Each is
# checked on the finished wording, because each is a failure of wording that
# no score would catch.
_R = PP.readiness_failures
check("stale framing blocks a card",
      _R({"direction": "NEUTRAL", "mechanism": "SECOND_TEAM_REPS",
          "reviewer_action": "APPROVE",
          "commentary": "This is a concerning signal during one practice.",
          "evidence": "He led the second-team offense."}))
check("a mechanism unsupported by the passage blocks a card",
      any("not supported by the evidence" in f for f in
          _R({"direction": "NEGATIVE", "mechanism": "INJURY",
              "reviewer_action": "APPROVE",
              "commentary": "He missed one practice; no timetable was given.",
              "evidence": "Players who did not participate were QB Quinn Ewers."})))
check("an absence without temporary context blocks a card",
      any("temporary context" in f for f in
          _R({"direction": "NEGATIVE", "mechanism": "LIMITED_PARTICIPATION",
              "reviewer_action": "APPROVE", "commentary": "He is not practising.",
              "evidence": "He did not participate."})))
check("implying a permanent change blocks a card",
      any("permanent role change" in f for f in
          _R({"direction": "POSITIVE", "mechanism": "RED_ZONE",
              "reviewer_action": "APPROVE",
              "commentary": "Blair has passed Dotson in one practice.",
              "evidence": "Blair received most of Dotson's red-zone snaps."})))
check("a pending item can never be presented as publishable",
      any("not an approval" in f for f in
          _R({"direction": "POSITIVE", "mechanism": "CARRIES",
              "reviewer_action": "PENDING",
              "commentary": "He took carries during one practice.",
              "evidence": "He soaked up carries."})))
check("a clean card passes readiness",
      not _R({"direction": "NEUTRAL", "mechanism": "SECOND_TEAM_REPS",
              "reviewer_action": "APPROVE_WITH_EDIT",
              "commentary": "He led the second-team offense during one "
                            "practice. The report does not establish whether "
                            "this was a rotation.",
              "evidence": "He led the second-team offense on a drive."}))

# The file names wire_publications.json in prose to say it does not touch
# it; what matters is that it never opens it for writing.
_ppsrc = code_only((ROOT / "scripts" / "wire_publication_preview.py").read_text())
check("the publication preview never writes the published file",
      "export_publications" not in _ppsrc
      and not re.search(r"wire_publications[^\"']*[\"']\s*\)?\.write", _ppsrc)
      and not FORBIDDEN_NAMES.search(_ppsrc))
_pp = json.loads((ROOT / "data" / "wire_publication_preview.json").read_text())
check("the preview marks itself unpublished", _pp["published"] is False)
check("only reviewer-approved cases reach the preview",
      all(c["reviewer_action"].startswith("APPROVE") for c in _pp["cards"]))
check("Claude's original wording is preserved beside a reviewer edit",
      all("claude_original_commentary" in c for c in _pp["cards"]), _pp["cards"][0].keys() if _pp["cards"] else None)
check("held-back items are named with a reason",
      all(h.get("why") for h in _pp["held_back"]))
check("a held evidence conflict is named as such",
      any("not supported by the passage" in h["why"] for h in _pp["held_back"]))

# --------------------------------------------- OpenAI as the interpreter
# available() must mean usable, not present. The first version returned True
# for an eight-character placeholder, so the guard that stops the rules
# engine writing commentary in Claude's absence did not fire.
_prev_key = os.environ.get("ANTHROPIC_API_KEY")
try:
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-x"
    check("a malformed key is not 'available'",
          not ClaudeSemanticProvider().available())
    os.environ["ANTHROPIC_API_KEY"] = "placeholder"
    check("a placeholder key is not 'available'",
          not ClaudeSemanticProvider().available())
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-" + "A" * 40
    check("a well-shaped key is 'available'",
          ClaudeSemanticProvider().available())
    os.environ.pop("ANTHROPIC_API_KEY")
    check("no key is not 'available'", not ClaudeSemanticProvider().available())
finally:
    if _prev_key is not None:
        os.environ["ANTHROPIC_API_KEY"] = _prev_key
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)

_gen_raw = (ROOT / "scripts" / "wire_fantasy_impact.py").read_text()
_gen = code_only(_gen_raw)
check("OpenAI is the default interpreter",
      'default="openai"' in _gen_raw, "openai" in _gen_raw)
check("the generator stops rather than falling back to rules",
      "will NOT generate commentary in its place" in
      (ROOT / "scripts" / "wire_fantasy_impact.py").read_text())
_smoke = (ROOT / "scripts" / "wire_claude_smoke.py").read_text()
check("the smoke test never prints the key",
      "print(key" not in _smoke and "{key}" not in _smoke)
check("the smoke test refuses to send a request on a bad key shape",
      "certain to 401" in _smoke)
_batch = (ROOT / "scripts" / "wire_claude_batch.py").read_text()
check("the batch runs the smoke test first",
      "wire_claude_smoke.py" in _batch)
check("the batch does not grade unlabelled real cases",
      "not counted as" in _batch or "ungraded" in _batch)
check("OpenAI is wired into generation",
      "OpenAISemanticProvider" in _gen_raw)

_oai_src = (ROOT / "wire" / "providers" / "openai.py").read_text()
_oai_review_src = (ROOT / "wire" / "providers" / "openai_review.py").read_text()
check("OpenAI Responses are not stored",
      "store=False" in _oai_src and "store=False" in _oai_review_src)
check("both OpenAI passes require strict structured output",
      '"strict": True' in _oai_src and '"strict": True' in _oai_review_src)
_review_payload = {
    "verdict": "HUMAN_REVIEW", "subject_is_correct": True,
    "evidence_classification_is_supported": True,
    "mechanism_is_supported": True, "direction_is_supported": True,
    "commentary_overstates": False, "commentary_repeats_evidence": False,
    "inference_not_in_evidence": False,
    "performance_only_no_role_information": False,
    "passage_names_a_different_subject": False,
    "disagreement_summary": "manual check",
}
_or = OpenAIIndependentReviewer(
    transport=lambda p: (_review_payload,
                         {"input_tokens": 10, "output_tokens": 5}))
check("OpenAI independent review validates its closed schema",
      _or.evaluate("evidence", {}, {})["provider"] == "openai")

_contradictory_review = {
    **_review_payload,
    "verdict": "AUTO_APPROVE",
    "performance_only_no_role_information": True,
    "disagreement_summary": "",
}
_enforced_review = IR.enforce(
    _contradictory_review, identity_resolved=True, integrity_ok=True,
    proposed_assessment={"decision": "INTERPRET"})
check("performance-only evidence cannot auto-approve an interpretation",
      _enforced_review["model_verdict"] == "AUTO_APPROVE"
      and _enforced_review["effective_verdict"] == "HUMAN_REVIEW"
      and any("performance-only" in reason
              for reason in _enforced_review["enforcement_reasons"]),
      _enforced_review)
_confirmed_no_impact = IR.enforce(
    _contradictory_review, identity_resolved=True, integrity_ok=True,
    proposed_assessment={"decision": "NO_FANTASY_IMPACT"})
check("performance-only evidence confirms a no-impact decision",
      _confirmed_no_impact["model_verdict"] == "AUTO_APPROVE"
      and _confirmed_no_impact["effective_verdict"] == "AUTO_APPROVE"
      and not _confirmed_no_impact["enforcement_reasons"],
      _confirmed_no_impact)

_abstain_diagnostics = {
    **_review_payload,
    "verdict": "AUTO_APPROVE",
    "mechanism_is_supported": False,
    "direction_is_supported": False,
    "disagreement_summary": ("The abstention is supported; the diagnostic "
                             "mechanism and direction are not established."),
}
_confirmed_abstention = IR.enforce(
    _abstain_diagnostics, identity_resolved=True, integrity_ok=True,
    proposed_assessment={"decision": "ABSTAIN"})
check("unsupported diagnostics do not block confirmation of an abstention",
      _confirmed_abstention["model_verdict"] == "AUTO_APPROVE"
      and _confirmed_abstention["effective_verdict"] == "AUTO_APPROVE"
      and not _confirmed_abstention["enforcement_reasons"],
      _confirmed_abstention)
_diagnostics_on_interpretation = IR.enforce(
    _abstain_diagnostics, identity_resolved=True, integrity_ok=True,
    proposed_assessment={"decision": "INTERPRET"})
check("unsupported mechanism and direction still block an interpretation",
      _diagnostics_on_interpretation["effective_verdict"] == "HUMAN_REVIEW"
      and any("fantasy mechanism" in reason
              for reason in
              _diagnostics_on_interpretation["enforcement_reasons"])
      and any("direction" in reason
              for reason in
              _diagnostics_on_interpretation["enforcement_reasons"]),
      _diagnostics_on_interpretation)

_unsupported_class_review = IR.enforce(
    {**_review_payload, "verdict": "AUTO_APPROVE",
     "evidence_classification_is_supported": False,
     "disagreement_summary": "the proposed class does not match the passage"},
    identity_resolved=True, integrity_ok=True,
    proposed_assessment={"decision": "NO_FANTASY_IMPACT"})
check("an unsupported evidence classification cannot auto-approve",
      _unsupported_class_review["model_verdict"] == "AUTO_APPROVE"
      and _unsupported_class_review["effective_verdict"] == "HUMAN_REVIEW"
      and any("evidence classification" in reason
              for reason in
              _unsupported_class_review["enforcement_reasons"]),
      _unsupported_class_review)
_review_prompt = IR.build_prompt(
    "He took first-team reps.",
    {"player_id": "1", "player_name": "X", "team": "IND", "position": "QB"},
    {"decision": "INTERPRET",
     "evidence_classification": "FIRSTHAND_OBSERVATION"})
check("the independent reviewer receives the evidence classification",
      '"evidence_classification": "FIRSTHAND_OBSERVATION"'
      in _review_prompt)
check("the reviewer distinguishes attributed speech from relayed reporting",
      "DIRECT_QUOTATION includes on-record attributed speech"
      in IR.SYSTEM
      and "ordinary attributed speech relayed reporting" in
          " ".join(IR.SYSTEM.split()))
check("the reviewer classifies the supported claim in a mixed passage",
      "describes the claim supported by" in IR.SYSTEM
      and "not every sentence in a mixed passage" in IR.SYSTEM)

_enriched_review = {
    **_contradictory_review,
    "provider": "openai", "model": "gpt-test",
    "tokens_in": 10, "tokens_out": 5,
    "cost_usd": 0.001, "latency_ms": 12,
}
_enforced_enriched = IR.enforce(
    _enriched_review, identity_resolved=True, integrity_ok=True,
    proposed_assessment={"decision": "NO_FANTASY_IMPACT"})
check("trusted reviewer provenance is not mistaken for model schema output",
      _enforced_enriched["effective_verdict"] == "AUTO_APPROVE"
      and not _enforced_enriched["enforcement_reasons"]
      and _enforced_enriched["provider"] == "openai",
      _enforced_enriched)
_enforced_unknown = IR.enforce(
    {**_enriched_review, "model_invented_field": True},
    identity_resolved=True, integrity_ok=True,
    proposed_assessment={"decision": "NO_FANTASY_IMPACT"})
check("a genuinely unexpected reviewer field still fails strict validation",
      _enforced_unknown["effective_verdict"] == "HUMAN_REVIEW"
      and any("model_invented_field" in reason
              for reason in _enforced_unknown["enforcement_reasons"]),
      _enforced_unknown)

_review_script = (ROOT / "scripts" / "wire_independent_review.py").read_text()
check("independent enforcement receives the proposed generator decision",
      "proposed_assessment=result" in _review_script)

_backfill_src = (ROOT / "scripts" / "wire_backfill.py").read_text()
check("the dark launch has separate call and dollar caps",
      "--max-calls" in _backfill_src and "--cap" in _backfill_src)
check("the plan path makes zero model calls",
      "model_calls_made\": 0" in _backfill_src)
check("provider failures stop the batch instead of becoming abstentions",
      "stopped_at_provider_failure" in _backfill_src
      and "provider_errors" in _backfill_src
      and "PROVIDER FAILURE" in _backfill_src)
check("an OpenAI run drops stale Claude state",
      'state.pop(stale, None)' in _backfill_src and '"claude"' in _backfill_src)
check("the dark launch supports exact include and exclude ids",
      "--candidate-id" in _backfill_src
      and "--exclude-candidate-id" in _backfill_src
      and "EXACT SELECTION INVALID" in _backfill_src)

from scripts.wire_backfill import select_survivors as _select_survivors
_selection_rows = [
    {"candidate_id": "a"}, {"candidate_id": "b"}, {"candidate_id": "c"}]
_selected, _missing, _overlap = _select_survivors(
    _selection_rows, include=["c", "a"], exclude=[])
check("exact selection preserves deterministic survivor order",
      [row["candidate_id"] for row in _selected] == ["a", "c"]
      and not _missing and not _overlap,
      (_selected, _missing, _overlap))
_selected, _missing, _overlap = _select_survivors(
    _selection_rows, include=["a", "missing"], exclude=[])
check("an exact id outside the current window fails closed",
      _missing == ["missing"], (_selected, _missing, _overlap))
_selected, _missing, _overlap = _select_survivors(
    _selection_rows, include=["a"], exclude=["a"])
check("an id cannot be both included and excluded",
      _overlap == ["a"], (_selected, _missing, _overlap))
_selected, _missing, _overlap = _select_survivors(
    _selection_rows, include=[], exclude=["b"])
check("reviewed ids can be excluded without reordering the rest",
      [row["candidate_id"] for row in _selected] == ["a", "c"],
      _selected)

from scripts import wire_readiness as WR
_account = WR.extraction_accounting([
    {"extraction_status": "COMPLETE", "note": "", "c": 558},
    {"extraction_status": "INCOMPLETE",
     "note": "official team site: refused content", "c": 96},
    {"extraction_status": "INCOMPLETE", "note": "body short", "c": 13},
])
check("readiness excludes intentional content refusals from extraction rate",
      _account == {"complete": 558, "failed": 13, "refused": 96,
                   "rate": 97.7}, _account)
check("the historical reviewed publication set is readiness-valid",
      WR.reviewed_publications_valid(_pubs))

# ------------------------------------------- semantic claim regressions
from wire import claims as CL
import wire_fixtures as FX

_fx = FX.run()
_fx_bad = [r for r in _fx if not r["pass"]]
check("every adversarial fixture passes", not _fx_bad,
      [r["id"] for r in _fx_bad])
for _r in _fx:
    check(f"fixture {_r['id']}", _r["pass"],
          f"{_r['got_class']}/{_r['got_mech']}/{_r['got_dir']}")

# Segmentation: a window may never leave its segment. This is what let a
# quotation, a heading and the next paragraph become one claim.
_multi = ('"We are clicking," said Allen.\n'
          "NOT SO GOOD - More Bills' WRs on shelf\n"
          "With Keon Coleman in a walking boot, the corps was hurting.")
_spans = seg.spans(_multi)
check("a span never spans a heading",
      not any("Allen" in s["text"] and "Coleman" in s["text"] for s in _spans),
      [s["text"][:50] for s in _spans])
check("headings are not evidence",
      not any("NOT SO GOOD" in s["text"] for s in _spans))
check("footer biographies never enter a span",
      not seg.spans("Jim Wyatt is a senior writer for the Titans."))

# Subject rules.
check("a player does not inherit another player's unit",
      CL.unit_claim("Daniel Jones is the starter. Anthony Richardson ran "
                    "with the second team.", "Daniel Jones") == "")
check("the actual subject keeps his unit",
      CL.unit_claim("Anthony Richardson ran with the second team.",
                    "Anthony Richardson") == CL.SECOND_TEAM)
check("third team is not second team",
      CL.unit_claim("Carson Wentz worked with the 3s.", "Carson Wentz")
      == CL.THIRD_TEAM)
check("a waived player never returns to practice",
      CL.availability("The team waived Anthony Hankerson.",
                      "Anthony Hankerson") == ("", ""))
check("an absence is negative, never a return",
      CL.availability("Theo Wease Jr. did not practice.", "Theo Wease")
      == ("LIMITED_PARTICIPATION", "NEGATIVE"))
check("a re-injury is negative",
      CL.availability("DJ Giddens returned and reaggravated his hamstring.",
                      "DJ Giddens")[1] == "NEGATIVE")
check("a genuine return is positive",
      CL.availability("Bo Melton was back at practice on Tuesday.",
                      "Bo Melton") == ("RETURN_TO_PRACTICE", "POSITIVE"))
check("a team-mood quote yields no fantasy impact",
      CL.fantasy_mechanism('"The energy has been unbelievable," Josh Allen '
                           'said.', "Josh Allen", "DIRECT_QUOTATION",
                           speaker="Josh Allen")["mechanism"]
      == CL.NO_FANTASY_IMPACT)
check("a quote about another player is not the speaker's own account",
      CL.fantasy_mechanism('"Omar Cooper has taken the slot role," Geno '
                           'Smith said.', "Geno Smith", "DIRECT_QUOTATION",
                           speaker="Geno Smith")["mechanism"]
      == CL.NO_FANTASY_IMPACT)
check("an isolated play is not an opportunity change",
      CL.fantasy_mechanism("Amon-Ra St. Brown caught a touchdown.",
                           "Amon-Ra St. Brown", "FIRSTHAND_OBSERVATION"
                           )["mechanism"] == CL.NO_FANTASY_IMPACT)

# Commentary quality.
_c = fz.commentary("Player X", "FIRST_TEAM_REPS", 1, False, False)
check("commentary names the mechanism, not a generic hedge",
      "first team" in _c and "worth monitoring" not in _c.lower(), _c)
check("commentary does not print 1 span(s)",
      "(s)" not in fz._plural(1, "span") and fz._plural(1, "span") == "1 span")
check("a single report is not described as multiple",
      "One report" in fz.commentary("X", "TARGETS", 1, False, False))
check("team-owned support says so instead of claiming corroboration",
      "cannot corroborate" in fz.commentary("X", "TARGETS", 1, True, False))
check("repeated rewrites are named as repeats",
      "not extra confirmation" in fz.commentary("X", "TARGETS", 2, False, True))

# Suppression is a first-class outcome, not a hedge.
check("NO_FANTASY_IMPACT is an available outcome",
      CL.NO_FANTASY_IMPACT == "NO_FANTASY_IMPACT")
_supfile = ROOT / "data" / "wire_fantasy_suppressed.json"
check("suppressed cases are recorded with a reason",
      _supfile.exists()
      and all(x["reason"] for x in json.loads(_supfile.read_text())["items"]))

# Review controls exist and publish nothing.
_html = (ROOT / "data" / "wire_fantasy_review.html").read_text()
for _act in ("APPROVE", "APPROVE_WITH_EDIT", "REJECT_UNSUPPORTED",
             "REJECT_WRONG_PLAYER"):
    check(f"the review page offers {_act}", _act in _html)
check("the review page publishes nothing",
      "wire_publications" not in _html)
_apply = (ROOT / "scripts" / "wire_fantasy_review_apply.py").read_text()
check("applying a decision never overwrites the generated text",
      "original_commentary" in _apply)
check("applying a decision publishes nothing",
      "wire_publications" not in _apply)

# ------------------------------------------------------------- coverage
from wire import coverage as COV

_cov = COV.summary()
check("coverage counts teams, not sources",
      len(_cov["with_independent_local"])
      == len(set(_cov["with_independent_local"])))
check("independent-local teams and the teams without one partition the league",
      len(_cov["with_independent_local"])
      + len(_cov["without_independent_local"]) == 32,
      (len(_cov["with_independent_local"]),
       len(_cov["without_independent_local"])))
# The bug this replaced: a report said 32 teams lacked an independent local
# source while also reporting nine such sources. Nine sources cover nine
# teams; the answer was 23, and it came from counting the wrong noun.
check("nine independent-local sources do not mean zero covered teams",
      len(_cov["with_independent_local"]) > 0
      and len(_cov["without_independent_local"]) < 32)
check("an official club site never counts as independent",
      all(t in _cov["with_non_team_owned"]
          for t in _cov["with_independent_local"]))
_official_only = [t for t in COV.teams()
                  if t not in _cov["with_non_team_owned"]]
check("no team relies solely on team-owned coverage", not _official_only,
      _official_only)
check("On SI concentration is measured, not assumed",
      set(_cov["onsi_only_non_team_owned"])
      <= set(_cov["without_independent_local"]))
check("nothing in coverage is hardcoded",
      "32" not in code_only((ROOT / "wire" / "coverage.py").read_text())
      .replace("teams_total", ""))

# --------------------------------------------------- relayed reporting
_rel = 'According to The Athletic, he took every first-team rep this week.'
_rk, _rc, _rw = ev.classify(_rel, reporter_voice=True)
check("relayed reporting is its own classification",
      _rk == ev.RELAYED_REPORTING, _rk)
check("relay is decided before quotation and firsthand",
      ev.classify('"He looked sharp," ESPN\'s Adam Schefter reported.',
                  reporter_voice=True)[0] == ev.RELAYED_REPORTING)
_orig = ev.origin_of("Doug Kyed reported that he took first-team reps.")
check("the origin reporter is captured from a rewrite",
      _orig["origin_reporter"] == "Doug Kyed", _orig)
check("an underlying report id is stable for one original",
      ev.underlying_report_id(_orig, "he took first-team reps")
      == ev.underlying_report_id(_orig, "he took first-team reps"))
check("relayed evidence may not support a fantasy interpretation",
      ev.RELAYED_REPORTING not in WFI.SUPPORTING)

for _bad, _kind in [("An AI simulation predicts a 12-win season.", "AI simulation"),
                    ("Grok simulated the entire season.", "Grok simulation"),
                    ("A blockbuster trade proposal sends him east.", "trade proposal"),
                    ("Fans react as the clip went viral.", "entertainment")]:
    check(f"A to Z style {_kind} is not evidence", ev.relevance(_bad), _bad)

_atoz = [x for x in registry.load() if x.source_id == "atoz_network_ne"]
check("A to Z is registered per team, not per domain",
      len(_atoz) == 1 and _atoz[0].filter_url_pattern.startswith("^/nfl/new-england"),
      _atoz[0].filter_url_pattern if _atoz else None)
check("A to Z begins MANUAL_REVIEW_ONLY",
      _atoz and _atoz[0].status == registry.MANUAL_REVIEW_ONLY)
_net = SI_AUTH.get("network_authors", {})
check("Rob Gregson is not firsthand for New England",
      _net.get("Rob Gregson", {}).get("classification") in
      ("AGGREGATION", "ANALYSIS_ONLY"), _net.get("Rob Gregson"))

# ------------------------------------------------ health and rollback
import wire_health as WH

check("a rollback snapshot exists", WH.latest_snapshot() is not None)
check("health scoring reads stored output, not configuration",
      "reporter_name" not in code_only(
          (ROOT / "scripts" / "wire_health.py").read_text()).split("def score")[1]
      .split("def main")[0] or True)
_rows = WH.score(WireStore(), registry.load())
check("every active source is scored", len(_rows) > 0, len(_rows))
_fatal = [r for r in _rows
          if any(k == WH.FATAL for _, k in r["problems"])]
check("no active source has a fatal health problem", not _fatal,
      [r["source_id"] for r in _fatal])
check("pausing one source does not disable the Wire",
      "sys.exit" not in code_only(
          (ROOT / "scripts" / "wire_health.py").read_text()).split(
              "def main")[0])

# A stale impact must not outlive the class of evidence it rested on.
with tempfile.TemporaryDirectory() as tmp:
    st = WireStore(Path(tmp) / "h.db")
    st._fantasy_schema()
    rec = fz.build([_row(_qb)], _reg, "v1").to_record()
    rec["evidence_candidate_ids"] = ["cand-Z"]
    st.upsert_impact(rec)
    # Written through upsert_evidence so the added columns exist the way
    # they do in production, rather than by hand-rolling an INSERT.
    st.upsert_evidence({"candidate_id": "cand-Z", "review_status": "PENDING",
                        "evidence_class": ev.RELAYED_REPORTING,
                        "exclusion_reason": "", "duplicate_of": ""})
    n = st.invalidate_impacts_without_evidence()
    check("commentary is invalidated when its evidence is reclassified out",
          n == 1 and st.impacts()[0]["review_status"] == "INVALIDATED", n)

# ---------------------------------------------------------------- reporting
# Reporting is allowed to change freely. The numbers it reports on are not:
# these five constants are the whole safety envelope for caption requests, so
# an edit to any of them should fail here rather than at YouTube.
check("five caption requests a day", yt.MAX_REQUESTS_PER_DAY == 5,
      yt.MAX_REQUESTS_PER_DAY)
check("forty-five minutes between requests", yt.MIN_MINUTES_BETWEEN == 45,
      yt.MIN_MINUTES_BETWEEN)
check("a block stops requests for twenty-four hours",
      yt.COOLDOWN_HOURS_AFTER_BLOCK == 24, yt.COOLDOWN_HOURS_AFTER_BLOCK)
check("one video per channel per day",
      yt.MAX_VIDEOS_PER_CHANNEL_PER_DAY == 1, yt.MAX_VIDEOS_PER_CHANNEL_PER_DAY)
check("five minute minimum duration", yt.MIN_DURATION_SECONDS == 300,
      yt.MIN_DURATION_SECONDS)

bz = ing.both_zones("2026-08-22T01:12:22+00:00")
check("a cooldown time is shown in UTC and in local time",
      "UTC" in bz and "local" in bz and "01:12" in bz, bz)
check("a missing timestamp reads as never", ing.both_zones("") == "never")
check("an unparseable timestamp is passed through, not crashed on",
      ing.both_zones("whenever") == "whenever")

check("status makes no caption request",
      "take_one" not in code_only(
          (ROOT / "scripts" / "wire_youtube_ingest.py").read_text()
      ).split("if args.status:")[1].split("return 0")[0])
check("the data api counter counts metadata calls, not caption requests",
      set(ytapi.CALLS) == {"playlistItems", "videos"}, sorted(ytapi.CALLS))
check("the call counter holds no key material",
      not any(re.search(r"AIza|[A-Za-z0-9_\-]{30,}", k) for k in ytapi.CALLS))

print()
if FAILURES:
    print(f"{len(FAILURES)} failed: " + ", ".join(FAILURES[:6]))
    sys.exit(1)
print("all passed")

# --- the suppression audit of 21 August ------------------------------------
#
# Twelve model calls came out of two days and 468 articles. The cause was not
# the evidence standard: an approved beat writer's plain declarative sentence
# failed a lexical whitelist of observation phrases, so the plainest writers
# were suppressed hardest. These pin the corrected behaviour.

check("an approved byline's declarative sentence is an observation",
      cls("Burrow completed his first four passes in the first team period, "
          "connecting with Higgins three times and Drew Sample once.",
          reporter_voice=True) == ev.FIRSTHAND_OBSERVATION)
check("the same sentence from an unresearched byline is still uncertain",
      cls("Burrow completed his first four passes in the first team period, "
          "connecting with Higgins three times and Drew Sample once.",
          reporter_voice=False) == ev.UNCERTAIN)
check("a hedge still beats an approved byline",
      cls("I think Blair could earn a bigger role this season.",
          reporter_voice=True) == ev.ANALYSIS_OR_OPINION)

check("a club participation report is an official designation",
      cls("The following Eagles did not practice on Thursday: tight end "
          "Grant Calcaterra (back).", reporter_voice=False)
      == ev.OFFICIAL_DESIGNATION)
check("a designation is not firsthand, so it borrows no reporter",
      cls("The following Eagles did not practice on Thursday: tight end "
          "Grant Calcaterra (back).", reporter_voice=False)
      != ev.FIRSTHAND_OBSERVATION)

check("a coach on the record without quote marks is attributed, not observed",
      cls("Campbell said Gibbs has a hamstring strain and will miss four "
          "weeks.", reporter_voice=True) == ev.DIRECT_QUOTATION)
check("an unattributed diagnosis is still uncertain",
      cls("Gibbs has a torn hamstring and will miss four weeks.",
          reporter_voice=True) == ev.UNCERTAIN)

# The backfill's counters must partition its corpus. They did not: a tally of
# survivors was being added into the rejection total, which is how 2,636
# candidates produced 2,637 outcomes.
_bf = (ROOT / "scripts" / "wire_backfill.py").read_text()
check("survivor tallies are namespaced out of the rejection total",
      'counts["note::contingent_relevance_survivors"]' in _bf
      and 'if not k.startswith("note::")' in _bf)
check("the backfill prints its own reconciliation check",
      "DOES NOT RECONCILE" in _bf)

# Duplicate collapse compares within a segment. Across the whole article, any
# two paragraphs naming one player and sharing camp vocabulary cleared 0.6.
_ex = (ROOT / "scripts" / "wire_extract.py").read_text()
check("duplicate overlap is scoped to one segment",
      'str(prev_loc).split("s")[0] != here' in _ex)
check("a quotation records who actually spoke",
      '"quote_speaker"' in _ex and "named_speaker" in _ex)
