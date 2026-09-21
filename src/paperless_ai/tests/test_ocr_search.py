from types import SimpleNamespace

from paperless_ai.ocr_search import format_matches
from paperless_ai.ocr_search import normalize
from paperless_ai.ocr_search import overview
from paperless_ai.ocr_search import query_terms
from paperless_ai.ocr_search import search_documents


def doc(pk, title, content, created="2026-01-01"):
    return SimpleNamespace(pk=pk, title=title, content=content, created=created)


def search(question, documents, **overrides):
    options = {"max_documents": 3, "passages_per_document": 2, "passage_chars": 300}
    options.update(overrides)
    return search_documents(question, documents, **options)


class TestNormalize:
    def test_marks_and_letter_variants_are_folded(self):
        # Vowel marks, tatweel and the alef/yeh/teh-marbuta variants must not
        # stop two spellings of one word from matching.
        assert normalize("مُدِيرَة") == normalize("مديره")
        assert normalize("إدارة") == normalize("ادارة") == normalize("أدارة")
        assert normalize("علـــى") == normalize("علي")

    def test_arabic_indic_digits_become_ascii(self):
        assert normalize("رقم ١٢٣ و ٤٥") == "رقم 123 و 45"

    def test_presentation_forms_from_ocr_become_letters(self):
        # Lam and alef in their joined presentation forms, as OCR emits them.
        assert normalize("ﻟﺎ") == "لا"

    def test_non_text_is_empty(self):
        assert normalize(None) == ""
        assert normalize(5) == ""  # type: ignore[arg-type]


class TestQueryTerms:
    def test_question_words_and_filler_are_dropped(self):
        assert query_terms("ما هي شروط عقد الإيجار؟") == ["شروط", "عقد", "ايجار"]

    def test_article_and_plural_endings_are_trimmed(self):
        assert query_terms("العقود") == ["عقود"]
        assert query_terms("الموظفون") == ["موظف"]
        # Terms are compared in folded form, so the teh marbuta reads as heh.
        assert query_terms("بالمستندات المالية") == ["ماليه"]
        assert query_terms("contracts") == ["contract"]

    def test_generic_archive_words_do_not_count(self):
        assert query_terms("كم عدد المستندات؟") == ["عدد"]
        assert query_terms("لخّص لي المستندات المتوفرة") == ["متوفره"]

    def test_duplicates_are_removed(self):
        assert query_terms("عقد عقد العقد") == ["عقد"]

    def test_english_stopwords_are_dropped(self):
        assert query_terms("What is the salary of the manager?") == [
            "salary",
            "manager",
        ]

    def test_a_question_of_only_filler_has_no_terms(self):
        assert query_terms("ما هو هذا؟") == []
        assert query_terms("") == []


