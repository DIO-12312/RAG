"""PowerPoint OOXML parser with PDF-like slide provenance."""

from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment

_DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_OFFICE_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_PRESENTATION_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_MAX_ARCHIVE_FILES = 4096
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_ENTRY_BYTES = 32 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 100
_MAX_XML_BYTES = 16 * 1024 * 1024


class PptxParser:
    """Extract visible slide text in presentation order without executing embedded content."""

    def __init__(self, max_slides: int = 1000) -> None:
        self._max_slides = max_slides

    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        del source_name
        try:
            with ZipFile(BytesIO(content)) as archive:
                self._validate_archive(archive)
                slide_names = self._slide_names(archive)
                if not slide_names:
                    raise ValueError("presentation has no slides")
                if len(slide_names) > self._max_slides:
                    raise ValueError("presentation exceeds slide limit")
                segments = tuple(
                    ParsedSegment(
                        text=self._slide_text(self._read_xml(archive, name)),
                        locator=Locator(page_number=index),
                        metadata={"source_type": "pptx", "parser_mode": "slides"},
                    )
                    for index, name in enumerate(slide_names, start=1)
                )
        except (BadZipFile, KeyError, ElementTree.ParseError, ValueError) as error:
            raise DomainError(
                DomainFailure("INVALID_PPTX", "PowerPoint file is invalid or unsupported")
            ) from error
        return tuple(segment for segment in segments if segment.text)

    @staticmethod
    def _slide_names(archive: ZipFile) -> tuple[str, ...]:
        presentation = ElementTree.fromstring(PptxParser._read_xml(archive, "ppt/presentation.xml"))
        relationships = ElementTree.fromstring(
            PptxParser._read_xml(archive, "ppt/_rels/presentation.xml.rels")
        )
        targets = {
            relation.attrib["Id"]: PptxParser._slide_target(relation.attrib["Target"])
            for relation in relationships.findall(f"{_PACKAGE_REL_NS}Relationship")
            if relation.attrib.get("Type", "").endswith("/slide")
        }
        return tuple(
            targets[slide.attrib[f"{_OFFICE_REL_NS}id"]]
            for slide in presentation.findall(f".//{_PRESENTATION_NS}sldId")
            if slide.attrib.get(f"{_OFFICE_REL_NS}id") in targets
        )

    @staticmethod
    def _validate_archive(archive: ZipFile) -> None:
        entries = archive.infolist()
        if len(entries) > _MAX_ARCHIVE_FILES:
            raise ValueError("presentation contains too many files")
        total = 0
        for entry in entries:
            if entry.is_dir():
                continue
            if entry.file_size > _MAX_ARCHIVE_ENTRY_BYTES:
                raise ValueError("presentation entry exceeds size limit")
            if entry.file_size and (
                entry.compress_size == 0
                or entry.file_size > entry.compress_size * _MAX_COMPRESSION_RATIO
            ):
                raise ValueError("presentation compression ratio exceeds limit")
            total += entry.file_size
            if total > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError("presentation exceeds uncompressed size limit")

    @staticmethod
    def _read_xml(archive: ZipFile, name: str) -> bytes:
        info = archive.getinfo(name)
        if info.file_size > _MAX_XML_BYTES:
            raise ValueError("presentation XML exceeds size limit")
        xml = archive.read(info)
        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            raise ValueError("presentation XML declarations are not supported")
        return xml

    @staticmethod
    def _slide_target(target: str) -> str:
        path = PurePosixPath(target)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("presentation slide target is unsafe")
        resolved = PurePosixPath("ppt") / path
        if not str(resolved).startswith("ppt/slides/"):
            raise ValueError("presentation slide target is invalid")
        return str(resolved)

    @staticmethod
    def _slide_text(xml: bytes) -> str:
        root = ElementTree.fromstring(xml)
        return "\n".join(
            text.strip()
            for text in (node.text or "" for node in root.iter(f"{_DRAWING_NS}t"))
            if text.strip()
        )
