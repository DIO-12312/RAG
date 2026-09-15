"""PowerPoint OOXML parser with PDF-like slide provenance."""

from __future__ import annotations

import posixpath
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import structlog

from rag_mvp.adapters.parsers.image_ocr import RASTER_SUFFIXES, ImageOcr
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment

_DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_OFFICE_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_PRESENTATION_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_MAX_ARCHIVE_FILES = 4096
# 单条目上限与单文件上限（64 MiB）对齐：合法的 41 MiB 讲稿若含一张大图或一段视频，
# 不应因为"单条目 32 MiB"这种比入口更严的隐性限制而解析失败。总量与压缩比继续兜底。
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_ARCHIVE_ENTRY_BYTES = 64 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 100
_MAX_XML_BYTES = 16 * 1024 * 1024


_LOGGER = structlog.get_logger("rag_mvp")


# 截图文字在 segment 内用该标记与幻灯片正文分隔，便于模型区分来源。
_IMAGE_TEXT_MARKER = "图片文字："


class PptxParser:
    """Extract visible slide text in presentation order without executing embedded content."""

    def __init__(
        self,
        max_slides: int = 1000,
        *,
        image_ocr: ImageOcr | None = None,
        ocr_language: str = "chi_sim+eng",
        ocr_timeout_seconds: float = 60.0,
        ocr_max_images_per_slide: int = 8,
        ocr_max_image_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if ocr_timeout_seconds <= 0:
            raise ValueError("ocr_timeout_seconds must be positive")
        if ocr_max_images_per_slide < 0:
            raise ValueError("ocr_max_images_per_slide must not be negative")
        if ocr_max_image_bytes < 1:
            raise ValueError("ocr_max_image_bytes must be at least 1")
        self._max_slides = max_slides
        self._image_ocr = image_ocr
        self._ocr_language = ocr_language
        self._ocr_timeout_seconds = ocr_timeout_seconds
        self._ocr_max_images_per_slide = ocr_max_images_per_slide
        self._ocr_max_image_bytes = ocr_max_image_bytes

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
                segments = []
                # 同一张截图可能被多页复用，按素材路径缓存识别结果。
                cache: dict[str, str] = {}
                for index, name in enumerate(slide_names, start=1):
                    text = self._slide_text(self._read_xml(archive, name))
                    images = await self._slide_image_text(archive, name, cache)
                    if images:
                        text = f"{text}\n\n{_IMAGE_TEXT_MARKER}\n{images}" if text else images
                    metadata = {"source_type": "pptx", "parser_mode": "slides"}
                    if images:
                        metadata["slide_image_text"] = "true"
                    segments.append(
                        ParsedSegment(
                            text=text,
                            locator=Locator(page_number=index),
                            metadata=metadata,
                        )
                    )
        except (BadZipFile, KeyError, ElementTree.ParseError, ValueError) as error:
            raise DomainError(
                DomainFailure("INVALID_PPTX", "PowerPoint file is invalid or unsupported")
            ) from error
        return tuple(segment for segment in segments if segment.text)

    # 读取本页引用的点阵图片并识别文字；矢量素材与识别失败都不影响正文入库。
    async def _slide_image_text(
        self,
        archive: ZipFile,
        slide_name: str,
        cache: dict[str, str],
    ) -> str:
        if self._image_ocr is None or self._ocr_max_images_per_slide == 0:
            return ""
        slide = PurePosixPath(slide_name)
        rels_name = str(slide.parent / "_rels" / f"{slide.name}.rels")
        if rels_name not in archive.namelist():
            return ""
        try:
            relationships = ElementTree.fromstring(self._read_xml(archive, rels_name))
        except (KeyError, ElementTree.ParseError):
            return ""
        resolved: list[str] = []
        for relation in relationships.findall(f"{_PACKAGE_REL_NS}Relationship"):
            if not relation.attrib.get("Type", "").endswith("/image"):
                continue
            target = relation.attrib.get("Target", "")
            if not target:
                continue
            # 图片关系是相对幻灯片目录的（真实讲稿写作 ../media/image1.png），
            # 因此允许 `..` 但必须规范化后仍落在 ppt/media/ 之内。
            media = posixpath.normpath(str(slide.parent / PurePosixPath(target)))
            if not media.startswith("ppt/media/"):
                continue
            if PurePosixPath(media).suffix.casefold() not in RASTER_SUFFIXES:
                continue
            if media in archive.namelist() and media not in resolved:
                resolved.append(media)
        texts: list[str] = []
        for media in sorted(resolved)[: self._ocr_max_images_per_slide]:
            if media not in cache:
                cache[media] = await self._recognize(archive, media)
            if cache[media]:
                texts.append(cache[media])
        return "\n".join(texts)

    async def _recognize(self, archive: ZipFile, media: str) -> str:
        assert self._image_ocr is not None
        try:
            info = archive.getinfo(media)
        except KeyError:
            return ""
        if info.file_size > self._ocr_max_image_bytes:
            return ""
        suffix = PurePosixPath(media).suffix.casefold()
        try:
            return await self._image_ocr.extract(
                archive.read(info),
                suffix=suffix,
                language=self._ocr_language,
                timeout_seconds=self._ocr_timeout_seconds,
            )
        except Exception as error:  # noqa: BLE001 - 识别是尽力而为的增强，不得中断摄取
            _LOGGER.info(
                "pptx_image_ocr_skipped",
                media=media,
                error=type(error).__name__,
            )
            return ""

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
