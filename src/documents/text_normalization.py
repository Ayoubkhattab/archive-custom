"""
Arabic-aware clean-up of text extracted from documents.

Two levels, applied in different places:

- ``normalize_extracted_text`` fixes what is plainly an extraction artefact and
  is applied to the stored document content: shaped glyphs ("presentation
  forms") that many PDF producers write instead of the base letters, text
  stored in visual (reversed) order, invisible direction marks and tatweel.
  After this the text reads the same, but it is searchable and usable by
  the classifier, the date parser and the AI.

- ``normalize_for_search`` is lossy and is only applied to search terms and
  index tokens, never to the stored content: it drops diacritics and folds
  letter variants that people type interchangeably (أ/إ/آ/ا, ى/ي, ة/ه) as
  well as Arabic-Indic digits, so a query matches however the word was
  spelled in the document.
"""

import re
import unicodedata

# Shaped Arabic glyphs (initial/medial/final/isolated forms and ligatures).
_PRESENTATION_FORMS = re.compile(r"[ﭐ-﷿ﹰ-ﻼ]+")
_PRESENTATION_WORD = re.compile(r"[ﭐ-﷿ﹰ-ﻼ]{2,}")

# Direction marks/embeddings/isolates, zero width space and BOM. ZWJ/ZWNJ are
# kept: they carry meaning in some scripts.
_INVISIBLE = re.compile(r"[​‎‏‪-‮⁦-⁩؜﻿]")

_TATWEEL = "ـ"

# Harakat, Quranic annotation marks and superscript alef.
ARABIC_MARKS = (
    "ؐ-ًؚ-ٰٟۖ-ۜ۟-۪ۨ-ۭ"
)
_MARKS = re.compile(f"[{ARABIC_MARKS}]")

_ARABIC_LETTER = re.compile(r"[ء-يٱ-ۓﭐ-﷿ﹰ-ﻼ]")

# Runs that are written left to right inside a right to left line.
_LTR_RUN = re.compile(
    r"[A-Za-z0-9À-ɏ٠-٩۰-۹]+"
    r"(?:[ .,:/\-_%+]+[A-Za-z0-9À-ɏ٠-٩۰-۹]+)*",
)
_MIRRORED = str.maketrans("()[]{}<>", ")(][}{><")

_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩"
    "۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

_SEARCH_FOLD = str.maketrans(
    {
        "أ": "ا",  # أ -> ا
        "إ": "ا",  # إ -> ا
        "آ": "ا",  # آ -> ا
        "ٱ": "ا",  # ٱ -> ا
        "ى": "ي",  # ى -> ي
        "ی": "ي",  # Persian yeh -> ي
        "ک": "ك",  # Persian kaf -> ك
        "ة": "ه",  # ة -> ه
        _TATWEEL: None,
    },
)


def to_ascii_digits(text: str) -> str:
    """Replaces Arabic-Indic and Persian digits with 0-9."""
    return text.translate(_DIGITS)


def _shape(char: str) -> str | None:
    decomposition = unicodedata.decomposition(char)
    if decomposition.startswith("<"):
        return decomposition[1 : decomposition.index(">")]
    return None


def is_visual_order(text: str) -> bool:
    """
    Detects Arabic stored in visual (display) order, which some PDF producers
    write and pdftotext passes through as reversed words.

    Shaped glyphs tell the direction: a word read in logical order starts
    with an initial form and ends with a final one, a reversed word the
    other way around. Text with plain letters carries no such signal and is
    treated as logical.
    """
    logical = reversed_ = 0
    for match in _PRESENTATION_WORD.finditer(text):
        word = match.group()
        first, last = _shape(word[0]), _shape(word[-1])
        forward = first == "initial" or last == "final"
        backward = first == "final" or last == "initial"
        if forward and not backward:
            logical += 1
        elif backward and not forward:
            reversed_ += 1
    return reversed_ >= 3 and reversed_ > 2 * logical


def _visual_line_to_logical(line: str) -> str:
    if not _ARABIC_LETTER.search(line):
        return line
    flipped = line[::-1].translate(_MIRRORED)
    # Numbers and Latin words were displayed left to right, so reversing the
    # line reversed them too: put them back.
    return _LTR_RUN.sub(lambda m: m.group()[::-1], flipped)


def normalize_extracted_text(text: str | None) -> str | None:
    """
    Repairs extraction artefacts without changing what the text says.
    Safe to run more than once.
    """
    if not text:
        return text

    text = _INVISIBLE.sub("", text)

    if _PRESENTATION_FORMS.search(text):
        if is_visual_order(text):
            text = "\n".join(
                _visual_line_to_logical(line) for line in text.split("\n")
            )
        text = _PRESENTATION_FORMS.sub(
            lambda m: unicodedata.normalize("NFKC", m.group()),
            text,
        )

    return text.replace(_TATWEEL, "")


def normalize_for_search(text: str) -> str:
    """
    Folds spelling variants so that search terms match regardless of
    diacritics, hamza on alef, ta marbuta/ha, alef maqsura/ya and digit style.
    """
    return to_ascii_digits(_MARKS.sub("", text).translate(_SEARCH_FOLD))


def looks_garbled(text: str | None, *, expect_arabic: bool) -> bool:
    """
    Detects a text layer that exists but is unreadable: PDFs whose fonts have
    no usable Unicode mapping come out of pdftotext as private-use glyphs,
    replacement characters, or Arabic decoded as Latin-1 (e.g. "ÇáÓáÇã").
    Such documents need OCR even though they "have text".
    """
    if not text:
        return False
    chars = [c for c in text if not c.isspace()]
    if len(chars) < 50:
        return False

    unreadable = sum(
        1
        for c in chars
        if c == "�"
        or "" <= c <= ""
        or unicodedata.category(c) == "Cc"
    )
    if unreadable / len(chars) > 0.1:
        return True

    if expect_arabic and not _ARABIC_LETTER.search(text):
        letters = [c for c in chars if c.isalpha()]
        mojibake = sum(1 for c in letters if "À" <= c <= "ÿ")
        if letters and mojibake / len(letters) > 0.3:
            return True

    return False
