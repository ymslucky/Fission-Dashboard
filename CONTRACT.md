# Fission Console 数据契约表（④ 回填版）

> 基线：Fission v1.27.0（字段经 pkg/apis/core/v1/types.go 源码核实）。覆盖旅程测试暴露的契约缺口；"可空性"与"前端兜底"两列为阶段 D 新增。

| 端点/数据 | 字段 | 类型/可空性 | 前端兜底策略 | 备注 |
|---|---|---|---|---|
| GET /api/resources/functions → items[].status | ready/replicas/desiredReplicas | **v1.27 不存在（恒 undefined）** | 副本列显 '—/—'；状态列需走 describe（计划内改造） | 运行时状态来源应为 Deployment/executor |
| describe().package | name/buildStatus/buildlog/object | buildStatus 恒有值（unknown 兜底）；object 可 null | 构建徽章 failed→红/succeeded→绿/none→中性 | CRD: status.buildstatus.{status,buildlog}（小写） |
| describe().pods | {warm,ready} | 恒有值 | warm=0 时 invocable 按 executorType 推导 | poolmgr 按环境池 Deployment 统计 |
| describe().triggers[] | kind/ns/name/url | 数组可空 | 空时页签「触发器 0」+ 空态文案 | functionref 为对象：{type:'name',name} 或 {type:'function-weights',functionweights:{fn:w}}；weights 内嵌于 functionref |
| POST test 响应 | status | **可 null（超时）** | `status != null` 判空 → UNKNOWN 徽章 | requestId 来自 X-Fission-Request-ID |
| POST test 调用路径 | — | — | — | 内部监听器 router-internal:8889（8888 公共仅 HTTPTrigger 路由）；HMAC 见 functions_ext.py _internal_auth_headers |
| GET versions | versions[].created | 字符串可空 | fmtVersionTime(created, creationTimestamp) → '—' | **v1.27 无此 CRD**，端点 501 降级；解析假设待真实数据验证 |
| GET canaryconfigs → items[] | metadata.{name,namespace} + spec.{七字段} | functionweights 场景 name 为空串 | CanaryCell metadata 兜底 'default'/'—' | spec.duration 为 **Go duration 字符串**（"2m"）；failureType 混合大小写 |
| POST /canaryconfigs 请求体 | — | — | CRD envelope：{apiVersion,kind,metadata:{name,namespace},spec} | 后端读 obj.metadata.namespace + 整体 crd_create |
| GET canaryconfigs/{ns}/{name}/status | stage/functionWeights/failures | 可空 | stage String()；weights ?? {} | 阶段枚举含 Succeed（phaseTone 精确匹配） |
| GET /api/mcp/tools → tools[] | ns/name/toolDescription/inputSchema | 可空 | 列出即暴露（无 enabled 字段） | inputSchema 为对象 |
| GET /api/mcp/tokens → tokens[] | expiresAt | Unix **秒** | new Date(expiresAt*1000)；expired→已过期徽章 | |
| GET /api/audit → items[] | ts/user/action/kind/ns/name/summary | resource 列不存在 | 前端拼接 [kind, ns/name] | 超过 500 条截断最旧 |
| GET /api/gitops/repo/status | 计数 map（非数组） | — | 五格计数观测带 | conflict>0 强制选边 |
| GET .../plan | create/update/delete/conflict/unchanged 数组 | — | 条目 path/kind/ns/name/key | BE 无内容字段，diff 显示路径+动作 |
| GET .../file | {path,content} | — | 渲染 .content | — |
| POST .../apply | deletes:key[], conflicts:{key:'repo' 或 'cluster'} | — | plan 无变更时禁用 APPLY | 未确认不写入（验收） |
| logs follow=true | SSE data:{pod,line} | — | 解析失败保留原样；3s×5 重连 | 无 functionName 标签 pod → 404 → 前端引导文案 |
| pod log 容器选择 | — | — | _pick_log_container 排除 *fetcher* | 多容器 Pod 必带 container 参数；勿发 Accept: text/plain（406） |
