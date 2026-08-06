"""RawItem -> list[Nugget].

Two stages, because unit economics decide whether this business works:

  1. A free prefilter that discards items mentioning no rostered player.
     In practice this drops the large majority of a beat feed (game recaps,
     opinion columns, ticket promos) before you spend a cent.
  2. An LLM call on what survives, returning structured nuggets.

Never reproduce source text verbatim. `claim` must be a paraphrase in your
own words with attribution and a link back. That is both the legal posture
the incumbents use and the thing that keeps beat writers willing to tolerate
you existing.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from .models import CATEGORIES, EVENTS, Nugget, RawItem, Source
from .registry import SportProfile
from .resolve import Resolver, normalize, surname

MODEL = os.environ.get("BEATWIRE_MODEL", "claude-haiku-4-5")

# A clip stays attached only if the post it came from is about fewer than
# this many players. Three is the line between "here is a player doing
# something" and "here is everything that happened at practice today".
MEDIA_MAX_PLAYERS = 3

SYSTEM = """You extract structured player notes from local beat reporting.

Rules:
- One nugget per distinct claim about one player. Split compound sentences.
- `horizon` says how long the claim stays true:
    "day"    - practice status, a single game, this week's availability.
               "Limited in practice", "did not participate", "questionable".
    "season" - a claim about the year ahead: role under a new coordinator,
               expected workload, a contract situation, a scheme change, a
               season-ending injury, a job won or lost in camp.
  When in doubt use "day". A season claim asserts something about months, so
  it should read like one: "expected to be the lead back", "will see a bigger
  role in the new offence", "out for the year".
- `event` must be exactly one token from the supplied list. It is how two
  writers reporting the SAME real-world event get merged into one item, so
  pick the token for what happened, not for how it was phrased. If a writer
  says "placed on IR" and another says "season is over after surgery", both
  are `ir_placement`. If nothing in the list fits, use `context_note`.
- `claim` must be YOUR OWN paraphrase. Never copy phrasing from the source,
  never quote, and never reproduce a source's wording or sentence structure.
  Default to one sentence under 25 words.
- If the prompt says DETAIL MODE, `claim` may run to two or three sentences
  and should carry the reasoning, not just the fact: what the injury is, what
  it typically means, and what the timeline looks like. Still your own words,
  still shorter than the original.
- Only extract claims the source actually makes. Do not infer, project, or
  add fantasy analysis. You are a wire, not an analyst.
- Match the SEVERITY of the source. Do not resolve ambiguity toward the more
  alarming reading, which is the single most damaging mistake available here.
  "Banged up", "limited", "held out as a precaution" and "not in team drills"
  are status notes, not diagnoses. Write them as what they are.
- A passing mention of ANY past event is a REFERENCE, not a REPORT. This
  applies to transactions as much as injuries, and the failure is the same
  shape: an article about Emeka Egbuka adjusting to camp mentioned that Mike
  Evans "spent 12 seasons in Tampa Bay before beginning a new chapter with
  the 49ers", and that became a card reading "Traded to the San Francisco
  49ers", filed as news from twenty hours ago. He signed there in March, and
  the sentence was background in a story about somebody else.
  Ask: is the writer REPORTING this, or REFERRING to it? A verb in the past
  perfect, a subordinate clause, or a mention used to explain something else
  is a reference. Extract nothing from it.
- Get the transaction TYPE right. Signed, traded, released and claimed are
  different events with different consequences, and "joined the 49ers" does
  not tell you which. If the source does not say, use the vaguer event
  rather than guessing at a trade.
- A parenthetical or passing mention of an injury is a REFERENCE, not a
  REPORT. "Tucker Kraft (ACL) and Luke Musgrave (neck) are banged up and out
  of team drills" says two players are limited, and names the conditions they
  are working back from. It does not say either was injured today. Extract
  "out of team drills" and, if the context supports it, that he is working
  back from an ACL injury. Do NOT write "out with an ACL injury", which
  asserts something the writer did not.
- Distinguish a NEW injury from ongoing recovery from an old one. If the
  source does not say when it happened, do not imply it just happened.
- Keep the source's hedging. "Expected to", "could", "is believed to" and
  "if he hits his benchmarks" all carry meaning, and a claim that drops them
  says more than the reporter did.
