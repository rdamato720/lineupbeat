"""Batch-level editorial digest for trusted NFL reports."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

from .providers.openai import MODEL, PRICES, OpenAIProviderError, redact


EVENT_TYPES = {"AVAILABILITY", "TRANSACTION", "SUSPENSION", "LEGAL", "ROLE"}
HISTORICAL = re.compile(r"(?i)\b(earlier in (?:the )?offseason|last season|last year|months ago)\b")
PRESEASON_ONLY = re.compile(r"(?i)\b(preseason finale|preseason game|preseason week)\b")
REGULAR_CONTEXT = re.compile(r"(?i)\b(week 1|regular season|season opener|53-man|"
                             r"active roster|injured reserve|\bir\b|\bpup\b)\b")
NEGATED_EVENT = re.compile(r"(?i)\b(nothing to do with|not related to|if they|might|could possibly)\b")
HIGH_SIGNAL = re.compile(
    r"(?i)\b(charged?|arrested|traded?|released?|waived?|signed?|activated|"
    r"placed on (?:injured reserve|ir|pup)|returned? to practice|"
    r"did not practice|dnp|limited participant|full participant|"
    r"concussion|walking boot|ruled out|expected to miss|will miss|"
    r"on track for week 1|named (?:the )?starter|will start|first[- ]team|"
    r"larger role|top backup)\b")
EVENT_SUPPORT = {
    "TRANSACTION": re.compile(r"(?i)\b(traded?|released?|waived?|signed?|activated|"
                               r"placed on (?:injured reserve|ir|pup)|claimed)\b"),
    "SUSPENSION": re.compile(r"(?i)\b(suspended?|suspension|discipline)\b"),
    "LEGAL": re.compile(r"(?i)\b(charged?|arrested|misdemeanor|felony)\b"),
    "AVAILABILITY": re.compile(r"(?i)\b(ruled out|questionable|doubtful|will miss|"
                                r"expected to miss|returned? to practice|did not practice|"
                                r"limited|full participant|concussion|injur(?:y|ed)|"
                                r"walking boot|surgery|pup|injured reserve)\b"),
    "ROLE": re.compile(r"(?i)\b(named (?:the )?starter|will start|first[- ]team|"
                        r"larger role|top backup|wr1|rb1|depth chart|"
                        r"inactive this season|quarterback competition)\b"),
}
INJURY_TERMS = {"ankle", "knee", "hamstring", "calf", "groin", "quad", "hip",
                "back", "shoulder", "elbow", "wrist", "hand", "foot", "toe",
                "neck", "head", "concussion", "achilles", "acl", "mcl"}

CARD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["player_id", "event_type", "bullet", "report_id",
                 "evidence_quote", "reason"],
    "properties": {
        "player_id": {"type": "string"},
        "event_type": {"type": "string", "enum": sorted(EVENT_TYPES)},
        "bullet": {"type": "string"},
        "report_id": {"type": "string"},
        "evidence_quote": {"type": "string"},
        "reason": {"type": "string"},
    },
}
RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["updates", "summary"],
    "properties": {
        "updates": {"type": "array", "minItems": 0, "maxItems": 20,
                    "items": CARD_SCHEMA},
        "summary": {"type": "string"},
    },
}

SYSTEM = """You are the news editor for Lineup Beat. Create one concise NFL
fantasy-news digest from the supplied standalone trusted reports. Nothing you
return is approved or published; a human editor reviews the complete list.

Select every qualifying concrete development in the batch, up to 20: injury/participation or
return updates, trades/releases/signings/activations, suspensions/legal news,
and official or explicit regular-season role/starter decisions. Ignore
practice highlights, generic praise, roster speculation, hypothetical events,
negated rumors, preseason-only performance, old facts repeated in a new item,
backup-quarterback activity without a starting-job consequence, and inferred
beneficiaries unless the report explicitly states a direct larger role.

Treat each REPORT as a standalone unit. The supplied identities are exact and
authoritative, but an update is allowed only when its evidence quote explicitly
names that player by full name or surname. A mention elsewhere in the report
does not make another sentence about that player. Never assign one player's
injury, transaction or role to another player named nearby.

