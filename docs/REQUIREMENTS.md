# 灵枢 (LingShu) 生产级 AI 自动化编程系统 — 需求规格说明书

**版本：** v1.3
**日期：** 2026-05-09
**状态：** 终稿（全模块实现）

---

## 0. 改版历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1.0 | 2026-05-09 | 初始定稿 |
| v1.1 | 2026-05-09 | 新增智能调度、根因分析管道、反馈学习器、反向验证器 |
| v1.3 | 2026-05-09 | 新增 Trailmark/Codebadger/谛听三大内置插件、commands/prompts 目录、langgraph StateGraph 智能调度完全体、完整 CLI 命令体系 |

## 1. 背景与目标

### 1.1 项目背景

现有 `pure-ai-orchestrator`（Node.js 版本）已实现 35+ 功能模块、9 种 Agent 链协作模式、6 种执行模式、5 个模型提供方。在此基础上，用 **Python 3.12+** 完全重写，融合多语言混合架构（Node.js CLI/Web + Python 核心 + Go 可选高并发），打造生产级 AI 自动化编程工具。

### 1.2 产品定位

灵枢是一款 **生产级 AI 自动化编程系统**。核心工作理念是：

> **以 Agent 自身能力为底座**，通过**大模型做规划/分析/根因定位 + 小模型执行子任务**的双模型架构，自动完成需求分析、根因定位、任务拆解、代码实现、审查和补丁生成的全流程。

### 1.3 核心目标

- **任务高度自动化**：从需求输入到代码产出全自动
- **精准分工**：大模型规划/根因定位，小模型执行子任务
- **接近 100% 精度**：严格任务拆解 + 双层校验 + 检查点持久化 + 复核回退
- **多语言混合架构**：Node.js CLI/Web UI + Python 核心 + Go 可选高并发

### 1.4 目标用户

- 使用 AI 辅助编码的开发者
- 需要自动化代码审查、修改和重构的团队
- 在服务器环境（Windows/Linux/macOS）工作的远程开发人员

---

## 2. 核心架构（多语言混合）

### 2.1 总体架构

```
用户交互层（Node.js / TypeScript）
  CLI / Web UI          — 实时任务进度、Agent 决策链、模型选择
        | RPC/HTTP/WebSocket
核心 Agent 层（Python）
  Agent 调度 & 流水线   — 架构师 Agent → 根因分析师 → 编辑器 Agent → Reviewer
  模型路由 & Fallback   — 按 role/task_type 选择最佳模型，自动故障转移
  错误恢复 & Watchdog   — 8 种错误分类 + 自动重试/降级/回退
  任务持久化 & 检查点    — SQLite 存储，断点恢复
  LSP / 静态分析 / 记忆系统
        | RPC/HTTP/gRPC
高并发执行层（Go，可选）
  小模型 / 子任务执行   — 并发限制、资源调度
        | RPC/HTTP
外部工具适配层
  LSP / pylsp / Cursor / OpenCode / ECC — 统一 BaseTool 接口
```

### 2.2 层间通信

| 发送方 | 接收方 | 协议 | 端口 |
|--------|--------|------|------|
| Node.js CLI | Python 核心 | HTTP/WebSocket | 8000 |
| Python 核心 | Go 执行层 | HTTP | 9000 |
| Python 核心 | 外部工具 Adapter | HTTP/subprocess | 动态 |

### 2.3 模型分工

| 层级 | 角色 | 推荐模型 | 核心职责 |
|------|------|----------|----------|
| Elite | 根因分析师 | claude-sonnet-4 | 深度分析 bug 根因、复杂推理、任务拆解 |
| Strong | 架构师 / Reviewer | gpt-4.1 / deepseek-chat | 需求分析、任务分配、复核结果 |
| Light | 编辑器 | gpt-4o-mini / gemini-2.0-flash | 执行子任务、生成代码/修改、运行测试 |
| Local | 本地模型 | qwen2 via ollama | 低成本 fallback，处理轻量任务 |

### 2.4 6 级模型层级系统

| 层级 | 级别 | 分配角色 | 典型模型 |
|------|------|----------|----------|
| elite | 5 | advisor（顾问） | claude-sonnet-4 |
| strong | 4 | analyzer, root-cause, review, patch | gpt-4.1 / deepseek-chat |
| medium | 3 | 通用任务 | gpt-4o-mini |
| light | 2 | worker, editor | gpt-4o-mini / gemini-2.0-flash |
| local | 1 | 本地执行 | qwen2 via ollama |
| mock | 0 | 流程验证 | 固定回复 |

### 2.5 模型提供方

