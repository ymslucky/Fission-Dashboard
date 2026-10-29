# FISSION CONSOLE · 观测站 — DESIGN

> 本文档完全取代旧版 "Airport wayfinding" 设计记录，按 token 承载方式归纳自构建成品（`web/src/styles/tokens.css`、`web/src/styles/base.css`）与 finish review 截图（`.impeccable/review/`）。

## World

**整座平台是一座持续记录的观测站。** 冷纸白（`--paper: #f4f6f5`）作地，墨青（`--ink: #1c2b33`）作字与顶条；数据、选中与函数用观测蓝（`--obs: #2b6a7c`），朱砂红（`--alarm: #c0392b`）只留给异常警示。函数调用率是观测曲线，异常是插旗的观测记录——拒绝品类默认的"灰底仪表盘 + 圆角卡片"排布。诚实、精密、克制：**禁用阴影 / 渐变 / 玻璃**，一切分隔靠发丝线（`--hairline` 系）与下沉面（`--paper-sink`）。

## Tokens

全部定义在 `web/src/styles/tokens.css` 的 `:root`，组件层只消费 `var(--*)`，不另造色值。

### 纸与墨（paper & ink）
- `--paper: #f4f6f5` — 全局地色（`body`、粘性表头 `th`、`.drawer`、`.modal`）
- `--paper-raise: #fbfcfc` — 面板浮起一档（`.obs-card`、`.count-band`）
- `--paper-sink: #eef1f0` — 下沉面（`.sidenav`）
- `--ink: #1c2b33` — 墨青正文/顶条底（`.topbar`、`.toast`、overlay 遮罩 `rgba(28,43,51,.42)`）
- `--ink-soft: #3c4f58` — 次级墨色（`.sidenav a`）

### 信号色（signal palette）
- `--obs: #2b6a7c` — 观测蓝：选中（`.sidenav a.on`、`.tabs button.on`）、主按钮（`.btn.pri`）、文字选区 `::selection`
- `--obs-soft: #e2ebec` / `--obs-faint: #eaf1f2` — 观测蓝淡底：选中行 `tr.row-sel`、hover `tr:hover`、`.status-ok`
- `--alarm: #c0392b` — 朱砂红**仅异常/警示**：异常行 `tr.row-alert` 左旗（`inset 3px 0 0`）、`.status-error`、`.btn.danger`、`.toast.error`
- `--alarm-soft: #f9ecea` — 异常行淡朱底（`tr.row-alert`）
- `--alarm-text: #f2c4bd` — 深底上的异常文字（`.chip.alert`、`.toast.error .t-kind`）
- `--gray-teal: #7c8f96` — 次级信息通色：`.muted`、`.fn-sub`、表头 `th`、`.meta`、滚动条

### 资源类别色相（resource kind hues，手册篇色纪律并入）
- `--kind-fn: #2b6a7c` — 函数 = 观测蓝
- `--kind-pkg: #1f6f54` — 包 = 草绿
- `--kind-env: #2e6e8e` — 环境 = 青
- `--kind-http: #3d4fa1` — HTTP 触发器 = 群青
- `--kind-timer: #7b4b94` — 定时触发器 = 绛紫
- `--kind-canary: #b97a1f` — 金丝雀 = 琥珀

由 8px 方点 `.kind-dot`（`border-radius: 2px`）承载，总览计数带（`.count-cell .l`）同色复现——overview.png 中五个量级数字旁即此色点。

### 状态（status：冗余编码 = 颜色 + 形状 + 文本）
- `--ok: #1f6f54`、`--warn: #b97a1f`、`--warn-soft: #e8e3d5`、`--warn-text: #8a7b3a`、`--warn-line: #d8cf9e`
- `.status` 徽标：`.sig-dot`（圆点）/ `.sig-square`（方点）/ `.sig-tri`（三角，`border-bottom: 8px solid var(--warn)`）三种信号形状配四档底色：`.status-ok`、`.status-error`（朱底白字）、`.status-pending`（warn 淡底+描边）、`.status-neutral`（灰青描边）。overview.png 中 READY 与 DEGRADED 即此。

### 线（lines）
`--hairline: #dfe5e3`（`.hairline` 分隔线）、`--hairline-strong: #dde4e3`（面板边、表头底边）、`--row-line: #e8edec`（表格行线）。全站仅发丝线，无边框阴影。

### 度量（metrics）与焦点
`--topbar-h: 56px` · `--nav-w: 200px` · `--list-w: 380px` · `--row-h: 36px` · `--focus-ring: 0 0 0 3px rgba(43,106,124,.55)`（`:focus-visible` 统一消费，输入聚焦另加 `border-color: var(--obs)`）。

## Type

三字体轨道，token 化于 `--font-display` / `--font-body` / `--font-mono`：

- **窄体 grotesk 标题** `--font-display: 'Archivo Narrow', 'Noto Sans SC'`：品牌 `.brand`（20px / 2px 字距）、页头 `.pane-h`（15px）、检查器标题 `.ins-h h2`（19px）、卡片小标 `.obs-card h3`、量级大数 `.count-cell .n`（30px 窄体数字）、导航英文小字 `.sidenav .en`（9px / 1.5px 字距）。工具类 `.display` 统一 +0.5px 字距。
- **正文** `--font-body: 'Noto Sans SC'`：全局 13px / 1.5 行高（`body`）。
- **等宽数值** `--font-mono: 'IBM Plex Mono'`：工具类 `.num` / `.mono`（`font-variant-numeric: tabular-nums`），状态徽标 `.status`、顶条 chips `.chip`、说明行 `.cap`、toast 来源 `.t-kind`。所有可比较数字一律等宽表格数字。

