"""YouTube ingestion for the Wire. Local only, and never a publisher.

Runs on a laptop rather than in CI: caption retrieval is routinely refused
from datacenter addresses, and a proxy to get round that is not something to
add before the pipeline has proved useful.

THE PROBLEM THIS MODULE IS BUILT AROUND

A transcript is words and timestamps. It carries no speaker labels. On
PewterReport's "Bucs DT Vita Vea Speaks!" the reporter's questions and Vea's
answers arrive as one stream, and nothing in the text says which is which.
Thirty videos across the ten approved channels produced twenty-four
transcripts and not one manual caption, so this is the normal case rather
than an edge.

So speaker identity is decided by the FORMAT of the video, from its title,
and never inferred from the content. A camp report is one approved voice. An
interview is not. Anything the title does not clearly place is treated as an
interview, because the cost of guessing wrong is a player's words published
as a reporter's.

There is no diarization here and there should not be in V1: another
uncertain model between the video and the Wire is the opposite of the point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import ytapi
from .capture import _get

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "sources" / "wire_youtube.yaml"

SINGLE_VOICE = "SINGLE_VOICE"
MULTI_SPEAKER = "MULTI_SPEAKER"
UNCERTAIN = "UNCERTAIN"

FIRSTHAND_APPROVED = "FIRSTHAND_APPROVED"
MIXED_CHANNEL = "MIXED_CHANNEL"
ANALYSIS_ONLY = "ANALYSIS_ONLY"
BLOCKED = "BLOCKED"

AUTO_READY = "AUTO_READY"
MANUAL_REVIEW_ONLY = "MANUAL_REVIEW_ONLY"


@dataclass
class Channel:
    source_id: str
    team: str
    source_name: str
    channel_id: str
    handle: str
    canonical_url: str
    approved_reporters: list[str]
    classification: str
    attends_practice: bool
    transcript_languages: list[str]
    active: bool
    verified_on: str = ""
    verified_by: str = ""
    blocked_reason: str = ""
    title_filter: str = ""

    @property
    def pollable(self) -> bool:
        return (self.active and self.classification != BLOCKED
                and bool(self.channel_id))


@dataclass
class Rules:
    """Registry-wide settings. `paused` is the production kill switch."""

    min_transcript_chars: int = 1500
    transcript_languages: list[str] = field(default_factory=lambda: ["en"])
    allowed_transcript_sources: list[str] = field(
        default_factory=lambda: ["MANUAL_CAPTIONS", "AUTO_CAPTIONS"])
    multi_patterns: list[str] = field(default_factory=list)
    single_patterns: list[str] = field(default_factory=list)
    # The production kill switch, declared in the registry rather than in an
    # environment variable: the pause is an editorial decision about this
    # pilot, and it should travel with the pilot's own configuration.
    paused: bool = False


def load(path: Path | None = None) -> tuple[list[Channel], Rules]:
    doc = yaml.safe_load((path or REGISTRY).read_text()) or {}
    d = doc.get("defaults") or {}
    rules = Rules(
        min_transcript_chars=int(d.get("min_transcript_chars", 1500)),
        transcript_languages=d.get("transcript_languages") or ["en"],
        allowed_transcript_sources=d.get("allowed_transcript_sources")
        or ["MANUAL_CAPTIONS", "AUTO_CAPTIONS"],
        multi_patterns=doc.get("multi_speaker_patterns") or [],
        single_patterns=doc.get("single_voice_patterns") or [],
        paused=bool(doc.get("paused", False)),
    )
    out = []
    for row in doc.get("sources", []):
        out.append(Channel(
            source_id=row["source_id"], team=row.get("team", ""),
            source_name=row.get("source_name", ""),
            channel_id=row.get("channel_id", ""),
            handle=row.get("handle", ""),
            canonical_url=row.get("canonical_url", ""),
            approved_reporters=row.get("approved_reporters") or [],
            classification=row.get("classification", BLOCKED),
            attends_practice=bool(row.get("attends_practice", False)),
            transcript_languages=row.get("transcript_languages")
            or rules.transcript_languages,
            active=bool(row.get("active", False)),
            verified_on=str(row.get("verified_on", "")),
            verified_by=row.get("verified_by", "") or "",
            blocked_reason=row.get("blocked_reason", "") or "",
            title_filter=row.get("title_filter", "") or "",
        ))
    return out, rules


def problems(channels: list[Channel]) -> list[str]:
    bad, seen = [], set()
    for c in channels:
        if c.source_id in seen:
            bad.append(f"{c.source_id}: duplicate source_id")
        seen.add(c.source_id)
        # A handle is not an identity. Only a UC id is.
        if not re.fullmatch(r"UC[\w-]{22}", c.channel_id or ""):
            bad.append(f"{c.source_id}: {c.channel_id!r} is not a UC channel id")
        if c.classification == BLOCKED and c.active:
            bad.append(f"{c.source_id}: BLOCKED but active")
        if c.classification == BLOCKED and not c.blocked_reason:
            bad.append(f"{c.source_id}: BLOCKED with no recorded reason")
        if not c.verified_on or not c.verified_by:
            bad.append(f"{c.source_id}: no verification date or evidence")
        if c.pollable and not c.approved_reporters:
            bad.append(f"{c.source_id}: active with no approved reporter")
        # A network channel with no filter would file Sixers and Suns
        # coverage as football reporting.
        if c.pollable and c.classification == MIXED_CHANNEL and not c.title_filter:
            bad.append(f"{c.source_id}: MIXED_CHANNEL with no title filter")
    return bad


def speaker_mode(title: str, rules: Rules) -> str:
    """One voice, several, or unknown -- from the title alone.

    Multi wins ties. "Packers Training Camp Report" is one voice;
    "Camp Report with Matt LaFleur" is not, and the safe reading of an
    ambiguous title is the one that sends it to a human.
    """
    t = (title or "").lower()
    for p in rules.multi_patterns:
        if re.search(p, t):
            return MULTI_SPEAKER
    for p in rules.single_patterns:
        if re.search(p, t):
            return SINGLE_VOICE
    return UNCERTAIN


def readiness(channel: Channel, mode: str) -> str:
    """Whether a candidate may be created without a human first seeing it.

    AUTO_READY still means a candidate, never a publication: every Wire item
    passes review_wire.py regardless of how it was found.
    """
    if mode == SINGLE_VOICE and channel.classification in (
            FIRSTHAND_APPROVED, MIXED_CHANNEL):
        return AUTO_READY
    return MANUAL_REVIEW_ONLY


# The transcript budget. Not tuning knobs -- these are what the address will
# tolerate. Thirty caption requests worked; roughly forty in an hour earned an
# IpBlocked that outlasted several minutes, so the budget sits far below the
# line rather than near it.
MAX_REQUESTS_PER_DAY = 5
MIN_MINUTES_BETWEEN = 45
MAX_VIDEOS_PER_CHANNEL_PER_DAY = 1
COOLDOWN_HOURS_AFTER_BLOCK = 24
MIN_DURATION_SECONDS = 300          # under five minutes is a clip, not a report


def duration_seconds(video_id: str) -> tuple[int, str]:
    """How long the video is, without asking for captions.

    The watch page carries lengthSeconds in its metadata. This is the cheap
    half of discovery -- metadata costs nothing against the caption limit,
    and knowing the length is what keeps a two-minute highlight from spending
    one of five daily transcript requests.

    With a Data API key this is videos.list(part=contentDetails), one unit.
    """
    status, html, _ = _get(f"https://www.youtube.com/watch?v={video_id}",
                           timeout=25)
    if not (isinstance(status, int) and status == 200 and html):
        return 0, f"http {status}"
    m = re.search(r'"lengthSeconds":"(\d+)"', html)
    if not m:
        m = re.search(r'"approxDurationMs":"(\d+)"', html)
        if m:
            return int(m.group(1)) // 1000, ""
        return 0, "no duration in page"
    return int(m.group(1)), ""


def eligible(channel: Channel, video: dict, rules: Rules,
             seconds: int | None = None) -> tuple[bool, str, str]:
    """Whether this video may spend one of the day's five requests.

    Returns (ok, speaker_mode, why_not). Deliberately strict: the budget is
    small enough that one wasted request is a fifth of the day.
    """
    title = video.get("title", "")
    if channel.title_filter and channel.title_filter.lower() not in title.lower():
        return False, "", f"not a {channel.title_filter} video"
    mode = speaker_mode(title, rules)
    if mode != SINGLE_VOICE:
        # Interviews, press conferences, panels and anything ambiguous are
        # still reviewable material -- they are just not worth a scarce
        # request while the budget is five a day.
        return False, mode, f"{mode.lower()} format"
    if re.search(r"#shorts\b|\bshorts?\b", title, re.I):
        return False, mode, "short"
    if seconds is not None and seconds < MIN_DURATION_SECONDS:
        return False, mode, f"only {seconds}s long"
    return True, mode, ""


def discover(channel: Channel, limit: int = 10) -> tuple[list[dict], str, str]:
    """Recent uploads with as much metadata as the route can give.

    Returns (videos, method, note). The Data API is preferred and gives
    duration; RSS is the fallback and does not, which is why a video
    discovered by RSS with no safely established length is never eligible for
    an automatic transcript request.

    Either way this costs nothing against the transcript budget. Metadata and
    captions are different endpoints with different limits, and conflating
    them is how a discovery run burns a day's allowance.
    """
    if ytapi.available():
        try:
            vids = ytapi.list_uploads(channel.channel_id, limit=limit)
            ids = [v["video_id"] for v in vids]
            try:
                lengths = ytapi.durations(ids)
            except ytapi.YouTubeAPIError as e:
                lengths = {i: None for i in ids}
                note = f"durations unavailable: {e}"
            else:
                note = ""
            for v in vids:
                v["duration_seconds"] = lengths.get(v["video_id"])
            return vids, ytapi.DATA_API, note
        except ytapi.YouTubeAPIError as e:
            # A missing, invalid, restricted or exhausted key all land here,
            # and all mean the same thing: carry on with less.
            vids, err = uploads(channel, limit=limit)
            for v in vids:
                v["duration_seconds"] = None
                v["discovery_method"] = ytapi.RSS
                v["channel_id"] = channel.channel_id
                v["channel_name"] = channel.source_name
            return vids, ytapi.RSS, f"data api unavailable ({e}); used RSS"
    vids, err = uploads(channel, limit=limit)
    for v in vids:
        v["duration_seconds"] = None
        v["discovery_method"] = ytapi.RSS
        # RSS gives no owner id, so the channel we asked is the best claim
        # available -- and it is exactly why RSS-discovered videos need the
        # Data API before they can be transcribed automatically.
        v["channel_id"] = channel.channel_id
        v["channel_name"] = channel.source_name
    return vids, ytapi.RSS, err or "no YOUTUBE_API_KEY; used RSS"


def owner_matches(channel: Channel, video: dict) -> bool:
    """The video must belong to the registered channel.

    Identity is the UC id and nothing else. A display name or a handle can be
    changed by its owner or claimed by somebody else -- @PHLYEagles is a
    stranger's travel channel -- so neither is ever accepted as proof.
    """
    owner = (video.get("channel_id") or "").strip()
    return owner == channel.channel_id


def uploads(channel: Channel, limit: int = 15) -> tuple[list[dict], str]:
    """Recent uploads, from the channel feed.

    The keyless equivalent of the uploads playlist: same ordering, no quota,
    fifteen most recent. When a Data API key exists this becomes
    playlistItems.list, which costs one unit against search.list's hundred --
    at ten-minute polling across a hundred channels that is the difference
    between fitting the daily quota and needing ninety-six times it.
    """
    url = ("https://www.youtube.com/feeds/videos.xml?channel_id="
           + channel.channel_id)
    status, xml, _ = _get(url, timeout=30)
    if not (isinstance(status, int) and status == 200 and xml):
        return [], f"feed http {status}"
    out = []
    for m in re.finditer(r"<entry>(.*?)</entry>", xml, re.S):
        e = m.group(1)
        vid = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", e)
        title = re.search(r"<title>(.*?)</title>", e, re.S)
        pub = re.search(r"<published>([^<]+)</published>", e)
        desc = re.search(r"<media:description>(.*?)</media:description>", e, re.S)
        if not vid:
            continue
        out.append({
            "video_id": vid.group(1),
            "title": _unescape((title.group(1) if title else "").strip()),
            "description": _unescape((desc.group(1) if desc else "").strip()),
            "published_at": pub.group(1) if pub else "",
            "url": f"https://www.youtube.com/watch?v={vid.group(1)}",
        })
        if len(out) >= limit:
            break
    return out, ""


def _unescape(s: str) -> str:
    import html
    return html.unescape(s)


def fetch_transcript(video_id: str, languages: list[str]) -> dict:
    """Captions, with their provenance kept.

    Manual and generated captions are different evidence and are labelled as
    such. Automatic speech recognition is never a silent fallback: if Whisper
    is ever added it gets its own transcript_source value and its own, higher
    review bar.
    """
    out = {"ok": False, "transcript_source": None, "language": None,
           "segments": [], "chars": 0, "error": ""}
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        listing = api.list(video_id)
        chosen = None
        for t in listing:                      # prefer a human-written track
            if not t.is_generated and t.language_code in languages:
                chosen = t
                break
        if chosen is None:
            for t in listing:
                if t.language_code in languages:
                    chosen = t
                    break
        if chosen is None:
            out["error"] = "no caption track in an allowed language"
            return out
        segs = [{"start_seconds": round(s.start, 2),
                 "duration_seconds": round(s.duration, 2),
                 "text": s.text}
                for s in chosen.fetch()]
        out.update(ok=True,
                   transcript_source=("MANUAL_CAPTIONS" if not chosen.is_generated
                                      else "AUTO_CAPTIONS"),
                   language=chosen.language_code,
                   segments=segs,
                   chars=sum(len(s["text"]) for s in segs))
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


def deep_link(video_id: str, start_seconds: float) -> str:
    """The source link a reviewer clicks: the video, at the moment claimed."""
    return f"https://youtube.com/watch?v={video_id}&t={int(start_seconds)}s"


def evidence_spans(video_id: str, segments: list[dict],
                   window: int = 8) -> list[dict]:
    """Timestamped passages a claim can be hung on.

    Segments arrive a line at a time, which is too small to carry meaning, so
    they are grouped into passages of a few seconds each. Every passage keeps
    its start and end and its own deep link -- with no speaker label, because
    the transcript does not have one and this module will not invent it.
    """
    spans = []
    for i in range(0, len(segments), window):
        chunk = segments[i:i + window]
        if not chunk:
            continue
        start = chunk[0]["start_seconds"]
        end = chunk[-1]["start_seconds"] + chunk[-1]["duration_seconds"]
        text = " ".join(s["text"].replace("\n", " ") for s in chunk).strip()
        if not text:
            continue
        spans.append({"start_seconds": start, "end_seconds": round(end, 2),
                      "text": text, "url": deep_link(video_id, start)})
    return spans
