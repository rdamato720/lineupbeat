"""Event-time safeguards for rolling and live-update pages.

An article timestamp is not necessarily the timestamp of every paragraph in
the article.  Team ``/updates`` pages are commonly edited for months.  Treating
the page's latest modification time as the time of an old minicamp note made a
June Dak Prescott item look like August news.

Normal articles may use their publisher timestamp.  Rolling pages need a
stored span/event timestamp; absent that, they are retained for human review
and may not be automatically interpreted or published.
"""

from __future__ import annotations

import re


ROLLING_URL = re.compile(
    r"(?i)(?:^|/)(?:updates?|live|live-blog|tracker|news-and-notes)(?:/|$|[-_])"
)


def is_rolling_page(url: str) -> bool:
    return bool(ROLLING_URL.search(url or ""))


def automatic_currentness(url: str, event_timestamp: str = "") -> dict:
    """Whether a row has enough time provenance for automatic treatment."""
    if not is_rolling_page(url):
        return {"eligible": True, "reason": "article timestamp applies"}
    if (event_timestamp or "").strip():
        return {"eligible": True, "reason": "rolling-page span has event time"}
    return {
        "eligible": False,
        "reason": "rolling page has no span-level event timestamp",
    }
