---
title: Mr Financer Learn
sdk: docker
app_port: 7860
license: apache-2.0
---

# Mr Financer Learn CPU service

This Docker Space runs Qwen2.5-1.5B-Instruct Q4_K_M with llama.cpp for fresh,
transcript-based questions. An ONNX build of DeBERTa classifies English viewer
comments as specific learning feedback, confusion, or possible corrections.
Run on CPU Basic. The service handles generation and classification in memory.
Course progress and scoring are stored by Mr. Financer.

The app calls POST /questions and POST /signals. GET /health confirms the
question model is ready. Use Protected visibility so the runtime is reachable
from other Hugging Face accounts. Both POST endpoints validate the caller's
Hugging Face user access token. A read token is sufficient.

Models: [Qwen](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF)
(Apache-2.0) and
[DeBERTa](https://huggingface.co/MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33)
(MIT).
