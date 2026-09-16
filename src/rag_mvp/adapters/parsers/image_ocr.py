"""图片文字识别适配器：把演示文稿截图里的文字变成可检索文本。

复用 PDF 扫描件同一条 Tesseract 依赖，不引入新的渲染工具；EMF/SVG 等
矢量素材没有点阵也无法被 Tesseract 读取，由调用方按扩展名过滤。
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

# OCR 结果只用于检索：压掉排版空白、丢掉纯符号行，并限制单图上限。
_WHITESPACE = re.compile(r"[ \t\u00a0\u3000]+")
_MEANINGFUL = re.compile(r"[0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_MAX_IMAGE_TEXT_CHARS = 20_000
# 可被 Tesseract 直接读取的点阵格式；EMF/WMF/SVG 由调用方跳过。
RASTER_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"})


class ImageOcr(Protocol):
    """OCR boundary kept inside the parser adapter."""

    def available(self) -> bool: ...

    async def extract(
        self,
        content: bytes,
        *,
        suffix: str,
        language: str,
        timeout_seconds: float,
    ) -> str: ...


class TesseractImageOcr:
    """Read text from one raster image with the system Tesseract binary."""

    def __init__(self, executable: str = "tesseract") -> None:
        self._executable = executable

    def available(self) -> bool:
        return shutil.which(self._executable) is not None

    async def extract(
        self,
        content: bytes,
        *,
        suffix: str,
        language: str,
        timeout_seconds: float,
    ) -> str:
        return await asyncio.to_thread(
            self._extract_sync,
            content,
            suffix,
            language,
            timeout_seconds,
        )

    def _extract_sync(
        self,
        content: bytes,
        suffix: str,
        language: str,
        timeout_seconds: float,
    ) -> str:
        # 不可用、空内容或超时都只返回空字符串：演示文稿仍按已有正文入库，
        # 不能因为截图识别失败让整份文档摄取失败。
        if not content or not self.available():
            return ""
        with TemporaryDirectory(prefix="rag-image-ocr-") as temporary:
            image = Path(temporary) / f"image{suffix or '.png'}"
            image.write_bytes(content)
            try:
                completed = subprocess.run(
                    [
                        self._executable,
                        str(image),
                        "stdout",
                        "-l",
                        language,
                        "--psm",
                        "6",
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    check=False,
                    timeout=timeout_seconds,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return ""
        if completed.returncode != 0:
            return ""
        return normalize_image_text(completed.stdout.decode("utf-8", "ignore"))


# 规范化 OCR 文本：去掉排版空白与无意义行，保留换行以维持可读性。
def normalize_image_text(text: str) -> str:
    """Collapse OCR whitespace and drop lines without any alphanumeric or CJK character."""

    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
    kept = [line for line in lines if _MEANINGFUL.search(line)]
    return "\n".join(kept)[:_MAX_IMAGE_TEXT_CHARS]
