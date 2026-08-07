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

from . import local_model
from .models import CATEGORIES, EVENTS, Nugget, RawItem, Source
from .registry import SportProfile
from .resolve import Resolver, normalize, surname

MODEL = os.environ.get("BEATWIRE_MODEL", "claude-haiku-4-5")

# A clip stays attached only if the post it came from is about fewer than
# this many players. Three is the line between "here is a player doing
# something" and "here is everything that happened at practice today".
MEDIA_MAX_PLAYERS = 3

# Extract only items mentioning a quarterback, back, receiver or tight end.
#
# The wire shows skill players by default and half of all items mention none:
# a practice report about four linemen costs a model call to discover nobody
# a reader will see is in it. Measured at 49% of items that produced claims.
#
# Off by default because it IS a real narrowing -- a shutdown corner going
# down changes a receiver's outlook, and somebody playing IDP wants the rest.
# Set BEATWIRE_SKILL_ONLY=1 to turn it on.
SKILL_ONLY = os.environ.get("BEATWIRE_SKILL_ONLY", "") == "1"

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
- A confirmation is not a revelation, and the claim has to say which.
  When a source says formally, officially, announced, confirmed, made it
  official, passed his physical, or signed his contract, the news is that a
  known thing has COMPLETED. It is not the thing itself.
  Washington announcing on Friday that Stefon Diggs had passed his physical
  and signed became a card reading "Signed a one-year contract worth up to
  $12 million", which reads as the deal breaking. It broke on Wednesday. A
  reader seeing that card on Friday believes he is first to something two
  days old.
  Write what actually happened today: "Passed his physical and signed;
  Washington announced it Friday." Same for a player officially placed on
  injured reserve after a week of reports, or activated from the PUP list.
  The completion is the news, and it usually scores lower than the original
  report did.

- A transaction is only news on the day it happens. Extract one only if the
  source is REPORTING it: "the Rams are signing", "agreed to terms", "per
  source", "the team announced". A sentence that mentions a move while
  discussing something else is not a report, however the sentence is built.
  Watch for the shape "[player] joined the Rams after [achievement]" and
  "traded to the Rams after winning [award]". The move is the main clause,
  so it reads like a report, and it is not: the article is about a sack
  record and the move happened in March. Two separate cards were filed for
  the same Myles Garrett signing, one calling it a trade and one a signing,
  from two articles neither of which was reporting anything.
  If a move is being used to establish who a player is now, it is context.
  Return a context_note about the subject of the article, or nothing.

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

WORKED EXAMPLES

Every one of these is a real post and the output it should have produced.
Most are here because the wire got them wrong first, and the wrong version
is shown so the failure is recognisable rather than abstract.

---
SOURCE: "Kraft opens camp on PUP. Kraft begins training camp on the PUP list
as he continues rehab from last season's knee injury."

WRONG:
  {"player": "Tucker Kraft", "event": "injury_reported",
   "claim": "Recovering from a torn ACL suffered last season.",
   "actionability": 3}

RIGHT:
  [{"player": "Tucker Kraft", "category": "injury", "event": "pup_list",
    "horizon": "day",
    "claim": "Opens training camp on the PUP list, still rehabbing a knee.",
    "actionability": 2, "tags": ["knee"]}]

WHY: The source says knee. It does not say torn, does not say ACL, does not
say season-ending. Resolving a vague injury toward the more serious reading
is the single most damaging thing an extractor can do, because a reader acts
on it. "Last season's knee injury" is also a reference to a past event, not
a report of a new one.

---
SOURCE: "The Tampa Bay Buccaneers have entered unfamiliar territory without
Mike Evans in training camp. Evans spent 12 seasons in Tampa Bay before
beginning a new chapter with the San Francisco 49ers this offseason.
Egbuka said it is a little weird not having him around."

WRONG:
  {"player": "Mike Evans", "event": "traded",
   "claim": "Traded to the San Francisco 49ers after 12 seasons with Tampa Bay."}

RIGHT:
  [{"player": "Emeka Egbuka", "category": "context", "event": "context_note",
    "horizon": "season",
    "claim": "Says the receiver room feels different in camp without Evans.",
    "actionability": 1, "tags": []}]

