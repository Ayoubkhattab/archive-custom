"""
Find the passages of the archive that answer a question, from the OCR text.

Every document already carries its OCR text in the database, so a question can
be matched against that text directly. That needs no embedding model and no
vector index, which is what makes it fast: the vector route has to load a
sentence-transformer and an index from disk and embed the question before the
language model even starts, all on the CPU.

The matching is lexical (BM25 over normalised words), tuned for Arabic:
diacritics and tatweel are dropped, the letter variants OCR confuses are folded
together, and the definite-article prefixes and common plural endings are
trimmed so "العقود" finds "عقد". Words are also compared with their weak letters
(alef, waw, yeh) left out, which is what makes a broken plural and its singular
meet: "رواتب" and "راتب", "عقود" and "عقد". It cannot match a paraphrase, which is
why the caller keeps the vector index as a fallback for questions this finds
nothing for.

Free of Django and of any LLM library so it can be tested on its own.
"""

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from functools import lru_cache
from typing import Any

_MARKS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_WORD = re.compile(r"\w+", re.UNICODE)
_PARAGRAPHS = re.compile(r"\n\s*\n|\r\n\s*\r\n")

# Letters that OCR and typists use interchangeably, folded to one form.
_FOLD = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ؤ": "و",
        "ئ": "ي",
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
        "۰": "0",
        "۱": "1",
        "۲": "2",
        "۳": "3",
        "۴": "4",
        "۵": "5",
        "۶": "6",
        "۷": "7",
        "۸": "8",
        "۹": "9",
    },
)

# Words that say nothing about which document is meant. Written in their folded
# form, since they are compared after normalisation.
_STOPWORDS = frozenset(
    """
    من في علي الي عن ما ماذا هو هي هذا هذه ذلك تلك كم كيف اين متي لماذا هل ان
    او ثم قد لا لم لن كل بعض التي الذي الذين هناك هنا مع بين حول عند لدي لي
    اريد اعطني اخبرني اذكر اعرض لخص لخّص اشرح وضح ابحث هي هم نحن انا انت
    مستند مستندات ملف ملفات وثيقه وثائق ورقه اوراق ارشيف
    the a an of to in on for and or is are was were be been what which who whom
    how many much do does did about tell me give show list all any with from
    this that these those it its can could you your my our their there here
    document documents file files please
    """.split(),
)

# The letters that change inside a broken plural. Removed from both the words of
# the question and the text, but only for words that stay distinctive without
# them: a two-letter skeleton would match half the language.
_WEAK_LETTERS = str.maketrans("", "", "اويى")
_MIN_SKELETON = 3

# Trimmed in this order, longest first, and only while enough of the word stays.
_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")
_SUFFIXES = ("ات", "ون", "ين")


def normalize(text: str | None) -> str:
    """Lower-case text with the differences that should not affect matching removed."""
    if not isinstance(text, str):
        return ""
    # NFKC also turns the presentation forms OCR often emits back into letters.
    return _MARKS.sub("", unicodedata.normalize("NFKC", text).lower()).translate(
        _FOLD,
    )


@lru_cache(maxsize=2048)
def _normalized(text: str) -> str:
    return normalize(text)


@lru_cache(maxsize=2048)
def _skeleton(text: str) -> str:
    return _normalized(text).translate(_WEAK_LETTERS)


def _needle(term: str) -> tuple[str, bool]:
    """How to look a term up: its skeleton when that is long enough, else as is."""
    skeleton = term.translate(_WEAK_LETTERS)
    return (skeleton, True) if len(skeleton) >= _MIN_SKELETON else (term, False)


def _count(needle: tuple[str, bool], text: str) -> int:
    word, use_skeleton = needle
    return (_skeleton(text) if use_skeleton else _normalized(text)).count(word)


def _trim(word: str) -> str:
    for prefix in _PREFIXES:
        if word.startswith(prefix) and len(word) - len(prefix) >= 2:
            word = word[len(prefix) :]
            break
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    if word.isascii() and len(word) > 3 and word.endswith("s") and word[-2] != "s":
        return word[:-1]
    return word


def query_terms(question: str) -> list[str]:
    """The words of a question that can tell one document from another."""
    terms: list[str] = []
    for word in _WORD.findall(normalize(question)):
        if word in _STOPWORDS:
            continue
        word = _trim(word)
        if len(word) < 2 and not word.isdigit():
            continue
        if word in _STOPWORDS or word in terms:
            continue
        terms.append(word)
    return terms


@dataclass
class Match:
    document: Any
    score: float
    passages: list[str] = field(default_factory=list)


