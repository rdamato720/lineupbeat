#!/usr/bin/env python3
"""Read-only presentation adapter for validated local College team logos."""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data/college/team_logo_provenance.json"


def load_registry() -> dict[str, dict]:
    payload = json.loads(REGISTRY.read_text())
    teams = payload.get("teams", [])
    if payload.get("required_team_count") != 68 or len(teams) != 68:
        raise ValueError("College team-logo registry is not the validated 68-team cohort")
    result = {row["internal_team_id"]: row for row in teams}
    if len(result) != 68:
        raise ValueError("College team-logo registry contains duplicate identities")
    for row in result.values():
        local = ROOT / "site" / row["local_asset_path"].lstrip("/")
        if not local.is_file():
            raise ValueError(f"missing College team-logo asset: {row['internal_team_id']}")
    return result


def initials(name: str) -> str:
    return "".join(part[0] for part in name.split() if part)[:3].upper()


def logo_html(team_id: str, team_name: str, css_class: str = "college-team-logo",
              lazy: bool = True) -> str:
    row = load_registry()[team_id]
    loading = ' loading="lazy"' if lazy else ""
    return (f'<span class="college-logo-wrap" data-fallback="{html.escape(initials(team_name))}">'
            f'<img class="{html.escape(css_class)}" src="{html.escape(row["local_asset_path"])}" '
            f'alt="{html.escape(team_name)}" width="{row["width"]}" height="{row["height"]}"{loading} '
            'onerror="this.hidden=true;this.parentElement.classList.add(\'logo-failed\')"></span>')


CSS = """
.college-logo-wrap{display:inline-grid;place-items:center;flex:none;width:2rem;height:2rem;
  border-radius:.35rem;background:rgba(255,255,255,.045);overflow:hidden;vertical-align:middle}
.college-team-logo{display:block;width:100%;height:100%;padding:.15rem;object-fit:contain}
.college-logo-wrap::after{content:attr(data-fallback);display:none;color:var(--ink);
  font:700 .64rem/1 var(--agate);letter-spacing:.04em}.college-logo-wrap.logo-failed::after{display:block}
.college-team-cell{display:inline-flex;align-items:center;gap:.5rem;min-width:0}
"""