WHY: The article is about Egbuka. Evans moving is background, phrased in the
past ("spent 12 seasons before beginning"), and it happened months ago -- but
the article is from today, so extracting it files old news as breaking. It
also was not a trade. "Beginning a new chapter with" does not say signed,
traded, released or claimed, and guessing produces a specific false claim.

---
SOURCE: "Source: The Colts and RB Jonathan Taylor have agreed to a two-year,
$44 million extension with $39 million guaranteed, the largest ever for a
running back at signing."

RIGHT:
  [{"player": "Jonathan Taylor", "category": "transaction",
    "event": "restructure", "horizon": "season",
    "claim": "Agreed to a two-year extension worth $44 million, $39 million
              guaranteed.",
    "actionability": 3, "tags": ["contract"]}]

WHY: Actionability 3. This does not change who anybody starts on Sunday, and
an earlier rubric that asked whether a claim changed a lineup TODAY scored it
2 -- so it sank beneath a wall of practice reports. It settles a player's
role for two years. The test is whether a reader needs to know.

---
SOURCE: "Bijan Robinson was limited in practice Wednesday with what the team
called general soreness. He is expected to play Sunday."

RIGHT:
  [{"player": "Bijan Robinson", "category": "injury",
    "event": "practice_limited", "horizon": "day",
    "claim": "Limited Wednesday with general soreness, expected to play.",
    "actionability": 2, "tags": ["soreness"]}]

WHY: One nugget, not two. The limitation and the expectation are one claim
about one week. And "expected to play" keeps the source's hedge -- dropping
it turns a probability into a fact.

---
SOURCE: "With Kyren Williams sidelined, Blake Corum took the majority of
first-team reps and looked comfortable in pass protection."

RIGHT:
  [{"player": "Blake Corum", "category": "usage", "event": "first_team_reps",
    "horizon": "day",
    "claim": "Took most of the first-team work with Williams out.",
    "actionability": 3, "tags": []},
   {"player": "Kyren Williams", "category": "injury",
    "event": "practice_absent", "horizon": "day",
    "claim": "Sidelined for the session.", "actionability": 3, "tags": []}]

WHY: Two players, two nuggets. Williams being out is a claim in its own
right, not context for somebody else's. Note "took the majority of
first-team reps and looked comfortable in pass protection" is the writer's
phrasing -- the claim rewrites it rather than lifting it.

---
SOURCE: "Patriots transactions: Claimed on waivers WR Tejhaun Palmer from
Cardinals. Waived TE Jeremiah Franklin. Signed DL Marcus Harris."

RIGHT:
  [{"player": "Tejhaun Palmer", "category": "transaction", "event": "claimed",
    "horizon": "season", "claim": "Claimed off waivers from Arizona.",
    "actionability": 3, "tags": []},
   {"player": "Jeremiah Franklin", "category": "transaction",
    "event": "waived", "horizon": "season", "claim": "Waived by New England.",
    "actionability": 3, "tags": []},
   {"player": "Marcus Harris", "category": "transaction", "event": "signed",
    "horizon": "season", "claim": "Signed by New England.",
    "actionability": 3, "tags": []}]

WHY: A transaction roundup is several reports in one post, and each gets its
own nugget with the RIGHT verb. Claimed, waived and signed are different
events with different consequences, and the post says which for each.

---
SOURCE: "Washington Commanders formally announce Stefon Diggs signing. The
veteran receiver passed his physical and then signed his one-year contract.
Washington formally announced the acquisition on Friday morning. This
formally secures Diggs' services following a Wednesday pact that will pay up
to $12 million for the 2026 season."

WRONG:
  {"player": "Stefon Diggs", "event": "signed",
   "claim": "Signed a one-year contract worth up to $12 million.",
   "actionability": 3}

RIGHT:
  [{"player": "Stefon Diggs", "category": "transaction", "event": "signed",
    "horizon": "season",
    "claim": "Passed his physical and signed; Washington announced it Friday.",
    "actionability": 2, "tags": ["contract"]}]

WHY: The deal broke on Wednesday. Friday's news is that it completed --
physical passed, contract signed, announcement made. The wrong version reads
as the signing breaking, so a reader on Friday thinks he is first to
something two days old. Note the terms are dropped: they were Wednesday's
news, not today's, and repeating them is what makes the card look new.

---
SOURCE: "Reacted in real time to the Stefon Diggs news with @LoganPaulsenNFL.
What this signing means for the WR room. Listen now on @team980."

