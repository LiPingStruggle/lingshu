"""
StructuredOutputEngine - 结构化输出引擎

将 LLM 自由文本输出转化为结构化对象，支持：
- JSON Schema 约束
- Pydantic 模型验证
- 修复模式（malformed JSON 自动修复）
- 类型安全的代码块提取（markdown 代码块 → 结构化数据）
- Streaming 结构化输出
"""
from __future__ import annotations
import json
import re
import logging
import traceback
from typing import Any, Dict, List, Optional, Type, TypeVar, Union, Callable
from enum import Enum
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

try:
    import pydantic
    from pydantic import BaseModel, ValidationError
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False
    # 退化类型
    class BaseModel:
        def model_dump(self): return {}
        @classmethod
        def model_validate(cls, obj): return cls(**obj)

    class ValidationError(Exception):
        pass


T = TypeVar('T', bound=BaseModel)


class OutputFormat(Enum):
    """输出格式"""
    PLAIN = 'plain'           # 自由文本
    JSON = 'json'             # JSON 对象
    MARKDOWN_CODE = 'markdown_code'  # markdown 代码块
    XML = 'xml'               # XML 片段
    CSV = 'csv'               # CSV 表格


@dataclass
class ExtractedBlock:
    """提取的代码块"""
    language: str
    content: str
    line_start: int
    line_end: int


@dataclass
class ParseResult:
    """解析结果"""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    raw_text: str = ''
    blocks: List[ExtractedBlock] = field(default_factory=list)
    fix_attempts: int = 0


