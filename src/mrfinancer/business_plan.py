"""Same-origin proxy from Mr Financer to its Gradio Space."""
import json
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .config import get_settings
from .plan_export import PLAN_MEDIA_TYPES, PlanExport, export_plan
from .plan_financials import PlanRequest, build_exhibits

router = APIRouter()
MAX_PAYLOAD = 80_000


def upstream_error(payload):
    detail = payload.get("error") if isinstance(payload, dict) else payload
    if isinstance(detail, dict):
        return (str(detail.get("code", "")), str(detail.get("message", ""))[:500])
    return "", str(detail or "")[:500]


def translate(status, payload):
    code, message = upstream_error(payload)
    if code in {"hf_token_required", "hf_user_required", "hf_token_invalid", "hf_token_permissions",
                "hf_quota_exhausted", "hf_rate_limit", "hf_unavailable"} and message:
        return HTTPException(status_code=status, detail=message)
    lower = message.lower()
    if "quota" in lower or "runs limit" in lower or status == 429:
        return HTTPException(429, "Your Hugging Face ZeroGPU quota or request limit is exhausted. "
                                     "Check your quota and reset time on Hugging Face.")
    if status == 401 or any(term in lower for term in ("unauthorized", "invalid token", "expired zerogpu proxy token")):
        return HTTPException(401, "Hugging Face rejected your token. Enter a valid personal access token.")
    if status == 403 or any(term in lower for term in ("permission", "forbidden")):
        return HTTPException(403, "Your Hugging Face token lacks permission to call the Space or use ZeroGPU. "
                                     "Check your token permissions.")
    if "queue" in lower or "no gpu" in lower or status in (503, 504):
        return HTTPException(503, "Hugging Face ZeroGPU is busy or the Space is starting. Try again shortly.")
    return HTTPException(502, "The business plan Space could not complete the request. Try again shortly.")


@router.get("/business-plan/questionnaire")
def questionnaire():
    path = Path(__file__).with_name("web") / "Mr_Mentor_Business_Plan_Questionnaire.md"
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=path.name)


@router.post("/business-plan/preview")
async def preview(request: Request):
    payload = await read_payload(request)
    return {"ok": True, "sections": [], "exhibits": build_exhibits(payload)}


@router.post("/business-plan/export/{file_format}")
async def download_plan(file_format: Literal["pdf", "docx"], request: Request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 1_048_576:
            raise HTTPException(413, "This business plan is too large to export.")
        data.extend(chunk)
    try:
        plan = PlanExport.model_validate_json(data)
    except ValidationError:
        raise HTTPException(422, "Provide a valid business plan to export.") from None
    content = await run_in_threadpool(export_plan, plan, file_format)
    name = "Business-Plan-Financial-Preview" if plan.preview else "Business-Plan"
    return Response(content, media_type=PLAN_MEDIA_TYPES[file_format], headers={
        "Content-Disposition": f'attachment; filename="Mr-Financer-{name}.{file_format}"',
        "Cache-Control": "no-store",
    })


@router.post("/business-plan/draft")
async def draft(request: Request, authorization: str = Header(default="")):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.startswith("hf_") or len(token) > 2048 or not token.isascii() or not all(c.isalnum() or c in "._-" for c in token[3:]):
        raise HTTPException(401, "Enter your personal Hugging Face access token.")
    payload = (await read_payload(request)).model_dump(mode="json")
    base = httpx.URL(get_settings().business_plan_space_url)
    if (base.scheme != "https" or not base.host or not base.host.endswith(".hf.space")
            or base.username or base.password or base.port or base.path != "/" or base.query or base.fragment):
        raise HTTPException(503, "Configure the Hugging Face Space URL for the business plan module.")
    url = str(base).rstrip("/") + "/gradio_api/call/draft_plan"
    try:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=150,
                                     follow_redirects=False) as client:
            submitted = await client.post(url, json={"data": [payload]})
            if not submitted.is_success:
                raise translate(submitted.status_code, parse_json(submitted))
            event_id = parse_json(submitted).get("event_id", "")
            if not isinstance(event_id, str) or not event_id.isascii() or not event_id.isalnum():
                raise HTTPException(502, "Hugging Face did not start the plan request.")
            async with client.stream("GET", f"{url}/{event_id}") as response:
                if not response.is_success:
                    await response.aread()
                    raise translate(response.status_code, parse_json(response))
                event = ""
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line.partition(":")[2].strip()
                    elif line.startswith("data:") and event in ("complete", "error"):
                        try:
                            output = json.loads(line.partition(":")[2].strip())
                        except ValueError:
                            raise translate(502, {"error": line.partition(":")[2]}) from None
                        if event == "error":
                            raise translate(502, {"error": output})
                        result = output[0] if isinstance(output, list) and len(output) == 1 else None
                        if isinstance(result, dict) and "ok" in result:
                            return result
                        raise HTTPException(502, "The Space returned an incomplete plan response.")
    except httpx.TimeoutException:
        raise HTTPException(504, "Hugging Face took too long to respond. Check the Space and try again.") from None
    except httpx.HTTPError:
        raise HTTPException(503, "Mr Financer could not reach the Hugging Face Space.") from None
    raise HTTPException(502, "Hugging Face closed the request before sending a result.")


def parse_json(response):
    try:
        result = response.json()
        return result if isinstance(result, dict) else {}
    except ValueError:
        return {}


async def read_payload(request):
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_PAYLOAD:
            raise HTTPException(413, "Shorten your notes or select fewer competitor figures.")
    try:
        return PlanRequest.model_validate_json(chunks)
    except ValidationError as exc:
        issue = exc.errors(include_input=False, include_context=False, include_url=False)[0]
        field = ".".join(str(part) for part in issue["loc"])
        message = f"{field}: {issue['msg']}" if field else issue["msg"]
        raise HTTPException(422, message) from None
