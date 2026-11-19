"""Gradio ZeroGPU service for the Mr Financer business plan module."""
import os
import time
from pathlib import Path

import spaces  # Load before any torch import for ZeroGPU.
import gradio as gr
import httpx
from httpx2 import HTTPError as HubTransportError
from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError
from pydantic import ValidationError
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from narrative import conversation, extract_narrative
from planning import PlanRequest, build_exhibits
from viability_contract import BriefPacket, conversation as brief_conversation, response_schema, extract_brief

ZERO_GPU = os.getenv("SPACES_ZERO_GPU") == "1"
GPU_SECONDS = int(os.getenv("PLAN_GPU_SECONDS", "60"))
BRIEF_GPU_SECONDS = int(os.getenv("BRIEF_GPU_SECONDS", "120"))
MODEL_PATH = Path(os.getenv("QWEN_MODEL_PATH", "/mnt/qwen"))
api = HfApi()
model = tokenizer = None
grammar_compiler = None


def failure(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


class RequireHuggingFaceUser:
    """Gate direct and queued Gradio inference before a GPU can be allocated."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        public_read = scope["method"] in ("GET", "HEAD", "OPTIONS") and not any(
            marker in scope["path"] for marker in ("/call/", "/queue/data", "/stream/")
        )
        if public_read:
            await self.app(scope, receive, send)
            return
        from anyio import to_thread

        request = Request(scope)
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token or len(token) > 2048:
            outcome = (401, failure("hf_token_required", "Enter your own Hugging Face access token."))
        else:
            outcome = await to_thread.run_sync(authorize, token, request.method)
            if outcome is None and ZERO_GPU and request.method == "POST":
                identity = request.headers.get("x-ip-token")
                if not authenticated_gpu_identity(identity):
                    outcome = (403, failure("hf_token_permissions",
                        "Hugging Face did not attach your token to a signed-in ZeroGPU request. Check the access token permissions."))
        if outcome is None:
            await self.app(scope, receive, send)
        else:
            await JSONResponse(outcome[1], status_code=outcome[0])(scope, receive, send)


def authenticated_gpu_identity(identity):
    # The Hub creates and signs this token. The ZeroGPU scheduler verifies the signature.
    if not identity:
        return False
    from base64 import urlsafe_b64decode
    import json

    try:
        payload = identity.split(".")[1]
        decoded = json.loads(urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return isinstance(decoded, dict) and not decoded.get("error") and bool(decoded.get("user"))
    except (IndexError, ValueError, TypeError):
        return False


def authorize(token, method):
    try:
        account = api.whoami(token=token)
        if account.get("type") != "user":
            return 403, failure("hf_user_required", "Use a token from a personal Hugging Face account.")
        # The signed Hub identity and scheduler enforce quota and eligible credit
        # usage. A remaining-daily-quota precheck would block paid extensions.
    except HfHubHTTPError as exc:
        status = exc.response.status_code
        if status == 401:
            return 401, failure("hf_token_invalid", "Hugging Face rejected this access token. Check that it is valid and active.")
        if status == 403:
            return 403, failure("hf_token_permissions", "Hugging Face denied access to your account or to this Space. Check your token permissions.")
        if status == 429:
            return 429, failure("hf_rate_limit", "Hugging Face rate-limited the account check. Try again shortly.")
        return 503, failure("hf_unavailable", "Hugging Face could not verify your account or ZeroGPU quota.")
    except (TimeoutError, ConnectionError, OSError, httpx.HTTPError, HubTransportError):
        return 503, failure("hf_unavailable", "Hugging Face could not verify your account or ZeroGPU quota.")
    return None


def enable_gpu_kernels(module, _inputs):
    # Device discovery must run inside the GPU worker, after allocation.
    if not module.use_kernels:
        started = time.monotonic()
        import triton

        # Blackwell kernels can require global scratch. FLA is imported before
        # ZeroGPU allocates hardware, so its import-time allocator probe misses it.
        def allocate_scratch(size, alignment, stream):
            return torch.empty(size, device="cuda", dtype=torch.int8)

        triton.set_allocator(allocate_scratch)
        module.use_kernels = True
        print(f"GPU kernels enabled: {torch.__version__}, {torch.cuda.get_device_name()}, {time.monotonic() - started:.2f}s", flush=True)


if ZERO_GPU and (MODEL_PATH / "config.json").is_file():
    import torch
    from transformers import AutoTokenizer, Qwen3_5ForCausalLM

    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
    model = Qwen3_5ForCausalLM.from_pretrained(MODEL_PATH, dtype=torch.bfloat16,
        local_files_only=True, attn_implementation="sdpa", low_cpu_mem_usage=True)
    model.eval().to("cuda")
    model.register_forward_pre_hook(enable_gpu_kernels)
    # Tokenizer indexing is CPU work; prepare it once before a caller leases a GPU.
    import xgrammar as xgr
    grammar_compiler = xgr.GrammarCompiler(
        xgr.TokenizerInfo.from_huggingface(tokenizer, vocab_size=model.config.vocab_size),
        max_threads=2, cache_limit_bytes=64 * 1024 * 1024,
    )


@spaces.GPU(duration=GPU_SECONDS)
def draft_plan(payload):
    if not ZERO_GPU:
        return failure("zerogpu_unavailable", "The business plan Space is awaiting ZeroGPU hardware. The forecast preview remains available in Mr Financer.")
    if model is None:
        return failure("model_unavailable", "The operator's private Qwen checkpoint is not available.")
    try:
        data = PlanRequest.model_validate(payload)
    except ValidationError as exc:
        issue = exc.errors(include_input=False, include_url=False, include_context=False)[0]
        return failure("invalid_answers", f"{'.'.join(map(str, issue['loc']))}: {issue['msg']}")
    exhibits = build_exhibits(data)
    inputs = tokenizer.apply_chat_template(conversation(data, exhibits), tokenize=True,
        return_dict=True, return_tensors="pt", add_generation_prompt=True, enable_thinking=False)
    if inputs["input_ids"].shape[-1] > 16000:
        return failure("input_too_long", "Shorten the notes or select fewer competitor figures, then try again.")
    try:
        with torch.inference_mode():
            tokens = model.generate(**inputs.to("cuda"), max_new_tokens=1000, do_sample=False)
        raw = tokenizer.decode(tokens[0, inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
        sections = extract_narrative(raw, exhibits["competitor_figures"])
    except (ValueError, TypeError):
        return {"ok": True, "sections": [], "exhibits": exhibits,
                "warning": "The model draft could not be validated. Your financial tables are available. Try generating a new draft."}
    return {"ok": True, "sections": sections, "exhibits": exhibits, "model": "Qwen3.5-9B"}


@spaces.GPU(duration=BRIEF_GPU_SECONDS)
def draft_viability(payload):
    if not ZERO_GPU or model is None:
        return failure("model_unavailable", "The ZeroGPU brief model is unavailable. Your calculations remain available in Mr Financer.")
    try:
        started = time.monotonic()
        packet = BriefPacket.model_validate(payload)
        inputs = tokenizer.apply_chat_template(brief_conversation(packet), tokenize=True, return_dict=True,
            return_tensors="pt", add_generation_prompt=True, enable_thinking=False)
        if inputs["input_ids"].shape[-1] > 22000:
            return failure("input_too_long", "The retrieved evidence exceeds the brief model's input limit.")
        import json
        from xgrammar.contrib.hf import LogitsProcessor
        grammar = grammar_compiler.compile_json_schema(json.dumps(response_schema(packet)))
        constraint = LogitsProcessor(grammar)
        print(f"Viability input prepared: {inputs['input_ids'].shape[-1]} tokens, {time.monotonic() - started:.2f}s", flush=True)
        from transformers import StoppingCriteria, StoppingCriteriaList
        input_length = inputs["input_ids"].shape[-1]

        class Timing(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                count = input_ids.shape[-1] - input_length
                if count <= 5 or count % 50 == 0:
                    print(f"Viability progress: {count} new tokens, {time.monotonic() - started:.2f}s", flush=True)
                return False

        with torch.inference_mode():
            tokens = model.generate(**inputs.to("cuda"), max_new_tokens=1400, do_sample=False,
                                    logits_processor=[constraint], stopping_criteria=StoppingCriteriaList([Timing()]))
        raw = tokenizer.decode(tokens[0, inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
        print(f"Viability generation finished: {tokens.shape[-1] - inputs['input_ids'].shape[-1]} new tokens, {time.monotonic() - started:.2f}s", flush=True)
        sections = extract_brief(raw, packet)
        # Canonicalize evidence-absence guidance before returning to any caller.
        import json
        raw = json.dumps({row["key"]: row["text"] for row in sections}, ensure_ascii=False)
        return {"ok": True, "raw": raw, "model": "Qwen3.5-9B"}
    except (ValidationError, ValueError, TypeError):
        return failure("invalid_brief", "The brief inputs could not be processed.")


with gr.Blocks(title="Mr Financer Business Plan API", analytics_enabled=False) as demo:
    gr.Markdown("## Mr Financer Business Plan API\nOpen the Business plan module in Mr Financer to draft a plan.")
    input_payload = gr.JSON(visible=False)
    output_plan = gr.JSON(visible=False)
    generate = gr.Button(visible=False)
    generate.click(draft_plan, inputs=input_payload, outputs=output_plan,
                   api_name="draft_plan", api_visibility="undocumented", concurrency_limit=1,
                   concurrency_id="qwen-model")
    brief_button = gr.Button(visible=False)
    brief_button.click(draft_viability, inputs=input_payload, outputs=output_plan,
                       api_name="draft_viability", api_visibility="undocumented", concurrency_limit=1,
                       concurrency_id="qwen-model")

if __name__ == "__main__":
    demo.queue(max_size=16).launch(show_error=False,
        app_kwargs={"middleware": [Middleware(RequireHuggingFaceUser)]})