- A block marked "[Responding to]:" is somebody else's post, included so you
  know which player is under discussion. Use it to identify the player, but
  extract only what THIS source says. Do not turn the quoted report into a
  nugget of its own; the beat writer who filed it is already a source here and
  it would double up.
- If an item contains no concrete player news, return an empty list.
- `player` is the name as written in the source, including bare surnames.
  Do not try to expand or correct it. Resolution happens downstream.

Actionability rubric:
  3 = changes a lineup or roster decision, OR settles something about a
      player's season: a trade, a signing, an extension, a release, a job
      won or lost, a season-ending injury, a suspension.
  2 = changes an expectation for this week
  1 = useful background, no decision attached
  0 = noise

  The first tier used to read "changes a lineup or roster decision TODAY",
  which sounds right and is not. Jonathan Taylor signing a two-year,
  forty-four-million-dollar extension does not change who anybody starts on
  Sunday, so it scored a 2 and sank beneath a wall of practice reports. That
  is among the most consequential things that can happen to a running back.
  The test is whether a reader needs to know, not whether it moves a lineup
  this week.

Return ONLY a JSON array. No prose, no markdown fences."""

USER_TMPL = """{profile}

Valid categories: {categories}

Valid events (choose exactly one per nugget):
{events}

Source: {source_name} ({outlet}), covering {teams}
Published: {published}

--- ITEM ---
{text}
--- END ITEM ---

