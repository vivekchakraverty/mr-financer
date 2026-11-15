"""Forward pitch illustration requests to the configured Hugging Face Space."""
import asyncio
import base64
import binascii
import json
from typing import Annotated

import httpx
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, StringConstraints

from .config import get_settings

router = APIRouter(prefix="/business-plan/pitch")
Prompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1200)]


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: Prompt


def upstream_failure(status, data):
    message = ""
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("error")
        if isinstance(detail, list):
            detail = " ".join(item.get("message", "") for item in detail if isinstance(item, dict))
        message = str(detail or "")
    elif isinstance(data, str):
        message = data
    message = message.lower()
    if "quota" in message or "runs limit" in message or status == 429:
        return HTTPException(429, "Your Hugging Face ZeroGPU quota or request limit was reached.")
    if status == 401 or "expired zerogpu proxy token" in message:
        return HTTPException(401, "Hugging Face rejected your access token. Enter a valid personal token.")
    if status == 403 or "permission" in message:
        return HTTPException(403, "Hugging Face could not authorize this token for ZeroGPU image generation.")
    if "checkpoint" in message:
        return HTTPException(503, "The FLUX checkpoint is currently unavailable. Try again shortly.")
    if status in (503, 504) or "queue" in message or "no gpu" in message:
        return HTTPException(503, "The image Space is starting or ZeroGPU is busy. Try again shortly.")
    if status == 404:
        return HTTPException(502, "The image Space did not expose this endpoint or the result expired.")
    return HTTPException(502, "The image Space did not complete the request.")


def response_data(response):
    try:
        return response.json()
    except ValueError:
        return {}


def decode_image(output):
    result = output[0] if isinstance(output, list) and len(output) == 1 else None
    if isinstance(result, dict) and result.get("ok") is True:
        encoded = result.get("image_base64")
        if isinstance(encoded, str) and 0 < len(encoded) <= 3_000_000:
            try:
                image = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                image = b""
            if image.startswith(b"\xff\xd8\xff") and image.endswith(b"\xff\xd9"):
                return image
    raise HTTPException(502, "The image Space did not return a valid JPEG.")


@router.post("/image")
async def image(body: ImageRequest, authorization: str = Header(default="")):
    scheme, _, token = authorization.partition(" ")
    if (scheme.lower() != "bearer" or not token.startswith("hf_") or not 3 < len(token) <= 2048
            or not token.isascii() or not all(c.isalnum() or c in "._-" for c in token[3:])):
        raise HTTPException(401, "Enter your personal Hugging Face access token.")
    base = httpx.URL(get_settings().pitch_image_space_url)
    if (base.scheme != "https" or not base.host or not base.host.endswith(".hf.space")
            or base.username or base.password or base.port or base.path != "/" or base.query or base.fragment):
        raise HTTPException(503, "Configure a valid Hugging Face pitch image Space URL.")
    url = str(base).rstrip("/") + "/gradio_api/call/generate_pitch_image"
    try:
        async with asyncio.timeout(190):
            async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                         timeout=httpx.Timeout(155, connect=15),
                                         follow_redirects=False) as client:
                submitted = await client.post(url, json={"data": [body.prompt]})
                data = response_data(submitted)
                if not submitted.is_success:
                    raise upstream_failure(submitted.status_code, data)
                event_id = data.get("event_id") if isinstance(data, dict) else None
                if not isinstance(event_id, str) or not 1 <= len(event_id) <= 80 or not event_id.isascii() or not event_id.isalnum():
                    raise HTTPException(502, "Hugging Face did not start the image job.")
                async with client.stream("GET", f"{url}/{event_id}") as response:
                    if not response.is_success:
                        await response.aread()
                        raise upstream_failure(response.status_code, response_data(response))
                    event = ""
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:") and event in ("complete", "error"):
                            try:
                                output = json.loads(line[5:])
                            except ValueError:
                                output = line[5:].strip()
                            if event == "error":
                                raise upstream_failure(502, output)
                            return Response(decode_image(output), media_type="image/jpeg",
                                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    except (TimeoutError, httpx.TimeoutException):
        raise HTTPException(504, "Hugging Face took too long to generate the image.") from None
    except httpx.HTTPError:
        raise HTTPException(503, "Mr Financer could not reach the Hugging Face image Space.") from None
    raise HTTPException(502, "Hugging Face closed the image job before returning its result.")

