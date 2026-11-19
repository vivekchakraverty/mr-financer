"""Text-only Qwen CPU service. Retrieval and calculations run in the app."""
import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from huggingface_hub import hf_hub_download

from viability_contract import BriefPacket, native_request, extract_brief

MODEL = "unsloth/Qwen3-4B-Instruct-2507-GGUF"
WEIGHT = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
LLAMA = "http://127.0.0.1:8080"
generation = asyncio.Semaphore(1)
bearer = HTTPBearer(auto_error=False)
schema_checked = False


@asynccontextmanager
async def lifespan(_app):
    path = hf_hub_download(MODEL, WEIGHT, local_files_only=True)
    process = subprocess.Popen([os.getenv("IDEATION_LLAMA_SERVER", "/app/llama-server"), "--model", path,
        "--host", "127.0.0.1", "--port", "8080", "--n-gpu-layers", "0", "--ctx-size", "24576",
        "--parallel", "1", "--threads", "2", "--threads-batch", "2",
        "--chat-template", "chatml", "--skip-chat-parsing"])
    try:
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


app = FastAPI(title="Mr Financer Ideation CPU", lifespan=lifespan)


@app.get("/")
async def index():
    return {"service": "Mr Financer Ideation CPU", "model": MODEL, "api_docs": "/docs",
            "health": "/health", "generate": "/brief", "authentication": "Your Hugging Face personal access token"}


async def require_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None or len(credentials.credentials) > 2048:
        raise HTTPException(401, "Use your personal Hugging Face token.")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get("https://huggingface.co/api/whoami-v2",
                headers={"Authorization": "Bearer " + credentials.credentials})
        if response.status_code in (401, 403):
            raise HTTPException(response.status_code, "Hugging Face rejected this token.")
        response.raise_for_status()
        if response.json().get("type") != "user":
            raise HTTPException(403, "Use a personal Hugging Face account token.")
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Hugging Face account verification is unavailable.") from None


@app.get("/health")
async def health():
    global schema_checked
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(LLAMA + "/health", timeout=3)
            response.raise_for_status()
            if not schema_checked:
                async with generation:
                    response = await client.post(LLAMA + "/completion", json={"prompt": "Say hello.",
                        "n_predict": 32, "temperature": 0, "json_schema": {"type": "object", "properties": {
                            "ready": {"type": "string", "enum": ["OK"]}}, "required": ["ready"], "additionalProperties": False}})
                    response.raise_for_status()
                    import json
                    if json.loads(response.json()["content"]) != {"ready": "OK"}:
                        raise ValueError("Schema enforcement failed.")
                    schema_checked = True
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "CPU model is warming up or its structured-output self-check has not passed.") from None
    return {"status": "ready", "model": MODEL, "hardware": "CPU", "decoding": "cited-json-content"}


@app.post("/brief", dependencies=[Depends(require_user)])
async def brief(packet: BriefPacket):
    try:
        async with generation, httpx.AsyncClient(timeout=600) as client:
            response = await client.post(LLAMA + "/completion", json=native_request(packet))
            response.raise_for_status()
            raw = response.json()["content"]
            sections = extract_brief(raw, packet)
            import json
            raw = json.dumps({row["key"]: row["text"] for row in sections}, ensure_ascii=False)
            return {"raw": raw, "model": MODEL}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(503, "The CPU model is starting, busy, or could not produce a validated brief. Retry shortly.") from None


if __name__ == "__main__" and "--download-models" in sys.argv:
    hf_hub_download(MODEL, WEIGHT)
