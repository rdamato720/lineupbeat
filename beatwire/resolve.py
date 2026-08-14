"""Mention -> player_id.

This is the layer that decides whether the product is trustworthy. A nugget
filed under the wrong player is worse than a missing nugget, because it costs
the user a lineup decision and costs you the account.

The central trick is team scoping. A beat writer covering one team refers to
players the way locals do: bare surnames, no team context, nicknames. Scoring
candidates against that source's team first collapses almost all of the
ambiguity that makes league-wide name matching miserable.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from rapidfuzz import fuzz

from .models import Player, Source

SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

# Minimum surname similarity before a full-name fuzzy match is even considered.
# High on purpose: it should tolerate a typo or a dropped accent, not a
# different name that happens to share letters.
SURNAME_GATE = 85
PUNCT_RE = re.compile(r"[^\w\s]")


def split_camel(s: str) -> str:
    """TyreekHill -> Tyreek Hill.

    X handles are camelCase and they land in prose constantly: quoted tweets,
    @mentions the model has stripped the @ from, hashtags. Without this the
    resolver sees one long token, finds no surname to match, and silently
    drops a real player. Only fires on strings with no spaces, so ordinary
    names are untouched.
    """
    s = s.strip()
    if not s or " " in s:
        return s
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)


def normalize(name: str) -> str:
    """Casefold, strip accents and punctuation, drop generational suffixes."""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = PUNCT_RE.sub(" ", n).lower()
    parts = [p for p in n.split() if p not in SUFFIXES]
    return " ".join(parts)


def surname(name: str) -> str:
    parts = normalize(name).split()
    return parts[-1] if parts else ""


class Resolver:
    def __init__(self, players: list[Player], position_groups: dict | None = None):
        self.players = players
        # {"P": ["SP","RP","P"]} -> lets a context hint break a surname tie.
        self.position_groups = position_groups or {}
        self.by_id = {p.id: p for p in players}

        self._exact: dict[str, list[Player]] = defaultdict(list)
        self._surname: dict[str, list[Player]] = defaultdict(list)

        for p in players:
            for form in [p.name, *p.aliases]:
                self._exact[normalize(form)].append(p)
            self._surname[surname(p.name)].append(p)

    # -- scoring ------------------------------------------------------------

    def _in_group(self, player: Player, hint: str | None) -> bool:
        if not hint:
            return False
        return player.position in self.position_groups.get(hint, [hint])

    # Names that are the same person written two ways.
    #
    # A prefix test gets most of them -- Josh/Joshua, Cam/Cameron -- but not
    # the ones where the spelling diverges: Mike is not a prefix of Michael,
    # the fourth letter differs, and the same is true of Nick/Nicholas. Those
    # have to be written down.
    NICKNAMES = {
        "mike": "michael", "nick": "nicholas", "nic": "nicholas",
        "bo": "beau", "rob": "robert", "bob": "robert", "bobby": "robert",
        "bill": "william", "billy": "william", "will": "william",
        "dick": "richard", "rick": "richard", "ricky": "richard",
        "jim": "james", "jimmy": "james", "jamie": "james",
        "tony": "anthony", "chris": "christopher", "matt": "matthew",
        "dan": "daniel", "danny": "daniel", "dave": "david",
        "steve": "steven", "greg": "gregory", "jeff": "jeffrey",
        "ken": "kenneth", "kenny": "kenneth", "ted": "theodore",
        "tom": "thomas", "tommy": "thomas", "ben": "benjamin",
        "sam": "samuel", "alex": "alexander", "zach": "zachary",
        "zac": "zachary", "gabe": "gabriel", "nate": "nathaniel",
        "andy": "andrew", "drew": "andrew", "pat": "patrick",
        "eddie": "edward", "ed": "edward", "charlie": "charles",
        "chuck": "charles", "frank": "franklin", "jake": "jacob",
        "joe": "joseph", "joey": "joseph", "tim": "timothy",
        "ty": "tyler", "deebo": "tyshun",
    }

    def _first_ok(self, m_first: str, p_first: str) -> bool:
        """Could these be the same person's first name?

        The surname gate had no counterpart, so a contradicting first name
        scored purely on string overlap: "Harrison Bryant" matched Pat Bryant
        at 0.66, exactly what a bare "Bryant" scores. A first name that
        disagrees was being treated as no first name at all.

        It is the opposite. "Bryant" is ambiguous and 0.66 is fair. "Harrison
        Bryant" is specific, and what it specifies is that this is not Pat --
        so a claim about a newly signed tight end went onto another player's
        page.
        """
        if not m_first or not p_first or m_first == p_first:
            return True
        a = self.NICKNAMES.get(m_first, m_first)
        b = self.NICKNAMES.get(p_first, p_first)
        if a == b:
            return True
        if a[:1] != b[:1]:
            return False                     # Harrison is not Pat
        return (a.startswith(b) or b.startswith(a)
                or fuzz.ratio(a, b) >= 78)   # Chris/Coby stays out

    def _score(
        self,
        mention: str,
        player: Player,
        team_hint: str | None,
        pos_hint: str | None = None,
    ) -> float:
        m = normalize(mention)
        forms = [normalize(player.name)] + [normalize(a) for a in player.aliases]

        if " " not in m and m == surname(player.name):
            # Bare surname. Do NOT fall through to fuzzy full-name matching:
            # comparing "naylor" against "bo naylor" and "josh naylor" scores
            # the shorter first name higher, which is an artifact of string
            # length and not evidence about who was meant. Every player
            # sharing the surname starts level, and team and position decide.
            best = 0.72
        else:
            # Surname gate. Whole-name fuzzy matching is character-level and
            # produces confident nonsense: "Ricky Pearsall" scores 0.696
            # against "Erick All" purely on the shared letters in "rick" and
            # "all". A beat writer always writes the player's real surname, so
            # if no surname form is close, this is not that player. Returning
            # the nearest string instead of nothing is how a feed ends up
            # attributing an injury to a tight end on another team.
            m_sn = surname(mention)
            sn_forms = [surname(player.name)] + [surname(a) for a in player.aliases]
            if max(fuzz.ratio(m_sn, s) for s in sn_forms) < SURNAME_GATE:
                return 0.0

            # First-name gate.
            #
            # There was a surname gate and nothing for the other half, so a
            # contradicting first name scored purely on string overlap:
            # "Harrison Bryant" matched Pat Bryant at 0.66, exactly what a
            # bare "Bryant" scores. A first name that disagrees was being
            # treated as no first name at all.
            #
            # It is the opposite. "Bryant" is ambiguous and 0.66 is fair.
            # "Harrison Bryant" is specific, and what it specifies is that
            # this is not Pat -- so a claim about a newly signed tight end
            # went onto another player's page.
            #
            # Allowed through: a matching initial plus either a prefix
            # relationship or real similarity. Mike/Michael and Josh/Joshua
            # are prefixes; Bo/Beau is close enough; Harrison/Pat and
            # Chris/Coby are not.
            m_first = m.split()[0] if " " in m else ""
            if m_first and not any(
                    self._first_ok(m_first, f.split()[0] if " " in f else "")
                    for f in forms):
                return 0.0

            best = max(fuzz.token_sort_ratio(m, f) for f in forms) / 100.0

            # A single initial is not a first name.
            #
            # "C" is a prefix of Chase, so the prefix rule above let it
            # through, and "C Brown" in a Patriots-Colts summary resolved
            # to Chase Brown of Cincinnati at high confidence. But "C" is
            # equally a prefix of Cam, Cedric and Chris: an initial says
            # which letter, not which player.
            #
            # It scores as a bare surname instead. Every Brown starts
            # level and the team hint decides, which is exactly what
            # happens when a writer types "Brown" alone -- because an
            # initial carries no more information than that about who was
            # meant.
            if len(m_first) == 1:
                best = min(best, 0.72)

        if team_hint and player.team == team_hint:
            best += 0.25
        elif team_hint:
            best -= 0.15

        # Position context is the tiebreaker that makes MLB workable. A
        # 26-man roster with repeated surnames defeats team scoping alone;
        # knowing the sentence was about pitching usually does not.
        if pos_hint:
            # Deliberately weaker than the team swing. Team comes from source
            # config and is reliable; position is inferred by the model from
            # prose and is not. At equal weight a wrong position guess exactly
            # cancels a correct team hint: two Byron Youngs (LAR LB, PHI DL)
            # both scored 1.05 on a "DL" guess from a Rams source, tying and
            # forcing a refusal. Position breaks ties within a team, it does
            # not outvote the team.
            best += 0.10 if self._in_group(player, pos_hint) else -0.10

        # Deliberately NOT clamped to 1.0 here. Clamping compresses the gap
        # between the top two candidates, which is exactly the signal the
        # ambiguity check below depends on. Clamp only when reporting.
        return max(0.0, best)

    def resolve(
        self,
        mention: str,
        team_hint: str | None = None,
        pos_hint: str | None = None,
        threshold: float = 0.62,
    ) -> tuple[Player | None, float]:
        """Return (player, confidence). Confidence is deliberately returned
        rather than swallowed so the pipeline can route low-confidence hits to
        a review queue instead of publishing them."""
        if not mention:
            return None, 0.0

        m = normalize(mention)
        candidates: list[Player] = []

        if m in self._exact:
            candidates.extend(self._exact[m])
        if not candidates and " " not in m:
            candidates.extend(self._surname.get(m, []))
        if not candidates:
            # last resort: fuzzy across the team, then the league
            pool = (
                [p for p in self.players if p.team == team_hint]
                if team_hint
                else self.players
            )
            candidates = pool or self.players

        # Deduplicate by player id first. A name and one of its aliases can
        # normalize to the same string ("CeeDee Lamb" / "Ceedee Lamb"), which
        # put the same player in the candidate list twice and then tripped the
        # ambiguity check below with a gap of zero. The player resolved to
        # nobody because he matched himself.
        seen: set[str] = set()
        unique = []
        for p in candidates:
            if p.id not in seen:
                seen.add(p.id)
                unique.append(p)

        scored = sorted(
            ((self._score(mention, p, team_hint, pos_hint), p) for p in unique),
            key=lambda t: t[0],
            reverse=True,
        )
        # Second attempt on a run-together mention: "TyreekHill" is an X
        # handle, and handles land in prose all the time. Tried only after the
        # literal form fails, so names with internal capitals -- McCaffrey,
        # DeVonta -- are never damaged by a split they did not need.
        if (not scored or scored[0][0] < threshold):
            split = split_camel(mention)
            if split != mention:
                got, conf = self.resolve(split, team_hint, pos_hint, threshold)
                if got:
                    return got, conf

        if (not scored or scored[0][0] < threshold) and team_hint:
            # Cross-team mention. A team-scoped writer talking about an
            # opponent is normal, so retry league-wide rather than dropping
            # it. Safe now only because the surname gate is in place: without
            # it, a league-wide fuzzy pass returns confident nonsense.
            return self.resolve(mention, None, pos_hint, threshold)

        if not scored or scored[0][0] < threshold:
            return None, min(1.0, scored[0][0]) if scored else 0.0

        top_score, top = scored[0]

        # Genuine ambiguity: two players score within a hair of each other and
        # the team hint did not break the tie.
        if len(scored) > 1 and (top_score - scored[1][0]) < 0.08:
            # Before refusing, try prominence. Buffalo has Josh Allen and Kyle
            # Allen, both quarterbacks, so neither team nor position separates
            # them -- but a writer typing "Allen" in a Bills story means the
            # starter, and every reader knows it. Refusing there is not
            # caution, it is dropping the most newsworthy player on the roster.
            #
            # Only fires when the gap in standing is large. Two players of
            # similar prominence stay ambiguous, which is the right answer:
            # this exists to separate a franchise quarterback from his backup,
            # not to guess between two starters.
            tied = [p for s, p in scored if (top_score - s) < 0.08]
            ranked = [p for p in tied if getattr(p, "rank", 0) and p.rank < 900]
            if len(ranked) == 1:
                return ranked[0], min(1.0, top_score) * 0.92

            starters = [p for p in tied if getattr(p, "depth_order", 0) == 1]
            if len(starters) == 1:
                return starters[0], min(1.0, top_score) * 0.90

            return None, min(1.0, top_score) * 0.5

        return top, min(1.0, top_score)

    def source_team_hint(self, source: Source) -> str | None:
        return source.teams[0] if source.is_team_scoped else None
