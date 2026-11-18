"""Content type detection from magic bytes. The client's header is ignored."""

from __future__ import annotations

SVG = "image/svg+xml"


def sniff_mime(head: bytes) -> str | None:
    """``head`` = the first 64+ bytes of the file."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        box_size = int.from_bytes(head[0:4], "big")
        brands = {head[8:12]}
        # compatible brands follow major brand + minor version
        for i in range(16, min(box_size, len(head)) - 3, 4):
            brands.add(head[i : i + 4])
        if brands & {b"avif", b"avis"}:
            return "image/avif"
        return None  # HEIC, MP4, ... are not images we accept
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith((b"<svg", b"<?xml", b"<!doctype svg")) or b"<svg" in text[:256]:
        return SVG
    return None
