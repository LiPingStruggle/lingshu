"""
WorkflowTemplateEngine - 工作流模板引擎

需求覆盖（第 14 章）：
- 可复用的工作流模板定义（YAML/JSON）
- 模板变量插值
- 条件步骤、循环步骤
- 步骤间数据传递
- 模板版本管理
"""
from __future__ import annotations
import os
import re
import json
import yaml
import logging
import copy
from typing import Any, Dict, List, Optional, Callable, Union
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class StepType(Enum):
    TASK = 'task'             # 执行任务
    DECISION = 'decision'     # 条件分支
    PARALLEL = 'parallel'     # 并行执行
    LOOP = 'loop'             # 循环
    SUBFLOW = 'subflow'       # 子工作流
    HOOK = 'hook'             # 钩子/回调
    WAIT = 'wait'             # 等待条件


@dataclass
class WorkflowStep:
    """工作流步骤"""
    id: str
    name: str
    type: StepType = StepType.TASK
    description: str = ''
    agent_role: str = 'worker'
    prompt_template: str = ''
    input_mapping: Dict[str, str] = field(default_factory=dict)  # 变量映射
    output_mapping: Dict[str, str] = field(default_factory=dict)
    condition: Optional[str] = None  # 条件表达式
    loop_over: Optional[str] = None  # 循环变量
    max_iterations: int = 10
    timeout_seconds: int = 300
    retry_count: int = 3
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowTemplate:
    """工作流模板"""
    id: str
    name: str
    version: str = '1.0.0'
    description: str = ''
    author: str = ''
    tags: List[str] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)  # 默认变量
    steps: List[WorkflowStep] = field(default_factory=list)
    output_template: str = ''
    on_error: Optional[str] = None  # 错误处理策略
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowContext:
    """工作流执行上下文"""
    template_id: str
    run_id: str
    variables: Dict[str, Any] = field(default_factory=dict)
    step_results: Dict[str, Any] = field(default_factory=dict)
    current_step: Optional[str] = None
    status: str = 'pending'  # pending | running | completed | failed | cancelled
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class StepResult:
    """步骤执行结果"""
    step_id: str
    status: str  # success | failed | skipped
    output: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    retries: int = 0


