"""The post-Call-A attribution strip.

These cases decide the rule rather than sample it: a leading "<x>:" is an
outlet credit, a quoted speaker, or a dateline, and only the first may be
removed. Every shape here is drawn from fixtures/live-timed.jsonl.
"""
from newsai.adapters import assemble, clean_credit, strip_attribution

CASES = [
    # text, cited_source, expect_strip, note
    ("مراسل الجديد: شن الطيران الحربي غارة على دوحة كفررمان", "مراسل الجديد", True,
     "the channel's own correspondent — the case that started this"),
    ("الوكالة الوطنية للإعلام: قصف مدفعي على بلدة", "الوكالة الوطنية", True,
     "credit named with one word the model's answer omits"),
    ("نيويورك تايمز: تقرير جديد عن المفاوضات", "صحيفة نيويورك تايمز", True,
     "credit named with one word the prefix omits"),
    ("ترامب: لن نتراجع عن الاتفاق", "نيويورك تايمز", False,
     "cited_source names someone else, so the prefix is the speaker"),
    ("ترامب: لن نتراجع عن الاتفاق", None, False,
     "no credit extracted at all"),
    ('قيادي في قسد المُنحلة ل"العربية": الرئيس مهتمّ بالحقوق', "العربية", False,
     "outlet sits inside a prefix that is mostly the actor"),
    ("مراسل الجديد:", "مراسل الجديد", False,
     "the credit is the whole post — nothing left to cluster on"),
    ("قال المتحدث في مؤتمر صحافي عقد اليوم في العاصمة: الوضع مستقر", "المتحدث", False,
     "a prefix that long is a sentence, not a field"),
    # a credit can close the post instead of opening it
    ("الملتقى الاقتصادي اللبناني الإماراتي يبدأ أعماله في بيروت.. التفاصيل مع مراسل الجديد",
     "مراسل الجديد", True,
     "trailer: cut back to the boundary the post itself wrote"),
    ("الملتقى يبدأ في بيروت.. التفاصيل مع مراسل الجديد", "مراسل الجديد", False,
     "same shape, but the trailer is 40%+ of the post — that is the post"),
    ("قصف مدفعي على بلدة كذا وفق مراسل الجديد", "مراسل الجديد", True,
     "trailer with no boundary: cut at the credit"),
    ("مراسل الجديد وصل إلى الموقع وبدأ التصوير", "مراسل الجديد", False,
     "credit is the subject of the sentence, not a trailer"),
    ("تصريح مراسل الجديد", "مراسل الجديد", False,
     "removing it would take most of the post"),
]


# text as received, expected assembled text, note. The tail run is the channel
# signing its own post; a mirror bot glues it onto the last word with no newline.
TAILS = [
    ("زيارة وزير الأشغال الى دمشق انطلق@petraabouhaidar #LBCI #LBCINews #Lebanon",
     "زيارة وزير الأشغال الى دمشق انطلق",
     "handle glued to the last word, then a brand tag run"),
    ("لن تدخل أي مبالغ مالية قبل حلّ مسألة السلاح#الجديد #الحدث@rifakil @Abdallahbaroudy",
     "لن تدخل أي مبالغ مالية قبل حلّ مسألة السلاح",
     "tag glued to the last word, tags and handles mixed"),
    ("فلماذا نطلب فعل كلّ شيء؟\n\n@lebanonNewsNow",
     "فلماذا نطلب فعل كلّ شيء؟",
     "handle on its own line — a sentence ending in ؟ is finished, not dangling"),
    ("قاصران في قبضة الأمن بـ #صاليما",
     "قاصران في قبضة الأمن بـ صاليما",
     "tag completes the sentence after a proclitic: keep the word, drop the #"),
    ("قاصران في قبضة الأمن ب #صاليما",
     "قاصران في قبضة الأمن ب صاليما",
     "same, with the proclitic written without tatweel"),
    ("#LBCINews", "LBCINews",
     "nothing but the signature — stripping it would leave no post at all"),
    ("استعدوا.. تحويلات مرورية باتجاه العدلية ⏬", "استعدوا.. تحويلات مرورية باتجاه العدلية",
     "a trailing pictograph is decoration"),
    ("🎥 لحظة الغارة على دوحة كفررمان", "لحظة الغارة على دوحة كفررمان",
     "and a leading one"),
    ("ارتفاع بنسبة 50% وبفارق +5 درجات وسعره $3", "ارتفاع بنسبة 50% وبفارق +5 درجات وسعره $3",
     "math and currency signs attach to figures — dropping them changes the number"),
    ("مروّج مخدّرات في قبضة الأمن | للتفاصيل ⏬", "مروّج مخدّرات في قبضة الأمن",
     "a short label behind a pipe is furniture, like the one channels put in front"),
    ("غارة على بلدة كذا | وقصف مدفعي على بلدة أخرى وأضرار كبيرة في المنازل",
     "غارة على بلدة كذا | وقصف مدفعي على بلدة أخرى وأضرار كبيرة في المنازل",
     "a full clause behind the pipe is content, not a label"),
    ("خبر | نص", "خبر | نص", "too short: the label would be a third of the post"),
]


CREDITS = [
    ("مراسل #الجديد", "مراسل الجديد", "the channel tagged the name it copied"),
    ("@LBCI_NEWS", "LBCI_NEWS", "a handle is a name with a sigil"),
    (None, None, "nothing to clean"),
]


def main():
    fails = 0
    for raw, want, note in CREDITS:
        got = clean_credit(raw)
        ok = got == want
        fails += not ok
        print(f"{'ok  ' if ok else 'FAIL'} credit| {note}")
        if not ok:
            print(f"       want: {want}\n       got : {got}")
    for raw, want, note in TAILS:
        got = assemble("t", "lbci", raw).text
        ok = got == want
        fails += not ok
        print(f"{'ok  ' if ok else 'FAIL'} tail  | {note}")
        if not ok:
            print(f"       want: {want}\n       got : {got}")

    for text, cited, want, note in CASES:
        out, notes = strip_attribution(text, cited)
        got = bool(notes)
        ok = got == want and (out != text if want else out == text)
        fails += not ok
        print(f"{'ok  ' if ok else 'FAIL'} strip={str(got):<6}want={str(want):<6}| {note}")
        if not ok:
            print(f"       in : {text}\n       out: {out}")
    total = len(CASES) + len(TAILS) + len(CREDITS)
    print(f"\n{total - fails}/{total} passed")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
