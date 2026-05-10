# LingShu (灵枢) AI 自动化编程工具

大模型规划分析 + 小模型执行 + 强模型复核 的自动化编程流水线。

## 架构

```
User Input → Engine(持续循环) → AgentChain(Agent 调度) → Pipeline(流水线)
                ↕                          ↕
           ModelRouter(4级路由)       ErrorRecovery(8种恢复策略)
                ↕                          ↕
          Elite/Strong/Light Agent    ContextManager(渐进压缩)
                ↕                          ↕
          CodeMap / CodeAct / Composer   StructuredOutput
                ↕
     CostAwareRouter(成本感知) + WorkflowTemplate(模板引擎)
                ↕
          PluginManager(BasePlugin 插件底座)
                ↕
     InverseVerifier(反向验证) + FeedbackLearner(反馈学习)
```

## 快速开始

```bash
# 安装依赖
pip install -e .

# 批量执行所有任务
python main.py batch

# 启动持续运行引擎（10小时）
python main.py engine 10

# 提交单个任务
python main.py run "修复 parser 中的 bug"

# 智能调度模式（多模型并发）
python main.py smart "分析并修复登录模块的认证逻辑缺陷"

# 生成仓库地图
python main.py map

# 查看任务状态
python main.py list

# 管理插件
python main.py plugin list
python main.py plugin run --phase evidence_collection

# 成本分析
python main.py cost summary

# 教会 AI 规则
python main.py teach "coding_style = 函数式优先"
```

## 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **持续循环引擎** | `core/engine.py` | 循环扫描任务清单，自动执行，失败重试，10h 不间断 |
| **Agent 调度** | `core/agent_chain.py` | Elite/Strong/Light 三级调度 + dispatch + review |
| **任务流水线** | `core/pipeline.py` | SQLite 检查点 + 断点恢复 + 依赖排序 |
| **模型路由** | `core/model_router.py` | 4 级分层 + fallback 链 |
| **错误恢复** | `core/error_recovery.py` | 8 种错误分类 + Watchdog 超时 |
| **上下文管理** | `core/context_manager.py` | 4 级渐进压缩 + Token 估算 |
| **资源监控** | `core/resource_monitor.py` | 并发限制 + 限流 |
| **主编排器** | `core/orchestrator.py` | 整合所有子系统，5 阶段 DAG 流水线 |
| **智能调度** | `core/orchestration_core.py` | LangGraph StateGraph + 多模型并发 |
| **根因分析管道** | `core/root_cause_pipeline.py` | 证据驱动 + 反幻觉 + 辩论子图 |
| **反向验证器** | `core/inverse_verifier.py` | SQLite 持久化信任分 + 对抗验证 |
| **反馈学习器** | `core/feedback_learner.py` | 隐式反馈 + teach 接口 + 画像进化 |
| **CodeMap 仓库地图** | `core/code_map.py` | 符号索引 + 依赖分析 + 紧凑地图 |
| **CodeAct 引擎** | `core/codeact_engine.py` | Agent 直接输出代码动作 |
| **Composer 引擎** | `core/composer_engine.py` | 多文件并行编辑 |
| **StructuredOutput** | `core/structured_output.py` | JSON Schema/Pydantic 验证、自动修复 |
| **成本感知路由** | `core/cost_aware_router.py` | 按预算选模型、自动降级 |
| **工作流模板引擎** | `core/workflow_templates.py` | YAML/JSON 模板 + 条件/循环/并行 |
| **适配器注册中心** | `core/adapter_registry.py` | LSP/Cursor/ECC 统一注册 |
| **用户画像** | `core/profile_loader.py` | 自动注入 + teach 规则 |
| **意图解析** | `core/smart_references.py` | @ 引用引擎 + IntentParser |
| **安全沙箱** | `core/sandbox.py` | 子进程超时 + 命令白名单 |
| **Git 集成** | `core/git_integration.py` | 自动 commit / branch / PR |

## 插件系统

| 组件 | 文件 | 说明 |
|------|------|------|
| BasePlugin | `plugins/base.py` | 抽象基类：name, description, validate(), run() |
| PluginManager | `plugins/manager.py` | 加载/编排/执行，按 phase 分组 |
| 配置 | `lingshu.yaml` | 定义插件 phase 和开关 |

```bash
python main.py plugin list              # 列出插件
python main.py plugin run --phase evidence_collection  # 执行阶段
```

## 模型分级

| 层级 | 模型 | 用途 |
|------|------|------|
| Elite (5) | claude-sonnet-4 | 根因分析 / 高级决策 |
| Strong (4) | gpt-4.1 | 架构设计 / Review |
| Medium (3) | gpt-4o-mini | 轻量分析 |
| Light (2) | gemini-2.0-flash | 代码生成 / 执行 |
| Local (1) | qwen2 (ollama) | 离线推理 |

## CLI 命令

| 命令 | 说明 |
|------|------|
| `run <desc>` | 执行单个任务 |
| `batch` | 批量执行任务清单 |
| `resume <id>` | 从检查点恢复 |
| `list [status]` | 列出任务 |
| `status <id>` | 查看任务详情 |
| `server` | 启动 RPC 服务 |
| `engine <hours>` | 启动持续运行引擎 |
| `smart <desc>` | 智能调度模式 |
| `trust [action]` | 反向验证器信任管理 |
| `teach <rule>` | 教会 AI 规则 |
| `feedback <action>` | 反馈学习器管理 |
| `map` | 生成仓库地图 |
| `cost summary` | 成本汇总 |
| `plugin list / run` | 插件管理 |

## 三层持久化守护系统

```
eternal_watchdog.ps1    # 最外层：永不退出，死了拉活
start_eternal.bat       # 快速启动守护
stop_eternal.bat        # 安全停止
register_autostart.bat  # 注册开机自启
```

## 测试

```bash
pytest tests/ -v        # 67 项测试，全部通过
```

## 许可证

MIT