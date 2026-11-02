# CodeSage GUI 开发文档

> 面向二次开发与面试讲解。目标：说清「GUI 怎么接在既有内核上」「为什么这么分层」「哪些地方是妥协」。

## 1. 定位与边界

| 项        | 结论                                                            |
| -------- | ------------------------------------------------------------- |
| 形态       | 本地工具：`codesage ui` 启动 uvicorn，浏览器打开 `http://127.0.0.1:8787`   |
| 用户       | 单人单机；无登录、无多租户、不监听 0.0.0.0                                     |
| 与 CLI 关系 | **平级消费者**：CLI / Harness / GUI 都调 `run_review()`，不是 GUI 包一层命令行 |
| 不做什么     | 公网部署、团队协作、在线编辑代码、PR 评论机器人                                     |
| 依赖       | `web` 可选依赖（fastapi + uvicorn）；不装也能用全部 CLI                     |

## 2. 快速开始

```bash
pip install -e ".[web,dev]"
python scripts/build_ui.py     # 需 Node 18+；产物写入 src/codesage/server/static/
codesage ui --path D:/myproject
```

开发态双进程（HMR）：

```bash
codesage ui --dev              # 终端 A：只提供 /api
cd web && npm install && npm run dev   # 终端 B：Vite 5173，代理 /api → 8787
```

> 前端产物不入库（见 `.gitignore`），但会被 hatch 的 `force-include` 打进 wheel。  
> 产物缺失时 `/` 返回 503 构建指引，`/api/*` 仍可用。

## 3. 分层

```
React (web/)
   │  HTTP / SSE
   ▼
server/routers/*        协议转换、参数校验、状态码
   │
   ▼
services/*              业务编排（CLI 与 GUI 共用，禁止 import FastAPI）
   │
   ▼
pipeline / memory / optim / harness     既有内核（不知道 HTTP 存在）
```

纪律：

| 层           | 允许                       | 禁止                                           |
| ----------- | ------------------------ | -------------------------------------------- |
| `routers/`  | HTTP 语义、调 service        | `sqlite3`、直接调 `run_review`、路径业务判断            |
| `services/` | 业务编排                     | `Request` / `HTTPException` / `JSONResponse` |
| 内核          | 只认 `CancelToken` 这个零依赖对象 | 感知 Web                                       |

### 目录

```
src/codesage/
├── core/cancel.py                # 协作式取消令牌
├── services/
│   ├── store_factory.py          # 每请求/每任务新建 Store
│   ├── workspace_service.py      # 目录登记、文件树、读文件、扫描预检、审查状态
│   ├── review_service.py         # ReviewRequest → run_review
│   ├── history_service.py        # 分页、详情、两次对比、清理
│   ├── feedback_service.py       # 反馈 + 立即提炼经验
│   ├── lesson_service.py         # 错题本 CRUD + 注入预览
│   ├── eval_service.py           # 数据集发现、结果读取、A/B
│   └── export_service.py         # md/json（复用 cli/exporter）
├── server/
│   ├── app.py                    # create_app：路由 → /assets → SPA 回落
│   ├── serve.py                   # uvicorn workers=1 + 回环校验 + 开浏览器
│   ├── deps.py                   # settings / registry / jobs / store 依赖
│   ├── jobs.py                   # JobManager / Job / 事件环形缓冲
│   ├── sse.py                    # SSE 帧、心跳、Last-Event-ID 重放
│   ├── security.py               # Host/Origin 守卫、回环校验
│   ├── errors.py                 # 异常 → {code,message,detail}
│   ├── schemas.py                # 请求模型
│   └── routers/{system,workspace,jobs,reports,feedback,lessons,evals}.py
└── cli/commands/ui_cmd.py        # 懒加载 web 依赖
```

## 4. 一次审查的请求流

```
React 表单 ──POST /api/jobs──► jobs router（校验 target 在已登记 root 内）
                                   │
                             JobManager.submit() → ThreadPoolExecutor
                                   │
        services.review_service.run → pipeline.run_review(on_event=job.publish)
                                   │
        scan / phase / progress / static_done / start / context /
        tool_calls / tool_result / assistant_message / compress /
        finish / cancelled / end
                                   │
                    Job.events(deque) → 每个订阅者 asyncio.Queue
                                   │
React ◄──────text/event-stream──── sse.py（快照 + 重放 + 心跳）
   └─ 终态后 GET /api/reviews/{id} 取完整报告（SSE 不传 78KB payload）
```

