"""Match keys per language, and the prompt fallback."""
from newsai import llm
from newsai.normalize import normalize_ar_key, normalize_generic_key, normalize_key


def check(name, got, want):
    ok = "ok  " if got == want else "FAIL"
    print(f"{ok} {name:<48} got={got!r}")
    return got == want


results = [
    check("arabic: alef variants and diacritics fold",
          normalize_ar_key("أَحْمَد"), normalize_ar_key("احمد")),
    check("arabic: arabic-indic digits become ascii", normalize_ar_key("٢٠٢٦"), "2026"),
    check("generic: case and accents fold",
          normalize_generic_key("Café MÜNCHEN"), "cafe munchen"),
    check("generic: punctuation splits words",
          normalize_generic_key("Berlin—Mitte: fire!"), "berlin mitte fire"),
    check("generic: invisible characters removed",
          normalize_generic_key("Ham​burg"), "hamburg"),
    check("explicit lang=en uses the generic key",
          normalize_key("Köln", lang="en"), "koln"),
    check("explicit lang=ar uses the arabic key",
          normalize_key("أحمد", lang="ar"), normalize_ar_key("أحمد")),
    check("default prompt has no unfilled placeholder",
          "{LANGUAGE}" in (llm.PROMPTS / "default" / "call_a.txt").read_text(encoding="utf-8"), True),
]

if __name__ == "__main__":
    print(f"\n{sum(results)}/{len(results)} passed")
    raise SystemExit(0 if all(results) else 1)
