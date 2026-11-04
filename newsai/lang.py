"""Language selection. One setting drives normalisation, prompts and the gazetteer.

    NEWSAI_LANG=ar   (default) Arabic-specific normalisation, prompts and places
    NEWSAI_LANG=en   any other ISO code: generic Unicode normalisation and the
                     language-neutral prompts, told to answer in that language
"""
import os

NAMES = {
    "ar": "Arabic", "en": "English", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "tr": "Turkish", "fa": "Persian",
    "ru": "Russian", "uk": "Ukrainian", "pl": "Polish",
}

LANG = os.environ.get("NEWSAI_LANG", "ar").strip().lower() or "ar"
LANGUAGE = NAMES.get(LANG, LANG)
