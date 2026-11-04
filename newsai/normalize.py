"""Text normalisation for match keys: Arabic-specific rules plus a generic Unicode path.

Two levels, never conflated:
    aggressive -> hashing / exact-dup / match keys ONLY
    light      -> embedding input
    raw        -> what the LLM reads and what gets published (§29)
"""
import re
import unicodedata

from newsai.lang import LANG

_DIACRITICS = re.compile('[ً-ْٰٓ-ٕـ]')
_TATWEEL = 'ـ'
_AR_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')
_AR_PUNCT = re.compile(r'[،؛؟«»ـ“”"\'’‘‐-―\-–—_/|]')

# Enumerating punctuation by hand always misses a case, and a missed one splits
# a word into two terms depending on where in the sentence it sits. Unicode
# categories cover it without a list to maintain: P* is every punctuation mark
# in every script, S* is symbols and emoji. Letters, marks and digits survive.
_SEPARATORS = {}


def _to_space(ch: str) -> str:
    hit = _SEPARATORS.get(ch)
    if hit is None:
        hit = _SEPARATORS[ch] = ' ' if unicodedata.category(ch)[0] in 'PS' else ch
    return hit


_INVISIBLE = re.compile('[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]')


def normalize_ar_key(text: str) -> str:
    """Aggressive: for hashing / exact-dup / match keys ONLY. Never publish this."""
    t = unicodedata.normalize('NFKC', text)
    t = t.replace(_TATWEEL, '')
    t = _DIACRITICS.sub('', t)

    t = re.sub(r'[آأإٱ]', 'ا', t)
    t = t.replace('ى', 'ي')
    t = t.replace('ة', 'ه')
    t = t.replace('ؤ', 'و').replace('ئ', 'ي')
    t = t.replace('ء', '')

    t = t.translate(_AR_DIGITS)
    t = _AR_PUNCT.sub(' ', t)
    t = ''.join(_to_space(c) for c in t)
    t = _INVISIBLE.sub('', t)
    t = re.sub(r'\s+', ' ', t).strip().lower()
    return t


def normalize_ar_light(text: str) -> str:
    """Light: safe for embeddings. Not used by the prototype, kept for parity with §26."""
    t = unicodedata.normalize('NFKC', text)
    t = t.replace(_TATWEEL, '')
    t = _INVISIBLE.sub('', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def normalize_generic_key(text: str) -> str:
    """Aggressive key for any language: NFKC, accents folded, case folded,
    punctuation and symbols to spaces, invisible characters removed."""
    t = unicodedata.normalize('NFKC', text)
    t = _INVISIBLE.sub('', t)
    t = ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')
    t = ''.join(_to_space(c) for c in t)
    t = re.sub(r'\s+', ' ', t).strip().casefold()
    return t


_KEY_BY_LANG = {"ar": normalize_ar_key}


def normalize_key(text: str, lang: str = None) -> str:
    """The match key for the configured language (NEWSAI_LANG)."""
    return _KEY_BY_LANG.get(lang or LANG, normalize_generic_key)(text)
