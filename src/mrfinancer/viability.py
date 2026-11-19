"""Calculate locally, retrieve evidence, then ask a CPU or caller-funded GPU model."""
import json
import time
from typing import Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import Field, ValidationError

from . import workspace
from .business_plan import parse_json, translate
from .config import get_settings
from .plan_financials import Input
from .viability_contract import native_request, extract_brief
from .viability_financials import ViabilityInputs, calculate
from .viability_retrieval import build_packet

router = APIRouter()


class BriefRequest(Input):
    search_id: str = Field(min_length=24, max_length=50)
    financials: ViabilityInputs = Field(default_factory=ViabilityInputs)
    mode: Literal["cpu", "zerogpu"] = "zerogpu"


async def read_body(request):
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 24_000:
            raise HTTPException(413, "The brief inputs are too large.")
    try:
        return BriefRequest.model_validate_json(chunks)
    except ValidationError as exc:
        error = exc.errors(include_input=False, include_context=False, include_url=False)[0]
        raise HTTPException(422, f"{'.'.join(map(str, error['loc']))}: {error['msg']}") from None


def prepare(body):
    snapshot = workspace.searches.get(body.search_id)
    context = workspace.research_contexts.get(body.search_id)
    if not snapshot or not context or time.monotonic() - snapshot[0] > workspace.SEARCH_TTL:
        raise HTTPException(404, "This search expired. Run the idea search again before creating a brief.")
    exhibits = calculate(body.financials)
    packet = build_packet(snapshot[1], context, exhibits)
    return exhibits, packet


def personal_token(authorization):
    scheme, _, token = authorization.partition(" ")
    if (scheme.lower() != "bearer" or not token.startswith("hf_") or len(token) > 2048
            or len(token) <= 3 or not token.isascii() or not all(c.isalnum() or c in "._-" for c in token[3:])):
        raise HTTPException(401, "Enter your own Hugging Face personal access token for the hosted model.")
    return token


def model_base(value, allow_local=False):
    try:
        url = httpx.URL(value)
        local = allow_local and url.scheme == "http" and url.host in ("127.0.0.1", "localhost", "::1")
        hosted = url.scheme == "https" and bool(url.host) and url.host.endswith(".hf.space") and not url.port
        if not (local or hosted) or url.username or url.password or url.path != "/" or url.query or url.fragment:
            raise ValueError()
        return str(url).rstrip("/"), local
    except (ValueError, httpx.InvalidURL):
        raise HTTPException(503, "Configure an Ideation CPU Hugging Face Space or a local llama.cpp server in the app settings.") from None


async def generate_cpu(packet, authorization):
    base, local = model_base(get_settings().viability_cpu_url, allow_local=True)
    headers = {} if local else {"Authorization": "Bearer " + personal_token(authorization)}
    data = native_request(packet) if local else packet.model_dump(mode="json")
    async with httpx.AsyncClient(timeout=600, follow_redirects=False, headers=headers) as client:
        response = await client.post(base + ("/completion" if local else "/brief"), json=data)
        if not response.is_success:
            if response.status_code in (401, 403, 429):
                raise translate(response.status_code, parse_json(response))
            raise HTTPException(503, "The Ideation CPU model is starting or unavailable. Your calculations remain available.")
        result = response.json()
        raw = result["content"] if local else result["raw"]
        return raw, str(result.get("model") or "Qwen3-4B-Instruct-2507") + " (CPU)"


async def generate_gpu(packet, authorization):
    token = personal_token(authorization)
    base, _ = model_base(get_settings().business_plan_space_url)
    endpoint = base + "/gradio_api/call/draft_viability"
    # Only the caller's token is used on BOTH submission and result retrieval.
    async with httpx.AsyncClient(timeout=180, follow_redirects=False,
                                 headers={"Authorization": "Bearer " + token}) as client:
        response = await client.post(endpoint, json={"data": [packet.model_dump(mode="json")]})
        if not response.is_success:
            raise translate(response.status_code, parse_json(response))
        event_id = parse_json(response).get("event_id")
        if not isinstance(event_id, str) or not event_id.isascii() or not event_id.isalnum():
            raise HTTPException(502, "The Space did not start the viability brief request.")
        async with client.stream("GET", f"{endpoint}/{event_id}") as response:
            if not response.is_success:
                await response.aread()
                raise translate(response.status_code, parse_json(response))
            event = ""
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event = line.partition(":")[2].strip()
                elif line.startswith("data:") and event in ("complete", "error"):
                    result = json.loads(line.partition(":")[2].strip())
                    if event == "error":
                        raise translate(502, {"error": result})
                    item = result[0] if isinstance(result, list) and len(result) == 1 else {}
                    if not isinstance(item, dict) or not item.get("ok"):
                        raise translate(502, item)
                    return item["raw"], "Qwen3.5-9B (ZeroGPU, your account quota)"
    raise HTTPException(502, "The Space closed before returning the brief.")


@router.post("/idea/viability/preview")
async def preview(request: Request):
    exhibits, packet = prepare(await read_body(request))
    return {"exhibits": exhibits, "packet": packet.model_dump(mode="json")}


@router.post("/idea/viability/brief")
async def brief(request: Request, authorization: str = Header(default="")):
    body = await read_body(request)
    exhibits, packet = prepare(body)
    try:
        raw, model = await (generate_gpu(packet, authorization) if body.mode == "zerogpu" else generate_cpu(packet, authorization))
        sections = extract_brief(raw, packet)
    except httpx.TimeoutException:
        raise HTTPException(504, "The model took too long. Your calculations remain available; try a new draft shortly.") from None
    except httpx.HTTPError:
        raise HTTPException(503, "The model service could not be reached. Your calculations remain available.") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "The model draft failed the citation or number checks. Your calculated figures remain available. Try generating again.") from None
    return {"exhibits": exhibits, "packet": packet.model_dump(mode="json"), "sections": sections, "model": model}
