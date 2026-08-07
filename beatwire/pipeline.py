"""Orchestration.

Note that this file contains no sport-specific logic whatsoever. That is the
test: if adding the NBA requires editing this file, the design has failed.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ingest
from .extract import extract
from .registry import Registry
from .resolve import Resolver
from .store import Store
import concurrent.futures as cf

# Small enough to stay well inside the rate limit, large enough
# that a run finishes before the next one is due to start.
EXTRACT_WORKERS = 8


@dataclass
class RunReport:
    sport: str
    sources_polled: int = 0
    items_fetched: int = 0
    items_new: int = 0
    items_passed_prefilter: int = 0
    nuggets_new: int = 0
    nuggets_merged: int = 0
    nuggets_unresolved: int = 0

    def __str__(self) -> str:
        saved = self.items_new - self.items_passed_prefilter
        return (
            f"[{self.sport}] {self.sources_polled} sources, "
            f"{self.items_fetched} items ({self.items_new} new), "
            f"{self.items_passed_prefilter} through prefilter "
            f"({saved} skipped, no API spend), "
            f"{self.nuggets_new} new nuggets, {self.nuggets_merged} merged, "
            f"{self.nuggets_unresolved} unresolved"
        )


def run(
    sport: str,
    store: Store,
    client=None,
    stub: bool = False,
    offline: bool = False,
    x_daily_cap: float = 5.0,
    tapi_daily_cap: float = 12.0,
    only: str | None = None,
) -> RunReport:
    reg = Registry(sport)
    resolver = Resolver(reg.players, reg.profile.position_groups)
    report = RunReport(sport=sport)

    sources = reg.enabled_sources
    if only:
        # Substring match on the source id, so `--only profootballdoc` or
        # `--only nyj` both work. Polling 121 sources to test one is the kind
        # of wait that stops you testing.
        needle = only.lower()
        sources = [s for s in sources if needle in s.id.lower()
                   or needle in (s.handle or "").lower()]
        if not sources:
            print(f"  no sources match '{only}'")
            return report

    for source in sources:
        report.sources_polled += 1
        items = ingest.fetch(source, offline=offline, store=store,
                             x_daily_cap=x_daily_cap,
                             tapi_daily_cap=tapi_daily_cap)
        report.items_fetched += len(items)

        fresh = []
        for item in items:
            if store.is_seen(item.id):
                continue
            report.items_new += 1
            store.mark_seen(item.id, source.id, item.url, item)
            if item.kind == "podcast" and not item.transcript:
                item = ingest.transcribe(item, backend="none")
            fresh.append(item)

        # Extract concurrently.
        #
        # One model call per item, run one after another, made a full pass
        # take forty minutes. The schedule fires every twenty, so runs
        # overlapped -- and because the cache only saves when a job finishes,
        # each overlapping run restored a database from before the one ahead
        # of it had done any work, saw the same articles as unseen, and paid
        # to extract them again.
        #
        # The calls are independent and spend their time waiting on the
        # network, so a small pool collapses the wall clock without touching
        # the rate limit. Everything that writes to SQLite still happens on
        # this thread, one item at a time, because the connection is not
        # shared safely across threads.
        results = []
        if fresh:
            def work(it):
                return it, extract(it, source, reg.profile, resolver,
                                   client=client, stub=stub)
            if stub or len(fresh) == 1:
                results = [work(it) for it in fresh]
            else:
                with cf.ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
                    futures = [pool.submit(work, it) for it in fresh]
                    for f in cf.as_completed(futures):
                        try:
                            results.append(f.result())
                        except Exception as exc:
                            print(f"  ! extract failed: {str(exc)[:70]}")

        for _item, nuggets in results:
            if nuggets:
                report.items_passed_prefilter += 1
            for n in nuggets:
                if not n.resolved:
                    report.nuggets_unresolved += 1
                result = store.add_nugget(n)
                if result == "new":
                    report.nuggets_new += 1
                else:
                    report.nuggets_merged += 1

        store.commit()

    return report