| 提供方 | 接入方式 | 注册模型 |
|--------|----------|----------|
| OpenAI | litellm | gpt-4.1, gpt-4.1-mini, gpt-4o, gpt-4o-mini, o3-mini |
| Anthropic | litellm | claude-sonnet-4, claude-3.5-sonnet, claude-3.5-haiku |
| Google | litellm | gemini-2.5-pro, gemini-2.0-flash/lite, gemini-1.5-pro/flash |
| Ollama | litellm（OpenAI 兼容） | 本地模型（qwen2, codellama, llama3 等） |
| DeepSeek | litellm（OpenAI 兼容） | deepseek-chat, deepseek-r1 |

全部自动检测可用模型，无需手动配置即可使用。

### 2.6 自动故障转移链

```
local (Ollama) → light (gpt-4o-mini) → medium → strong (gpt-4.1) → elite (claude-sonnet-4)
```

调用失败时自动升级到更强模型，可用 Token 自动轮询。

---

## 3. 精确确认 + 根因确认 + 子任务执行流水线

### 3.1 总体流程 DAG

```
                       ┌────────────────────────────┐
                       │   用户输入任务/BUG报告      │
                       └─────────────┬──────────────┘
                                     │
                                     ▼
                       ┌────────────────────────────┐
                       │ 阶段 0: 需求自我确认 (ConfirmDemand) │
                       │ - Strong/Elite 自问自答          │
                       │ - 生成需求摘要                   │
                       │ - 用户确认                      │
                       └─────────────┬──────────────┘
                                     │ 用户确认通过
                                     ▼
                       ┌────────────────────────────┐
                       │ 阶段 1: 根因分析 (RootCause)      │
                       │ - Elite 分析日志 + 源码 + 测试   │
                       │ - 自问自答循环直到 100%确认       │
                       │ - 生成证据链（日志片段/源码/测试） │
                       └─────────────┬──────────────┘
                                     │ 根因确认完成
                                     ▼
                       ┌────────────────────────────┐
                       │ 阶段 2: 任务拆解 (Decompose)      │
                       │ - Elite/Strong 拆解子任务        │
                       │ - 子任务唯一 ID + 依赖关系       │
                       │ - 写入 SQLite 检查点             │
                       └─────────────┬──────────────┘
                                     │
                                     ▼
                       ┌────────────────────────────┐
                       │ 阶段 3: 子任务执行 (Execute)      │
                       │ - Light Agent 执行颗粒化任务      │
                       │ - 不跨越、不自作主张             │
                       │ - 完成写入 SQLite 检查点         │
                       │ - 失败自动重试/升级模型          │
                       └─────────────┬──────────────┘
                                     │
                                     ▼
                       ┌────────────────────────────┐
                       │ 阶段 4: 全局复核 + Patch         │
                       │ - Strong Agent 校验 diff & 测试 │
                       │ - 生成最终补丁 & 质量评分        │
                       │ - 不达标自动回退重做             │
                       └─────────────┬──────────────┘
                                     │
                                     ▼
                       ┌────────────────────────────┐
                       │ 输出结果 / 提交代码 / 用户确认   │
                       └────────────────────────────┘
```

### 3.2 循环逻辑与阻塞规则（详细版）

#### 阶段 0：需求确认

```
用户输入 → Strong/Elite 自问自答理解需求 → 生成需求摘要 → 请求用户确认
  └─ 用户确认通过 → 进入阶段 1
  └─ 用户未确认 → 主模型根据拒因自问自答重新理解 → 生成新摘要 → 再次请求用户确认
                    └─ 循环直到用户确认通过
```

**阻塞规则**：用户未确认时禁止进入阶段 1，必须循环直到确认。

#### 阶段 1：根因分析

```
阶段 0 确认通过 → Elite Agent 分析日志 + 源码 + 辅助测试
  → 自问自答评估根因置信度
  └─ 置信度 = 100% → 进入阶段 2
  └─ 置信度 < 100% → Elite 再次深度分析（日志/源码/测试）→ 重新评估
                    └─ 循环直到 100% 确认
```

**阻塞规则**：置信度未达 100% 前**阻塞阶段 2**，不得进入任务拆解。

#### 阶段 2：任务拆解

```
根因确认完成 → Elite/Strong 拆解为颗粒化子任务
  → 每个子任务分配唯一 ID
  → 声明依赖关系（dependencies 数组）
  → 持久化 SQLite 检查点
  └─ 进入阶段 3
```

**阻塞规则**：需要根因确认完成才能执行。

#### 阶段 3：子任务执行

```
拆解完成 → 按依赖排序执行子任务
  每个子任务:
    └─ 成功 → 写入检查点 → 下一个子任务
    └─ 失败 → 自动升级模型重试：light → medium → strong → elite
              → 无限循环直到执行成功
              → 写入检查点（含重试记录）
  全部完成 → 进入阶段 4
```

**阻塞规则**：
- 仅执行已确认子任务，不可跨越、不可自作主张
- 失败后必须自动升级模型重试（light → medium → strong → elite）
- 保证无限重试直到完成，无次数上限
- 每个子任务结果写入 SQLite 检查点

