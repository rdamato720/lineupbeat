"""Core data types.

Nothing in this module is sport-specific. A sport is just a string key that
selects a source registry, a roster, and a sport profile.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional


# Nugget categories. Deliberately small and shared across sports. If you find
# yourself wanting a sport-specific category, it probably belongs in `tags`.
CATEGORIES = [
    "injury",          # status, timeline, practice participation
    "usage",           # snaps, touches, reps, ice time, batting order
    "depth_chart",     # starter/backup, line combinations, rotation
    "transaction",     # signings, callups, waivers, activations
    "performance",     # looked good/bad, camp standout
    "context",         # scheme, coaching, weather, venue
]


# How much a source kind is trusted, before any per-source override.
# Primary beat reporting outranks community blogs, and today's data is the
# reason: SB Nation produced "may be downgraded to practice squad if he does
# not impress" while beat writers produced "expected to miss 7-10 days with a
# groin injury". Both are tier 3 by category, only one is worth reading.
# Volume makes this urgent rather than cosmetic: the 32 community feeds
# outproduce the beat writers roughly 4:1, so without weighting they bury
# them.
KIND_WEIGHTS = {
    "x":          2.0,
    "twitterapi": 2.0,
    "bluesky":    1.8,
    "podcast":    1.6,   # spoken beat content, once transcription is wired
    "threads":    1.5,
    "rss":        1.0,   # community blogs and team sites
}


def in_draft_season(now: datetime | None = None) -> bool:
    """Is ADP worth showing right now?

    ADP describes where players went in drafts that have not happened yet. It
    is the most useful number on the page in August and a historical artefact
    by October, so it turns itself off rather than sitting there going quietly
    stale. Roughly: on from the start of June, off once Week 1 has been played.

    Deliberately date-based rather than schedule-driven. Pulling a season
    calendar to decide whether to render a badge is a dependency this does not
    need, and being a few days off either side costs nothing -- nobody drafts
    in late May and nobody checks ADP in Week 2.
    """
    now = now or datetime.now(timezone.utc)
    # June 1 through the second Thursday of September, which is comfortably
    # after Week 1 kicks off.
    if now.month in (6, 7, 8):
        return True
    if now.month == 9:
        return now.day <= 14
    return False


# Canonical events. Deliberately small and closed: the point is that two
# writers describing the same thing land on the same token, which only works
# if the vocabulary is narrow enough that there is one obvious choice.
#
# This exists because fuzzy matching on the prose claim cannot work. Measured
# on real pairs, two writers describing the same IR placement scored 40-57 on
# token overlap, while "knee injury" versus "ankle injury" scored 93. The
# distributions are inverted, so no threshold separates them. Identity has to
# be structured; the prose is for reading, not for matching.
EVENTS = [
    # availability
    "ir_placement", "pup_placement", "activated", "cleared", "carted_off",
    "practice_full", "practice_limited", "practice_absent", "ruled_out",
    "injury_reported", "surgery", "return_timeline", "suspension",
    # roster
    "signed", "released", "waived", "traded", "retired", "restructure",
    "practice_squad", "called_up",
    # role
    "first_team_reps", "starter_named", "depth_chart_move", "position_change",
    "snap_share", "role_change", "committee",
    # other
    "performance_note", "context_note",
]


# Events group into families for merging. The event itself is kept because it
# drives display (severity colour, wording), but identity is the family.
#
# The reason: one knee injury produced three cards. A writer described the
# incident (carted_off), a second the diagnosis (injury_reported), a third the
# prognosis (return_timeline). All correct, all distinct events, and all the
# same story to anyone reading. Direction matters though, so recovery is a
# separate family from injury: "carted off" and "activated" on one day are two
# genuinely different pieces of news.
# Events that never stand alone. A timeline is always an elaboration on
# something else, and which something depends on direction: "expected to miss
# 7-10 days" elaborates an injury, "uncertain for Week 1 despite clearance"
# elaborates a return. One token, two meanings, so a fixed family cannot hold
# it -- it has to attach to whatever story the player already has that day.
JOINER_EVENTS = {"return_timeline", "context_note"}

EVENT_FAMILY = {
    # one injury episode
    "injury_reported": "injury", "carted_off": "injury",
    "return_timeline": "injury", "surgery": "injury",
    "ir_placement": "injury", "pup_placement": "injury",
    "ruled_out": "injury",
    # daily availability, deliberately not folded into the episode above:
    # limited on Wednesday and limited on Thursday are different reports, and
    # the day is already part of the key.
    "practice_limited": "practice_status", "practice_absent": "practice_status",
    # recovery, the opposite direction
    "activated": "recovery", "cleared": "recovery", "practice_full": "recovery",
    # role, where writers reach for different words for the same rep change
    "first_team_reps": "role", "starter_named": "role",
    "depth_chart_move": "role", "snap_share": "role",
    "role_change": "role", "committee": "role", "position_change": "role",
}


@dataclass
class Source:
    """One thing we poll. Never an X account."""

    id: str
    sport: str
    kind: str                      # "rss" | "podcast" | "bluesky"
    url: str = ""                  # feed url, or profile url for bluesky
    name: str = ""                 # display name, e.g. "Zack Rosenblatt"
    handle: str = ""               # bluesky handle or x username
    x_user_id: str = ""            # cache this: resolving it costs a User: Read
    outlet: str = ""
    teams: list[str] = field(default_factory=list)  # team codes this source covers
    # Free-form labels that flow onto every nugget from this source. Used to
    # route items into their own section, e.g. `medical` for a team physician
    # explaining what an injury mechanism actually means. Anything tagged is
    # pulled OUT of the main grid, so a section is never the same news twice.
    tags: list[str] = field(default_factory=list)
    # Some sources are worth more than one sentence. A team physician saying
    # "PCL sprain" is useless without "typically four to six weeks" -- the
    # interpretation IS the value, and one-line compression throws it away.
    # This still paraphrases a single source and links back; it is not
    # synthesis across sources, which would be our assertion rather than
    # theirs.
    detail: bool = False
    weight: float = 1.0            # trust prior, tuned later by scoring
    enabled: bool = True

    @property
    def effective_weight(self) -> float:
        """Kind weight times any per-source override."""
        return KIND_WEIGHTS.get(self.kind, 1.0) * self.weight

    @property
    def is_team_scoped(self) -> bool:
        """A single-team beat source lets us disambiguate bare surnames."""
        return len(self.teams) == 1


@dataclass
class RawItem:
    """An unprocessed fetched thing: an article, a post, a podcast episode."""

    source_id: str
    sport: str
    url: str
    title: str
    body: str
    published_at: datetime
    kind: str = "rss"
    audio_url: Optional[str] = None
    transcript: Optional[str] = None
    media: list = field(default_factory=list)

    @property
    def id(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()[:20]

    @property
    def text(self) -> str:
        """What the extractor actually reads."""
        parts = [self.title, self.transcript or self.body]
        return "\n\n".join(p for p in parts if p)


@dataclass
class Player:
    id: str
    sport: str
    name: str
    team: str
    position: str = ""
    aliases: list[str] = field(default_factory=list)
    espn_id: str = ""     # headshot fallback only, never used for resolution
    rank: int = 0         # fantasy relevance, lower is more notable; 0 = unknown
    depth_pos: str = ""   # official depth chart slot, e.g. "RB"
    depth_order: int = 0  # 1 = starter at that slot
    injury_status: str = ""
    years_exp: int = -1   # 0 = rookie, -1 = unknown
    # Average draft position from Fantasy Football Calculator. 0 = undrafted
    # or unmatched. This is what people actually did in real drafts, which is
    # a stronger signal than a popularity rank: it says how much a camp report
    # about this player is worth to a reader.
    adp: float = 0.0


@dataclass
class Nugget:
    """One atomic, attributable claim about one player.

    `player_id` may be None. Resolution failure is not a reason to throw the
    reporting away: an unresolved nugget still belongs in the team feed, it
    just cannot be filtered by roster. Dropping it instead produces a silent
    hole that is indistinguishable from a quiet news day, which is the worst
    failure mode this system has.
    """

    sport: str
    player_id: str | None
    player_name: str          # resolved name, or the raw mention if unresolved
    team: str
    category: str
    claim: str                 # paraphrased, one sentence, no verbatim quoting
    actionability: int         # 0-3, see extract.py for the rubric
    confidence: float          # 0-1, extractor's confidence in the resolution
    source_id: str
    source_name: str
    outlet: str
    url: str
    published_at: datetime
    tags: list[str] = field(default_factory=list)
    raw_item_id: str = ""
    mention: str = ""         # exactly what the writer wrote, always preserved
    event: str = ""           # canonical token from EVENTS; this is what merges
    # How long the claim stays true. "Limited Wednesday" describes a day;
    # "expected to see a bigger role under the new coordinator" describes a
    # season. They belong to different products -- day claims drive weekly
    # projections, season claims are the only beat information a yearly
    # projection should ever use.
    #
    # Without the distinction both were excluded together, which meant the
    # forward-looking reporting, the part that knows Omarion Hampton's role
    # is changing, never reached the season model at all.
    horizon: str = "day"      # "day" | "season"
    weight: float = 1.0       # source trust, used to rank within a tier
    media: list = field(default_factory=list)
    """Attached video/photo, if any.

    Beat writers post a lot of presser and player-interview clips where the
    tweet text is only a caption ("Ravens OL coach Dwayne Ledford on Vega
    Ioane") and every fact lives in the video. Carrying the media through
    means the card can show the clip even before transcription exists.
    """

    @property
    def resolved(self) -> bool:
        return self.player_id is not None

    # News days bucket on a shifted boundary rather than UTC midnight. UTC
    # midnight lands at 7-8pm US Eastern, mid-evening news cycle, which split
    # same-story duplicates across two "days" purely because one posted at
    # 9:55pm and another at 12:49am.
    NEWS_DAY_OFFSET_HOURS = 8

    @property
    def dedupe_key(self) -> str:
        """player + event + news-day = the same real-world story.

        Keyed on `event`, not `category`. Category is far too coarse: an IR
        placement and a practice absence are both "injury" and must stay
        separate, while six writers reporting one signing must collapse.

        Unresolved nuggets key on the normalized mention instead, so two
        writers using the same unfamiliar name still merge.
        """
        shifted = self.published_at - timedelta(hours=self.NEWS_DAY_OFFSET_HOURS)
        day = shifted.date().isoformat()
        key = self.player_id or f"?{self.mention.strip().lower()}"
        # Family, not event: see EVENT_FAMILY. Falls back to the event, then
        # the category, so a missing field degrades rather than breaks.
        axis = EVENT_FAMILY.get(self.event, self.event) or self.category
        if self.event in JOINER_EVENTS:
            axis = "?join"      # resolved against the day's stories in store
        return f"{self.sport}:{key}:{axis}:{day}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat()
        return d
