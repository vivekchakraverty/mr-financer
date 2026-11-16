FROM ghcr.io/ggml-org/llama.cpp:server

USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/learn-venv \
    && groupadd --non-unique --gid 1000 learn \
    && useradd --non-unique --uid 1000 --gid 1000 --create-home learn
ENV PATH="/opt/learn-venv/bin:$PATH" HF_HOME=/home/learn/.cache/huggingface LD_LIBRARY_PATH=/app
WORKDIR /opt/learn
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=1000:1000 app.py .
USER 1000:1000
RUN python app.py --download-models
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["app:app", "--host", "0.0.0.0", "--port", "7860"]