class StructuredOutputEngine:
    """结构化输出引擎"""

    def __init__(self):
        self._fix_count = 0
        self._total_parsed = 0

    # ── 核心接口 ──────────────────────────────────────────

    def parse_json(
        self,
        text: str,
        schema: Optional[Dict] = None,
        model_class: Optional[Type[BaseModel]] = None,
        auto_fix: bool = True,
    ) -> ParseResult:
        """从文本中解析 JSON"""
        self._total_parsed += 1

        # 1. 提取 JSON 内容（可能被 markdown 包裹）
        raw = self._extract_json_text(text)
        if not raw:
            return ParseResult(success=False, error='No JSON content found', raw_text=text[:200])

        # 2. 解析 JSON
        result = self._try_parse(raw, schema, model_class, auto_fix)
        result.raw_text = text
        return result

    def parse_code_blocks(self, text: str) -> ParseResult:
        """提取所有 markdown 代码块"""
        blocks = self._extract_blocks(text)
        return ParseResult(
            success=len(blocks) > 0,
            data=[asdict(b) for b in blocks],
            blocks=blocks,
            raw_text=text,
        )

    def parse_list(self, text: str, item_sep: str = '\n') -> ParseResult:
        """解析列表（每行一个项目，或 markdown 列表）"""
        items = []
        lines = text.splitlines()

        # 尝试 markdown 列表：- item 或 * item 或 1. item
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            m = re.match(r'^[-*+]\s+(.*)', stripped)
            if not m:
                m = re.match(r'^\d+\.\s+(.*)', stripped)
            if m:
                items.append(m.group(1).strip())
            elif not items:
                # fallback：每行作为一个 item
                items.append(stripped)

        if not items:
            # 按分隔符拆分
            items = [s.strip() for s in text.split(item_sep) if s.strip()]

        return ParseResult(
            success=len(items) > 0,
            data=items,
            raw_text=text,
        )

    def extract_xml(self, text: str, tag: str) -> ParseResult:
        """提取 XML 标签中的内容"""
        pattern = f'<{tag}[^>]*>(.*?)</{tag}>'
        matches = re.findall(pattern, text, re.DOTALL)
        if matches:
            return ParseResult(
                success=True,
                data=[m.strip() for m in matches],
                raw_text=text,
            )
        return ParseResult(success=False, error=f'No <{tag}>...</{tag}> found', raw_text=text[:200])

    def validate_with_schema(self, data: Dict, schema: Dict) -> ParseResult:
        """用 JSON Schema 验证数据"""
        errors = self._validate_schema(data, schema)
        if not errors:
            return ParseResult(success=True, data=data)
        return ParseResult(success=False, data=data, error='; '.join(errors[:3]))

    # ── Streaming 支持 ────────────────────────────────────

    def create_stream_accumulator(
        self,
        model_class: Optional[Type[BaseModel]] = None,
    ) -> 'StreamAccumulator':
        """创建流式累加器"""
        return StreamAccumulator(self, model_class)

    # ── 工具方法 ──────────────────────────────────────────

    def _extract_json_text(self, text: str) -> Optional[str]:
        """从文本中提取 JSON 字符串"""
        # 1. 尝试 ```json ... ```
        m = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if m:
            candidate = m.group(1).strip()
            if self._looks_like_json(candidate):
                return candidate

        # 2. 尝试 { ... } 顶层大括号
        brace_depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == '{':
                if brace_depth == 0:
                    start = i
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0 and start >= 0:
                    candidate = text[start:i + 1]
                    if self._looks_like_json(candidate):
                        return candidate
                    start = -1

        # 3. 尝试 [ ... ] 顶层方括号
        brace_depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == '[':
                if brace_depth == 0:
                    start = i
                brace_depth += 1
            elif ch == ']':
                brace_depth -= 1
                if brace_depth == 0 and start >= 0:
                    candidate = text[start:i + 1]
                    if self._looks_like_json(candidate):
                        return candidate
                    start = -1

        return None

    def _looks_like_json(self, text: str) -> bool:
        """检查文本看起来是否像 JSON"""
        text = text.strip()
        if not text:
            return False
        if not (text[0] in '{[' and text[-1] in '}]'):
            return False
        # 快速检查：至少包含一个 key
        if text[0] == '{' and '"' not in text[:50]:
            return False
        return True

    def _try_parse(
        self,
        raw: str,
        schema: Optional[Dict],
        model_class: Optional[Type[BaseModel]],
        auto_fix: bool,
    ) -> ParseResult:
        """尝试解析 JSON，含自动修复"""
        fix_attempts = 0

        for attempt in range(3 if auto_fix else 1):
            try:
                data = json.loads(raw)

                # schema 验证
                if schema:
                    vr = self.validate_with_schema(data, schema)
                    if not vr.success:
                        if auto_fix:
                            raw = self._auto_fix_json(raw, vr.error or '')
                            fix_attempts += 1
                            continue
                        return vr

                # pydantic 验证
                if model_class and HAS_PYDANTIC:
                    try:
                        validated = model_class.model_validate(data)
                        return ParseResult(success=True, data=validated, fix_attempts=fix_attempts)
                    except ValidationError as e:
                        if auto_fix:
                            raw = self._auto_fix_json(raw, str(e))
                            fix_attempts += 1
                            self._fix_count += 1
                            continue
                        return ParseResult(success=False, data=data, error=str(e), fix_attempts=fix_attempts)

                return ParseResult(success=True, data=data, fix_attempts=fix_attempts)

            except json.JSONDecodeError as e:
                if auto_fix:
                    raw = self._auto_fix_json(raw, str(e))
                    fix_attempts += 1
                    self._fix_count += 1
                    continue
                return ParseResult(success=False, error=str(e), fix_attempts=fix_attempts)

        return ParseResult(success=False, error='Max fix attempts reached', fix_attempts=fix_attempts)

    def _extract_blocks(self, text: str) -> List[ExtractedBlock]:
        """提取 markdown 代码块"""
        blocks = []
        pattern = re.compile(r'```(\w*)\n?(.*?)```', re.DOTALL)
        for m in pattern.finditer(text):
            lang = m.group(1).strip() or 'text'
            content = m.group(2).strip()
            start_line = text[:m.start()].count('\n') + 1
            end_line = start_line + content.count('\n')
            blocks.append(ExtractedBlock(lang, content, start_line, end_line))
        return blocks

    def _auto_fix_json(self, raw: str, error_hint: str) -> str:
        """自动修复常见 JSON 问题"""
        fixed = raw

        # 1. 去除尾随逗号
        fixed = re.sub(r',\s*([}\]])', r'\1', fixed)

        # 2. 单引号 → 双引号（key 级别）
        fixed = re.sub(r"'(\w+)'\s*:", r'"\1":', fixed)

        # 3. 修复 True/False/None → JSON 小写
        fixed = fixed.replace('True', 'true').replace('False', 'false').replace('None', 'null')

        # 4. 去除注释 // 和 /* */
        fixed = re.sub(r'//[^\n]*', '', fixed)
        fixed = re.sub(r'/\*.*?\*/', '', fixed, flags=re.DOTALL)

        # 5. 修复未闭合的字符串（在末尾补 " ）
        lines = fixed.splitlines()
        for i in range(len(lines)):
            # 粗略修复
            pass

        if fixed != raw:
            logger.debug(f"StructuredOutputEngine: auto-fixed JSON ({len(fixed)} chars)")
        return fixed

    def _validate_schema(self, data: Dict, schema: Dict) -> List[str]:
        """简单的 JSON Schema 验证"""
        errors = []
        required = schema.get('required', [])
        for field in required:
            if field not in data:
                errors.append(f"Missing required field: {field}")
        properties = schema.get('properties', {})
        for key, value in data.items():
            prop = properties.get(key)
            if prop:
                expected_type = prop.get('type')
                if expected_type:
                    type_map = {
                        'string': str, 'number': (int, float),
                        'integer': int, 'boolean': bool,
                        'array': list, 'object': dict,
                    }
                    py_type = type_map.get(expected_type)
                    if py_type and not isinstance(value, py_type):
                        errors.append(f"{key}: expected {expected_type}, got {type(value).__name__}")
        return errors

    @property
    def stats(self) -> dict:
        return {
            'total_parsed': self._total_parsed,
            'fix_count': self._fix_count,
        }