RIGHT:
  []

WHY: A show promo is not reporting. It refers to news without carrying it: no
terms, no role, no detail a reader could act on. Return an empty list rather
than manufacturing a claim from the fact that somebody discussed something.

---
SOURCE: "Practice notes: Borregales was perfect on all seven of his field
goal attempts including a long from 54 yards."

RIGHT:
  [{"player": "Andy Borregales", "category": "performance",
    "event": "performance_note", "horizon": "day",
    "claim": "Made all seven attempts in practice, longest from 54.",
    "actionability": 1, "tags": []}]

WHY: A detailed description is where paraphrasing is hardest, because the
facts arrive in an order that is difficult to restate. Compress rather than
follow the sentence: "made all seven, longest from 54" carries everything
without walking the same path as the writer.

---
SOURCE: "Gardner-Johnson is dealing with a calf strain. Separately, Buffalo's
receivers coach spoke about the new wideouts."

RIGHT:
  [{"player": "C.J. Gardner-Johnson", "category": "injury",
    "event": "injury_reported", "horizon": "day",
    "claim": "Dealing with a calf strain.", "actionability": 2,
    "tags": ["calf"]}]

WHY: Only the first sentence is about a player. A coach speaking generally
about a position group is not a claim about anybody, and inventing one to
have something to return is how a wire fills with noise.

---
SOURCE: "Jayden Daniels did not participate. Terry McLaurin was limited.
Brian Robinson was a full participant."

RIGHT:
  [{"player": "Jayden Daniels", "category": "injury",
    "event": "practice_absent", "horizon": "day",
    "claim": "Did not participate.", "actionability": 3, "tags": []},
   {"player": "Terry McLaurin", "category": "injury",
    "event": "practice_limited", "horizon": "day",
    "claim": "Limited in practice.", "actionability": 2, "tags": []},
   {"player": "Brian Robinson", "category": "injury",
    "event": "practice_full", "horizon": "day",
    "claim": "Full participant.", "actionability": 1, "tags": []}]

WHY: Three players, three nuggets, and three different actionability scores.
Not practising at all is a lineup decision; being limited changes an
expectation; a full participant is background confirming nothing is wrong.

---
SOURCE: "Chase Brown, who is on a contract year, is poised to earn the big
bucks if he can continue to improve as a receiver."

RIGHT:
  [{"player": "Chase Brown", "category": "context", "event": "context_note",
    "horizon": "season",
    "claim": "In a contract year and looking to improve as a receiver.",
    "actionability": 1, "tags": []}]

WHY: "Poised to" and "if he can" are the writer speculating. The claim keeps
what is factual -- the contract year, the stated aim -- and drops the
prediction. An extractor that hardens speculation into fact is inventing.

---
SOURCE: "Sources: Seahawks are signing RB TJ Harden, formerly of the Browns,
to a one-year deal, per his agent."

RIGHT:
  [{"player": "TJ Harden", "category": "transaction", "event": "signed",
    "horizon": "season",
    "claim": "Signing a one-year deal with Seattle.", "actionability": 3,
    "tags": ["contract"]}]

WHY: "Formerly of the Browns" is a reference to where he was, not a report
that he left -- so no second nugget about Cleveland. And "are signing" is
present tense reporting a current move, unlike "spent 12 seasons before
beginning a new chapter", which is not.

---
SOURCE: A clip captioned "Every rep from today's team period" showing
Mahomes, Kelce, Rice, Pacheco and Worthy in sequence.

RIGHT:
  []  -- or claims with NO media attached

WHY: A roundup clip belongs to no single player. Attaching it to each name
mentioned put the same video on five cards, and a reader clicking Kelce's
card to watch Kelce got a montage. If a post is about three or more players,
the video is about practice, not about a person.

---
SOURCE: "Achane has looked explosive, but the offensive line remains a
question and the schedule is brutal early."

RIGHT:
  [{"player": "De'Von Achane", "category": "performance",
    "event": "performance_note", "horizon": "season",
    "claim": "Has looked explosive in camp.", "actionability": 2,
    "tags": []}]

WHY: One nugget about one player. The line and the schedule are opinions
about a team, not claims about Achane, and folding them in would make the
claim say something the writer did not say about him. Horizon is season:
looking explosive in camp is a statement about the year, not about Wednesday.

