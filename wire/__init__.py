"""The editorial Wire.

Deliberately its own package, importing nothing from `beatwire` and nothing
from `scripts`. The Wire is a news product: it reports what a beat writer
said and what that means for fantasy, and it must never read a projection, a
ranking, an ADP, a draft value, a strength-of-schedule figure or a durability
rating -- nor recommend that any of them change.

That separation is enforced rather than trusted. `scripts/test_wire.py`
walks every module here and fails the build if one of them imports the
fantasy side, opens beatwire.db or names a fantasy data file.
"""
