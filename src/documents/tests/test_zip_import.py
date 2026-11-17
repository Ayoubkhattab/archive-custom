import io
import zipfile
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import TestCase
from django.test import override_settings

from documents.data_models import ConsumableDocument
from documents.data_models import DocumentMetadataOverrides
from documents.data_models import DocumentSource
from documents.models import Folder
from documents.plugins.base import StopConsumeTaskError
from documents.tests.utils import DirectoriesMixin
from documents.tests.utils import DocumentConsumeDelayMixin
from documents.tests.utils import DummyProgressManager
from documents.zip_import import ZipImportError
from documents.zip_import import decode_entry_name
from documents.zip_import import folder_name_for_archive
from documents.zip_import import is_document_package
from documents.zip_import import plan_archive
from documents.zip_import import split_entry_path
from documents.zip_plugin import ZipArchivePlugin

LIMITS = {"max_files": 100, "max_total_bytes": 10**9, "max_ratio": 200}


def make_zip(entries: dict[str, bytes], *, raw_names: dict[str, bytes] | None = None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    data = buffer.getvalue()
    # Replace names by legacy-encoded bytes (no UTF-8 flag), as old Windows
    # tools write them.
    for placeholder, raw in (raw_names or {}).items():
        encoded = placeholder.encode("ascii")
        assert len(encoded) == len(raw)
        data = data.replace(encoded, raw)
    return zipfile.ZipFile(io.BytesIO(data))


class TestEntryPaths:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("a/b/c.pdf", ["a", "b", "c.pdf"]),
            ("a\\b\\c.pdf", ["a", "b", "c.pdf"]),
            ("./a//c.pdf", ["a", "c.pdf"]),
            ("../evil.pdf", None),
            ("a/../../evil.pdf", None),
            ("/etc/passwd", None),
            ("C:/Windows/x.dll", None),
        ],
    )
    def test_split(self, name, expected):
        assert split_entry_path(name) == expected

    def test_folder_name(self):
        assert folder_name_for_archive("تقارير 2024.zip") == "تقارير 2024"
        assert folder_name_for_archive("C:\\x\\Reports.ZIP") == "Reports"
        assert folder_name_for_archive(".zip") == "archive"


class TestDecodeNames:
    def test_utf8_flag(self):
        archive = make_zip({"تقرير.pdf": b"x"})
        assert decode_entry_name(archive.infolist()[0]) == "تقرير.pdf"

    @pytest.mark.parametrize("encoding", ["cp1256", "cp720"])
    def test_legacy_arabic(self, encoding):
        arabic = "عقد.pdf"
        raw = arabic.encode(encoding)
        placeholder = "x" * (len(raw) - 4) + ".pdf"
        archive = make_zip({placeholder: b"x"}, raw_names={placeholder: raw})
        info = archive.infolist()[0]
        assert not info.flag_bits & 0x800
        assert decode_entry_name(info) == arabic

    def test_plain_ascii(self):
        archive = make_zip({"report.pdf": b"x"})
        assert decode_entry_name(archive.infolist()[0]) == "report.pdf"


class TestPlan:
    def test_folders_and_clutter(self):
        archive = make_zip(
            {
                "Reports/2024/a.pdf": b"a",
                "Reports/b.docx": b"b",
                "Reports/.DS_Store": b"x",
                "__MACOSX/Reports/._a.pdf": b"x",
                "Reports/Thumbs.db": b"x",
                "Reports/empty.txt": b"",
                "../escape.pdf": b"x",
            },
        )
        plan = plan_archive(archive, root_name="Reports", **LIMITS)
        files = {(p.folders, p.name) for p in plan.files}
        # The lone top folder repeats the archive's name and is dropped.
        assert files == {(("2024",), "a.pdf"), ((), "b.docx")}
        assert {reason for _, reason in plan.skipped} == {"empty", "unsafe path"}

    def test_keeps_a_differently_named_top_folder(self):
        archive = make_zip({"Inner/a.pdf": b"a"})
        plan = plan_archive(archive, root_name="Outer", **LIMITS)
        assert plan.files[0].folders == ("Inner",)

    def test_too_many_files(self):
        archive = make_zip({f"{i}.txt": b"x" for i in range(5)})
        with pytest.raises(ZipImportError):
            plan_archive(archive, root_name="r", **{**LIMITS, "max_files": 4})

    def test_too_large(self):
        archive = make_zip({"a.txt": b"x" * 2000})
        with pytest.raises(ZipImportError):
            plan_archive(archive, root_name="r", **{**LIMITS, "max_total_bytes": 1000})

    def test_bomb(self):
        archive = make_zip({"zeros.bin": b"\0" * (11 * 1024 * 1024)})
        with pytest.raises(ZipImportError):
            plan_archive(archive, root_name="r", **LIMITS)

    def test_document_package(self):
        assert is_document_package(make_zip({"[Content_Types].xml": b"x"}))
        assert is_document_package(make_zip({"mimetype": b"x", "content.xml": b"x"}))
        assert not is_document_package(make_zip({"docs/a.pdf": b"x"}))


