"""Time handling. Inert when the corpus carries no timestamps.

Two separate jobs, often confused:

  * lifecycle  — a story that nobody has touched for a while stops being a
                 candidate at all. This is what keeps the shortlist small and
                 stops a story from absorbing a same-named event days later.
  * proximity  — among stories still open, ones touched recently are likelier
                 matches than ones touched an hour ago. A ranking signal, never
                 a filter: a slow-burning story is still the right answer when
                 the wording matches.

INACTIVITY is fitted (see below). MAX_AGE and HALF_LIFE are still guesses. All
three are declared here, alone, so that fitting them is a one-file change.
"""
import re
from datetime import datetime, timedelta

# Fitted on fixtures/live-timed.jsonl: 623 posts, 5 Lebanese channels, 24h of
# 2026-08-25. Method: every cross-channel post pair within 6h scored by the
# same idf-weighted term overlap Stage 3 uses, pairs >= 0.50 read as the same
# event, and their time gaps measured. No hand labelling, no world knowledge.
#
#   gap median 5.9 min, p75 17.5, p90 41.1, p95 53.1, max 207
#   share of those pairs still catchable at a given window:
#     15 min 69% | 30 min 86% | 45 min 90% | 60 min 95% | 180 min 95%
#
# The curve is flat past 60 min, so a wider window buys nothing and only lets
# stories absorb unrelated later events. 30 min — the original guess — was
# discarding ~9% of cross-channel matches for no gain.
INACTIVITY = timedelta(minutes=60)

# Still guesses. MAX_AGE cannot be fitted from one 24h pull: the pairs past the
# 60 min knee are too few and too weak to tell a genuine slow follow-up from a
# lexical coincidence. HALF_LIFE is consistent with the measured gaps (p75 of
# 17.5 min lands at ~0.54) but nothing yet distinguishes it from 15 or 25.
MAX_AGE = timedelta(hours=3)
HALF_LIFE = timedelta(minutes=20)   # proximity decays to 0.5 over this span

_HHMM = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def parse(value, day=None):
    """Accept ISO-8601, epoch seconds, or a bare HH:MM anchored to `day`.

    A bare clock time is what a copy-pasted chat export gives you; it needs
    a date or consecutive posts across midnight sort backwards.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    value = str(value).strip()
    m = _HHMM.match(value)
    if m:
        base = day or datetime.now().date()
        h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        return datetime.combine(base, datetime.min.time()).replace(
            hour=h, minute=mi, second=sec)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def expired(first_seen, last_seen, now):
    """True when a story should stop being offered as a candidate."""
    if now is None:
        return False                      # no clock, no expiry
    if last_seen and now - last_seen > INACTIVITY:
        return True
    if first_seen and now - first_seen > MAX_AGE:
        return True
    return False


def proximity(last_seen, now):
    """1.0 for 'just now', decaying by half every HALF_LIFE. 0.5 when unknown.

    Unknown returns the midpoint rather than 0 so that a missing timestamp
    neither promotes nor buries a candidate — same principle as an empty field
    being UNKNOWN rather than a wildcard.
    """
    if now is None or last_seen is None:
        return 0.5
    gap = (now - last_seen).total_seconds()
    if gap <= 0:
        return 1.0
    return 0.5 ** (gap / HALF_LIFE.total_seconds())