## Composition

三段 shell（`.app` 纵向 flex → `.shell` 横向 flex），尺寸全部来自度量 token：

1. **顶条 `.topbar`**（56px，`--ink` 墨青底、纸白字）：`.brand` 品牌带 `.brand small` 观测站副标，`.topbar-spacer` 推开右侧 `.chips` 状态 chips（等宽字体；异常 chip 走 `.chip.alert` 朱底变体）。截图中 "fission API 正常 / watch 实时 / 异常 0 / fn 3 · pkg 3 / anonymous · 管理员"。
2. **导航 `.sidenav`**（200px，`--paper-sink` 下沉面）：中文项 + 英文小字双行；当前项 `a.on` 观测蓝左旗（`border-left: 3px solid`）+ `--obs-soft` 底；hover `--obs-faint`；底部 `.btn` "调用测试台"。
3. **内容区**：
   - **列表页**（functions.png）：`.list-pane` 380px 观测表 + 自适应 `.inspector`；空态 `.empty` "选择一条观测记录"。
   - **总览**（overview.png）：`.count-band`/`.count-cell` 量级带（窄体大数 + 类别色点）、`.obs-card` 面板、`.banner.blue` 信息横幅。
   - **观测表 `.obs-table`**：粘性小写表头（10px 灰青），行高 `--row-h` 36px，行线 `--row-line` 紧排；`tr.row-alert` 淡朱底 + 左 3px 朱旗**异常置顶驻留**；`tr.row-sel` 观测蓝旗；宽表 `.table-wrap` 横向滚动，长名 `.fn-name` 240px 截断（functions.png / function-detail.png）。
   - **检查器**（function-detail.png）：`.ins-h` 标题 + READY 状态徽标 + 右侧 `.btn` 动作组（编辑/删除/发起调用，删除走 `.btn.danger`）；`.meta` 属性行；`.obs-card` DESCRIBE 面板；`.tabs` 页签（日志/调用测试/YAML/事件/触发器，当前项观测蓝 2px 底边）；日志区深墨底回显。
   - **覆层**：`.overlay`（`rgba(28,43,51,.42)` 遮罩）承载右滑 `.drawer`（520px）与居中 `.modal`；写操作经策略预检后回吐 `.toasts`（右下，墨底纸字，`.toast.error` 朱左旗）。

## Interaction

- **触达**：`:focus-visible` 全站统一 `--focus-ring`（观测蓝 3px 光圈），输入聚焦叠加 `border-color: var(--obs)`；`::selection` 观测蓝底纸白字。
- **悬停**：表格行 hover `--obs-faint`；`.sidenav a:hover` 同底；`.btn:hover` 描边钮 `--obs-faint`、主钮加深 `#236071`、危险钮 `--alarm-soft`；可排序表头 `th.sortable:hover` 变观测蓝；禁用 `.btn:disabled` 0.45 透明度。
- **选中即观测**：行选中 `tr.row-sel` 观测蓝左旗 + 淡蓝底；异常行被选中仍保持朱旗朱底（`tr.row-alert.row-sel`）——警示不被选中覆盖。
- **状态冗余编码**：颜色 + 信号形状（圆/方/三角）+ 文本三重表达，色弱可辨。
- **动效纪律**：唯一过渡是移动端侧栏 `transform .15s ease`；全站尊重 `prefers-reduced-motion`（`base.css` 末段将动画/过渡压到 0.01ms）。

## Browser surfaces

- **滚动条**（`base.css` "浏览器表面" 段）：`scrollbar-width: thin`、`scrollbar-color: #7c8f96 transparent`；WebKit 侧 10px 轨道透明 + `#7c8f96` 内缩圆角滑块（`border: 3px solid transparent; background-clip: content-box`）——灰青即 `--gray-teal` 的字面值。
- **响应式**：
  - `≤1100px`：观测表低密度列 `.col-cpu` / `.col-p99` 直接移除（不做横向挤压）。
  - `≤900px`（mobile-functions.png）：`.menu-btn` 汉堡钮显现；`.sidenav` 变 fixed 抽屉（默认 `translateX(-100%)`，`.open` 滑入，唯一 transition）；`.shell` 转纵向、`.list-pane` 全宽 `max-height: 45vh` 上表下检；顶条 `.chips` 隐藏。
- **字体回退**：display 回退 Noto Sans SC，mono 回退 `ui-monospace, Consolas`；`-webkit-font-smoothing: antialiased`。
- **渲染基线**：全局 `box-sizing: border-box` 归零边距；`html/body/#root` 撑满视口；`img/svg` 块级限宽。

## 截图对应（.impeccable/review/）

| 截图 | 验证点 |
|---|---|
| `overview.png` | 量级带窄体大数 + 类别色点、READY/DEGRADED 状态徽标、`.banner.blue`、sidenav 选中态 |
| `functions.png` | 380px 观测表、发丝线紧排、空态 `.empty`、`.pane-h` + 主按钮 |
| `function-detail.png` | 检查器标题/状态徽标/动作组、`.obs-card` DESCRIBE、`.tabs`、深墨日志区 |
| `mobile-functions.png` | ≤900px 汉堡钮、全宽列表面板、chips 隐藏 |