@override_settings(CONSUMER_ZIP_EXTRACT=True)
class TestZipArchivePlugin(DirectoriesMixin, DocumentConsumeDelayMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("uploader")

    def write_zip(self, entries: dict[str, bytes], name="Project.zip") -> Path:
        path = self.dirs.scratch_dir / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, data in entries.items():
                archive.writestr(entry, data)
        return path

    def run_plugin(self, path: Path, **overrides):
        input_doc = ConsumableDocument(
            source=DocumentSource.ApiUpload,
            original_file=path,
        )
        metadata = DocumentMetadataOverrides(
            filename=path.name,
            owner_id=self.user.pk,
            **overrides,
        )
        plugin = ZipArchivePlugin(
            input_doc,
            metadata,
            DummyProgressManager(path.name, "task"),
            self.dirs.scratch_dir,
            "task",
        )
        assert plugin.able_to_run
        with pytest.raises(StopConsumeTaskError) as stop:
            plugin.run()
        return stop.value.message

    def test_unpacks_into_folders(self):
        path = self.write_zip(
            {
                "Project/plans/site.dwg": b"drawing",
                "Project/plans/old/v1.pdf": b"%PDF-1.4",
                "Project/readme.txt": b"hello",
            },
        )
        message = self.run_plugin(path, tag_ids=[1], title="ignored", asn=7)

        assert "3" in message
        assert not path.exists()

        root = Folder.objects.get(parent=None)
        assert root.name == "Project"
        assert root.owner == self.user
        plans = Folder.objects.get(parent=root, name="plans")
        old = Folder.objects.get(parent=plans, name="old")

        calls = {
            overrides.filename: (input_doc, overrides)
            for input_doc, overrides in self.get_all_consume_delay_call_args()
        }
        assert set(calls) == {"site.dwg", "v1.pdf", "readme.txt"}
        assert calls["site.dwg"][1].folder_id == plans.pk
        assert calls["v1.pdf"][1].folder_id == old.pk
        assert calls["readme.txt"][1].folder_id == root.pk
        input_doc, overrides = calls["site.dwg"]
        assert input_doc.original_file.read_bytes() == b"drawing"
        # Shared with every file: tags, owner. Not shared: title, ASN.
        assert overrides.tag_ids == [1]
        assert overrides.owner_id == self.user.pk
        assert overrides.title is None
        assert overrides.asn is None

    def test_same_archive_again_reuses_its_folder(self):
        self.run_plugin(self.write_zip({"a/x.txt": b"1"}))
        self.run_plugin(self.write_zip({"a/y.txt": b"2"}))
        assert Folder.objects.filter(name="Project").count() == 1
        assert Folder.objects.filter(name="a").count() == 1

    def test_into_the_folder_it_was_uploaded_to(self):
        target = Folder.objects.create(name="Contracts", owner=self.user)
        self.run_plugin(self.write_zip({"x.txt": b"1"}), folder_id=target.pk)
        assert Folder.objects.get(name="Project").parent == target

    def test_not_for_office_files(self):
        path = self.write_zip({"[Content_Types].xml": b"x", "word/document.xml": b"x"})
        plugin = ZipArchivePlugin(
            ConsumableDocument(source=DocumentSource.ApiUpload, original_file=path),
            DocumentMetadataOverrides(),
            DummyProgressManager(path.name, "task"),
            self.dirs.scratch_dir,
            "task",
        )
        assert not plugin.able_to_run

    @override_settings(CONSUMER_ZIP_EXTRACT=False)
    def test_disabled(self):
        path = self.write_zip({"x.txt": b"1"})
        plugin = ZipArchivePlugin(
            ConsumableDocument(source=DocumentSource.ApiUpload, original_file=path),
            DocumentMetadataOverrides(),
            DummyProgressManager(path.name, "task"),
            self.dirs.scratch_dir,
            "task",
        )
        assert not plugin.able_to_run