class WorkflowTemplateEngine:
    """工作流模板引擎"""

    def __init__(self, templates_dir: Optional[str] = None):
        self._templates: Dict[str, WorkflowTemplate] = {}
        self._contexts: Dict[str, WorkflowContext] = {}
        self._step_handlers: Dict[str, Callable] = {}
        self._templates_dir = templates_dir

        if templates_dir:
            self.load_templates(templates_dir)

    # ── 模板管理 ──────────────────────────────────────────

    def register_template(self, template: WorkflowTemplate) -> None:
        """注册模板"""
        key = f"{template.id}@{template.version}"
        self._templates[key] = template
        self._templates[template.id] = template  # 无版本号也存一份
        logger.info(f"WorkflowEngine: registered template '{template.id}' v{template.version}")

    def get_template(self, template_id: str, version: Optional[str] = None) -> Optional[WorkflowTemplate]:
        """获取模板"""
        if version:
            return self._templates.get(f"{template_id}@{version}")
        return self._templates.get(template_id)

    def load_templates(self, directory: str) -> int:
        """从目录加载所有模板"""
        count = 0
        if not os.path.isdir(directory):
            logger.warning(f"WorkflowEngine: templates dir not found: {directory}")
            return 0
        for fname in sorted(os.listdir(directory)):
            if not fname.endswith(('.yaml', '.yml', '.json')):
                continue
            path = os.path.join(directory, fname)
            try:
                tmpl = self._load_template_file(path)
                if tmpl:
                    self.register_template(tmpl)
                    count += 1
            except Exception as e:
                logger.error(f"WorkflowEngine: failed to load {fname}: {e}")
        logger.info(f"WorkflowEngine: loaded {count} templates from {directory}")
        return count

    def list_templates(self, tag: Optional[str] = None) -> List[Dict]:
        """列出所有模板"""
        results = []
        seen = set()
        for key, tmpl in self._templates.items():
            if '@' in key:
                continue  # 跳过版本号副本
            if tag and tag not in tmpl.tags:
                continue
            if tmpl.id in seen:
                continue
            seen.add(tmpl.id)
            results.append({
                'id': tmpl.id,
                'name': tmpl.name,
                'version': tmpl.version,
                'description': tmpl.description[:80],
                'steps': len(tmpl.steps),
                'tags': tmpl.tags,
            })
        return results

    # ── 执行 ──────────────────────────────────────────────

    def create_context(self, template_id: str, variables: Dict = None) -> WorkflowContext:
        """创建工作流执行上下文"""
        import uuid
        tmpl = self.get_template(template_id)
        if not tmpl:
            raise ValueError(f"Template '{template_id}' not found")

        merged_vars = dict(tmpl.variables)
        if variables:
            merged_vars.update(variables)

        ctx = WorkflowContext(
            template_id=template_id,
            run_id=f"wf_{uuid.uuid4().hex[:12]}",
            variables=merged_vars,
        )
        self._contexts[ctx.run_id] = ctx
        return ctx

    def get_context(self, run_id: str) -> Optional[WorkflowContext]:
        return self._contexts.get(run_id)

    def register_step_handler(self, step_type: str, handler: Callable) -> None:
        """注册自定义步骤处理器"""
        self._step_handlers[step_type] = handler

    async def execute(
        self,
        run_id: str,
        step_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """执行工作流"""
        import time
        ctx = self._contexts.get(run_id)
        if not ctx:
            raise ValueError(f"Context '{run_id}' not found")

        tmpl = self.get_template(ctx.template_id)
        if not tmpl:
            raise ValueError(f"Template '{ctx.template_id}' not found")

        ctx.status = 'running'
        ctx.started_at = time.time()

        for step in tmpl.steps:
            ctx.current_step = step.id

            step_time = time.time()
            try:
                result = await self._execute_step(step, ctx, step_callback)
                ctx.step_results[step.id] = result
                ctx.variables.update(result.output or {})

                if result.status == 'failed':
                    ctx.status = 'failed'
                    ctx.error = result.error
                    break

            except Exception as e:
                ctx.status = 'failed'
                ctx.error = str(e)
                logger.error(f"WorkflowEngine: step {step.id} failed: {e}")
                break

        if ctx.status == 'running':
            ctx.status = 'completed'
        ctx.completed_at = time.time()

        return {
            'run_id': ctx.run_id,
            'status': ctx.status,
            'template': ctx.template_id,
            'error': ctx.error,
            'duration_s': round((ctx.completed_at or time.time()) - (ctx.started_at or time.time()), 2),
            'step_results': {k: asdict(v) for k, v in ctx.step_results.items()},
        }

    # ── 内部 ──────────────────────────────────────────────

    def _load_template_file(self, path: str) -> Optional[WorkflowTemplate]:
        """从文件加载模板"""
        with open(path, 'r', encoding='utf-8') as f:
            if path.endswith('.json'):
                data = json.load(f)
            else:
                data = yaml.safe_load(f)
        if not data:
            return None

        steps = []
        for s in data.get('steps', []):
            step_type = StepType(s.get('type', 'task'))
            steps.append(WorkflowStep(
                id=s['id'],
                name=s.get('name', s['id']),
                type=step_type,
                description=s.get('description', ''),
                agent_role=s.get('agent_role', 'worker'),
                prompt_template=s.get('prompt_template', ''),
                input_mapping=s.get('input_mapping', {}),
                output_mapping=s.get('output_mapping', {}),
                condition=s.get('condition'),
                loop_over=s.get('loop_over'),
                max_iterations=s.get('max_iterations', 10),
                timeout_seconds=s.get('timeout_seconds', 300),
                retry_count=s.get('retry_count', 3),
                dependencies=s.get('dependencies', []),
                metadata=s.get('metadata', {}),
            ))

        return WorkflowTemplate(
            id=data['id'],
            name=data.get('name', data['id']),
            version=data.get('version', '1.0.0'),
            description=data.get('description', ''),
            author=data.get('author', ''),
            tags=data.get('tags', []),
            variables=data.get('variables', {}),
            steps=steps,
            output_template=data.get('output_template', ''),
            on_error=data.get('on_error'),
        )

    async def _execute_step(
        self,
        step: WorkflowStep,
        ctx: WorkflowContext,
        callback: Optional[Callable],
    ) -> StepResult:
        """执行单个步骤"""
        import time
        start = time.time()

        # 条件检查
        if step.condition:
            passed = self._evaluate_condition(step.condition, ctx.variables)
            if not passed:
                return StepResult(step_id=step.id, status='skipped', duration_ms=0)

        # 自定义处理器
        handler = self._step_handlers.get(step.type.value)
        if handler:
            try:
                output = await handler(step, ctx)
                return StepResult(
                    step_id=step.id,
                    status='success',
                    output=output,
                    duration_ms=(time.time() - start) * 1000,
                )
            except Exception as e:
                return StepResult(
                    step_id=step.id,
                    status='failed',
                    error=str(e),
                    duration_ms=(time.time() - start) * 1000,
                )

        # 默认：渲染 prompt 模板
        prompt = self._render_template(step.prompt_template, ctx.variables)

        if callback:
            try:
                output = await callback(step, ctx, prompt)
                return StepResult(
                    step_id=step.id,
                    status='success' if output else 'failed',
                    output=output,
                    duration_ms=(time.time() - start) * 1000,
                )
            except Exception as e:
                return StepResult(
                    step_id=step.id,
                    status='failed',
                    error=str(e),
                    duration_ms=(time.time() - start) * 1000,
                )

        return StepResult(step_id=step.id, status='skipped', duration_ms=(time.time() - start) * 1000)

    def _render_template(self, template: str, variables: Dict) -> str:
        """模板渲染：替换 {{ var }} 占位符"""
        def replacer(m):
            key = m.group(1).strip()
            val = variables.get(key, m.group(0))
            if isinstance(val, dict) or isinstance(val, list):
                return json.dumps(val, ensure_ascii=False)
            return str(val)

        return re.sub(r'\{\{\s*(\w+(?:\.\w+)*)\s*\}\}', replacer, template)

    def _evaluate_condition(self, condition: str, variables: Dict) -> bool:
        """评估条件表达式（简单 DSL）"""
        # 支持: var == value, var != value, var > value, var exists, not var
        condition = condition.strip()

        # var exists
        if condition.endswith(' exists'):
            var = condition[:-7].strip()
            return variables.get(var) is not None

        # not var
        if condition.startswith('not '):
            var = condition[4:].strip()
            return not variables.get(var)

        # var == value, var != value, var > value, var < value
        m = re.match(r'(\w+)\s*(==|!=|>=|<=|>|<)\s*(.+)', condition)
        if m:
            var, op, val = m.groups()
            actual = variables.get(var)
            val = val.strip().strip("'\"")
            # 尝试数值比较
            try:
                actual = float(actual) if actual is not None else actual
                val = float(val)
            except (ValueError, TypeError):
                pass
            if op == '==': return actual == val
            if op == '!=': return actual != val
            if op == '>': return (actual or 0) > val
            if op == '<': return (actual or 0) < val
            if op == '>=': return (actual or 0) >= val
            if op == '<=': return (actual or 0) <= val

        return True  # 默认通过

    @property
    def stats(self) -> dict:
        return {
            'templates': len([k for k in self._templates if '@' not in k]),
            'active_runs': sum(1 for c in self._contexts.values() if c.status == 'running'),
        }