## 5. 并发模型

### 5.1 任务

| 项    | 设计                                                                     |
| ---- | ---------------------------------------------------------------------- |
| 线程池  | review×2、eval×1（评测内部串行，且会写盘）                                           |
| 并发闸门 | 同类运行中任务达上限 → `429 {"code":"busy","retry_after":5}`，**不排队**（排队会让用户以为卡死） |
| 事件缓冲 | `deque(maxlen=2000)` + `dropped` 计数；序号单调递增，直接作 SSE id                  |
| 扇出   | 线程 → 事件循环用 `loop.call_soon_threadsafe`；慢客户端丢最旧，不反压任务线程                 |
| 淘汰   | 终态任务保留 2 小时或最近 50 个                                                    |

### 5.2 SSE

- 建连立刻发 `snapshot`（无 id，不干扰 `Last-Event-ID`）
- 带 `Last-Event-ID` 时只回放更新的历史；已被挤出缓冲 → `truncated`，前端改拉快照
- 15s 无事件发 `: ping`（防代理掐空闲连接）
- 收到 `end` 主动结束，**前端必须 close**（否则 EventSource 自动重连成死循环）

### 5.3 取消（协作式）

| 阶段      | 检查点                           | 结果                           | 落盘             |
| ------- | ----------------------------- | ---------------------------- | -------------- |
| 扫描 / 静态 | 每文件、每分析器调用前                   | 返回空报告 + `metadata.cancelled` | 否              |
| LLM 循环  | 每轮顶部、`chat()` 前、`execute()` 前 | 保留已有发现，正常合并且评分               | 是（标 `partial`） |
| LLM 在途  | 无检查点                          | job 置 `cancelling`，返回后立刻终态   | 按上             |

**不能真正打断在途 HTTP 请求**（同步 SDK，最长 `llm.timeout` 120s）。这是语义妥协，UI 上明确写了。

### 5.4 SQLite

```python
sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
PRAGMA busy_timeout=5000 / synchronous=NORMAL / temp_store=MEMORY
PRAGMA journal_mode=WAL   # 只在建库时设一次（数据库级持久属性）
```

- 连接**绝不跨线程共享**：每请求一个（`yield` 依赖）、每任务一个
- 写操作用 `BEGIN IMMEDIATE`，锁冲突退避 50/150/400ms 重试 3 次 → 仍失败抛 `StoreBusyError`（503）
- schema 用 `PRAGMA user_version` 做一次性初始化，避免并发首启抢 DDL

## 6. API

统一错误体 `{code,message,detail}`；统一分页 `{total,limit,offset,items}`。

| 方法       | 路径                                              | 说明                           |
| -------- | ----------------------------------------------- | ---------------------------- |
| GET      | `/api/health`                                   | 版本、存储、Key、分析器可用性             |
| GET      | `/api/meta`                                     | profiles / 严重级 / 类别 / 来源     |
| GET      | `/api/config`                                   | 白名单序列化，**永不含 api_key**       |
| GET      | `/api/stats`                                    | 记录 / 反馈 / 经验 / 待提炼           |
| GET      | `/api/workspace/roots`                          | 已登记目录                        |
| DELETE   | `/api/workspace/roots`                          | 移除一条"最近打开"记录（不动磁盘）           |
| GET      | `/api/workspace/browse`                         | 目录浏览（只列目录，含盘符/快捷位置）          |
| POST     | `/api/workspace/pick`                           | 弹本机系统文件夹对话框（不支持时 501）        |
| POST     | `/api/workspace/open`                           | 登记目录并返回概览（文件数、git、分析器）       |
| GET      | `/api/workspace/tree`                           | 惰性一层文件树                      |
| GET      | `/api/workspace/file`                           | 源码窗口（默认 2000 行）              |
| GET      | `/api/workspace/status`                         | 上次审查 + 文件覆盖 + 是否过期 + 子目录已有记录 |
| GET      | `/api/workspace/scan-preview`                   | 零 Token 预检（纳入 / 跳过及原因）       |
| POST     | `/api/jobs`                                     | 创建审查任务（202 / 429）            |
| GET      | `/api/jobs` `/api/jobs/{id}`                    | 任务列表 / 快照                    |
| GET      | `/api/jobs/{id}/events`                         | SSE                          |
| POST     | `/api/jobs/{id}/cancel`                         | 取消（已终态 → 409）                |
| GET      | `/api/reviews`                                  | 分页（project / q）              |
| GET      | `/api/reviews/compare`                          | 两次对比                         |
| GET      | `/api/reviews/{id}`                             | 完整报告 + 每条 finding 的反馈        |
| GET      | `/api/reviews/{id}/export`                      | md / json 下载                 |
| DELETE   | `/api/reviews/{id}`                             | 删除记录                         |
| POST     | `/api/reviews/purge`                            | 清理评测遗留记录                     |
| POST     | `/api/feedback`                                 | 提交反馈（默认自动提炼经验）               |
| GET      | `/api/feedback` `/stats`                        | 流水 / 统计                      |
| GET/POST | `/api/lessons`                                  | 列表 / 新增                      |
| GET      | `/api/lessons/preview`                          | **真实注入文本** + 字符上限            |
| POST     | `/api/lessons/missed`                           | 手动补漏报                        |
| POST     | `/api/lessons/mine`                             | 批量提炼未处理的反馈（幂等，不复活已删经验） |
| POST     | `/api/lessons/reset-mined`                      | 清除"已提炼"标记，允许历史反馈重新提炼 |
| DELETE   | `/api/lessons/{id}`                             | 删除                           |
| GET      | `/api/eval/datasets`                            | 数据集发现                        |
| POST     | `/api/eval`                                     | 启动评测（202，走同一 SSE）            |
| GET      | `/api/eval/results` `/results/{tag}` `/compare` | 结果 / 详情 / A-B                |

