"""Stage 0 — per-channel post assembly.

Every channel examined carries channel-specific boilerplate (footers, credit
lines, bilingual duplicates, mirrored-post prefixes) that defeats a Stage 1 rule
or wastes tokens.

Each adapter strips boilerplate and extracts whatever fields the channel gives
for free, so Stage 2 pays only for what is actually missing.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from newsai.normalize import _INVISIBLE, normalize_key


@dataclass
class Channel:
    name: str
    own_domains: tuple = ()          # links here are "read more", not an article signal
    footer_patterns: tuple = ()      # fixed per-post boilerplate, stripped before the gate
    bilingual: bool = False          # post carries a translation below a divider rule
    prefix_fields: tuple = ()        # which of (location, source) the prefix carries, in order
    prefix_stopwords: tuple = ()     # markers that look like a prefix but are not a source
    lead_patterns: tuple = ()        # leading section labels ("📹 تقرير مصور |") — boilerplate, not content


CHANNELS = {
    "hdth": Channel(
        name="hdth",
        own_domains=("hdth.tv",),
    ),
    "mayadeen": Channel(
        name="mayadeen",
        prefix_fields=("location", "source"),
    ),
    "mtv_desk": Channel(
        name="mtv_desk",
        prefix_fields=("source",),
        prefix_stopwords=("بالصورة", "بالفيديو", "بالصور", "عاجل"),
    ),
    "cnbc_ar": Channel(
        name="cnbc_ar",
        prefix_fields=("source",),
    ),
    "almanar": Channel(
        name="almanar",
        own_domains=("almanar.com.lb",),
        # emoji marker, optional section label, pipe separator
        lead_patterns=(r"^\s*[\U0001F300-\U0001FAFF\u2600-\u27BF]+\s*(?:[^|\n]{0,30}\|)?\s*",),
    ),
    "bintjbeil": Channel(
        name="bintjbeil",
        bilingual=True,
        footer_patterns=(r"📲.*$", r"https?://whatsapp\.com/\S+",),
    ),
    "lebanon_news": Channel(
        name="lebanon_news",
        lead_patterns=("ورد الآن",),
    ),
}

DEFAULT = Channel(name="_default")

_URL = re.compile(r"https?://([^\s/]+)")
# The whole link, for removal. Links carry no clustering signal — a shortener
# hash is unique per post, so leaving one in gives idf's highest weight to a
# string that means nothing.
_URL_FULL = re.compile(r"https?://\S+")
# Some channels are mirrors of another platform and append the origin post's
# own timestamp in English prose. It is a second copy of a field the ingester
# already carries, and it is the same on every post from that channel.
_DATELINE = re.compile(
    r"(?m)^[ \t]*[A-Z][a-z]+ \d{1,2}, \d{4}(?: at \d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?)?[ \t]*$")
_HASHTAG = re.compile(r"#(\S+)")
# A trailing run of #tags and @handles is the channel signing its own post: its
# brand tags, its reporters. It repeats on every post from that channel, so it
# says nothing about the event — and being identical across posts, it looks like
# agreement to anything that compares two posts by their words.
#
# The run does not have to be on its own line. A mirror bot glues it straight
# onto the last word of the sentence ("...السلاح#الجديد #الحدث@rifakil"), which
# is why the earlier line-anchored version missed 133 posts across lbci and
# aljadeed and left "LBCI LBCILebanon LBCIVideos" sitting in the headline.
_META_TAIL = re.compile(r"(?:[ \t]*[#@][^\s#@]+)+[\s]*$")
# A hashtag INSIDE a sentence is a word the channel happened to tag; deleting it
# removes the actor or the object. The trailing run is the same thing whenever
# the sentence runs into it — measured on 825 posts, exactly one case: a tag that
# completes the sentence after a bare proclitic ("...في قبضة الأمن بـ #صاليما").
# A token ending in tatweel is an unfinished word, so what follows is content.
_DANGLING = re.compile(r"\u0640$")
# Pictographs and arrows carry no fact and no clustering signal — normalize.py
# already reads every symbol as a space when it matches two posts, so the text a
# reader sees should agree. Only So/Sk go: Sm and Sc attach to numbers (+5, $3,
# 20°C is So but 50% is punctuation), and dropping those changes a figure.
# A short segment hung off the end behind a pipe is a label, the same furniture as
# the "📹 تقرير مصور |" a channel puts in front. Shape only — one or two words
# after the bar — so it does not depend on which word the channel uses. Measured
# on 282 live posts: every pipe in the corpus is this shape.
_LABEL_TAIL = re.compile(r"\s*[|\uFF5C\u00A6]\s*\S+(?:\s+\S+)?\s*$")


def _strip_label_tail(text: str) -> str:
    m = _LABEL_TAIL.search(text)
    if not m or m.start() == 0:
        return text
    head = text[:m.start()].rstrip()
    # a label is an afterthought. A third of the post is the post.
    if not head or (len(text) - len(head)) * 5 > len(text) * 2:
        return text
    return head


def _drop_pictographs(text: str) -> str:
    return "".join(" " if unicodedata.category(ch) in ("So", "Sk") else ch for ch in text)


# Stripping a lead marker leaves the separator that introduced it ("🚨 / …").
# Any run of symbols and separators before the first word is decoration.
_LEADING_JUNK = re.compile(r"^[\s\W\d]*?(?=[^\W\d_])", re.UNICODE)
# a prefix segment: short, ends in a colon, no sentence punctuation inside
_PREFIX = re.compile(r"^\s*([^:\n]{1,40}?)\s*:\s*")


@dataclass
class Post:
    id: str
    channel: str
    raw: str                  # exactly as received — what the LLM reads
    text: str                 # boilerplate stripped — what the gate and hash see
    location: Optional[str] = None    # free from the channel, if it provides one
    source: Optional[str] = None      # attribution slot
    external_links: list = field(default_factory=list)   # links NOT on the channel's own domain
    own_links: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _strip_footer(text: str, ch: Channel) -> tuple:
    notes = []
    for pat in ch.footer_patterns:
        new = re.sub(pat, "", text, flags=re.MULTILINE | re.DOTALL)
        if new != text:
            notes.append(f"footer stripped: {pat}")
            text = new
    return text.strip(), notes


def _strip_lead(text: str, ch: Channel) -> tuple:
    """Leading section labels. Same category as a footer: how the channel dresses
    a post, carrying no fact about the event."""
    notes = []
    for pat in ch.lead_patterns:
        new = re.sub(pat, "", text, count=1)
        if new != text:
            notes.append(f"lead label stripped: {pat}")
            text = new
    return text.lstrip(), notes


# A divider rule: a line that is nothing but a run of one repeated glyph. Matching
# the *shape* rather than a literal survives a channel swapping ─ for ــ or ===,
# which is exactly how the first version of this broke.
_DIVIDER = re.compile(r"^[ \t]*([\u2500-\u257F\u0640=_*~\-–—])\1{4,}[ \t]*$", re.MULTILINE)


def _strip_translation(text: str, ch: Channel) -> tuple:
    """Keep the Arabic half of a bilingual post. Halves token cost, undilutes hashes."""
    if not ch.bilingual:
        return text, []
    m = _DIVIDER.search(text)
    if not m:
        return text, []
    return text[:m.start()].strip(), ["translation stripped at divider"]


def _parse_prefixes(text: str, ch: Channel) -> tuple:
    """Consume as many leading '<field>:' segments as the channel declares."""
    found, notes = {}, []
    for slot in ch.prefix_fields:
        m = _PREFIX.match(text)
        if not m:
            break
        value = m.group(1).strip()
        if value in ch.prefix_stopwords:
            text = text[m.end():]
            notes.append(f"prefix stopword dropped: {value}")
            continue
        # a prefix that is itself a long clause is part of the sentence, not a field
        if len(value.split()) > 6:
            break
        found[slot] = value
        text = text[m.end():]
    return text.strip(), found, notes


def _strip_meta_tail(text: str) -> str:
    """Drop the channel's own tag/handle signature off the end of a post.

    Refuses in the two cases where the run is not a signature: when the sentence
    is still mid-word in front of it, and when removing it would leave nothing.
    """
    m = _META_TAIL.search(text)
    if not m or m.start() == 0:
        return text
    head = text[:m.start()].rstrip()
    last = head.split()[-1]
    # a proclitic left on its own — with the tatweel a channel writes it with, or
    # bare — means the sentence runs into the run and the run is part of it
    if not head or len(last) == 1 or _DANGLING.search(last):
        return text
    return head


def assemble(post_id: str, channel: str, raw: str) -> Post:
    ch = CHANNELS.get(channel, DEFAULT)
    notes = []

    # bidi controls first: invisible in every editor, and they defeat both the
    # prefix regex and exact-hash dedup on an entire channel
    text = _INVISIBLE.sub("", raw)
    if text != raw:
        notes.append("bidi control characters stripped")

    before = text
    text = _DATELINE.sub("", text)
    if text != before:
        notes.append("mirrored dateline stripped")

    # Before the hashtag pass: a mirror bot glues tag and link straight onto the
    # last word, so the link has to go first or the word cannot be recovered.
    before = text
    text = _URL_FULL.sub(" ", text)
    if text != before:
        notes.append(f"{len(_URL_FULL.findall(before))} link(s) stripped from body")

    text, n = _strip_translation(text, ch); notes += n
    text, n = _strip_footer(text, ch); notes += n
    text, n = _strip_lead(text, ch); notes += n
    text = _LEADING_JUNK.sub("", text, count=1)

    tags = _HASHTAG.findall(text)
    # the عاجل tag is deliberately NOT passed to the classifier
    text = _strip_meta_tail(text)
    # keep the word, drop the #, and leave a space: the # may be the only thing
    # separating the tag from the word before it
    text = _HASHTAG.sub(r" \1 ", text)
    text = _strip_label_tail(_drop_pictographs(text))
    text = re.sub(r"[ \t]{2,}", " ", text).strip()

    text, fields, n = _parse_prefixes(text, ch); notes += n

    own, ext = [], []
    for host in _URL.findall(raw):
        (own if any(host.endswith(d) for d in ch.own_domains) else ext).append(host)
    if own:
        notes.append(f"own-domain link (not an article signal): {own}")

    return Post(
        id=post_id, channel=channel, raw=raw, text=text,
        location=fields.get("location"), source=clean_credit(fields.get("source")),
        external_links=ext, own_links=own, tags=tags, notes=notes,
    )


# A sentence end, so a promo tail can be cut at the boundary the post already
# wrote rather than mid-clause. Shape, not vocabulary: runs of stops, or any of
# the separators a channel uses to hang a trailer off a headline.
_BOUNDARY = re.compile(r"[.\u2026]{1,3}|[\u060C\u061B|\u2013\u2014-]")


def clean_credit(s):
    """A credit is copied verbatim out of the post, so it arrives with whatever
    markup the channel tagged it with ("مراسل #الجديد"). The markup is not part
    of the name."""
    if not s:
        return s
    return re.sub(r"[#@]", "", s).strip() or None


def _key_tokens(s: str) -> set:
    return {t for t in normalize_key(s or "").split() if len(t) > 1}


def strip_attribution(text: str, cited: Optional[str]) -> tuple:
    """Drop a leading '<outlet>:' credit — but only the ones Call A calls a credit.

    A leading '<x>:' segment is three different things and the shape of the string
    does not say which: an outlet the post credits ("الوكالة الوطنية:"), the person
    being quoted ("ترامب:"), or a dateline the channel stamps on every post
    ("لبنان:"). Measured on fixtures/live-timed.jsonl, 25-99% of posts per channel
    carry one, and all three kinds appear on the same channel — so a regex that
    removes them all deletes the actor from every quote post, and the headline with
    it.

    Deciding which is which needs to know that الوكالة الوطنية is an agency and
    ترامب is a person. That is world knowledge, so it does not live here: Call A
    already fills `cited_source` for an outlet and `actor` for a speaker. This
    removes the prefix only when the model's `cited_source` is what the prefix
    says, and leaves it alone otherwise.
    """
    if not cited:
        return text, []
    m = _PREFIX.match(text)
    if not m:
        return _strip_trailing_credit(text, cited)
    head = _key_tokens(m.group(1))
    if not head or len(m.group(1).split()) > 6:
        return text, []
    src = _key_tokens(cited)
    shared = head & src
    # The prefix has to be *mostly* the credit, and the ratio is taken against the
    # prefix, not the smaller set. Otherwise a speaker who names an outlet mid-line
    # — 'قيادي في قسد ل"العربية":' with cited_source 'العربية' — shares one token
    # out of five and the actor gets deleted along with the credit.
    if not shared or len(shared) * 2 < len(head):
        return text, []
    rest = _LEADING_JUNK.sub("", text[m.end():], count=1).strip()
    if not rest:                     # the credit was the whole post
        return text, []
    return rest, [f"attribution prefix stripped: {m.group(1).strip()}"]


def _strip_trailing_credit(text: str, cited: str) -> tuple:
    """The same credit, hung off the end as a trailer ("...التفاصيل مع مراسل الجديد").

    Only fires when the post ends ON the credit, so a credit named mid-sentence —
    where it is doing grammatical work — is left alone. The cut is taken back to
    the sentence boundary the post itself wrote, when there is one close by,
    because the words leading into a trailer belong to the trailer.
    """
    want = [t for t in normalize_key(cited).split() if t]
    if not want:
        return text, []
    spans = [(m.start(), m.group(0)) for m in re.finditer(r"\S+", text)]
    tail = spans[-len(want):]
    if len(tail) < len(want):
        return text, []
    if [normalize_key(w).strip() for _, w in tail] != want:
        return text, []

    cut = tail[0][0]
    b = None
    for m in _BOUNDARY.finditer(text, 0, cut):
        b = m
    # only a boundary near the trailer: an earlier one would take a real sentence
    if b and cut - b.end() <= 40:
        cut = b.start()
    head = text[:cut].rstrip()
    # a trailer is a tail. Removing a third of the post means this was the post.
    if not head or (len(text) - len(head)) * 5 > len(text) * 2:
        return text, []
    return head, [f"attribution trailer stripped: {text[tail[0][0]:].strip()}"]
