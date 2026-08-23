"""Validate the banked OpenAI semantic-promotion result.

The raw evaluation report remains the source of model outputs.  This small
receipt binds that report to the locked corpus, the tested commit, and the
unchanged publication file so a stale or edited report cannot satisfy
readiness silently.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from pathlib import Path


EVAL = Path("data/wire_semantic_eval.json")
CORPUS = Path("data/wire_eval_corpus.json")
RECEIPT = Path("data/wire_openai_promotion.json")
PUBLICATIONS = Path("data/wire_publications.json")
SCHEMA_VERSION = "wire-openai-promotion-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_KEYS = {
    "schema_version", "status", "qualified_on", "provider", "model",
    "run_commit", "eval_report_sha256", "corpus_sha256",
    "publications_sha256", "publication_count", "graded", "correct",
    "precision_num", "precision_den", "recall_num", "recall_den",
    "abstain_num", "abstain_den", "model_calls", "cost_usd",
    "tokens_in", "tokens_out", "median_latency_ms", "p95_latency_ms",
    "max_calls", "cap_usd",
    "promotion_gate_passed", "zero_tolerance_errors",
    "unexpected_validation_failures", "publishing_authorized",
    "deployment_triggered",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, label: str, errors: list[str]) -> dict:
    if not path.exists():
        errors.append(f"{label} is missing: {path}")
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is not valid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a JSON object")
        return {}
    return payload


def validate(receipt_path: Path = RECEIPT, eval_path: Path = EVAL,
             corpus_path: Path = CORPUS,
             publications_path: Path = PUBLICATIONS) \
        -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    receipt = _load(receipt_path, "OpenAI promotion receipt", errors)
    report = _load(eval_path, "semantic evaluation report", errors)
    corpus = _load(corpus_path, "locked semantic corpus", errors)
    publications = _load(publications_path, "Wire publications", errors)
    if not receipt or not report or not corpus or not publications:
        return receipt or None, errors

    extra = set(receipt) - _KEYS
    missing = _KEYS - set(receipt)
    if extra:
        errors.append("promotion receipt has unexpected fields: " +
                      ", ".join(sorted(extra)))
    if missing:
        errors.append("promotion receipt is missing fields: " +
                      ", ".join(sorted(missing)))
    if receipt.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"promotion schema must be {SCHEMA_VERSION}")
    if receipt.get("status") != "QUALIFIED":
        errors.append("OpenAI semantic provider is not marked QUALIFIED")
    try:
        date.fromisoformat(str(receipt.get("qualified_on") or ""))
    except ValueError:
        errors.append("promotion receipt qualified_on must be an ISO date")
    if receipt.get("provider") != "openai":
        errors.append("promotion provider must be openai")
    if not str(receipt.get("model") or "").startswith("gpt-"):
        errors.append("promotion receipt has no OpenAI model")
    if not _COMMIT.fullmatch(str(receipt.get("run_commit") or "")):
        errors.append("promotion receipt needs the tested commit SHA")
    for field, path in (("eval_report_sha256", eval_path),
                        ("corpus_sha256", corpus_path),
                        ("publications_sha256", publications_path)):
        value = str(receipt.get(field) or "")
        if not _SHA256.fullmatch(value) or value != sha256_file(path):
            errors.append(f"promotion receipt {field} does not match {path}")

    provider = (report.get("providers") or {}).get("openai") or {}
    summary = provider.get("summary") or {}
    gate = summary.get("promotion_gate") or {}
    graded = provider.get("graded") or []
    results = provider.get("results") or []
    gold = [x for x in corpus.get("items", []) if x.get("kind") == "GOLD"]
    gold_ids = {x.get("id") for x in gold}
    graded_ids = {x.get("id") for x in graded}
    result_ids = {x.get("item") for x in results}
    if (report.get("corpus") != str(corpus_path)
            or report.get("graded_items") != len(gold)
            or corpus.get("gold_items") != len(gold)
            or len(graded) != len(gold) or gold_ids != graded_ids):
        errors.append("evaluation does not cover the exact locked gold corpus")
    if len(results) != len(gold) or result_ids != gold_ids:
        errors.append("evaluation results do not match the exact locked corpus")
    if (summary.get("available") is not True
            or summary.get("locked_gold_items") != len(gold)
            or summary.get("selected_items") != len(gold)
            or summary.get("completed_items") != len(gold)):
        errors.append("OpenAI evaluation did not complete the locked corpus")
    if not gate.get("passed") or not all((gate.get("checks") or {}).values()):
        errors.append("OpenAI promotion gate did not pass every check")
    if summary.get("provider_error"):
        errors.append("OpenAI evaluation recorded a provider error")
    if gate.get("zero_tolerance_errors"):
        errors.append("OpenAI evaluation has zero-tolerance errors")
    if gate.get("unexpected_validation_failures"):
        errors.append("OpenAI evaluation has unexpected validation failures")

    comparisons = {
        "model": summary.get("model"),
        "graded": summary.get("graded"),
        "correct": summary.get("correct_num"),
        "precision_num": summary.get("precision_num"),
        "precision_den": summary.get("precision_den"),
        "recall_num": summary.get("recall_num"),
        "recall_den": summary.get("recall_den"),
        "abstain_num": summary.get("abstain_num"),
        "abstain_den": summary.get("abstain_den"),
        "model_calls": summary.get("calls"),
        "max_calls": summary.get("max_calls"),
        "cap_usd": summary.get("cap_usd"),
        "tokens_in": sum(x.get("assessment", {}).get("tokens_in", 0)
                         for x in results),
        "tokens_out": sum(x.get("assessment", {}).get("tokens_out", 0)
                          for x in results),
        "median_latency_ms": summary.get("median_latency_ms"),
        "p95_latency_ms": summary.get("p95_latency_ms"),
        "publication_count": publications.get("count"),
    }
    for field, actual in comparisons.items():
        if receipt.get(field) != actual:
            errors.append(f"promotion receipt {field} does not match evaluation")
    try:
        cost = float(receipt.get("cost_usd"))
        if (not math.isfinite(cost) or cost < 0
                or round(cost, 4) != round(float(summary.get(
                    "cost_usd_total")), 4)):
            errors.append("promotion receipt cost does not match evaluation")
    except (TypeError, ValueError):
        errors.append("promotion receipt cost is invalid")
    if receipt.get("promotion_gate_passed") is not True:
        errors.append("promotion receipt does not record a passing gate")
    if receipt.get("zero_tolerance_errors") != {}:
        errors.append("promotion receipt records zero-tolerance errors")
    if receipt.get("unexpected_validation_failures") != []:
        errors.append("promotion receipt records validation failures")
    if receipt.get("publishing_authorized") is not False:
        errors.append("semantic promotion must not authorize publishing")
    if receipt.get("deployment_triggered") is not False:
        errors.append("semantic promotion must not claim a deployment")
    return receipt, errors


def readiness(receipt_path: Path = RECEIPT, eval_path: Path = EVAL,
              corpus_path: Path = CORPUS,
              publications_path: Path = PUBLICATIONS) -> tuple[bool, str]:
    receipt, errors = validate(receipt_path, eval_path, corpus_path,
                               publications_path)
    if errors:
        return False, errors[0]
    return True, (f"{receipt['model']} passed {receipt['correct']}/"
                  f"{receipt['graded']} at ${receipt['cost_usd']:.4f}")
