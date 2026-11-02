# CodeSage

> AI 代码审查 Agent（CLI + 本地浏览器 GUI）：确定性静态分析（零 Token）+ 自研 ReAct 内核（语义推理）

一条命令完成代码质量分析、潜在 Bug 检测、性能建议、安全扫描与最佳实践推荐。

```bash
codesage review ./src          # 审查目录
codesage review app.py --no-llm  # 只跑确定性分析（零 API 成本）
codesage eval --mock           # 运行评测（检出率 / 误报率 / 成本）
codesage ui                    # 打开浏览器 GUI（审查过程可视化 + 错题本一键闭环）
```

## 核心设计哲学

**LLM 只在需要语义推理的环节介入，高确定性任务交给工程代码（零 Token）。**

```
                     ┌───────────────── 一次审查 ─────────────────┐
  扫描/过滤/切块      │  确定性分析（零 Token）     Agent 内核（LLM）  │
  ────────────────►  │  AST 规则 · Ruff          复核候选发现        │   ──►  报告
  （零 Token）        │  Bandit · radon          补充独立发现         │       评分/分级
                     │  ↓ 候选发现              生成修复建议         │       导出
                     └─────────────────────────────────────────────┘
```

三级分工：

| 通道    | 任务                               | 成本      |
| ----- | -------------------------------- | ------- |
| 确定性层  | 文件筛选、AST 规则、Ruff/Bandit/radon 扫描 | 零 Token |
| LLM 层 | 候选复核（误报过滤）、逻辑/边界问题发现、修复建议        | 按量计费    |
| 评测层   | Harness 驱动真实链路，量化质量与成本           | 评测时计费   |

## 特性

- **自研 Agent 内核（零框架依赖）**：约 150 行的 ReAct 循环，`function calling` 协议驱动——模型每一轮决策、工具执行、错误回填与停止条件都清晰可调试
- **13 条自研 AST 规则 + 3 个外部分析器**：覆盖质量 / Bug / 性能 / 安全 / 最佳实践五个维度，确定性发现零成本
- **三区记忆压缩**：冻结区 / 压缩区 / 活跃区，60%/80% 双阈值触发——长审查不爆上下文，token 成本线性可控
- **Harness 评测框架**：真实案例集 + 软匹配指标（检出率 / 误报率 / F1 / 成本）+ A/B 回归对比
- **自优化闭环**：`feedback` 反馈 → 错题本沉淀 → Prompt 注入 → 评测验证
- **本地浏览器 GUI**：审查过程可视化（阶段时间线 / 工具流水 / Token 仪表）、问题与源码行联动、错题本「注入预览」——只监听 127.0.0.1，`pip install -e ".[web]"` 可选安装
- **CI 友好**：`--fail-on` 退出码、`--format json` 导出、`--changed` 增量审查

## 快速开始

```bash
# 1. 安装（Python >= 3.10）
python -m venv .venv
.venv/Scripts/activate        # Windows（Linux/macOS: source .venv/bin/activate）
pip install -e ".[dev]"

# 2. 配置 LLM（可选——不配置可运行 --no-llm 模式）
cp .env.example .env          # 填入 DeepSeek / OpenAI 兼容服务的 API Key

# 3. 审查代码
codesage review ./your_project

# 4. 评测（验证效果，量化数据）
codesage eval --mock --tag baseline   # 无需 API Key

# 5. 浏览器 GUI（可选）
pip install -e ".[web]"
python scripts/build_ui.py            # 构建前端（需 Node 18+，产物已随 wheel 打包时可跳过）
codesage ui                           # 自动打开 http://127.0.0.1:8787
```

配置优先级：环境变量 > `codesage.toml` > 默认值（模板见 `codesage.toml.example`）。

> **升级 / 切换 Python 版本后，`codesage` 报 `cannot import name ... (unknown location)`？**
> 虚拟环境不能跨小版本复用：`pydantic_core` 这类包带的是 `cp313` / `cp314` 专用 `.pyd`，
> 只改 `pyvenv.cfg` 不会重装 site-packages。正确做法是先停掉正在运行的服务
> （否则 `python.exe` 被占用，`venv` 会报 `Unable to copy venvlauncher.exe`），
> 删掉 `.venv` 再用当前 Python 重建：
>
> ```bash
> python -m venv .venv
> .venv/Scripts/python.exe -m pip install -e ".[dev,web]"
> ```

## 命令说明

### `codesage review <path>`