接口文档：`http://127.0.0.1:8787/api/docs`。

## 7. 前端约定

| 项      | 约定                                                                                           |
| ------ | -------------------------------------------------------------------------------------------- |
| 路由     | `/` 工作台、`/report/:id`、`/history`、`/lessons`、`/evals`、`/settings`                             |
| 服务端状态  | React Query（key 如 `['reviews',{limit,offset}]`；写后 invalidate）                                |
| 客户端状态  | zustand：workspace（localStorage 持久化 root）、job（事件流，上限 400 条）                                   |
| 当前 job | `sessionStorage`，刷新可恢复                                                                       |
| 代码视图   | CodeMirror 6 核心 API：`lineNumbers` + `lang-python` + 自定义 `StateField` 行装饰 + 自定义 gutter marker |
| 语法高亮   | 只染**文字**，且避开审查标记的红/橙/琥珀/蓝：关键字紫、字符串青绿、数字棕、注释灰斜体。审查标记只染**背景**，两套色系互不干扰                         |
| 懒加载    | 报告/历史/错题本/评测/设置 全部 `React.lazy`                                                              |
| 分包     | vendor-react / vendor-query / vendor-editor / vendor-charts                                  |
| 包体预算   | 首屏 gzip < 350KB（实测 ≈194KB，超出应让构建失败）                                                          |
| 样式     | Tailwind（关闭 preflight 冲突风险由组件自行控制），不引入 UI 组件库                                                |

构建产物结构：`static/index.html` + `static/assets/*`（带 hash，immutable 缓存）。

### 为什么工作区选择要由后端弹对话框

浏览器出于安全限制，**拿不到用户选中目录的绝对路径**：

| 浏览器能力                                       | 能拿到               | 为什么不够               |
| ------------------------------------------- | ----------------- | ------------------- |
| `showDirectoryPicker()`（File System Access） | 目录句柄              | 只有句柄，没有路径，后端无法据此读源码 |
| 拖拽文件夹                                       | `FileSystemEntry` | 同上，只有名字与文件内容        |
| `<input webkitdirectory>`                   | 相对路径 + 全部文件       | 需要把整个项目上传，且仍无绝对路径   |

所以只能由**本机后端**调用系统对话框（`server/dialogs.py`，tkinter `askdirectory`）：

- 路由声明为 `def`，FastAPI 会丢到线程池，不阻塞事件循环；
- Windows/Linux 可用；macOS 的 Tk 必须在主线程，直接返回 501，前端自动回退到「目录浏览」；
- 全局串行锁，避免连点弹出多个模态；
- 请求会一直挂到用户选完或取消，前端在此期间显示「系统对话框已在你桌面上打开」。

兜底的「目录浏览」（`GET /api/workspace/browse`）只列目录、不读文件，顺带解决网络盘、超长路径的场景。