class StreamAccumulator:
    """流式累加器：逐块接收流式输出，逐步构建结构化结果"""

    def __init__(self, engine: StructuredOutputEngine, model_class: Optional[Type[BaseModel]] = None):
        self._engine = engine
        self._model_class = model_class
        self._buffer = ''
        self._partial: Optional[Dict] = None
        self._blocks: List[ExtractedBlock] = []
        self._in_block = False
        self._block_lang = ''
        self._block_content = ''

    def feed(self, chunk: str) -> None:
        """喂入流式块"""
        self._buffer += chunk
        # 检测代码块
        lines = chunk.splitlines(keepends=True)
        for line in lines:
            if line.startswith('```'):
                if self._in_block:
                    self._in_block = False
                    self._blocks.append(ExtractedBlock(
                        self._block_lang, self._block_content.strip(), 0, 0
                    ))
                    self._block_content = ''
                else:
                    self._in_block = True
                    self._block_lang = line.strip('`\n')
            elif self._in_block:
                self._block_content += line

    def flush(self) -> ParseResult:
        """完成累加并解析"""
        return self._engine.parse_json(
            self._buffer,
            model_class=self._model_class,
        )

    @property
    def blocks(self) -> List[ExtractedBlock]:
        return self._blocks

    @property
    def text(self) -> str:
        return self._buffer


# ── Schema 工具 ──────────────────────────────────────────

def make_json_schema(
    model_class: Optional[Type[BaseModel]] = None,
    **fields,
) -> Dict:
    """快速构建 JSON Schema"""
    if model_class and HAS_PYDANTIC:
        return model_class.model_json_schema()

    properties = {}
    required = []
    for name, typ in fields.items():
        if isinstance(typ, tuple):
            typ, desc = typ
        else:
            desc = ''

        type_map = {
            str: 'string', int: 'integer', float: 'number',
            bool: 'boolean', list: 'array', dict: 'object',
        }
        json_type = type_map.get(typ, 'string')
        prop = {'type': json_type}
        if desc:
            prop['description'] = desc
        properties[name] = prop
        required.append(name)

    return {
        'type': 'object',
        'properties': properties,
        'required': required,
    }

def extract_json(text: str) -> Optional[Any]:
    """快捷函数：从文本提取 JSON"""
    engine = StructuredOutputEngine()
    result = engine.parse_json(text)
    return result.data if result.success else None

def extract_code(text: str, language: Optional[str] = None) -> List[str]:
    """快捷函数：提取代码块"""
    engine = StructuredOutputEngine()
    result = engine.parse_code_blocks(text)
    if not result.success:
        return []
    blocks = [ExtractedBlock(**b) if isinstance(b, dict) else b for b in (result.data or [])]
    if language:
        return [b.content for b in blocks if b.language == language]
    return [b.content for b in blocks]