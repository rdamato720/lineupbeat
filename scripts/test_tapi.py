#!/usr/bin/env python3
"""Regressions for the X fetch path. No network, no key, no spend.

    python3 scripts/test_tapi.py

The resolver has its own file because its failures are invisible. This one
exists for the same reason: every rule here is about a way the fetch can go
wrong while still looking like a quiet news day, or while quietly costing
twelve times what it should.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatwire import tapi  # noqa: E402
from beatwire.models import Source  # noqa: E402

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'  ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


class FakeStore:
    def __init__(self, cursors=None):
        self.cursors = dict(cursors or {})
        self.spend = []

    def get_cursor(self, key):
        return self.cursors.get(key)

    def set_cursor(self, key, value):
        self.cursors[key] = value

    def spend_today(self, provider):
        return sum(c for _, _, _, c in self.spend)

    def record_spend(self, provider, source_id, units, cost):
        self.spend.append((provider, source_id, units, cost))


class FakeAPI:
    """Stands in for tapi._get. Records every request it is asked to make."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, path, params, key, timeout=20):
        self.calls.append((path, params))
        if not self.responses:
            return {"tweets": []}
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    @property
    def paths(self):
        return [p for p, _ in self.calls]


def tweet(tid, when, text="Practice report: everyone was limited."):
    return {"id": str(tid), "text": text, "createdAt": when.strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "conversationId": str(tid), "url": f"https://x.com/x/status/{tid}"}


def source():
    return Source(id="nfl-nyj-tapi-test", sport="nfl", kind="twitterapi",
                  handle="TestWriter", name="Test", url="", teams=["NYJ"])


def run(responses, cursors=None, env=None):
    store = FakeStore(cursors)
    api = FakeAPI(responses)
    original, tapi._get = tapi._get, api
    saved = {k: os.environ.get(k) for k in ("BEATWIRE_TAPI_MODE",)}
    try:
        for k, v in (env or {}).items():
            os.environ[k] = v
        items = tapi.fetch(source(), store=store, key="test-key")
    finally:
        tapi._get = original
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return items, store, api


now = datetime.now(timezone.utc)

# -- 1. the search path is the default, and it is bounded by the age floor --
items, store, api = run([{"tweets": [tweet(1, now - timedelta(hours=1))]}])
check("default path is advanced_search", api.paths == [tapi.SEARCH_PATH], str(api.paths))
q = api.calls[0][1]["query"]
check("query is scoped to the handle", "from:TestWriter" in q, q)
since = int(q.split("since_time:")[1].split()[0])
floor = (now - timedelta(days=tapi.MAX_AGE_DAYS)).timestamp()
check("first run starts at the age floor, not at 2018", abs(since - floor) < 120,
      f"since={since} floor={floor:.0f}")

# -- 2. the high-water mark advances, forward, from a real post -------------
newest = now - timedelta(hours=1)
check("high-water set from the newest post received",
      store.cursors.get("nfl-nyj-tapi-test", "").startswith(newest.strftime("%Y-%m-%dT%H:%M")),
      store.cursors.get("nfl-nyj-tapi-test", ""))

# -- 3. the second poll asks only for what is new --------------------------
hw = (now - timedelta(hours=3)).isoformat()
items, store, api = run([{"tweets": [tweet(2, now - timedelta(minutes=5))]}],
                        cursors={"nfl-nyj-tapi-test": hw})
since = int(api.calls[0][1]["query"].split("since_time:")[1].split()[0])
expected = (now - timedelta(hours=3, seconds=tapi.OVERLAP_SECONDS)).timestamp()
check("second poll resumes from the high-water mark, minus the overlap",
      abs(since - expected) < 120, f"since={since} expected={expected:.0f}")

# -- 4. THE ONE THAT MATTERS: an empty answer must not move the mark -------
# Search returned an empty page once for a writer who had posted an hour
# earlier. If that advanced the mark to "now", the window would be skipped
# permanently and the posts in it would never be seen again.
hw = (now - timedelta(hours=2)).isoformat()
items, store, api = run([{"tweets": []}],
                        cursors={"nfl-nyj-tapi-test": hw,
                                 "nfl-nyj-tapi-test#reconciled": now.isoformat()})
check("an empty response does NOT advance the high-water mark",
      store.cursors["nfl-nyj-tapi-test"] == hw,
      f"{store.cursors['nfl-nyj-tapi-test']} (was {hw})")
check("an empty response does not fall back while the source is recently seen",
      api.paths == [tapi.SEARCH_PATH], str(api.paths))

# -- 5. silence past the reconcile window buys one timeline read -----------
old = (now - timedelta(hours=tapi.RECONCILE_AFTER_HOURS + 2)).isoformat()
items, store, api = run([{"tweets": []},
                         {"tweets": [tweet(3, now - timedelta(minutes=30))]}],
                        cursors={"nfl-nyj-tapi-test": old,
                                 "nfl-nyj-tapi-test#reconciled": old})
check("a source silent past the reconcile window reads the timeline",
      api.paths == [tapi.SEARCH_PATH, tapi.TIMELINE_PATH], str(api.paths))
check("the reconciliation read asks for replies",
      api.calls[1][1].get("includeReplies") == "true", str(api.calls[1][1]))
check("a post found by reconciliation advances the mark",
      store.cursors["nfl-nyj-tapi-test"] > old, store.cursors["nfl-nyj-tapi-test"])
check("reconciliation is stamped so it does not repeat every poll",
      store.cursors["nfl-nyj-tapi-test#reconciled"] > old)

# -- 6. a broken search falls back rather than going quiet -----------------
items, store, api = run([RuntimeError("HTTP 503"),
                         {"tweets": [tweet(4, now - timedelta(minutes=10))]}])
check("a failed search falls back to the timeline",
      api.paths == [tapi.SEARCH_PATH, tapi.TIMELINE_PATH], str(api.paths))
check("the fallback still yields items", len(items) == 1, f"{len(items)} items")

# -- 7. the rollback switch ------------------------------------------------
items, store, api = run([{"tweets": [tweet(5, now - timedelta(minutes=10))]}],
                        env={"BEATWIRE_TAPI_MODE": "timeline"})
check("BEATWIRE_TAPI_MODE=timeline takes the old path",
      api.paths == [tapi.TIMELINE_PATH], str(api.paths))
check("the old path asks for replies too",
      api.calls[0][1].get("includeReplies") == "true", str(api.calls[0][1]))

# -- 8. billing floors and counts -----------------------------------------
items, store, api = run([{"tweets": []}],
                        cursors={"nfl-nyj-tapi-test": now.isoformat(),
                                 "nfl-nyj-tapi-test#reconciled": now.isoformat()})
check("an empty request still bills the one-tweet floor",
      store.spend and store.spend[0][2] == 1, str(store.spend))
items, store, api = run([{"tweets": [tweet(i, now - timedelta(minutes=i))
                                     for i in range(1, 6)]}])
check("a page bills for what it returned",
      store.spend[0][2] == 5, str(store.spend))

# -- 9. the age floor still applies ---------------------------------------
items, store, api = run([{"tweets": [tweet(9, now - timedelta(days=tapi.MAX_AGE_DAYS + 3)),
                                     tweet(10, now - timedelta(hours=2))]}])
check("posts older than the age floor are dropped", len(items) == 1,
      f"{len(items)} items kept")

print()
if FAILURES:
    print(f"{len(FAILURES)} failed: " + ", ".join(FAILURES))
    sys.exit(1)
print("all passed")