## 8. 对内核的改动（CLI 行为不变）

| 文件                  | 改动                                                                                                                  | 兼容性                          |
| ------------------- | ------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| `core/cancel.py`    | 新增 `CancelToken`                                                                                                    | 纯新增                          |
| `memory/store.py`   | WAL/索引/分页/事务重试/删除/清理；新列 `by_severity`/`llm_enabled`/`partial`                                                       | 旧库自动迁移；`recent_reviews` 签名不变 |
| `pipeline.py`       | `cancel_token`/`persist`/`persist_on_cancel`；新增 `phase`/`progress`/`cancelled`/`persist_failed` 事件；`metadata.files` | 全默认值；CLI 无需改动                |
| `agent/loop.py`     | `cancel_token`；`assistant_message` 事件；`tool_calls` 带参数摘要；钩子异常记日志                                                    | 关键字参数默认 None                 |
| `harness/runner.py` | 评测案例 `persist=False`                                                                                                | 评测结果不变，只是不写历史表               |
| `core/config.py`    | `ServerSettings`；`load_settings(config_file=...)`                                                                   | 默认行为不变                       |

## 9. 测试

| 文件                          | 覆盖                                                           |
| --------------------------- | ------------------------------------------------------------ |
| `tests/conftest.py`         | 临时存储 / 临时工作区 / TestClient（**测试不碰真实 `~/.codesage/store.db`**） |
| `test_cancel.py`            | 令牌语义、阶段取消不落盘、循环取消、事件载荷                                       |
| `test_store_concurrency.py` | WAL、8 线程并发写、分页、清理、经验删除                                       |
| `test_server_api.py`        | 路径穿越/.env/二进制拦截、文件窗口、报告详情、导出逐字一致、反馈→经验、错题本、统计                |
| `test_server_jobs.py`       | 任务生命周期、SSE 回放去重、取消 409、并发 429                                |
| `test_server_eval.py`       | mock 评测、`persist=False` 生效、A/B                               |
| `test_cli_ui.py`            | `ui --help`、回环校验、Host/Origin 守卫                              |

共 156 例（原 119 + 新增 37）。

手工验收：中文路径全流程、审查中刷新页面恢复进度、休眠后 SSE 重连不重复、取消后标 `partial`、删掉 dist 后服务仍能起。

## 10. 已知限制与后续

| 限制                 | 原因                 | 后续                                                  |
| ------------------ | ------------------ | --------------------------------------------------- |
| 取消要等在途 LLM 返回      | 同步 SDK，无 stream 中断 | 改 `stream=True` + 主动关闭连接                            |
| 必须单进程              | Job/事件/线程池在进程内存    | 引入外部队列与状态存储                                         |
| 无登录                | 定位本地单人工具           | 需要时加一次性 token                                       |
| 评测只有 5 例           | 数据集未扩充             | 按 `examples/eval_dataset/README.md` 挖 bugfix commit |
| "误报率"= 1−Precision | 软匹配口径的近似           | 引入人工标注的负样本                                          |
| 事件不含完整工具输出         | payload 体积控制       | 前端按需拉取单条工具结果                                        |
| 无暗色主题 / 快捷键        | 优先级                | 视使用频率                                               |

## 11. 排错

| 现象                                                                | 处理                                                         |
| ----------------------------------------------------------------- | ---------------------------------------------------------- |
| `codesage` 报 `cannot import name 'ArgsKwargs' (unknown location)` | 虚拟环境被换过 Python 版本：删 `.venv` 用当前 Python 重建（先停服务，否则 exe 被占用） |
| 打开 `http://127.0.0.1:8787` 显示构建提示                                 | `python scripts/build_ui.py`                               |
| `codesage ui` 报缺少 web 依赖                                          | `pip install -e ".[web]"`                                  |
| 403 `host_not_allowed`                                            | 不要用非回环地址访问（如局域网 IP）                                        |
| 403 `path_outside_root`                                           | 先在界面「打开目录」登记工作区                                            |
| 429 `busy`                                                        | 等当前审查结束（并发上限 `server.max_review_jobs`）                     |
| 503 `store_busy`                                                  | 写冲突重试仍失败，稍后再试；检查磁盘权限                                       |
| 进度条不动                                                             | 看 `/api/jobs/{id}` 快照；确认没开多 worker（必须单进程）                  |
