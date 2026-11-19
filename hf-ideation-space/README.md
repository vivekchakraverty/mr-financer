---
title: Mr Financer Ideation CPU
emoji: 💡
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 7860
---

# Ideation CPU brief service

Qwen3-4B-Instruct-2507 Q4_K_M runs through llama.cpp with GPU layers disabled. The model
weights are downloaded at build time. Use CPU Basic initially, one concurrent
generation, then measure memory and response time with real packets. A weight
file fitting in memory does not establish acceptable response time.

POST `/brief` with a `BriefPacket` and the requesting user's HF bearer token.
The service checks the personal account, uses the supplied retrieved context,
and returns validated qualitative prose. Financial numbers are calculated and
rendered by Mr Financer. No inference-provider or ZeroGPU quota is used here;
the Space owner remains responsible for any chosen paid CPU hardware.

Deploy this directory to a Docker Space and set `MRF_VIABILITY_CPU_URL` to its
HTTPS root URL. Keep `viability_contract.py` identical to the application's
copy; the test suite verifies this. Model: https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507

The deployment uses a text-only instruction model and llama.cpp's native
completion endpoint, with explicit ChatML and constrained JSON sampling. The
readiness probe verifies schema enforcement. The app validates every returned
draft, and reports missing market evidence explicitly. Expect several minutes
per CPU Basic draft; the smallest tested model was faster but gave weaker analysis.

For a local model, run a current llama.cpp server with the Q4_K_M file,
`--n-gpu-layers 0 --ctx-size 24576 --parallel 1 --chat-template chatml --skip-chat-parsing`, then set
`MRF_VIABILITY_CPU_URL=http://127.0.0.1:8080`. No HF token is needed locally.