#### 阶段 4：复核

```
执行完成 → Strong Agent 差异校验 + 自动运行测试
  └─ 达标 → 输出最终结果
  └─ 不达标 → 生成审查反馈 → 自动回退 → 重新执行阶段 3
               └─ 循环直到复核通过
```

**阻塞规则**：复核不达标必须自动回退重做阶段 3，直到通过。

#### 证据链（贯穿全流程）

```
每阶段结束时:
  1. 生成阶段摘要 + 证据链（分析过程/日志/源码/测试结果/评分）
  2. 持久化写入 SQLite（checkpoints 表）
  3. 可追踪审计，支持生产环境排查
  证据链包含: phase, summary, detail, evidence[], timestamp
```

### 3.3 持续运行引擎要求

- **硬性核心**：引擎一旦启动必须持续运行，永不主动退出
- **默认运行时长**：10 小时不间断
- **心跳机制**：每 60 秒输出心跳日志，证明引擎存活
- **扫描间隔**：每 15 秒扫描一次 tasks/ 目录
- **断开恢复**：进程重启后从 SQLite 检查点恢复进度，不丢失已完成任务
- **信号处理**：收到 SIGINT/SIGTERM 时先保存检查点再退出
- **任务清单驱动**：引擎循环读取 tasks/ 目录下的 JSON 文件作为任务来源，支持热加载新任务

### 3.4 证据链要求

- 每阶段生成 **摘要 + 证据链** → 持久化 SQLite
- 可追踪审计，支持生产环境
- 证据链包含：输入摘要、分析过程关键结论、输出结果、质量评分

### 3.5 关键约束

- 每个步骤都有**检查点**，确保失败时可恢复
- 子任务由大模型严格拆解，小模型执行时**不可跨越、不可自由调整**
- 最终结果必须通过 Strong Agent 复核才能提交
- 自动对比 diff、运行测试、LSP 检查
- 引擎默认持续运行 10 小时，完成后自动输出运行报告

---

## 附录 B：智能调度总控 (IntelligentOrchestrator) 设计规格

### B.1 设计哲学

《灵枢》智能调度总控将系统从一个"工具"进化为真正有"调度智慧"的 AI 工程指挥部：

> 本地模型（轻骑兵）处理高频、低延迟任务；云端大模型（重装军）攻坚复杂推理；调度大脑根据任务复杂度、模型可用性和成本，动态、自动、不中断地完成一切。

融合 **《道德经》"知不知，尚矣"** 的哲理和 **"实事求是"** 的科学精神：
- 承认不确定性比假装确定更可贵
- 一切结论必须基于源码和日志的双重证据
- 审查过程恪守"言有宗，事有君"，证据不足时坦然承认未知

### B.2 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `IntelligentOrchestrator` | `core/orchestration_core.py` | LangGraph StateGraph 调度循环 |
| `EvidenceBasedRootCausePipeline` | `core/root_cause_pipeline.py` | 源码+日志双驱动的根因分析 |
| `ModelPool` | `core/orchestration_core.py` | 模型池管理与降级链 |
| `Orchestrator` | `core/orchestrator.py` | 主编排器（集成双模式） |

### B.3 调度图结构

```
task_analyzer → resource_allocator → plan_decomposer
    ↓
parallel_executor (asyncio.gather 多Agent并发)
    ↓
review_and_debate (实事求是审查 + 辩论)
    ├─ (通过) → checkpoint_saver → final_synthesizer → END
    └─ (不通过) → parallel_executor (降级重试循环)
```

### B.4 模型智能分配策略

| 复杂度 | Planner 模型 | Worker 模型 | 策略 |
|--------|-------------|-------------|------|
| **complex** | elite/strong 云端 | 混合本地+云端 | 深入分析，强模型规划 |
| **moderate** | strong/medium 云端 | 优先本地 | 平衡性能与成本 |
| **simple** | 本地模型 | 本地模型 | 完全本地，零成本 |

### B.5 降级链（自动故障转移）

```
local (Ollama) → light (gpt-4o-mini) → medium → strong (gpt-4.1) → elite (claude-sonnet-4)
```

每个 Worker 失败后自动沿降级链向下尝试，直到成功或链耗尽。

### B.6 证据驱动根因分析流程

```
load_evidences → free_analysis → uncertainty_monitor
    ├─ (未检测到猜测) → final_output
    └─ (检测到猜测/缺乏证据引用) → deep_analysis → structured_debate
                                                    ├─ adversarial_challenge
                                                    ├─ proposer_response
                                                    └─ judge (PASS/REJECT)
```

反幻觉监控器检测："可能是、一般来说、按理说、推测"等猜测性表述，以及是否引用了代码行号或日志时间戳。

### B.7 100% 任务完成保障机制