| 选项               | 说明                                       |
| ---------------- | ---------------------------------------- |
| `--no-llm`       | 只跑确定性分析（零 Token，无需 API Key）              |
| `--changed`      | 只审查 git 变更涉及的文件（增量模式）                    |
| `--profile`      | 审查角色：`full` / `security` / `performance` |
| `--format`       | 输出格式：`terminal` / `markdown` / `json`    |
| `--output`       | 导出到文件（配合 `--format`）                     |
| `--min-severity` | 终端展示的最低严重级                               |
| `--fail-on`      | CI 退出码阈值（`critical`/`major`/`minor`）     |

### `codesage eval`

```bash
codesage eval --mock --tag det-baseline      # 确定性层基线
codesage eval --tag v1-llm                   # 完整链路评测
codesage eval --compare det-baseline,v1-llm  # A/B 对比
codesage eval --sample 3 --tag quick         # 小样本快速迭代
```

### `codesage feedback`

```bash
codesage feedback <finding_id> -v wrong -n "这个场景下是刻意的"  # 标记误报
codesage feedback --mine    # 提炼错题本（下次审查自动注入经验）
codesage feedback --show    # 查看错题本
```

### `codesage history`

```bash
codesage history                 # 最近的审查记录（全局存储，默认展示全部项目）
codesage history --detail 25     # 查看某次审查的完整发现（重新渲染完整报告）
codesage history --project .     # 只看当前项目的记录
codesage history --feedback      # 附带反馈统计
```

审查记录存储在 `~/.codesage/store.db`（用户级，不写入被审查目录）；错题本经验按项目隔离。

### `codesage ui`（浏览器 GUI）

| 选项 | 说明 |
| --- | --- |
| `--path, -p` | 启动时打开的工作区目录（默认当前目录） |
| `--port` | 监听端口，默认 8787 |
| `--host` | 监听地址，**只允许回环地址**（127.0.0.1 / localhost） |
| `--no-browser` | 不自动打开浏览器 |
| `--dev` | 开发模式：不托管前端产物，配合 `cd web && npm run dev` |
| `--config` | 指定 `codesage.toml`（默认按当前目录探测） |

六个页面：**工作台**（文件树 / 代码行级标注 / 实时审查进度）、**报告**（评分 + P0–P3 分组 + 一键反馈）、**历史**（分页 / 评分趋势 / 两次对比）、**错题本**（经验管理 + 注入预览 + 手动补漏报）、**评测**（mock 运行 / 指标 / A-B）、**设置**（自检与维护）。

选择工作区有三种方式（不需要手抄路径）：**系统文件夹对话框**（推荐，直接弹本机选择框）、**网页内目录浏览**（逐层点选，含盘符与常用位置）、**最近打开**；网络盘等特殊场景仍可手动输入。

**已知限制**（详见 `docs/GUI.md`）：取消是协作式的（在途 LLM 请求需先返回，≤120s）· 必须单进程单 worker · 仅回环地址、无登录 · 评测只有 5 例种子集，"误报率"用 1 − Precision 近似。

## 项目结构

```
src/codesage/
├── core/          # 领域模型（Finding/Report）、配置、异常、取消令牌
├── ingest/        # 感知层：扫描 / 过滤 / 切块 / git diff 解析
├── analyzers/     # 确定性分析层：AST 规则引擎 + Ruff/Bandit/radon
├── agent/         # Agent 内核：ReAct 循环 / 工具注册表 / Prompt / 停止策略
├── tools/         # 工具集：read_file / search_code / run_analyzer / submit_finding ...
├── knowledge/     # 最佳实践知识库（卡片 + 关键词检索）
├── memory/        # 三区压缩记忆 / SQLite 持久化（WAL）/ 项目画像
├── llm/           # LLM 接入：客户端 / 模型路由 / Token 预算 / 估算器
├── optim/         # 自优化：反馈 / 错题本 / Prompt 版本管理
├── harness/       # 评测框架：数据集 / 指标 / 运行器 / A-B 对比
├── services/      # 业务层：CLI 与 GUI 共用（工作区/审查/历史/反馈/错题本/评测）
├── server/        # HTTP 层：FastAPI 路由 + 任务队列 + SSE + 静态托管
├── cli/           # Typer 命令 + Rich 渲染 + 导出
└── pipeline.py    # 编排管线（扫描 → 分析 → Agent → 聚合 → 报告）

web/               # 前端工程（React + TS + Vite + CodeMirror 6）
```