Return a JSON array of objects with keys:
player, category, event, horizon, claim, actionability, tags,
position_hint (optional)"""


# ---------------------------------------------------------------------------
# Stage 1: prefilter
# ---------------------------------------------------------------------------

def mentions_any_player(item: RawItem, resolver: Resolver, team: str | None) -> bool:
    """Cheap gate. Surname presence, source team first, then league-wide.

    Scoping to the source's team alone was too strict and silently discarded
    real reporting. A Rams beat writer posting "Packers TE Tucker Kraft ... is
    out here in pads" never reached the model, because Kraft is not on the LAR
    roster. Beat writers cover joint practices, opponents and league news
    constantly, so cross-team mentions are routine rather than exceptional.

    The team pass still runs first because it is ~90 players instead of ~3000
    and covers the overwhelming majority of items cheaply.
    """
    text = normalize(item.text)
    if not text:
        return False
    tokens = set(text.split())

    if team:
        for p in resolver.players:
            if p.team == team and surname(p.name) in tokens:
                return True

    return any(surname(p.name) in tokens for p in resolver.players)


# ---------------------------------------------------------------------------
# Stage 2: extraction
# ---------------------------------------------------------------------------

def _strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    return s


def _call_model(prompt: str, client) -> list[dict]:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        parsed = json.loads(_strip_fences(text))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def extract(
    item: RawItem,
    source: Source,
    profile: SportProfile,
    resolver: Resolver,
    client=None,
    stub: bool = False,
) -> list[Nugget]:
    team_hint = resolver.source_team_hint(source)

    if not mentions_any_player(item, resolver, team_hint):
        return []

    if source.detail:
        prompt_extra = ("\n\nDETAIL MODE: this source explains rather than "
                        "reports. Keep the reasoning.")
    else:
        prompt_extra = ""

    prompt = USER_TMPL.format(
        profile=profile.prompt_block(),
        categories=", ".join(CATEGORIES),
        events=", ".join(EVENTS),
        source_name=source.name,
        outlet=source.outlet,
        teams=", ".join(source.teams) or "league-wide",
        published=item.published_at.isoformat(),
        text=item.text[:12000],
    ) + prompt_extra

    if stub:
        rows = _stub_extract(item, resolver, team_hint)
    else:
        if client is None:
            raise ValueError("Pass an Anthropic client or use stub=True")
        rows = _call_model(prompt, client)

    # How many distinct players does this item talk about? Counted before the
    # loop because it is a property of the source, not of any one nugget.
    _players = {(r.get("player") or "").strip().lower()
                for r in rows if (r.get("player") or "").strip()}
    _clip_fits = len(_players) < MEDIA_MAX_PLAYERS

    nuggets = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("player"):
            continue
        mention = str(row["player"]).strip()
        player, conf = resolver.resolve(
            mention, team_hint, row.get("position_hint")
        )
        # Unresolved is published, not discarded. It shows in the team feed
        # unlinked, stays out of roster filtering, and the count of these is a
        # health metric: a spike means a stale roster or a new alias.
        category = row.get("category", "context")
        if category not in CATEGORIES:
            category = "context"
        event = str(row.get("event", "") or "").strip().lower()
        if event not in EVENTS:
            event = ""      # falls back to category-based merging
        nuggets.append(
            Nugget(
                sport=item.sport,
                player_id=player.id if player else None,
                player_name=player.name if player else mention,
                team=player.team if player else (team_hint or ""),
                category=category,
                claim=str(row.get("claim", ""))[:600 if source.detail else 280],
                actionability=int(row.get("actionability", 1)),
                confidence=round(conf, 3),
                source_id=source.id,
                source_name=source.name,
                outlet=source.outlet,
                url=item.url,
                published_at=item.published_at,
                # Source tags only. The model was free-associating labels --
                # `coachspeak`, `veteran_management`, `thumb` -- and one run
                # produced `national`, which is a routing tag the League News
                # section keys on. Model-invented tags fed straight into
                # section routing is a bug waiting to happen, and nothing
                # actually read them.
                tags=list(source.tags or []),
                horizon=("season"
                         if str(row.get("horizon", "")).strip().lower() == "season"
                         else "day"),
                raw_item_id=item.id,
                mention=mention,
                event=event,
                weight=source.effective_weight,
                # Media only when the clip plausibly shows THIS claim.
                #
                # A beat writer's practice roundup is one post covering a
                # dozen players with a single clip attached. Every nugget
                # extracted from it inherited that clip, so a card about a
                # tight end's route running played footage of warmups, and so
                # did the nine cards next to it.
                #
                # A post about one or two players with a clip is very likely
                # a clip of them. A post about six is a summary of the day.
                # The rule fails safe: at worst we show fewer clips, and a
                # missing video is better than a misleading one.
                media=(getattr(item, "media", []) or []) if _clip_fits else [],
            )
        )
    return nuggets


def _stub_extract(item: RawItem, resolver: Resolver, team: str | None) -> list[dict]:
    """Offline stand-in so the pipeline is testable without API calls.

    Sentence-splits, keeps sentences containing a rostered surname, and
    guesses a category by keyword. Crude by design. It exists to prove the
    plumbing, not to be the extractor.
    """
    keywords = {
        "injury": ["injury", "injured", "hurt", "questionable", "doubtful",
                   "ir", "pup", "mri", "strain", "sprain", "surgery", "rehab",
                   "limited", "held out", "did not practice",
                   "soreness", "tightness", "tendinitis", "day to day"],
        "transaction": ["signed", "waived", "released", "activated", "claimed",
                        "called up", "traded", "scratched", "optioned",
                        "recalled", "designated", "injured list", "placed"],
        "depth_chart": ["starter", "starting", "first team", "first-team",
                        "backup", "depth", "rotation", "pairing", "unit",
                        "lineup", "leadoff", "cleanup", "behind the plate",
                        "catching", "batting second", "batting third"],
        "usage": ["snap", "snaps", "carries", "touches", "reps", "targets",
                  "minutes", "workload", "split", "third down",
                  "available", "unavailable", "bullpen", "closing",
                  "batting order", "plate appearances"],
        "performance": ["looked", "impressed", "struggled", "standout",
                        "sharp", "comfortable"],
    }
    pool = [p for p in resolver.players if p.team == team] if team else resolver.players
    out = []
    # Split on sentence terminators AND newlines so a headline never bleeds
    # into the first sentence of the body.
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", item.text):
        low = normalize(sentence)
        tokens = set(low.split())
        hit = next((p for p in pool if surname(p.name) in tokens), None)
        if not hit:
            continue
        category = "context"
        score = 1
        for cat, words in keywords.items():
            if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
                category = cat
                score = 3 if cat in ("injury", "depth_chart", "transaction") else 2
                break
        out.append({
            "player": hit.name,
            "category": category,
            "event": {"injury":"injury_reported", "transaction":"signed",
                      "depth_chart":"depth_chart_move", "usage":"snap_share",
                      "performance":"performance_note"}.get(category, "context_note"),
            "claim": sentence.strip()[:200],
            "actionability": score,
            "tags": ["stub"],
        })
    return out
