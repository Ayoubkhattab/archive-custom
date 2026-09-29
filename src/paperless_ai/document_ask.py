"""
"Ask about this file": questions about one specific document.

Separate from the archive-wide chat on purpose. Only the one document is in play,
so there is no retrieval over the archive, no vector index and no embedding
model: the answer comes straight from the document's own OCR text. Two things
make it fast and checkable:

Speed. When the document fits the context budget it is sent whole, as the
first thing in the prompt, and it is sent identically for every question about
that document. Ollama keeps the processed prompt of the previous request and
reuses the longest identical prefix, so a follow-up question skips re-reading
the document and only processes the new turn. On a CPU that prompt processing,
not the answer, is most of the wait. When the document is too long, only the
opening (parties, subject and date usually live there) and the passages that
match the question are sent, which keeps the prompt small instead.

Precision. The model has to end every answer with a "المصدر:" line quoting the
document verbatim. The interface looks each quote up in the document text and
marks it as found or not, so the reader can tell a grounded answer from a guess
at a glance.

Free of Django and of any LLM library so it can be tested on its own; the view
turns the (role, content) pairs into chat messages.
"""

from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace

from paperless_ai.ocr_search import search_documents

MAX_QUESTION_CHARS = 2000

# Earlier turns kept for follow-up questions ("and the second party?"). Bounded
# so a long conversation can't crowd the document out of the context window.
HISTORY_MAX_MESSAGES = 6
HISTORY_MESSAGE_CHARS = 1500

# For documents over budget: how much of the opening is always kept, and the
# size of the passages picked for the question.
HEAD_CHARS = 1200
PASSAGE_CHARS = 700

# The interface parses the line that starts with this label.
SOURCE_LABEL = "المصدر:"
NOT_IN_DOCUMENT = "لم يرد ذلك في المستند."

ASK_SYSTEM_PROMPT = (
    "أنت مدقق مستندات دقيق. تجيب عن أسئلة المستخدم عن مستند واحد محدد، "
    "اعتماداً على نصه المرفق أدناه وحده.\n"
    "القواعد:\n"
    "1. لا تستعمل أي معلومة من خارج نص المستند، ولا تخمّن ولا تفترض ما لم يُذكر فيه.\n"
    "2. ابدأ بالجواب مباشرة، في جملة أو بضع جمل، أو قائمة قصيرة إن طلب السؤال عدة "
    "عناصر، دون مقدمات.\n"
    "3. انقل الأرقام والتواريخ والأسماء والمبالغ والأرقام المرجعية كما وردت في النص "
    "حرفياً.\n"
    f"4. بعد الجواب اكتب سطراً مستقلاً يبدأ بـ {SOURCE_LABEL} يليه اقتباس حرفي قصير، "
    "أو اقتباسان، من نص المستند بين علامتي « » يثبت الجواب. انسخ الاقتباس من النص "
    "كما هو تماماً دون إعادة صياغة.\n"
    f"5. إذا لم يكن الجواب في النص فاكتب فقط: {NOT_IN_DOCUMENT} ولا تكتب سطر المصدر.\n"
    "6. النص مستخرج بالتعرف الضوئي على الحروف وقد يحوي أخطاء إملائية بسيطة؛ "
    "اعتمد المعنى الأقرب ولا تصحح الاقتباس.\n"
    "You check ONE document. Answer only from its text below, then add a line "
    f"'{SOURCE_LABEL} «verbatim quote»'. If the text does not contain the answer, "
    "say so and give no source line.\n"
)

DEEP_STYLE = (
    "هذا سؤال للتدقيق: افحص النص كاملاً بعناية، واذكر كل المواضع ذات الصلة، "
    "ونبّه صراحةً إلى أي تعارض أو غموض أو معلومة ناقصة في النص. "
    "يجوز أن تضع في سطر المصدر حتى ثلاثة اقتباسات.\n"
)

EXCERPTS_NOTE = (
    "ملاحظة: المستند طويل، والمرفق هو بدايته والمقاطع الأقرب إلى السؤال فقط. "
    "إن لم تجد الجواب فيها فقل إنه قد يكون في جزء آخر من المستند.\n"
)


@dataclass
class DocumentContext:
    text: str
    # True when the whole document was sent, False for excerpts.
    complete: bool


