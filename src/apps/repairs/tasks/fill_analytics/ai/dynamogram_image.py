"""Файл динамограммы → картинка для модели.

Тип определяется по сигнатуре, а не по расширению: под ``.png``/``.jpg`` в S3
лежат и HTML-страницы ABAI (вместо файла скачалась страница), и пустые файлы.
Такие модель не прочитает — на них LLM не вызывается. PDF отдаётся первой
страницей в PNG: картинку в PDF модель не принимает. Большие картинки
уменьшаются: огромный скан сервер модели может не принять, и он падал бы
на каждом повторе.

Вызывается из потока (``asyncio.to_thread``): pdfium не потокобезопасен,
поэтому работа с ним — под общим замком.
"""

import threading
from io import BytesIO

import pypdfium2 as pdfium
from PIL import Image

# Большая сторона картинки для модели. PDF: 72 dpi × 2 ≈ 144 dpi (подписи
# осей читаются, A4 — около 1200×1700 px), масштаб считается от размера
# страницы до рендера — иначе гигантская страница съест память воркера.
MAX_SIDE_PX = 2000
PDF_RENDER_SCALE = 2

_PDFIUM_LOCK = threading.Lock()

_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpeg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)


class UnsupportedDynamogramFileError(Exception):
    """Файл не картинка и не PDF; ``kind`` — что в нём оказалось."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def detect_kind(payload: bytes) -> str:
    if not payload.strip():
        return "empty"
    for signature, kind, _mime in _IMAGE_SIGNATURES:
        if payload.startswith(signature):
            return kind
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    if payload.startswith(b"%PDF"):
        return "pdf"
    head = payload[:512].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        return "html"
    return "unknown"


def prepare_image(payload: bytes) -> tuple[bytes, str]:
    """Байты и MIME картинки для модели.

    Raises:
        UnsupportedDynamogramFileError: файл пустой, HTML, битый PDF и т.п.
    """
    kind = detect_kind(payload)
    for _signature, image_kind, mime in _IMAGE_SIGNATURES:
        if kind == image_kind:
            return _prepare_raster(payload, mime)
    if kind == "webp":
        return _prepare_raster(payload, "image/webp")
    if kind == "pdf":
        with _PDFIUM_LOCK:
            return _render_pdf_first_page(payload), "image/png"
    raise UnsupportedDynamogramFileError(kind)


def _prepare_raster(payload: bytes, mime: str) -> tuple[bytes, str]:
    """Проверить картинку и уменьшить, если она больше ``MAX_SIDE_PX``.

    Обрезанную или битую картинку с верной сигнатурой модель не примет (400
    от сервера) — она отсекается здесь, без вызова LLM.
    """
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
        # После verify() картинку нужно открыть заново.
        with Image.open(BytesIO(payload)) as image:
            if max(image.size) <= MAX_SIDE_PX:
                return payload, mime
            image.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
            if mime == "image/jpeg":
                return _encode(image.convert("RGB"), "JPEG"), mime
            return _encode(image, "PNG"), "image/png"
    except Exception as exc:
        kind = "image_broken"
        raise UnsupportedDynamogramFileError(kind) from exc


def _render_pdf_first_page(payload: bytes) -> bytes:
    try:
        pdf = pdfium.PdfDocument(payload)
    except pdfium.PdfiumError as exc:
        kind = "pdf_broken"
        raise UnsupportedDynamogramFileError(kind) from exc
    try:
        return _render_first_page(pdf)
    except UnsupportedDynamogramFileError:
        raise
    except Exception as exc:
        # Битая страница, сбой рендера — свойство файла, а не сбой разбора.
        kind = "pdf_broken"
        raise UnsupportedDynamogramFileError(kind) from exc
    finally:
        pdf.close()


def _render_first_page(pdf: pdfium.PdfDocument) -> bytes:
    if len(pdf) == 0:
        kind = "pdf_empty"
        raise UnsupportedDynamogramFileError(kind)
    page = pdf[0]
    width, height = page.get_size()
    scale = min(PDF_RENDER_SCALE, MAX_SIDE_PX / max(width, height, 1))
    return _encode(page.render(scale=scale).to_pil(), "PNG")


def _encode(image: Image.Image, image_format: str) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()