def _passages(content: str, size: int) -> list[str]:
    """Paragraph-sized pieces of the original text, none longer than `size`."""
    pieces: list[str] = []
    for paragraph in _PARAGRAPHS.split(content):
        paragraph = " ".join(paragraph.split())
        while len(paragraph) > size:
            cut = paragraph.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            pieces.append(paragraph[:cut])
            paragraph = paragraph[cut:].lstrip()
        if paragraph:
            pieces.append(paragraph)

    # OCR breaks lines mid-sentence, so very short pieces are joined onto their
    # neighbour rather than left as one-line fragments.
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) < size // 3 and len(merged[-1]) + len(piece) < size:
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def search_documents(
    question: str,
    documents: Sequence[Any],
    *,
    max_documents: int,
    passages_per_document: int,
    passage_chars: int,
) -> list[Match]:
    """
    The best-matching documents for a question, each with its best passages.

    Empty when the question has no distinguishing words, or nothing contains any
    of them. Documents only need `title` and `content`; anything that is not text
    counts as empty, so a half-processed document cannot break the search.
    """
    terms = query_terms(question)
    if not terms or not documents:
        return []

    needles = [_needle(term) for term in terms]
    prepared = []
    for document in documents:
        title = getattr(document, "title", None)
        content = getattr(document, "content", None)
        prepared.append(
            (
                document,
                title if isinstance(title, str) else "",
                content if isinstance(content, str) else "",
            ),
        )

    average_length = max(
        sum(len(content) for _, _, content in prepared) / len(prepared),
        1.0,
    )

    counts = [
        [_count(n, content) + 3 * _count(n, title) for n in needles]
        for _, title, content in prepared
    ]
    total = len(prepared)
    document_frequency = [
        sum(1 for row in counts if row[i] > 0) for i in range(len(terms))
    ]
    inverse = [
        math.log(1 + (total - df + 0.5) / (df + 0.5)) for df in document_frequency
    ]

    k1, b = 1.5, 0.75
    scored: list[tuple[float, int]] = []
    for index, ((_, _, content), row) in enumerate(zip(prepared, counts, strict=True)):
        length_norm = 1 - b + b * len(content) / average_length
        score = sum(
            inverse[i] * (tf * (k1 + 1)) / (tf + k1 * length_norm)
            for i, tf in enumerate(row)
            if tf > 0
        )
        if score > 0:
            # Covering more of the question's words beats repeating one of them.
            matched = sum(1 for tf in row if tf > 0)
            scored.append((score * (0.5 + matched / len(terms)), index))

    scored.sort(key=lambda item: item[0], reverse=True)

    matches = []
    for score, index in scored[:max_documents]:
        document = prepared[index][0]
        matches.append(
            Match(
                document=document,
                score=score,
                passages=_best_passages(
                    getattr(document, "content", None),
                    needles,
                    inverse,
                    passages_per_document,
                    passage_chars,
                ),
            ),
        )
    return matches


def _best_passages(
    content: Any,
    needles: list[tuple[str, bool]],
    inverse: list[float],
    count: int,
    size: int,
) -> list[str]:
    if not isinstance(content, str) or not content.strip():
        return []
    passages = _passages(content, size)
    scored = []
    for position, passage in enumerate(passages):
        score = sum(
            inverse[i] * min(_count(needle, passage), 3)
            for i, needle in enumerate(needles)
        )
        scored.append((score, position))

    best = [item for item in sorted(scored, key=lambda s: (-s[0], s[1])) if item[0] > 0]
    chosen = best[:count] or [(0.0, 0)]  # matched by title only: give the opening
    # Back in reading order, so the passages still make sense together.
    return [passages[position] for _, position in sorted(chosen, key=lambda s: s[1])]


def format_matches(matches: Sequence[Match], *, budget_chars: int) -> str:
    """The matches as a context block, cut to fit the model's budget."""
    blocks: list[str] = []
    used = 0
    for match in matches:
        title = str(getattr(match.document, "title", "") or "")
        header = f"المستند: {title}"
        body = "\n".join(match.passages)
        block = f"{header}\n{body}".strip()
        remaining = budget_chars - used
        if remaining <= len(header) + 20:
            break
        if len(block) > remaining:
            block = block[:remaining].rsplit(" ", 1)[0]
        blocks.append(block)
        used += len(block) + 2
    return "\n\n".join(blocks)


def overview(
    documents: Sequence[Any],
    *,
    max_titles: int = 40,
    opening_chars: int = 400,
    openings: int = 3,
    budget_chars: int = 3000,
) -> str:
    """
    A picture of the archive for a question that names nothing in particular,
    such as "how many documents are there" or "summarise what I have": the count,
    the titles, and the opening of the newest few.
    """
    ordered = sorted(
        documents,
        key=lambda d: str(getattr(d, "created", "") or ""),
        reverse=True,
    )
    lines = [f"عدد المستندات: {len(ordered)}", "أحدث المستندات:"]
    for document in ordered[:max_titles]:
        lines.append(f"- {getattr(document, 'title', '') or 'بدون عنوان'}")
    text = "\n".join(lines)

    for document in ordered[:openings]:
        content = getattr(document, "content", None)
        if not isinstance(content, str) or not content.strip():
            continue
        opening = " ".join(content.split())[:opening_chars]
        text += f"\n\nبداية المستند «{getattr(document, 'title', '')}»:\n{opening}"
    return text[:budget_chars]
