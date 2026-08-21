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
    check("every stored candidate is PENDING",
          all(r["review_status"] == "PENDING" for r in rows))
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

print()
if FAILURES:
    print(f"{len(FAILURES)} failed: " + ", ".join(FAILURES[:6]))
    sys.exit(1)
print("all passed")
