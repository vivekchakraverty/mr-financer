---
title: Mr Financer Business Plan
emoji: 📘
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 6.32.0
python_version: "3.12"
app_file: app.py
---

# Mr Financer business plan backend

Call `/gradio_api/call/draft_plan` from Mr Financer using the requesting person's Hugging Face bearer token on submission and streaming requests. The Space requires a Hugging Face user account for plan API calls. The protected Space's read-only model bucket volume is mounted at `/mnt/qwen`. Use Hugging Face's ZeroGPU hardware for inference.

The forecast and competitor exhibits are calculated before generation. The model can reference filing IDs in prose. The returned exhibits hold canonical amounts, periods, reporting scope, and references. The application keeps unknown values open.