1. **自动降级重试**：每个 Worker 失败后自动切换模型
2. **检查点持久化**：每阶段保存状态到 SQLite，支持断点恢复
3. **审查闭环**：审查不通过自动回退重新执行
4. **看门狗超时**：配置最大执行时间，超时触发告警但不中止
5. **并发限流**：Semaphore 控制最大并发 Worker 数

### B.8 CLI 使用方式

```bash
# 标准 DAG 流水线（默认）
python main.py run "修复parser.py崩溃"

# 智能调度模式（多模型多Agent并发）
python main.py smart "分析并修复登录模块的认证逻辑缺陷"

# 仅分析模式
python main.py run "分析性能瓶颈" --mode analysis

# 辩论模式
python main.py run "选择最佳数据库方案" --mode debate
```

### B.9 模块依赖树

```
main.py
  └─ orchestrator.py (Orchestrator 主编排器)
       ├─ orchestration_core.py (IntelligentOrchestrator)
       │    ├─ ModelPool 模型池
       │    ├─ StateGraph 调度图
       │    └─ call_llm 统一 LLM 接口
       ├─ root_cause_pipeline.py (EvidenceBasedRootCausePipeline)
       │    ├─ uncertainty_monitor 反幻觉监控
       │    └─ structured_debate 辩论子图
       ├─ agent_chain.py (Agent 链)
       ├─ pipeline.py (检查点持久化)
       ├─ smart_references.py (意图解析)
       ├─ feedback_learner.py (反馈学习)
       └─ inverse_verifier.py (反向验证器)
```

---

## 8. 反向验证引擎 (Inverse Verifier)

### 8.1 核心理念：打破置信度悖论

AI 系统在分析问题时容易陷入**自洽性幻觉**——基于错误假设推导出看似完美的逻辑闭环。
独立模型反向验证相当于引入一个"辩论对手"，有效打破这种自洽幻觉。

### 8.2 工作流程

```
原始 Review:  架构师分析根因 → 编辑器实现修复 → Strong Agent 复核

增强后流程:  架构师分析根因 → 编辑器实现修复 → 反向验证器发起挑战 → 裁判最终裁决
```

### 8.3 角色定义

| 角色 | 职责 | 模型级别 |
|------|------|----------|
| 正方 (Proposer) | 提出根因和修复方案 | Strong/Elite |
| 反方 (Challenger) | 找出逻辑漏洞、提出替代根因、模拟边缘情况 | Elite（独立模型）|
| 裁判 (Adjudicator) | 阅读双方论据，做出最终裁决 | Elite（无偏见）|

### 8.4 验证强度分级

| 强度 | 机制 | 适用场景 |
|------|------|----------|
| **轻度 (Light)** | 单模型自我反驳 + 检查清单 | 日常小修小补 |
| **中度 (Medium)** | 启动反向验证器，走 1-3 轮辩论 | 中等复杂 Bug |
| **深度 (Deep)** | 反方+裁判+要求反方提供替代实现并通过测试 | 关键安全漏洞、生产事故 |

### 8.5 信任分追踪 (TrustScoreTracker)

核心逻辑:
- 每次验证通过累积信任分
- 连续 10 次 100% 通过 → 环境标记为"可信"
- 任何一次失败 → 信任分归零，重新累积
- 可信环境下可降低验证强度（加速）

```python
consecutive_pass = 0
while consecutive_pass < 10:
    result = inverse_verifier.verify(task)
    if result.passed and result.confidence >= 95:
        consecutive_pass += 1
    else:
        consecutive_pass = 0
# 环境可信！
```

### 8.6 集成到五阶段流水线

```
[需求分析] → [根因定位] → [任务拆解] → [执行] → [对抗验证] → [Patch 输出]
                                                        ↑
                                             反向验证器在这里介入
```

---

## 4. 执行模式

| 模式 ID | 名称 | 自动审批 | 终止策略 | 适用场景 |
|---------|------|----------|----------|----------|
| `task` | 标准任务 | 是 | stopOnError=false, maxRetries=2 | 全自动开发 |
| `analysis` | 分析模式 | 否 | stopOnError=true, maxPhases=2 | 仅分析和根因定位 |
| `auto-approve` | 自动审批 | 是 | maxRetries=3, autoEscalate=false | 低风险变更 |
| `manual` | 手动模式 | 否 | pauseOnEachTask=true | 高风险操作 |
| `cascade` | 级联模式 | 是 | 强模型拆→弱模型执行→强模型复核 | 复杂任务 |
| `debate` | 辩论模式 | 否 | 正方反方辩论+裁判裁决 | 方案决策 |

---

## 5. Agent 链协作模式（9 种）

