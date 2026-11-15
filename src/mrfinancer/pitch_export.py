"""Turn a batch of uploaded images into a widescreen PowerPoint."""
from io import BytesIO

from fastapi import APIRouter, HTTPException, Request, Response
from PIL import Image, ImageOps
from pptx import Presentation
from pptx.util import Inches
from python_multipart.exceptions import MultipartParseError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

router = APIRouter(prefix="/business-plan/pitch")
MAX_IMAGES = 40
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_BATCH_BYTES = 50 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_BATCH_BYTES + 1024 * 1024
MAX_PIXELS = 40_000_000
BATCH_ERROR = "Select images totaling 50 MB or less."
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class BatchTooLarge(MultiPartException):
    pass


async def _bounded_upload(request: Request):
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_REQUEST_BYTES:
            raise BatchTooLarge(BATCH_ERROR)
        yield chunk


def _prepare_image(upload: UploadFile):
    with Image.open(upload.file, formats=["JPEG", "PNG", "WEBP"]) as source:
        if source.width * source.height > MAX_PIXELS:
            raise ValueError("The image exceeds 40 megapixels.")
        source.load()
        orientation = source.getexif().get(274, 1)
        if (source.format in {"PNG", "JPEG"} and orientation == 1
                and not getattr(source, "is_animated", False)
                and source.mode in {"RGB", "RGBA", "L", "LA", "P"}):
            upload.file.seek(0)
            return upload.file, source.size

        with ImageOps.exif_transpose(source) as image:
            image.thumbnail((2560, 2560))
            output = BytesIO()
            if image.has_transparency_data:
                image.convert("RGBA").save(output, "PNG")
            else:
                image.convert("RGB").save(output, "JPEG", quality=95)
            output.seek(0)
            return output, image.size


def _build_deck(images: list[UploadFile]) -> bytes:
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13 + 1 / 3), Inches(7.5)
    deck.core_properties.title = "Image Slide Deck"
    for number, upload in enumerate(images, start=1):
        try:
            image, (width, height) = _prepare_image(upload)
            scale = min(deck.slide_width / width, deck.slide_height / height)
            slide_width, slide_height = max(1, round(width * scale)), max(1, round(height * scale))
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            slide.shapes.add_picture(
                image, (deck.slide_width - slide_width) // 2, (deck.slide_height - slide_height) // 2,
                slide_width, slide_height,
            )
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
            raise HTTPException(422, f"Image {number} must be a readable PNG, JPEG, or WebP under 40 megapixels.") from None
    output = BytesIO()
    deck.save(output)
    return output.getvalue()


@router.post("/export/pptx")
async def download_deck(request: Request):
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "multipart/form-data":
        raise HTTPException(415, "Upload image files using multipart form data.")
    content_length = request.headers.get("content-length", "")
    if content_length.isascii() and content_length.isdecimal() and int(content_length) > MAX_REQUEST_BYTES:
        raise HTTPException(413, BATCH_ERROR)
    try:
        form = await MultiPartParser(request.headers, _bounded_upload(request),
                                     max_files=MAX_IMAGES, max_fields=0).parse()
    except BatchTooLarge:
        raise HTTPException(413, BATCH_ERROR) from None
    except (MultiPartException, MultipartParseError):
        raise HTTPException(422, f"Upload up to {MAX_IMAGES} images in one batch.") from None
    try:
        images = form.getlist("images")
        if not images or len(images) != len(form.multi_items()) or any(
                not isinstance(image, UploadFile) for image in images):
            raise HTTPException(422, "Select at least one image to export.")
        for number, image in enumerate(images, start=1):
            if image.size is not None and image.size > MAX_IMAGE_BYTES:
                raise HTTPException(413, f"Image {number} must be 10 MB or less.")
        if sum(image.size or 0 for image in images) > MAX_BATCH_BYTES:
            raise HTTPException(413, BATCH_ERROR)
        content = await run_in_threadpool(_build_deck, images)
    finally:
        await form.close()
    return Response(content, media_type=PPTX_MEDIA_TYPE, headers={
        "Content-Disposition": 'attachment; filename="Mr-Financer-Slide-Deck.pptx"',
        "Cache-Control": "no-store",
    })
