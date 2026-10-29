# PRODUCT.md — Fission Console

## 产品定位
自建的 Fission 函数管理平台(Web 控制台),补齐 Fission 生态缺失的官方 UI。
单租户、内网 homelab 使用,管理员即唯一用户群体。

## 用户与场景
- 用户:homelab 管理员(经 Authentik forward-auth 认证后进入)。
- 场景:日常查看函数/触发器健康状态;新增或修改函数;排查函数故障(日志、调用测试);清理废弃资源。
- 成功标准:管理员不碰 CLI 即可完成函数全生命周期操作;10 秒内定位一个异常函数。

## 能力边界
- 完全依赖 Fission 官方接口:Kubernetes CRD API(fission.io/v1)+ Router 调用端点。不引入任何私有后端逻辑。
- 全生命周期:Functions / Packages / Environments / HTTPTriggers / TimerTriggers 的增删改查,函数日志,函数调用测试。
- 不做:多租户、配额管理、构建编排(构建仍由 Fission builder 完成)。

## 设计基调
Operate 模式:信息密度适中、扫读优先、状态一目了然(健康/异常用色彩冗余编码)。
中文界面,专业沉稳,避免玩具感。

## 架构（2026-10-29 重构后）
- 部署:Kubernetes Deployment + Service + Ingress(fission 命名空间,域名 fission-console.lab.msxor.cn,Authentik forward-auth;不再作为 Fission Function 自托管)。
- 后端:FastAPI + httpx 异步 BFF,直连 K8s API(CRD fission.io/v1)与 Fission Router;SSE watch/流式日志;策略预检(环境写限管理员、危险触发器字段校验)+ 审计(ConfigMap)。
- 前端:Vite + React + TS + TanStack Query + CodeMirror,静态产物由 BFF 同源托管。
- 能力分层:P0 生命周期+可观测(describe/调用归因/冷启动指标);P1 版本回滚(FunctionVersion/FunctionAlias,CRD 缺失时 501 降级)、金丝雀(CanaryConfig+functionWeights)、MCP 目录与令牌、GitOps 双向同步;P2 网格探测(Linkerd 已装)、租户视图。
- 指标源:PROMETHEUS_URL(当前 coroot-prometheus.coroot.svc:9090),表达式白名单。