| 模式 | 方式 | 来源启发 | 适用场景 |
|------|------|----------|----------|
| sequential | 串行链，逐步传递 | - | 流水线任务 |
| parallel | 并行执行 + 投票/择优/合并 | - | 多方案生成 |
| debate | 正反方多轮辩论 + 裁判裁决 | - | 技术方案决策 |
| cascade | 强模型拆→弱模型执行→复核 | - | 复杂任务拆分 |
| iterative | 循环执行直到质量达标 | - | 质量迭代优化 |
| architect-editor | 强模型设计→弱模型实现→复核 | Aider | 代码生成 |
| agent-loop | 推理→工具→观察自主循环 | Claude Code | 自主编程 |
| plan-agent-yolo | 规划→执行→审批三级链 | DeepSeek TUI | 安全与效率平衡 |
| best-of-n | N 方案并行择优 | Cursor | 方案择优 |

---

## 6. 预定义 Agent 团队模板

| 任务类型 | 协作模式 | Agent 角色组合 |
|----------|----------|----------------|
| code-review | sequential | 架构师 + 复核员 |
| bug-fix | cascade | 架构师 + 代码工人 + 复核员 |
| feature-dev | architect-editor | 架构师 + 实现者 |
| refactor | architect-editor | 重构分析师 + 执行者 |
| decision | debate | 正方 + 反方 + 裁判 |
| autonomous | agent-loop | 自主 Agent（推理→工具→观察循环） |
| optimize | best-of-n | N 个代码工人并行 + 择优 |
| workflow | plan-agent-yolo | 规划师 + 执行者 + 审批 |
| default | sequential | 默认工人 |

---

## 7. 任务准确性保障策略

### 7.1 严格任务拆解

- 每个子任务由 Elite Agent 生成唯一 ID + 文件/函数/操作描述
- 子任务不可跨越、不可自由修改
- 子任务之间可声明 `dependencies` 依赖关系，自动排序执行

### 7.2 双层校验

- 执行后编辑器 Agent 提交结果 → Strong Agent 复核
- 自动对比 diff、运行测试、LSP 检查
- 质量评分低于阈值自动回退重做

### 7.3 检查点与持久化

- 每个阶段完成后写入 SQLite
- 中断重启时从最近检查点恢复，保证任务完整性
- 每个子任务持久化到 `tasks` 表和 `checkpoints` 表

### 7.4 错误恢复

- 8 种错误分类，每种绑定独立恢复策略
- 可恢复错误自动重试/切换模型/降级
- 致命错误触发人工介入或回退

---

## 8. 基础设施需求

| 模块 | 优先级 | 功能要求 |
|------|--------|----------|
| **连接池** | P0 | 按 provider RPM/TPM 限速，优先级队列，并发控制，健康检测 + 自动重连 |
| **Token 池** | P0 | 环境变量加载多 Key，provider 内自动轮询，单 key 失效自动故障转移 |
| **缓存系统** | P1 | LRU 内存缓存 + 语义缓存（相同请求免调用 API）+ 可选持久化 |
| **健康监控** | P1 | API 可用性检测、内存监控、错误率统计、自动告警 |
| **错误恢复** | P0 | 8 种错误分类，指数退避/模型降级/上下文压缩/回退 |
| **沙箱执行** | P1 | 子进程超时 + 命令白名单 + 危险命令拦截 + 输出捕获 |
| **任务持久化** | P0 | SQLite 存储任务、检查点、执行结果；断点自动恢复 |
| **上下文管理** | P0 | 无限上下文引擎：4 级渐进压缩，Token 估算，智能提醒 |
| **Session 管理** | P1 | CLI 关闭后恢复会话，跨调用保持上下文 |
| **成本感知路由** | P1 | 自动选择能完成任务的最便宜模型 |
| **Prompt 缓存** | P1 | Claude/Gemini prompt caching，长上下文节省 90% 成本 |
| **Git 集成** | P1 | 自动 commit、branch 管理、PR 创建 |
| **自评估能力** | P2 | 对自己的输出做测试验证 + 语法检查 + 质量评分 |
| **MCP Server 模式** | P2 | 作为 MCP Server 暴露工具给其他 MCP 客户端调用 |
| **工作区元数据** | P1 | `.lingshu/` 目录存储项目级配置、状态、历史 |

---

## 9. 跨平台要求

| 平台 | 要求 |
|------|------|
| Windows | 支持本地盘 + 网络映射盘（Z:\ Y:\ 等）+ UNC 路径（\\\server\share） |
| Linux | 标准 POSIX 文件操作 |
| macOS | 标准 POSIX 文件操作 |
| 服务器场景 | SSH 远程文件操作支持 |

---

## 10. CLI 体验要求

- 全局命令 `lingshu`（类似 `opencode` / `claude` 直接敲名字启动）
- 毫秒级启动（Go CLI 壳 + Python 引擎）
- 简化日志输出（structlog 结构化日志，默认只显示关键信息）
- 内置命令：`lingshu run` / `lingshu batch` / `lingshu resume` / `lingshu list` / `lingshu config` / `lingshu login`
- `--dry-run` 输出完整任务计划
- 历史记录与断点恢复

---

## 10. CLI 体验要求

