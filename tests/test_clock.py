"""Lifecycle behaviour. Mechanism only — no claim about real newsroom timing.

The windows in clock.py are guesses until they are fitted against timestamped
data. What these assert is that the machinery responds to a clock at all:
a story goes stale, an old story stops being offered, and a missing timestamp
changes nothing.
"""
from datetime import datetime, timedelta

from newsai import clock

T0 = datetime(2026, 8, 25, 14, 0)


def check(name, got, want):
    ok = "ok  " if got == want else "FAIL"
    print(f"{ok} {name:<52} got={got!s:<6} want={want}")
    return got == want


results = []
# --- expiry
results.append(check("fresh story stays open",
                     clock.expired(T0, T0, T0 + timedelta(minutes=5)), False))
# Expressed relative to the constants, not as literals: these assert the
# mechanism, and refitting a window must not turn them red.
results.append(check("quiet past INACTIVITY -> closed",
                     clock.expired(T0, T0, T0 + clock.INACTIVITY + timedelta(minutes=1)), True))
results.append(check("active but past MAX_AGE -> closed (cap)",
                     clock.expired(T0, T0 + clock.MAX_AGE, T0 + clock.MAX_AGE + timedelta(minutes=1)),
                     True))
results.append(check("active and young -> open",
                     clock.expired(T0, T0 + clock.INACTIVITY - timedelta(minutes=5),
                                   T0 + clock.INACTIVITY), False))
results.append(check("no clock -> never expires",
                     clock.expired(T0, T0, None), False))

# --- proximity ranks but never zeroes
results.append(check("just now = 1.0", clock.proximity(T0, T0), 1.0))
results.append(check("one half-life = 0.5",
                     round(clock.proximity(T0, T0 + clock.HALF_LIFE), 3), 0.5))
results.append(check("unknown timestamp = 0.5, not 0",
                     clock.proximity(None, T0), 0.5))
results.append(check("old story still non-zero (ranks, not filters)",
                     clock.proximity(T0, T0 + timedelta(hours=2)) > 0, True))

# --- parsing
results.append(check("bare HH:MM anchors to the given day",
                     clock.parse("14:05", T0.date()), datetime(2026, 8, 25, 14, 5)))
results.append(check("ISO-8601 parses", clock.parse("2026-08-25T14:05:00"),
                     datetime(2026, 8, 25, 14, 5)))
results.append(check("junk -> None, not a crash", clock.parse("tomorrow"), None))
results.append(check("empty -> None", clock.parse(""), None))

print(f"\n{sum(results)}/{len(results)} passed")
raise SystemExit(0 if all(results) else 1)
