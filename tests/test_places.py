"""Assertions for the place hierarchy. The wildcard trap is the thing to protect."""
from newsai.places import match

CASES = [
    # (a, b, expected_kind, note)
    ("شحور", "قضاء صور", "contains", "town inside its district"),
    ("بلدة شحور", "شحور", "exact", "admin prefix stripped"),
    ("الزوايدة", "وسط القطاع", "contains", "stated by the corpus"),
    ("زوطر الشرقية", "النبطية", "contains", "town inside its governorate"),
    ("شحور", "المنصوري", "weak", "siblings in one district — context, not evidence"),
    ("لبنان", "شحور", "weak", "WILDCARD TRAP: country must never be a strong match"),
    ("شحور", "زوطر الشرقية", None, "different districts"),
    ("النبطية", "قضاء صور", None, "two units related only via the country"),
    ("الزوايدة", "شحور", None, "different countries"),
    ("بيروت", "النبطية", None, "unseeded vs seeded"),
    ("بيروت", "بيروت", "exact", "unseeded place still matches itself"),
    ("", "شحور", None, "empty is unknown, never a wildcard"),
]


def main():
    fails = 0
    for a, b, want, note in CASES:
        got = match(a, b)[0]
        ok = got == want
        fails += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {a or '(empty)':<15}{b:<15}"
              f"want={str(want):<9}got={str(got):<9}| {note}")
    print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