- 全局命令 `lingshu`（类似 `opencode` / `claude` 直接敲名字启动）
- 毫秒级启动（Go CLI 壳 + Python 引擎）
- 简化日志输出（structlog 结构化日志，默认只显示关键信息）
- 内置命令：`python main.py run` / `python main.py batch` / `python main.py resume` / `python main.py list` / `python main.py map` / `python main.py plugin list` / `python main.py plugin run <phase>`
- `--dry-run` 输出完整任务计划
- 历史记录与断点恢复

---

## 11. 插件系统 (Plugin System)

### 11.1 架构概览

插件系统分为两层：

| 层 | 文件 | 职责 |
|------|------|------|
| **BasePlugin** | `plugins/base.py` | 抽象基类，定义插件接口规范 |
| **PluginManager** | `plugins/manager.py` | 插件管理器，加载、编排、执行 |

### 11.2 BasePlugin 抽象基类

```python
class BasePlugin(ABC):
    name: str          # 类属性，插件名称
    description: str   # 类属性，插件描述
    phase: str         # 默认注册的阶段名

    enabled: bool      # 实例属性，是否启用（默认 True）

    async def validate(self) -> bool:
        """检查外部工具是否可用。失败时返回 False 并记录警告，不抛异常。"""

    async def run(self, state: dict) -> dict:
        """核心逻辑。必须在实现中调用 self.validate()。"""
```

**约束：**
- `run()` 必须调用 `validate()` 前置检查
- `validate()` 不允许抛出异常（用日志记录失败）
- `init()` / `cleanup()` 是可选钩子

### 11.3 PluginManager 插件管理器

```python
class PluginManager:
    def __init__(self, config: dict):
        """接收完整配置字典（含 plugins 字段）"""

    async def load_plugins(self) -> int:
        """根据配置动态导入并实例化插件
        查找策略: plugins.{name} → plugins.{name}.{name.capitalize()}Plugin
        每个插件执行前后记录 logger('lingshu.plugins') DEBUG 日志
        """

    async def execute_phase(self, phase: str, state: dict) -> dict:
        """执行指定阶段的全部已启用插件
        - 按注册顺序依次执行
        - validate() 失败自动跳过
        - 结果透传（上一个的输出是下一个的输入）
        """
```

### 11.4 配置格式 (lingshu.yaml)

```yaml
plugins:
  enabled: true
  phases:
    evidence_collection:
      - trailmark_callgraph
      - codebadger_inspect
    post_execution:
      - diting_verify
  trailmark:
    enabled: true
    auto_trigger: true
  codebadger:
    enabled: true
    auto_trigger: true
  diting:
    enabled: true
    depth: "medium"
```

### 11.5 插件查找策略

1. 尝试 `import plugins.{name}`
2. 尝试 `import plugins.{name}.{Name}Plugin`
3. 尝试 `import plugins.{name}.Plugin`
4. 在模块中查找 `BasePlugin` 子类，取第一个实例化

### 11.6 CLI 用法

```bash
# 列出当前所有已加载的插件
python main.py plugin list

# 执行指定阶段的所有插件
python main.py plugin run --phase evidence_collection
```

### 11.7 开发新插件

```python
from plugins.base import BasePlugin

class TrailmarkPlugin(BasePlugin):
    name = "trailmark_callgraph"
    description = "调用图分析"
    phase = "evidence_collection"

    async def validate(self) -> bool:
        # 检查 trailmark 是否安装
        ...

    async def run(self, state: dict) -> dict:
        if not await self.validate():
            return state
        # 执行分析
        ...
        return state
```

---

## 12. 10 大融合特性

### MVP（第一批）

| # | 特性 | 来源 | 说明 |
|---|------|------|------|
| 1 | 架构师/编辑双模型 + Repository Map | Aider | 强模型规划 + 弱模型实现，附带代码库理解 |
| 2 | 五阶段流水线 | ECC | 分析→根因→拆解→执行→Review→Patch 标准化流程 |
| 3 | CodeAct 纯代码执行模式 | Smolagents | Agent 直接输出代码动作而非自然语言指令 |
| 4 | 半自主审批链 | Cline | Plan-Agent-YOLO 三级模式，安全与效率平衡 |
| 5 | 插件系统 | Skills/Instincts | 插件加载机制：Agent/模型/提示词/工具 4 类资源注册 + 生命周期钩子 |

### 第二批

| # | 特性 | 来源 |
|---|------|------|
| 6 | 清单-运行时双层架构 | Claw-code |
| 7 | 安全沙箱隔离 | Daytona/OpenShell |
| 8 | 记忆系统（持久化 + 检索） | IronEngine/ECC |
| 9 | LSP 集成（代码符号索引） | Continue/OpenCode |
| 10 | 无限上下文引擎 | Cursor 上下文管理 |

### 第三批（高级特性）