Deduplicate the whole batch by underlying event. Keep the most complete report
when several sources cover the same event. Normally return one update per
player/event. Write one short factual sentence per bullet, name the player,
and add no fantasy-impact commentary. Do not add a team, injury area, timeline,
number, destination or transaction detail absent from the exact evidence
quote. evidence_quote must be one exact contiguous excerpt from the selected
report. report_id and player_id must be copied exactly from the supplied data.
"""


def allowed(candidate: dict) -> bool:
    return candidate.get("origin") == "X" or candidate.get("ownership") in {"OFFICIAL", "TEAM_OWNED"}


def collect(candidates: list[dict], max_reports: int = 80) -> list[dict]:
    """Collapse per-player capture rows back into standalone source reports."""
    grouped = {}
    for row in candidates:
        if not allowed(row):
            continue
        evidence = str(row.get("evidence") or "").strip()
        url = str(row.get("source_url") or "")
        if not evidence or not url.startswith("https://"):
            continue
        key = (url, evidence)
        report = grouped.setdefault(key, {
            "report_id": "report:" + hashlib.sha256(f"{url}|{evidence}".encode()).hexdigest()[:20],
            "source_name": str(row.get("source_name") or ""),
            "source_id": str(row.get("source_id") or ""),
            "author": str(row.get("author") or ""), "url": url,
            "published_at": str(row.get("published_at") or ""),
            "ownership": str(row.get("ownership") or ""),
            "origin": str(row.get("origin") or ""),
            "evidence": evidence[:4000], "identities": {},
        })
        report["identities"][row["player_id"]] = {
            "player_id": row["player_id"], "player": row["player"],
            "team": row["team"], "position": row["position"],
        }
    reports = []
    for report in grouped.values():
        report["identities"] = list(report["identities"].values())
        reports.append(report)
    reports.sort(key=report_priority, reverse=True)
    return reports[:max_reports]


def report_priority(report: dict) -> tuple:
    """Put explicit news and authoritative national/official sources first."""
    evidence = str(report.get("evidence") or "")
    signals = len({match.group(0).lower() for match in HIGH_SIGNAL.finditer(evidence)})
    source_id = str(report.get("source_id") or "").lower()
    author = re.sub(r"[^a-z]", "", str(report.get("author") or "").lower())
    national = int("natl" in source_id or author in {
        "adamschefter", "rapsheet", "robdemovsky", "byryanwood",
        "turrondavenport", "jakearthurnfl", "danieloyefusi",
    })
    official = int(report.get("ownership") in {"OFFICIAL", "TEAM_OWNED"})
    return (signals > 0, signals, national, official,
            report.get("origin") == "X", report.get("published_at", ""))


def build_prompt(reports: list[dict]) -> str:
    blocks = []
    for report in reports:
        blocks.append(
            f"REPORT {report['report_id']}\n"
            f"Source: {report['author']} · {report['source_name']}\n"
            f"URL: {report['url']}\nPublished: {report['published_at']}\n"
            f"AUTHORITATIVE IDENTITIES: {json.dumps(report['identities'], ensure_ascii=False, sort_keys=True)}\n"
            f"EVIDENCE:\n{report['evidence']}")
    return "\n\n---\n\n".join(blocks)


class OpenAIDigestProvider:
    def __init__(self, model=MODEL, transport=None):
        self.model, self._transport = model, transport

    def authenticate(self):
        if self._transport:
            return True
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise OpenAIProviderError("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
            OpenAI(api_key=key).models.retrieve(self.model)
        except Exception as exc:
            raise OpenAIProviderError(redact(f"{type(exc).__name__}: {exc}")) from None
        return True

    def draft(self, reports: list[dict]) -> tuple[dict, dict]:
        started = time.time()
        if self._transport:
            payload, usage = self._transport(build_prompt(reports))
        else:
            try:
                from openai import OpenAI
                response = OpenAI(api_key=os.environ.get("OPENAI_API_KEY")).responses.create(
                    model=self.model, instructions=SYSTEM, input=build_prompt(reports),
                    store=False, reasoning={"effort": "low"},
                    text={"format": {"type": "json_schema", "name": "wire_digest",
                                      "strict": True, "schema": RESPONSE_SCHEMA}},
                )
                payload = json.loads(response.output_text)
                raw_usage = getattr(response, "usage", None)
                usage = {"input_tokens": getattr(raw_usage, "input_tokens", 0),
                         "output_tokens": getattr(raw_usage, "output_tokens", 0)}
            except Exception as exc:
                raise OpenAIProviderError(redact(f"{type(exc).__name__}: {exc}")) from None
        tokens_in, tokens_out = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        price_in, price_out = PRICES.get(self.model, PRICES[MODEL])
        return payload, {"model": self.model, "tokens_in": tokens_in,
                         "tokens_out": tokens_out,
                         "cost_usd": tokens_in * price_in + tokens_out * price_out,
                         "latency_ms": int((time.time() - started) * 1000)}


def _named(text: str, player: str) -> bool:
    words = re.findall(r"[a-z0-9]+", player.lower())
    value = " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "
    full = " ".join(words)
    return bool(words and (f" {full} " in value or
                           (len(words[-1]) >= 4 and f" {words[-1]} " in value)))


def validate(payload: dict, reports: list[dict]) -> tuple[list[dict], list[dict]]:
    report_map = {row["report_id"]: row for row in reports}
    accepted, rejected, keys = [], [], set()
    updates = payload.get("updates")
    if not isinstance(updates, list):
        return [], [{"reason": "updates is not a list"}]
    for update in updates:
        failures = []
        report = report_map.get(update.get("report_id"))
        identity = None
        if report:
            identity = next((row for row in report["identities"]
                             if row["player_id"] == update.get("player_id")), None)
        if not report:
            failures.append("unknown report_id")
        if not identity:
            failures.append("player_id is not authoritative for the report")
        quote = str(update.get("evidence_quote") or "").strip()
        bullet = str(update.get("bullet") or "").strip()
        if not report or not quote or quote not in report["evidence"]:
            failures.append("evidence_quote is not an exact excerpt")
        if identity and not _named(quote, identity["player"]):
            failures.append("evidence quote does not name the player")
        if identity and not _named(bullet, identity["player"]):
            failures.append("bullet does not name the player")
        if not 12 <= len(bullet) <= 200:
            failures.append("bullet must be 12-200 characters")
        if update.get("event_type") not in EVENT_TYPES:
            failures.append("event_type is outside the closed vocabulary")
        elif quote and not EVENT_SUPPORT[update["event_type"]].search(quote):
            failures.append("evidence quote does not support the event type")
        if HISTORICAL.search(quote):
            failures.append("historical fact repeated as current news")
        if PRESEASON_ONLY.search(quote) and not REGULAR_CONTEXT.search(quote):
            failures.append("preseason-only development")
        if NEGATED_EVENT.search(quote):
            failures.append("negated or hypothetical event")
        quote_words = set(re.findall(r"[a-z0-9]+", quote.lower()))
        bullet_words = set(re.findall(r"[a-z0-9]+", bullet.lower()))
        invented_injuries = (bullet_words & INJURY_TERMS) - quote_words
        if invented_injuries:
            failures.append("bullet adds injury detail absent from the evidence")
        if set(re.findall(r"\b\d+(?:\.\d+)?\b", bullet)) - set(
                re.findall(r"\b\d+(?:\.\d+)?\b", quote)):
            failures.append("bullet adds a number absent from the evidence")
        key = (update.get("player_id"), update.get("event_type"), quote.lower())
        if key in keys:
            failures.append("duplicate update")
        if failures:
            rejected.append({"update": update, "failures": failures})
        else:
            keys.add(key)
            accepted.append({**update, "player": identity["player"], "team": identity["team"],
                             "position": identity["position"], "source_name": report["source_name"],
                             "author": report["author"], "source_url": report["url"],
                             "published_at": report["published_at"], "published": False})
    return accepted, rejected