class TestSearch:
    documents = [
        doc(1, "عقد إيجار المكتب", "شروط عقد الإيجار: مدة العقد سنة واحدة.\n\nالمبلغ الشهري ٥٠٠٠ ريال."),
        doc(2, "كشف الرواتب", "رواتب الموظفين لشهر يناير.\n\nإجمالي الرواتب ٩٠٠٠٠ ريال."),
        doc(3, "محضر اجتماع", "تمت مناقشة خطة العام القادم."),
    ]

    def test_the_document_about_the_question_ranks_first(self):
        matches = search("ما مدة عقد الإيجار؟", self.documents)
        assert [m.document.pk for m in matches][0] == 1

    def test_arabic_spelling_variants_still_match(self):
        # Question spelled with hamza, text without it.
        matches = search("ايجار المكتب", self.documents)
        assert matches[0].document.pk == 1

    def test_a_broken_plural_finds_its_singular_and_the_other_way_round(self):
        docs = [doc(1, "عقد", "هذا عقد عمل"), doc(2, "رواتب", "كشف رواتب الموظفين")]
        assert search("العقود", docs)[0].document.pk == 1
        assert search("راتب الموظف", docs)[0].document.pk == 2
        assert search("الرواتب", [doc(1, "x", "قيمة الراتب الشهري")])[0].document.pk == 1

    def test_a_short_word_is_not_reduced_to_a_skeleton_that_matches_everything(self):
        # "دور" would shrink to two letters and match half the language.
        docs = [doc(1, "x", "ملاحظات حول درج المكتب"), doc(2, "y", "دور الموظف")]
        assert [m.document.pk for m in search("دور", docs)] == [2]

    def test_the_passage_that_holds_the_answer_is_returned(self):
        matches = search("كم إجمالي الرواتب؟", self.documents, passages_per_document=1)
        assert matches[0].document.pk == 2
        assert "٩٠٠٠٠" in matches[0].passages[0]

    def test_a_document_matched_only_by_title_gives_its_opening(self):
        docs = [doc(1, "ميزانية 2026", "نص لا علاقة له بالسؤال إطلاقاً.")]
        matches = search("ميزانية", docs)
        assert matches[0].passages == ["نص لا علاقة له بالسؤال إطلاقاً."]

    def test_a_rarer_word_counts_for_more_than_a_common_one(self):
        docs = [
            doc(1, "أ", "الشركة الشركة الشركة الشركة الشركة"),
            doc(2, "ب", "الشركة فاتورة"),
            doc(3, "ج", "الشركة"),
        ]
        assert search("الشركة فاتورة", docs)[0].document.pk == 2

    def test_only_the_requested_number_of_documents_is_returned(self):
        docs = [doc(i, f"عقد {i}", "عقد") for i in range(10)]
        assert len(search("عقد", docs, max_documents=4)) == 4

    def test_nothing_matches_gives_an_empty_result(self):
        assert search("سيارة", self.documents) == []

    def test_a_question_without_distinguishing_words_gives_an_empty_result(self):
        assert search("ما هو؟", self.documents) == []

    def test_no_documents_gives_an_empty_result(self):
        assert search("عقد", []) == []

    def test_documents_without_text_do_not_break_the_search(self):
        docs = [
            doc(1, None, None),
            SimpleNamespace(pk=2),
            SimpleNamespace(pk=3, title=object(), content=12345),
            doc(4, "عقد", "نص العقد"),
        ]
        assert [m.document.pk for m in search("عقد", docs)] == [4]

    def test_a_long_paragraph_is_cut_to_the_passage_size(self):
        docs = [doc(1, "تقرير", "كلمة " * 400 + " عقد مهم " + "كلمة " * 400)]
        matches = search("عقد", docs, passage_chars=200)
        assert all(len(p) <= 200 for p in matches[0].passages)
        assert any("عقد" in p for p in matches[0].passages)

    def test_passages_come_back_in_reading_order(self):
        text = "\n\n".join(["عقد أول " + "ب" * 60, "حشو " * 30, "عقد ثان " + "ج" * 60])
        matches = search("عقد", [doc(1, "x", text)], passages_per_document=2)
        first, second = matches[0].passages
        assert "أول" in first
        assert "ثان" in second


class TestFormatMatches:
    def test_each_document_is_introduced_by_its_title(self):
        matches = search("الرواتب", TestSearch.documents)
        text = format_matches(matches, budget_chars=2000)
        assert text.startswith("المستند: كشف الرواتب")
        assert "٩٠٠٠٠" in text

    def test_the_context_is_cut_to_the_budget(self):
        docs = [doc(i, f"عقد {i}", "عقد " * 200) for i in range(5)]
        matches = search("عقد", docs, passage_chars=800)
        assert len(format_matches(matches, budget_chars=500)) <= 500

    def test_nothing_is_written_when_there_are_no_matches(self):
        assert format_matches([], budget_chars=500) == ""


class TestOverview:
    def test_it_gives_the_count_and_the_newest_titles_first(self):
        docs = [
            doc(1, "قديم", "نص قديم", created="2025-01-01"),
            doc(2, "جديد", "نص جديد", created="2026-06-01"),
        ]
        text = overview(docs)
        assert "عدد المستندات: 2" in text
        assert text.index("جديد") < text.index("قديم")

    def test_it_includes_the_opening_of_the_newest_documents(self):
        docs = [doc(1, "تقرير", "بداية التقرير " + "س" * 1000)]
        text = overview(docs, opening_chars=50)
        assert "بداية التقرير" in text
        assert "س" * 100 not in text

    def test_it_is_cut_to_the_budget(self):
        docs = [doc(i, "عنوان طويل " * 10, "نص") for i in range(100)]
        assert len(overview(docs, budget_chars=300)) <= 300

    def test_documents_without_text_are_still_counted(self):
        assert "عدد المستندات: 1" in overview([SimpleNamespace(pk=1)])
