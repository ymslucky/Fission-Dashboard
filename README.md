# Fission Dashboard（Fission Console）

自建的 Fission 函数管理平台：FastAPI 后端（BFF）+ React/Vite 前端，用于管理 Fission
的函数、环境、包与各类触发器，并可直接在页面上做调用测试、看日志和指标。

## 目录

| 路径 | 说明 |
|---|---|
| `server/` | FastAPI 后端（BFF）：对接 Fission CRD、Router、Prometheus |
| `web/` | 前端（Vite + React + TypeScript），构建产物 `web/dist` |
| `app/`、`fn/` | Fission 侧的单文件函数实现（历史形态，镜像不再打包，见 `.dockerignore`）|
| `build.py` | 把 `app/`+`fn/` 打包成单文件函数的脚本（历史形态）|
| `Dockerfile` | 两段构建：node 构建前端 → python 运行时 |

## 本地开发

```bash
# 前端
cd web && pnpm install && pnpm dev
# 后端
cd server && pip install -r requirements.txt && uvicorn main:app --reload --port 8080
```

## 构建与部署（CI/CD）

镜像由集群内的 Tekton 流水线构建并推到 Harbor，再由 Argo CD 部署，**不需要本地 docker**：

```
git push (main)
  → GitHub webhook → Tekton EventListener(https://tekton.lab.msxor.cn)
  → Kaniko: harbor.lab.msxor.cn/demo/fission-console:<short-sha>
  → 回写 HomeLab-ArgoCD 仓库 platform/fission-console/kustomization.yaml 的 newTag
  → Argo CD 同步 → 滚动更新 https://fission-console.lab.msxor.cn
```

- 集群侧清单与流水线定义：`ymslucky/HomeLab-ArgoCD` 的 `platform/fission-console/`、`platform/tekton-ci/`
- 访问入口：https://fission-console.lab.msxor.cn （Authentik Forward Auth 登录）

## 运行时配置

| 环境变量 | 说明 |
|---|---|
| `FISSION_ROUTER` | Fission Router 地址，默认 `http://router.fission.svc:80` |
| `PROMETHEUS_URL` | 指标源，默认 `http://coroot-prometheus.coroot.svc:9090` |
| `ADMIN_GROUPS` | 允许写操作的 Authentik 组名 |
| `PORT` / `STATIC_DIR` | 监听端口 / 前端静态文件目录（镜像内已设好）|
