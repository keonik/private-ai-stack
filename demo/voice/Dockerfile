# Two stages: node builds the page, python serves it. The runtime image carries no node and no
# node_modules — only the built assets and a small FastAPI app.
FROM node:24-slim AS web
WORKDIR /src
COPY web/package.json web/package-lock.json ./web/
RUN cd web && npm ci --no-audit --no-fund
COPY web ./web
RUN cd web && npm run build          # vite writes to /src/static

FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastapi==0.115.6" "uvicorn[standard]==0.34.0" "httpx==0.28.1" "python-multipart==0.0.20" \
    "numpy==2.5.3" "onnxruntime==1.30.0"
# Smart Turn v3.2 (Pipecat, BSD-2), pinned by checksum so a changed upstream file fails the build loudly.
RUN python -c "import hashlib,urllib.request as u; \
r=u.urlopen(u.Request('https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx',headers={'User-Agent':'voice-demo-build'})); b=r.read(); \
h=hashlib.sha256(b).hexdigest(); assert h=='2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f', h; \
open('smart-turn-v3.2-cpu.onnx','wb').write(b)"
COPY app.py smart_turn.py ./
COPY --from=web /src/static ./static
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/api/health',timeout=3)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
