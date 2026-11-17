"""
The consume task step that unpacks a ZIP archive (see documents.zip_import).

It runs right after the preflight checks. For an archive it creates a folder
named after the archive (inside the folder the archive was uploaded to, if
any) with the archive's own folders beneath it, queues every file as a
document of its own in its folder, and ends the task: the archive itself is
not kept as a document. A ZIP inside the archive is unpacked in turn when its
own task runs, into a folder of its name.

The files inherit what the archive was uploaded with (owner, permissions,
tags, correspondent, document type, custom fields), except what cannot be
shared: the title and the archive serial number.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.db import transaction

from documents.consumer import ConsumerPluginMixin
from documents.data_models import ConsumableDocument
from documents.loggers import LoggingMixin
from documents.models import Folder
from documents.plugins.base import ConsumeTaskPlugin
from documents.plugins.base import NoCleanupPluginMixin
from documents.plugins.base import NoSetupPluginMixin
from documents.plugins.base import StopConsumeTaskError
from documents.plugins.helpers import ProgressStatusOptions
from documents.zip_import import ZipImportError
from documents.zip_import import folder_name_for_archive
from documents.zip_import import is_document_package
from documents.zip_import import is_zip_mime_type
from documents.zip_import import plan_archive

_COPY_BUFFER = 1024 * 1024


class ZipArchivePlugin(
    NoSetupPluginMixin,
    NoCleanupPluginMixin,
    LoggingMixin,
    ConsumerPluginMixin,
    ConsumeTaskPlugin,
):
    NAME: str = "ZipArchivePlugin"
    logging_name = "paperless.consumer.zip"

    @property
    def able_to_run(self) -> bool:
        if not settings.CONSUMER_ZIP_EXTRACT:
            return False
        if not is_zip_mime_type(self.input_doc.mime_type):
            return False
        try:
            with zipfile.ZipFile(self.input_doc.original_file) as archive:
                return not is_document_package(archive)
        except (zipfile.BadZipFile, OSError):
            # Not readable as an archive: stored as a plain file instead.
            return False

    def run(self) -> str | None:
        root_name = folder_name_for_archive(self.filename)
        self._send_progress(
            10,
            100,
            ProgressStatusOptions.WORKING,
            f"Unpacking {self.filename}",
        )
        try:
            with zipfile.ZipFile(self.input_doc.original_file) as archive:
                plan = plan_archive(
                    archive,
                    root_name=root_name,
                    max_files=settings.CONSUMER_ZIP_MAX_FILES,
                    max_total_bytes=settings.CONSUMER_ZIP_MAX_UNCOMPRESSED_MB
                    * 1024
                    * 1024,
                    max_ratio=settings.CONSUMER_ZIP_MAX_RATIO,
                )
                if not plan.files:
                    self._fail(
                        "The archive contains no files to import",
                        f"{self.filename}: no files to import "
                        f"({len(plan.skipped)} skipped)",
                    )
                root = self._root_folder(root_name)
                extract_dir = Path(
                    tempfile.mkdtemp(
                        dir=settings.SCRATCH_DIR,
                        prefix="paperless-zip-",
                    ),
                )
                # Not removed on failure: files already queued are read from it.
                queued, skipped = self._extract_and_queue(
                    archive,
                    plan,
                    root,
                    extract_dir,
                )
        except ZipImportError as e:
            self._fail(str(e), f"{self.filename}: {e}")
        except zipfile.BadZipFile as e:
            self._fail("The archive is damaged", f"{self.filename}: {e}", exception=e)

        skipped += plan.skipped
        for name, reason in skipped:
            self.log.warning(f"{self.filename}: skipped {name} ({reason})")

        # The archive is now its files; it is not kept itself.
        self.input_doc.original_file.unlink(missing_ok=True)

        msg = f"Unpacked {queued} file(s) into folder «{root.name}»"
        if skipped:
            msg += f", {len(skipped)} skipped"
        self._send_progress(100, 100, ProgressStatusOptions.SUCCESS, msg)
        raise StopConsumeTaskError(msg)

    def _root_folder(self, name: str) -> Folder:
        """
        The archive's folder. The same archive uploaded again lands in the same
        folder (its files are then duplicates and skipped), not in a new copy.
        """
        parent = None
        if self.metadata.folder_id:
            parent = Folder.objects.filter(pk=self.metadata.folder_id).first()
        return self._child_folder(parent, name)

    def _child_folder(self, parent: Folder | None, name: str) -> Folder:
        with transaction.atomic():
            existing = Folder.objects.filter(
                parent=parent,
                name=name,
                owner_id=self.metadata.owner_id,
            ).first()
            if existing is not None:
                return existing
            return Folder.objects.create(
                parent=parent,
                name=name,
                owner_id=self.metadata.owner_id,
            )

    def _depth(self, folder: Folder) -> int:
        depth = 1
        while folder.parent_id is not None and depth <= Folder.MAX_NESTING_DEPTH:
            folder = Folder.objects.only("parent_id").get(pk=folder.parent_id)
            depth += 1
        return depth

    def _extract_and_queue(self, archive, plan, root, extract_dir):
        from documents import tasks

        folders: dict[tuple[str, ...], Folder] = {(): root}
        room = Folder.MAX_NESTING_DEPTH - self._depth(root)
        written = 0
        limit = settings.CONSUMER_ZIP_MAX_UNCOMPRESSED_MB * 1024 * 1024
        queued = 0
        skipped: list[tuple[str, str]] = []

        for index, planned in enumerate(plan.files):
            # Deeper than folders may nest: the file goes in the deepest one.
            path = planned.folders[: max(room, 0)]
            folder = self._folder_at(folders, path)

            target_dir = extract_dir / f"{index:05d}"
            target_dir.mkdir()
            target = target_dir / planned.name
            try:
                written += self._extract(archive, planned.info, target, limit - written)
            except ZipImportError as e:
                skipped.append((planned.info.filename, str(e)))
                shutil.rmtree(target_dir, ignore_errors=True)
                continue
            if planned.info.date_time:
                self._set_mtime(target, planned.info.date_time)

            overrides = copy.deepcopy(self.metadata)
            overrides.filename = planned.name
            overrides.title = None
            overrides.asn = None
            overrides.folder_id = folder.pk

            tasks.consume_file.delay(
                ConsumableDocument(
                    source=self.input_doc.source,
                    mailrule_id=self.input_doc.mailrule_id,
                    original_file=target.resolve(),
                    original_path=self.input_doc.original_file,
                ),
                overrides,
            )
            queued += 1
            if index % 25 == 0:
                self._send_progress(
                    10 + int(85 * index / len(plan.files)),
                    100,
                    ProgressStatusOptions.WORKING,
                    f"Unpacking {self.filename}: {index}/{len(plan.files)}",
                )
        return queued, skipped

    def _folder_at(self, folders: dict, path: tuple[str, ...]) -> Folder:
        if path in folders:
            return folders[path]
        parent = self._folder_at(folders, path[:-1])
        folder = self._child_folder(parent, path[-1])
        folders[path] = folder
        return folder

    @staticmethod
    def _extract(archive, info, target: Path, budget: int) -> int:
        """
        Copy one entry out, never more than it declared: the sizes in a ZIP's
        directory are what the limits were checked against, and a crafted
        archive can lie about them.
        """
        allowed = min(info.file_size, budget)
        written = 0
        with archive.open(info) as source, target.open("wb") as out:
            while chunk := source.read(_COPY_BUFFER):
                written += len(chunk)
                if written > allowed:
                    raise ZipImportError(
                        "larger than declared"
                        if written > info.file_size
                        else "over the size limit",
                    )
                out.write(chunk)
        return written

    @staticmethod
    def _set_mtime(target: Path, date_time) -> None:
        import datetime
        import os

        try:
            # ZIP stores local wall-clock time without a zone.
            stamp = datetime.datetime(*date_time).timestamp()  # noqa: DTZ001
            os.utime(target, (stamp, stamp))
        except (ValueError, OSError, OverflowError):
            pass
