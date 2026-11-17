"""
Reading a ZIP archive as a tree of folders and files.

This module only decides what an archive contains and where each file goes;
documents.zip_plugin creates the folders and queues the files. Kept free of
the consumer's imports so the upload validation can use ZIP_MIME_TYPES.

What an archive may do is limited, since it comes from a user:

- an entry may not leave the archive ("../", absolute paths, drive letters);
- encrypted entries are skipped, there is no password to open them with;
- the number of files, their total unpacked size and how much each one grows
  when unpacked are capped, against archives built to fill the disk;
- system clutter (__MACOSX, .DS_Store, Thumbs.db...) is left out.

File names are often not UTF-8: Windows' own "compressed folder" writes them
in the OEM code page, which for Arabic is 720, and other tools use the ANSI
code page 1256. Python reads such names as code page 437, which turns Arabic
into mojibake, so they are decoded again (see decode_entry_name).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import pathvalidate

if TYPE_CHECKING:
    import zipfile

ZIP_MIME_TYPES = frozenset(
    {
        "application/zip",
        "application/x-zip",
        "application/x-zip-compressed",
    },
)

# ZIP general purpose flags.
_FLAG_ENCRYPTED = 0x1
_FLAG_UTF8 = 0x800

IGNORED_DIRECTORIES = frozenset({"__macosx", ".git", ".svn", "@eadir"})
IGNORED_FILES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})

# Entries that make a ZIP an Office or OpenDocument file, an EPUB, a JAR...
# rather than an archive of documents.
_PACKAGE_MARKERS = ("[content_types].xml", "mimetype", "meta-inf/manifest.mf")

_DRIVE = re.compile(r"^[A-Za-z]:$")
_ARABIC = re.compile(r"[؀-ۿ]")

MAX_NAME_LENGTH = 255


class ZipImportError(Exception):
    """The archive as a whole cannot be imported; the message is for the user."""


@dataclass
class PlannedFile:
    info: zipfile.ZipInfo
    folders: tuple[str, ...]
    name: str


@dataclass
class ZipPlan:
    files: list[PlannedFile] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    total_size: int = 0


def is_zip_mime_type(mime_type: str | None) -> bool:
    return mime_type in ZIP_MIME_TYPES


def is_document_package(archive: zipfile.ZipFile) -> bool:
    """True for a file format built on ZIP (docx, odt, epub...), not a folder."""
    names = {name.lower() for name in archive.namelist()[:50]}
    return any(marker in names for marker in _PACKAGE_MARKERS)


def decode_entry_name(info: zipfile.ZipInfo) -> str:
    """The entry's name as its author saw it."""
    if info.flag_bits & _FLAG_UTF8:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        # Already decoded from a unicode path extra field.
        return info.filename
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    candidates = []
    for encoding in ("cp720", "cp1256"):
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        candidates.append((len(_ARABIC.findall(decoded)), decoded))
    if candidates:
        score, best = max(candidates, key=lambda c: c[0])
        if score:
            return best
    return info.filename


def _clean_part(part: str) -> str:
    part = pathvalidate.sanitize_filename(part.strip(), replacement_text="_")
    return part[:MAX_NAME_LENGTH].strip()


def split_entry_path(name: str) -> list[str] | None:
    """The folder names and file name of an entry, or None if it is unsafe."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return None
    if name.startswith(("/", "\\")) or _DRIVE.match(parts[0]):
        return None
    if any(p == ".." for p in parts):
        return None
    cleaned = [_clean_part(p) for p in parts]
    if any(not p for p in cleaned):
        return None
    return cleaned


def _ignored(parts: list[str]) -> bool:
    if any(p.lower() in IGNORED_DIRECTORIES for p in parts[:-1]):
        return True
    name = parts[-1].lower()
    # "._report.pdf" is macOS metadata about "report.pdf", not a document.
    return name in IGNORED_FILES or name.startswith(("._", "~$"))


def plan_archive(
    archive: zipfile.ZipFile,
    *,
    root_name: str,
    max_files: int,
    max_total_bytes: int,
    max_ratio: int,
) -> ZipPlan:
    """
    Which files to import and in which folders, relative to the archive's own
    folder. Raises ZipImportError when the archive breaks a limit as a whole.
    """
    plan = ZipPlan()
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = decode_entry_name(info)
        parts = split_entry_path(name)
        if parts is None:
            plan.skipped.append((name, "unsafe path"))
            continue
        if _ignored(parts):
            continue
        if info.flag_bits & _FLAG_ENCRYPTED:
            plan.skipped.append((name, "encrypted"))
            continue
        if info.file_size == 0:
            plan.skipped.append((name, "empty"))
            continue
        # Small files compress well legitimately (a page of zeros); only a
        # large file growing enormously is a bomb.
        if (
            info.file_size > 10 * 1024 * 1024
            and info.file_size > max(info.compress_size, 1) * max_ratio
        ):
            raise ZipImportError(
                f"«{name}» unpacks to {info.file_size} bytes from "
                f"{info.compress_size}; the archive looks like a ZIP bomb.",
            )
        plan.total_size += info.file_size
        plan.files.append(PlannedFile(info, tuple(parts[:-1]), parts[-1]))

    if len(plan.files) > max_files:
        raise ZipImportError(
            f"The archive holds {len(plan.files)} files; at most {max_files} "
            "are imported from one archive.",
        )
    if plan.total_size > max_total_bytes:
        raise ZipImportError(
            f"The archive unpacks to {plan.total_size // (1024 * 1024)} MB; "
            f"at most {max_total_bytes // (1024 * 1024)} MB are allowed.",
        )

    _strip_redundant_root(plan, root_name)
    return plan


def _strip_redundant_root(plan: ZipPlan, root_name: str) -> None:
    """
    "Reports.zip" usually holds a single "Reports/" folder. The archive already
    gets a folder of that name, so the inner one would only nest it twice.
    """
    if not plan.files:
        return
    first = {f.folders[0].casefold() if f.folders else None for f in plan.files}
    if len(first) == 1 and first == {root_name.casefold()}:
        for planned in plan.files:
            planned.folders = planned.folders[1:]


def folder_name_for_archive(filename: str) -> str:
    stem = PurePosixPath(filename.replace("\\", "/")).name
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    return _clean_part(stem) or "archive"