def _cut(text: str, limit: int) -> str:
    """At most `limit` characters, ending on a word boundary."""
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit // 2 else limit]


def _spread(text: str, budget: int) -> str:
    """Start, middle and end: for a question that names nothing to search for."""
    chunk = budget // 3
    middle_start = max(len(text) // 2 - chunk // 2, 0)
    return (
        "[بداية المستند]\n"
        + _cut(text[:chunk], chunk)
        + "\n\n[من وسط المستند]\n"
        + _cut(text[middle_start : middle_start + chunk], chunk)
        + "\n\n[نهاية المستند]\n"
        + text[-chunk:]
    )


def select_context(
    title: str,
    content: str | None,
    question: str,
    budget_chars: int,
) -> DocumentContext:
    """
    What of the document to put in front of the model.

    The whole text whenever it fits, so the prompt stays identical from one
    question to the next (see the module docstring); otherwise the opening plus
    the passages that match the question.
    """
    text = (content or "").strip()
    if len(text) <= budget_chars:
        return DocumentContext(text=text, complete=True)

    head = _cut(text[:HEAD_CHARS], min(HEAD_CHARS, budget_chars // 4))
    remaining = budget_chars - len(head) - 80
    matches = search_documents(
        question,
        [SimpleNamespace(title=title or "", content=text)],
        max_documents=1,
        passages_per_document=max(1, remaining // PASSAGE_CHARS),
        passage_chars=PASSAGE_CHARS,
    )
    passages = matches[0].passages if matches else []
    # The opening is already included; don't spend budget on it twice.
    head_flat = " ".join(head.split())
    passages = [p for p in passages if p not in head_flat]

    if not passages:
        return DocumentContext(text=_spread(text, budget_chars), complete=False)

    chosen: list[str] = []
    used = 0
    for passage in passages:
        if used + len(passage) > remaining:
            break
        chosen.append(passage)
        used += len(passage) + 3
    body = (
        "[بداية المستند]\n"
        + head
        + "\n\n[مقاطع من المستند ذات صلة بالسؤال]\n"
        + "\n…\n".join(chosen)
    )
    return DocumentContext(text=body, complete=False)


def clean_history(history: object) -> list[tuple[str, str]]:
    """
    The earlier turns as (role, content), from untrusted request data.

    Anything malformed is dropped rather than rejected: history only improves
    a follow-up answer, so it must never be the reason a question fails.
    """
    if not isinstance(history, list):
        return []
    turns: list[tuple[str, str]] = []
    for item in history:
        if not isinstance(item, Mapping):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        turns.append((role, _cut(content, HISTORY_MESSAGE_CHARS)))
    turns = turns[-HISTORY_MAX_MESSAGES:]
    # A conversation replayed to the model starts with the user.
    while turns and turns[0][0] != "user":
        turns.pop(0)
    return turns


def build_messages(
    *,
    title: str,
    context: DocumentContext,
    question: str,
    history: Iterable[tuple[str, str]] = (),
    deep: bool = False,
    closing_instruction: str = "",
) -> list[tuple[str, str]]:
    """
    The conversation for the model, as (role, content) pairs.

    Order matters for speed: the parts that never change for a document
    (instructions, then the document) come first, then the earlier turns, then
    the new question, so consecutive requests share the longest possible prefix.
    The per-question instructions therefore go at the end of the last message.
    """
    kind = "النص الكامل" if context.complete else "مقتطفات"
    system = (
        ASK_SYSTEM_PROMPT
        + ("" if context.complete else EXCERPTS_NOTE)
        + f"\nعنوان المستند: {title or ''}\n"  # noqa: RUF001
        + f"نص المستند ({kind}، كما استُخرج بالتعرف الضوئي على الحروف):\n"
        + "<<<\n"
        + context.text
        + "\n>>>"
    )
    messages = [("system", system)]
    messages.extend(history)
    reminder = (
        DEEP_STYLE if deep else ""
    ) + f"تذكّر: أجب من نص المستند وحده، ثم سطر {SOURCE_LABEL} باقتباس حرفي بين « »."
    user = f"{question.strip()}\n\n{reminder}"
    if closing_instruction:
        user += f"\n{closing_instruction}"
    messages.append(("user", user))
    return messages