## 评测结果

运行 `codesage eval --mock`（确定性层 · 5 个种子案例 · 零 API 成本）实测：

| 配置         | 检出率                                            | 精确率* | F1        | 平均成本/案例 |
| ---------- | ---------------------------------------------- | ---- | --------- | ------- |
| 确定性层（mock） | **100%** (5/5)                                 | 75%  | **0.857** | ¥0      |
| 完整链路（LLM）  | 待填（需 API Key 后运行 `codesage eval --tag v1-llm`） |      |           |         |

\* 精确率为近似指标：案例工作区隔离后，同一片段中的其他真实问题也会计入分母（详见 `harness/metrics.py` 的诚实标注）。

数据集：`examples/eval_dataset/`（种子 5 案例；扩充方法见该目录 README）

## 性能实践（自举审查发现的优化）

用 CodeSage 审查自己的源码（62 个文件）时，全量审查耗时 **92.7s**。  
逐层 cProfile 定位并优化：

| 阶段        | 优化前       | 优化后        | 手段                                                                              |
| --------- | --------- | ---------- | ------------------------------------------------------------------------------- |
| Bandit 扫描 | 90s+      | ~8s → 1.5s | 逐文件子进程调用（每次 ~1.5s 启动成本）→ 单次批量扫描目录                                               |
| Ruff 扫描   | ~7s       | ~0.2s      | 同上 + 直接扫描真实目录（不再复制源码）                                                           |
| 临时目录写删    | ~7s       | 0s         | cProfile 显示 `nt.remove`/`nt.rmdir` 删除 62 个文件占 6.7s（Windows 文件系统挂钩开销）→ 改为零临时文件设计 |
| **总耗时**   | **92.7s** | **2.1s**   | **约 44 倍提速**                                                                    |

这次优化本身也是评测驱动开发的例证：先有量化基线（Harness 记录每次耗时），再做剖析，最后用数据验证收益。

## 关键设计决策

| 决策       | 选择                  | 放弃                    | 理由                                         |
| -------- | ------------------- | --------------------- | ------------------------------------------ |
| Agent 框架 | 自研 ReAct 内核         | LangChain / LangGraph | 压缩时机与预算需精确控制；零学习成本；面试能讲原理而非报框架名            |
| 语言支持     | 仅 Python（v1）        | tree-sitter 多语言       | 一个月周期做深优先于做广                               |
| 知识检索     | 关键词打分卡片             | 向量检索                  | 卡片量小，检索收益边际低；接口已预留演进空间                     |
| Token 计算 | 启发式估算（默认）           | 仅 tiktoken            | 零联网零依赖（tiktoken 首次运行需下载编码文件）；±20% 精度满足预算决策 |
| 报告形态     | CLI 为主 + 本地 GUI 为辅    | 公网 Web 服务             | GUI 用来展示 ReAct 过程与错题本闭环（CLI 做不到）；只监听回环，不做多用户/鉴权 |
| GUI 技术栈  | FastAPI + React/Vite（无 UI 库） | Streamlit / Gradio     | 需要行级代码标注与实时事件流；CodeMirror 只用核心 API，首屏 JS ≈194KB gzip |
| 长任务模型    | 进程内线程池 + SSE          | Celery / Redis 队列      | 单机单人场景，外部队列是过度设计；代价是必须单进程单 worker |

## 开发

```bash
.venv/Scripts/python.exe -m pytest                 # 运行测试（156 例）
.venv/Scripts/python.exe -m ruff check src tests    # 代码检查

# 前端开发态（Vite HMR，代理 /api 到 8787）
codesage ui --dev                                   # 终端 A：只提供 API
cd web && npm install && npm run dev                 # 终端 B：Vite 5173

python scripts/build_ui.py                          # 构建产物到 src/codesage/server/static/
```

GUI 的架构、API 契约与开发约定见 `docs/GUI.md`。

## Roadmap（v2）

- [x] 本地浏览器 GUI（审查过程可视化 / 错题本闭环 / 评测面板）
- [ ] tree-sitter 多语言支持（analyzer 协议已语言无关）
- [ ] 向量检索知识库（`knowledge.query` 接口不变）
- [ ] MCP Server 化（把 tools/ 注册表暴露为 MCP 协议）
- [ ] GitHub Action / PR Bot 集成
- [ ] 参数自适应（压缩阈值、模型路由的自动调优）