| # | 特性 | 来源灵感 |
|---|------|----------|
| 11 | Composer 多文件编辑引擎 | Cursor |
| 12 | @ 符号智能上下文引用 | Cursor |
| 13 | 结构化输出引擎 | Hermes |
| 14 | 工作流模板引擎（YAML 编排） | OpenCode |
| 13 | CodeMap 仓库地图 | Aider | 符号索引+依赖分析+紧凑地图，token 预算裁剪 |
| 14 | StructuredOutput 结构化输出引擎 | Hermes | JSON Schema/Pydantic 验证、自动修复、流式累加 |
| 15 | CostAwareRouter 成本感知路由 | DeepSeek | 按预算选模型、自动降级、性价比排序 |
| 16 | WorkflowTemplate 工作流模板引擎 | OpenCode | YAML/JSON 模板、变量插值、条件/循环/并行 |
| 17 | AdapterRegistry 适配器注册中心 | - | LSP/Cursor/ECC 统一注册，桥接外部工具 |

---

## 12. 技术栈

| 层面 | 技术选型 |
|------|----------|
| 语言 | Python 3.12+ |
| CLI 框架 | Typer + Rich |
| 模型调用 | litellm（统一覆盖 5 个 provider） |
| 配置管理 | pydantic-settings + YAML 自定义源 |
| 异步 HTTP | httpx |
| 日志 | structlog（JSON 日志 + processor pipeline） |
| 数据库 | SQLAlchemy 2.0 + aiosqlite |
| 代码索引 | tree-sitter |
| RPC 服务 | FastAPI + uvicorn |
| 沙箱 | subprocess + timeout |
| 测试 | pytest + pytest-asyncio |
| 包管理 | pip + pyproject.toml |

---

## 13. 交付路线图

### Phase 1 — MVP（5 核心模块）

- 架构师/编辑双模型 + Repository Map
- 五阶段流水线
- CodeAct 执行模式
- Plan-Agent-YOLO 三级审批
- 插件系统

### Phase 2 — 基础设施加固

- 连接池 + Token 池 + 缓存
- 错误恢复 + 健康监控
- 任务持久化 + 上下文管理
- 沙箱执行

### Phase 3 — 高级特性

- 清单-运行时架构
- 记忆系统
- LSP 集成
- 跨平台完整支持

### Phase 4 — 体验打磨

- Composer 引擎
- 智能上下文引用
- 推理成本优化
- Web UI 模式

---

## 14. 已有 Node.js 版参考模块清单

以下为 `G:\pure-ai-orchestrator\src\` 中已有的 35+ 模块，Python 重写时参考：

```
src/
  orchestrator.js       # 主编排器（6 阶段流水线）
  cli.js                # CLI 入口
  model-client.js       # 模型客户端（5 providers）
  router.js             # Agent/模型智能路由
  agent-chain.js        # 9 种 Agent 链模式
  pipeline-engine.js    # YAML 流水线引擎
  context-manager.js    # 无限上下文引擎
  plugin-loader.js      # 插件系统
  error-recovery.js     # 错误恢复
  tool-executor.js      # 工具执行
  sandbox.js            # 沙箱
  task-store.js         # SQLite 任务持久化
  config-manager.js     # 配置管理
  cache-manager.js      # 缓存
  connection-pool.js    # 连接池
  token-pool.js         # Token 池
  health-monitor.js     # 健康监控
  cost-tracker.js       # 成本追踪
  diff-engine.js        # Diff 引擎
  validator.js          # 结果验证
  logger.js             # 结构化日志
  progress-reporter.js  # 进度报告
  workspace.js          # 工作区管理（含 UNC 路径）
  code-indexer.js       # 代码符号索引
  composer-engine.js    # 多文件编辑
  context-ref-engine.js # @引用引擎
  structured-output.js  # 结构化输出
  workflow-template.js  # 工作流模板
  inference-optimizer.js# 推理优化
  self-iterate.js       # 自迭代
  escalation-engine.js  # 智能升级
  feedback-loop.js      # 反馈循环
  task-decomposer.js    # 任务拆解
  multi-repo.js         # 多仓库支持
  cli-history.js        # CLI 历史