---
SOURCE: "Rodgers, who turns 42 in December, took every first-team snap."

RIGHT:
  [{"player": "Aaron Rodgers", "category": "usage",
    "event": "first_team_reps", "horizon": "day",
    "claim": "Took every first-team snap.", "actionability": 3, "tags": []}]

WHY: His age is a parenthetical the writer added for colour. It is true, it
is not news, and it was not reported today. A claim built from it -- "is 41
years old" -- would be a fact with no source event behind it.

---
SOURCE: "Hall was limited in team drills again after tweaking something in
his hamstring on Monday."

RIGHT:
  [{"player": "Breece Hall", "category": "injury",
    "event": "practice_limited", "horizon": "day",
    "claim": "Limited again, still managing a hamstring from Monday.",
    "actionability": 3, "tags": ["hamstring"]}]

WHY: "Again" and "on Monday" mean this is ongoing, not new. Same injury,
continuing -- so the claim says so rather than reporting a fresh hamstring
injury each day it is mentioned. Distinguishing a new injury from ongoing
recovery keeps a wire from multiplying one problem into five.

---
SOURCE: "Malik Willis will start Sunday with Tua out. Willis has been taking
the majority of first-team reps this week."

RIGHT:
  [{"player": "Malik Willis", "category": "depth_chart",
    "event": "starter_named", "horizon": "day",
    "claim": "Will start Sunday with Tagovailoa out.", "actionability": 3,
    "tags": []},
   {"player": "Tua Tagovailoa", "category": "injury", "event": "ruled_out",
    "horizon": "day", "claim": "Ruled out for Sunday.", "actionability": 3,
    "tags": []}]

WHY: Both men are the subject of a claim, and both score 3 -- one is starting
who was not, the other is not playing who was. Horizon is day for both: this
is about Sunday, not about the season. A quarterback change that lasts is a
different claim, and the source would have to say so.

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

# Positions the site actually shows by default. Everything else is carried,
# displayed on request, and filtered out of the wire for most readers.
SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}


def mentions_any_player(item: RawItem, resolver: Resolver, team: str | None,
                        skill_only: bool = False) -> bool:
    """Cheap gate. Surname presence, source team first, then league-wide.

    With skill_only, an item has to mention somebody at a position the wire
    shows by default. Half of all items do not: a practice report naming four
    offensive linemen and a defensive tackle costs a model call to discover it
    is about nobody a reader will see.

    Measured on 926 items that produced claims, 458 mentioned no skill player
    at all -- 49%, every one of them paid for.

    A name we cannot match still passes. Somebody who signed this morning is
    unresolvable until the roster catches up, and that is exactly the news
    worth having.

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

    def counts(p):
        if not skill_only:
            return True
        pos = (getattr(p, "position", "") or "").upper()
        # An unknown position is not evidence against him.
        return pos in SKILL_POSITIONS or not pos

    if team:
        for p in resolver.players:
            if p.team == team and counts(p) and surname(p.name) in tokens:
                return True

    return any(counts(p) and surname(p.name) in tokens for p in resolver.players)


# ---------------------------------------------------------------------------
# Stage 2: extraction
# ---------------------------------------------------------------------------

def _strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    return s


def _call_model(prompt: str, client) -> list[dict]:
    # Cache the system prompt.
    #
    # It is identical on every call and it is most of the input: the rules,
    # the event vocabulary, the severity and reference guidance run to a few
    # thousand tokens, against a few hundred for the article itself. We were
    # paying to send all of it thirteen hundred times a day.
    #
    # A week of billing came to $59.61, of which $40.36 -- sixty-eight
    # percent -- was uncached input, with no cache reads at all.
    #
    # The marker goes on the system block only. The article text is different
    # every time and there is nothing to reuse there.
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=[{
            "type": "text",
            "text": SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
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

    if not mentions_any_player(item, resolver, team_hint,
                               skill_only=SKILL_ONLY):
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
    elif local_model.enabled():
        # A local model quotes the span that names somebody rather than
        # naming him, because one small enough to run on ten gigabytes
        # invents a first name when it does not have one. The resolver does
        # the identifying from here exactly as it does for the API path --
        # every row below is a mention, and always was.
        rows = local_model.extract_rows(item.text[:6000])
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
