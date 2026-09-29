from django.test import SimpleTestCase

from documents.index import text_analyzer
from documents.text_normalization import is_visual_order
from documents.text_normalization import looks_garbled
from documents.text_normalization import normalize_extracted_text
from documents.text_normalization import normalize_for_search
from documents.text_normalization import to_ascii_digits

# "محمد" as shaped glyphs: meem initial, hah medial, meem medial, dal final.
SHAPED = "ﻣﺤﻤﺪ"


class TestNormalizeExtractedText(SimpleTestCase):
    def test_presentation_forms_become_letters(self):
        self.assertEqual(normalize_extracted_text(SHAPED), "محمد")

    def test_lam_alef_ligature(self):
        self.assertEqual(normalize_extracted_text("ﻻ"), "لا")

    def test_visual_order_is_reversed(self):
        # How a PDF written in display order stores "محمد محمد محمد 2024":
        # left to right as shown, so the number comes first and every word
        # is reversed.
        visual = " ".join(["2024", SHAPED[::-1], SHAPED[::-1], SHAPED[::-1]])
        self.assertTrue(is_visual_order(visual))
        self.assertEqual(
            normalize_extracted_text(visual),
            "محمد محمد محمد 2024",
        )

    def test_logical_order_is_kept(self):
        logical = " ".join([SHAPED] * 3 + ["2024"])
        self.assertFalse(is_visual_order(logical))
        self.assertEqual(normalize_extracted_text(logical), "محمد محمد محمد 2024")

    def test_invisible_marks_and_tatweel_removed(self):
        self.assertEqual(
            normalize_extracted_text("‏محـــمد‎ ﻿علي"),
            "محمد علي",
        )

    def test_diacritics_and_digits_are_kept(self):
        text = "مُحَمَّد ٢٠٢٤"
        self.assertEqual(normalize_extracted_text(text), text)

    def test_idempotent_and_latin_untouched(self):
        text = "Invoice 2024 (paid)\nفاتورة رقم 15"
        self.assertEqual(normalize_extracted_text(text), text)
        once = normalize_extracted_text(SHAPED)
        self.assertEqual(normalize_extracted_text(once), once)

    def test_empty(self):
        self.assertIsNone(normalize_extracted_text(None))
        self.assertEqual(normalize_extracted_text(""), "")


class TestNormalizeForSearch(SimpleTestCase):
    def test_folding(self):
        for source, expected in [
            ("أَحْمَد", "احمد"),
            ("إسلام", "اسلام"),
            ("آمال", "امال"),
            ("مدرسة", "مدرسه"),
            ("مستشفى", "مستشفي"),
            ("محـمد", "محمد"),
            ("٢٠٢٤", "2024"),
            ("۱۲۳", "123"),
            ("Invoice", "Invoice"),
        ]:
            with self.subTest(source=source):
                self.assertEqual(normalize_for_search(source), expected)

    def test_ascii_digits(self):
        self.assertEqual(to_ascii_digits("١٥/٠٣/٢٠٢٣"), "15/03/2023")


class TestLooksGarbled(SimpleTestCase):
    def test_private_use_glyphs(self):
        self.assertTrue(looks_garbled(" " * 30, expect_arabic=True))

    def test_latin1_mojibake_of_arabic(self):
        self.assertTrue(looks_garbled("ÇáÓáÇã Úáíßã " * 10, expect_arabic=True))
        # Without Arabic expected, accented Latin text is legitimate.
        self.assertFalse(looks_garbled("ÇáÓáÇã Úáíßã " * 10, expect_arabic=False))

    def test_readable_text(self):
        self.assertFalse(looks_garbled("فاتورة ضريبية رقم 15 " * 10, expect_arabic=True))
        self.assertFalse(looks_garbled("This is an invoice. " * 10, expect_arabic=True))

    def test_short_text_is_not_judged(self):
        self.assertFalse(looks_garbled("", expect_arabic=True))


class TestSearchAnalyzer(SimpleTestCase):
    def tokens(self, text):
        return [t.text for t in text_analyzer()(text)]

    def test_diacritics_do_not_split_words(self):
        self.assertEqual(self.tokens("مُحَمَّد"), ["محمد"])

    def test_variants_fold_to_one_term(self):
        self.assertEqual(
            self.tokens("أحمد احمد مدرسة مدرسه ٢٠٢٤ Invoice"),
            ["احمد", "احمد", "مدرسه", "مدرسه", "2024", "invoice"],
        )

    def test_highlight_offsets_point_into_original(self):
        text = "رقم مُحَمَّد"
        token = list(text_analyzer()(text, chars=True))[-1]
        self.assertEqual(text[token.startchar : token.endchar], "مُحَمَّد")
