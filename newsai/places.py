"""Place hierarchy + aliases for locations.

§27's entity/alias table has no hierarchy, so `شحور` and `قضاء صور` are unrelated
strings even though one contains the other. Two channels naming the same place at
different granularity then never match.

THE DANGER: containment must not become a wildcard. `لبنان` contains every
Lebanese town. Matching a Beirut story to a Nabatieh story because both are "in
Lebanon" is a catastrophic merge. So containment counts only when the shared
ancestor is specific enough — see MIN_SHARED_LEVEL.

The gazetteer itself is DATA, in data/places.json — no place name belongs in
this file. Entries marked source="corpus" are stated verbatim in a post
("بلدة شحور قضاء صور"); "manual" ones are hand-added and need review. Per §27 a
bad alias silently merges unrelated stories, so nothing is auto-inserted — call
A proposes, a human confirms.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from newsai.lang import LANG
from newsai.normalize import normalize_key

COUNTRY, GOVERNORATE, DISTRICT, TOWN = 0, 1, 2, 3

# A shared ancestor at DISTRICT or finer is a real match.
# Sharing only a governorate or a country is NOT — that is the wildcard trap.
MIN_SHARED_LEVEL = DISTRICT


@dataclass
class Place:
    canonical: str
    level: int
    parent: str = None
    aliases: tuple = ()
    source: str = "manual"


LEVELS = {"country": COUNTRY, "governorate": GOVERNORATE,
          "district": DISTRICT, "town": TOWN}

# The gazetteer is data and lives outside the code. Nothing about any particular
# place belongs in this module — a fact written here is a fact the pipeline can
# never be measured without. Override the path with PLACES_PATH.
GAZETTEER = Path(os.getenv("PLACES_PATH") or Path(__file__).with_name("data") / f"places.{LANG}.json")


def load(path=None):
    """Read the gazetteer. A missing or empty file is valid: everything is UNKNOWN."""
    path = Path(path or GAZETTEER)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for r in raw.get("places", []):
        level = LEVELS.get(r.get("level"))
        if level is None or not r.get("canonical"):
            continue                     # an unreadable row is dropped, never guessed at
        out.append(Place(r["canonical"], level, r.get("parent"),
                         tuple(r.get("aliases") or ()), r.get("source", "manual")))
    return out


SEED = load()

_BY_ALIAS = {}
_BY_CANON = {}
for _p in SEED:
    _BY_CANON[_p.canonical] = _p
    for _a in (_p.canonical,) + tuple(_p.aliases):
        _BY_ALIAS.setdefault(normalize_key(_a), _p.canonical)

# stripped before lookup so "بلدة شحور" and "شحور" resolve alike
_PREFIXES = ("بلدة", "مدينة", "قضاء", "محافظة", "منطقة", "محلة", "قرية", "مخيم", "حي")


def resolve(name: str):
    """Place name -> canonical, or None if unknown."""
    if not name:
        return None
    key = normalize_key(name)
    if key in _BY_ALIAS:
        return _BY_ALIAS[key]
    words = key.split()
    if words and words[0] in {normalize_key(p) for p in _PREFIXES}:
        stripped = " ".join(words[1:])
        if stripped in _BY_ALIAS:
            return _BY_ALIAS[stripped]
    return None


def chain(name: str):
    """Canonical ancestor chain, most specific first. Empty if unknown."""
    canon = resolve(name)
    out = []
    seen = set()
    while canon and canon not in seen:
        seen.add(canon)
        out.append(canon)
        p = _BY_CANON.get(canon)
        canon = p.parent if p else None
    return out


def level_of(canon: str) -> int:
    p = _BY_CANON.get(canon)
    return p.level if p else TOWN


def match(a: str, b: str):
    """Compare two location strings.

    Returns (kind, shared_canonical):
      ("exact", c)     the same place
      ("contains", c)  one is genuinely inside the other (town in its district)
      ("weak", c)      siblings, or a town vs its governorate — related, but two
                       different places. NOT sufficient on its own; the caller
                       must require another field to agree.
      (None, None)     unrelated, unknown, or related only through a country
    """
    ca, cb = chain(a), chain(b)
    if not ca or not cb:
        # unknown to the table — fall back to a normalized string compare so an
        # unseeded place still matches itself
        na, nb = normalize_key(a or ""), normalize_key(b or "")
        return ("exact", na) if na and na == nb else (None, None)

    if ca[0] == cb[0]:
        return "exact", ca[0]

    # true ancestor relation: one's canonical sits in the other's chain.
    # A country-level ancestor is the wildcard trap ("لبنان" would match every
    # Lebanese town), so containment through a country is only ever weak.
    for anc in (ca[0], cb[0]):
        if (anc in cb) and (anc in ca):
            return ("weak" if level_of(anc) <= COUNTRY else "contains"), anc

    # neither contains the other — siblings under some shared ancestor
    shared = set(ca) & set(cb)
    if not shared:
        return None, None
    best = max(shared, key=level_of)
    if level_of(best) >= GOVERNORATE:
        return "weak", best
    return None, None       # shared only at country level — the wildcard trap
