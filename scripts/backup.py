#!/usr/bin/env python3
"""Copy what cannot be rebuilt somewhere safe.

    python3 scripts/backup.py
    python3 scripts/backup.py --to ~/Dropbox/lineupbeat-backups
    python3 scripts/backup.py --list

WHAT IS AT RISK

The code is on GitHub, so it is safe. The database is gitignored, which is
right -- it is 118MB of derived data and it does not belong in version
control -- but "derived" is doing a lot of work in that sentence.

It was derived from posts that scroll away. A beat writer's timeline goes
back a few hundred tweets and then it is gone; the nuggets built from it are
the only record. Lose the database and you lose the archive, and with it the
two thousand player pages that Google has just started indexing. Pages
appearing and then vanishing is worse than never having published them.

The cursors matter too, in a smaller way: without them the next run refetches
everything and pays to re-extract it.

HOW IT COPIES

sqlite's own backup API rather than a file copy, because a copy taken while
the pipeline is mid-write gives you a corrupt file that looks fine until you
need it. The API takes a consistent snapshot of a live database.

Old backups are thinned rather than kept forever: every one from the last
week, then one a week. A month of daily copies of a 118MB file is 3.5GB and
you will never look at day seventeen.
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = Path.home() / "lineupbeat-backups"

# Hand-curated and genuinely irreplaceable: the source registry is months of
# research, and the roster carries aliases nobody would enjoy retyping.
ALSO = ["sources/nfl.yaml", "rosters/nfl.csv", "rosters/adp_meta.json"]


def snapshot(db: Path, out: Path) -> int:
    """A consistent copy of a database that may be being written to."""
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tmp = out.with_suffix(".tmp")
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    with tmp.open("rb") as f, gzip.open(out, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g)
    tmp.unlink()
    return out.stat().st_size


def thin(where: Path, keep_days: int = 7) -> list[Path]:
    """Keep every backup from the last week, then one a week.

    A month of daily copies of a 118MB file is three and a half gigabytes,
    and nobody has ever wanted day seventeen specifically.
    """
    backups = sorted(where.glob("beatwire-*.db.gz"))
    if len(backups) < 3:
        return []
    now = datetime.now()
    keep, seen_weeks = set(), set()
    for b in backups:
        try:
            when = datetime.strptime(b.stem.split("-", 1)[1][:10], "%Y-%m-%d")
        except ValueError:
            keep.add(b)
            continue
        if (now - when).days <= keep_days:
            keep.add(b)
        else:
            wk = when.isocalendar()[:2]
            if wk not in seen_weeks:
                seen_weeks.add(wk)
                keep.add(b)
    keep.add(backups[-1])                    # never drop the newest
    return [b for b in backups if b not in keep]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "beatwire.db"))
    ap.add_argument("--to", default=str(DEFAULT))
    ap.add_argument("--keep-days", type=int, default=7)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    where = Path(args.to).expanduser()
    where.mkdir(parents=True, exist_ok=True)

    if args.list:
        rows = sorted(where.glob("beatwire-*.db.gz"))
        if not rows:
            print(f"  nothing in {where}")
            return
        print(f"\n  {len(rows)} backups in {where}\n")
        for b in rows:
            mb = b.stat().st_size / 1e6
            print(f"    {b.name:<34} {mb:>7.1f} MB")
        print(f"\n    {sum(b.stat().st_size for b in rows)/1e9:.2f} GB total")
        print(f"\n  restore with:  gunzip -c {rows[-1].name} > beatwire.db")
        return

    db = Path(args.db)
    if not db.exists():
        sys.exit(f"  no database at {db}")

    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = where / f"beatwire-{stamp}.db.gz"
    size = snapshot(db, out)
    print(f"  {out.name}  {db.stat().st_size/1e6:.0f} MB -> "
          f"{size/1e6:.0f} MB compressed")

    for rel in ALSO:
        f = ROOT / rel
        if f.exists():
            d = where / f"{stamp}-{f.name}"
            shutil.copy2(f, d)
            print(f"  {d.name}")

    gone = thin(where, args.keep_days)
    for b in gone:
        b.unlink()
        for extra in where.glob(b.stem.split(".")[0].replace("beatwire-", "") + "-*"):
            extra.unlink(missing_ok=True)
    if gone:
        print(f"  thinned {len(gone)} older backups; "
              f"every one from the last {args.keep_days} days is kept, "
              f"then one a week")

    total = sum(b.stat().st_size for b in where.glob("beatwire-*.db.gz"))
    print(f"  {len(list(where.glob('beatwire-*.db.gz')))} backups, "
          f"{total/1e9:.2f} GB in {where}")


if __name__ == "__main__":
    main()