```

---

## 附录 A：项目目录结构（产出代码骨架）

```
linsghu_full/
├── main.py                    # Python CLI / 单任务执行入口
├── rpc_server.py              # FastAPI RPC 服务
├── batch_runner.py            # 批量执行任务库
├── config/
│   ├── model_routing.yaml     # 模型路由配置
│   ├── path_map.yaml          # 项目路径映射
│   └── aliases.yaml           # Agent/模型别名
├── core/
│   ├── __init__.py
│   ├── adapter_registry.py   # 适配器注册中心
│   ├── agent_chain.py         # Agent 调度 + dispatch + review
│   ├── agent_modes.py         # 9 种 Agent 协作模式
│   ├── cache_manager.py       # LRU + 语义缓存
│   ├── code_map.py            # CodeMap 仓库地图
│   ├── code_indexer.py        # 代码符号索引
│   ├── codeact_engine.py      # CodeAct 纯代码模式
│   ├── composer_engine.py     # Composer 多文件编辑
│   ├── connection_pool.py     # 连接池
│   ├── context_manager.py     # 4 级渐进压缩 + Token 估算
│   ├── cost_aware_router.py   # 成本感知路由
│   ├── cost_tracker.py        # 成本追踪
│   ├── diff_engine.py         # Diff 引擎
│   ├── engine.py              # 持续运行引擎（核心）
│   ├── error_recovery.py      # 8 种错误分类 + Watchdog
│   ├── execution_modes.py     # 6 种执行模式
│   ├── feedback_learner.py    # 隐式反馈学习器
│   ├── git_integration.py     # Git 集成
│   ├── health_monitor.py      # 健康监控
│   ├── inverse_verifier.py    # 反向验证器
│   ├── model_router.py        # 4 级模型路由 + fallback
│   ├── orchestration_core.py  # IntelligentOrchestrator
│   ├── orchestrator.py        # 主编排器
│   ├── pipeline.py            # 流水线 + SQLite 检查点 + resume
│   ├── profile_loader.py      # 用户画像加载
│   ├── resource_monitor.py    # 并发监控
│   ├── root_cause_pipeline.py # 证据驱动根因分析
│   ├── sandbox.py             # 安全沙箱
│   ├── session_manager.py     # 会话管理
│   ├── smart_references.py    # @ 引用引擎 / IntentParser
│   ├── structured_output.py   # 结构化输出引擎
│   ├── token_pool.py          # Token 池
│   ├── workflow_templates.py  # 工作流模板引擎
│   └── workspace.py           # 工作区管理
├── agents/
│   ├── __init__.py
│   ├── elite_agent.py         # 根因分析 (claude-sonnet-4)
│   ├── strong_agent.py        # 架构师/Reviewer (gpt-4.1)
│   └── light_agent.py         # 编辑器 (gpt-4o-mini)
├── memory/
│   ├── __init__.py
│   ├── short_term.py          # 当前任务记忆
│   ├── mid_term.py            # 项目级记忆
│   └── long_term.py           # 跨项目经验
├── adapters/
│   ├── __init__.py
│   ├── lsp_adapter.py         # LSP 接口
│   ├── cursor_adapter.py      # Cursor/OpenCode
│   └── ecc_adapter.py         # ECC 外部工具
├── workflows/
│   └── task_workflow.py       # Task 基础类
├── tools/
│   └── utils.py               # 工具函数
├── plugins/
│   ├── __init__.py          # 导出 BasePlugin, PluginManager
│   ├── base.py              # BasePlugin 抽象基类
│   ├── manager.py           # PluginManager 插件管理器
│   ├── sample_plugin.py     # 插件示例
│   ├── trailmark_callgraph.py # Trailmark 调用图分析插件
│   ├── codebadger_inspect.py  # Codebadger 代码缺陷审查插件
│   ├── diting_verify.py       # 谛听辩论式验证插件
│   └── plugin_loader.py     # 旧版插件加载器（兼容）
├── prompts/
│   ├── __init__.py
│   ├── architect.md         # 架构师 Agent 系统提示词
│   ├── editor.md            # 编辑器 Agent 系统提示词
│   ├── root_cause.md        # 根因分析师 Agent 系统提示词
│   └── reviewer.md          # Review Agent 系统提示词
├── commands/
│   ├── __init__.py
│   ├── run.py               # CLI run 子命令（含 --mode 支持）
│   ├── configure.py         # 打开配置文件
│   ├── history.py           # 查看任务历史
│   └── resume.py            # 从断点恢复
├── tasks/
│   ├── bug_fix/               # Bug 修复任务
│   ├── refactor/              # 重构任务
│   └── test_generation/       # 测试生成任务
├── go_exec/
│   └── worker.go              # Go 高并发执行
├── node_cli/
│   ├── package.json
│   └── cli.js                 # Node.js CLI 调用
├── tests/
│   ├── __init__.py
│   ├── test_agent_chain.py
│   ├── test_auth.py            # 认证模块测试
│   ├── test_db.py              # 数据库模块测试
│   ├── test_error_recovery.py
│   ├── test_model_router.py
│   ├── test_parser.py          # 解析器模块测试
│   └── test_pipeline.py
├── docs/
│   └── REQUIREMENTS.md        # 本文件
├── lingshu.yaml               # 插件系统配置
├── pyproject.toml
├── .gitignore
├── README.md
├── eternal_watchdog.ps1       # 三层守护系统
├── register_autostart.bat
├── run_10h_watchdog.ps1
├── run_engine.py
├── start_engine.bat
├── start_eternal.bat
├── stop_eternal.bat
└── watchdog_10h.ps1
```