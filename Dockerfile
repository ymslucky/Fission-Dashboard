# ---- 前端构建 ----
FROM cr.msxor.cn/library/node:22-alpine AS web
WORKDIR /build
COPY web/package.json web/pnpm-lock.yaml* web/pnpm-workspace.yaml* ./
RUN corepack enable && pnpm install --ignore-scripts --no-frozen-lockfile
COPY web/ .
RUN pnpm build

# ---- 运行时 ----
FROM cr.msxor.cn/library/python:3.12-slim
WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/ ./server/
COPY --from=web /build/dist ./web/dist
ENV PORT=8080 STATIC_DIR=/app/web/dist
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8080"]
