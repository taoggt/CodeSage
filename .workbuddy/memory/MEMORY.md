# CodeSage 项目记忆

## 硬约束（不可违背）

- **不使用 LangChain / LangGraph / CrewAI 等 Agent 框架**（用户未学过，项目采用自研 ReAct 内核）
- 语言 Python、形态 CLI、周期一个月、定位 Agent 开发实习简历项目

## 关键技术决策

- 技术栈：openai SDK（OpenAI 兼容 → DeepSeek）+ Typer/Rich + Python 标准库 ast + Ruff/Bandit/radon + sqlite3 + pytest + uv
- 架构分层：确定性分析层（零 Token）与 LLM 语义层分工；自研循环位于 `src/codesage/agent/loop.py`
- 三大加分项：Harness 评测（检出率/误报率/F1/成本）、三区记忆压缩（60%/80% 双阈值）、自优化闭环（错题本 + prompt 版本 + A/B 回归）
- 规划文档：`docs/PROJECT_PLAN.md`（v1.0，2026-10-11）

## 项目状态（2026-10-11）

- **已完成完整搭建并通过验证**：62 个源文件（约 6253 行）、85 个测试全通过、端到端可运行
- 核心命令：`codesage review/eval/history/feedback`；`--no-llm` 模式零成本可演示
- 实测数据：确定性层 Recall 100%（5 案例种子集）；62 文件审查 2.1s（经 44 倍性能优化）
- 教学文档系列：`docs/《CodeSage项目》教学文档/`（v2，13 篇：00 导读 + 01–08 主线"八站"（顺 pipeline）+ 09–11 支线（评测/自优化/工程）+ 12 收尾附录；含 28 个面向 Java 转 Python 新手的语法加餐与总索引；「诚实清单」汇总未接线项；跨篇引用格式为「第 XX 篇」）
- 待办：配置 API Key 跑 LLM 完整链路；扩充评测数据集（Week 4 任务，见 `examples/eval_dataset/README.md`）

## 用户背景

- 计算机专业大三（二本），目标 Agent 开发或 Java 后端实习
- 已有 Ragent 项目（企业级 RAG 智能体平台，Java 技术栈），与 CodeSage 形成「平台 + 应用」互补叙事
- 学习偏好：需要具体选型理由、对比、可执行路径，不接受泛泛而谈
