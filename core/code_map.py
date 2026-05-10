"""
CodeMap - 仓库地图（Repository Map）

Aider 核心能力：构建紧凑的仓库结构地图，帮助 LLM 理解项目全貌。
- 文件树 + 符号索引
- 依赖关系追踪（import/require 分析）
- 紧凑地图生成（按 token 预算裁剪）
- 增量更新 / Watch 模式
"""
from __future__ import annotations
import os
import re
import json
import time
import logging
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


class RepoFile:
    """仓库文件元信息"""
    __slots__ = ('path', 'rel_path', 'size', 'lines', 'ext', 'modified', 'symbols', 'imports')

    def __init__(self, path: str, rel_path: str):
        self.path = path
        self.rel_path = rel_path
        self.size = 0
        self.lines = 0
        self.ext = os.path.splitext(path)[1]
        self.modified = 0.0
        self.symbols: List[SymbolInfo] = []
        self.imports: List[str] = []


class SymbolInfo:
    """符号信息"""
    __slots__ = ('name', 'kind', 'line', 'parent', 'signature')

    def __init__(self, name: str, kind: str, line: int, parent: str = '', signature: str = ''):
        self.name = name
        self.kind = kind  # class, function, method, variable, const
        self.line = line
        self.parent = parent
        self.signature = signature


class CodeMap:
    """仓库地图构建器"""

    # 要忽略的目录
    IGNORE_DIRS = {
        '.git', '__pycache__', 'node_modules', '.venv', 'venv',
        '.tox', '.eggs', 'dist', 'build', '.mypy_cache', '.pytest_cache',
        '.ruff_cache', '.cursor', 'site-packages', '.lingshu',
    }

    # 代码文件扩展名
    CODE_EXT = {
        '.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs', '.java', '.rb',
        '.php', '.c', '.cpp', '.h', '.hpp', '.cs', '.swift', '.kt',
        '.scala', '.ex', '.exs', '.ml', '.mli', '.vue', '.svelte',
        '.css', '.scss', '.less', '.html', '.xml', '.yaml', '.yml',
        '.json', '.toml', '.cfg', '.ini', '.sql', '.sh', '.ps1', '.bat',
        '.proto', '.graphql', '.gql', '.md', '.rst',
    }

    # 扩展名 → 注释前缀
    COMMENT_PREFIX: Dict[str, str] = defaultdict(lambda: '#')

    def __init__(self, root_path: str, max_map_tokens: int = 2000):
        self.root_path = os.path.abspath(root_path)
        self.max_map_tokens = max_map_tokens
        self._files: Dict[str, RepoFile] = {}
        self._dirty = True
        self._build_time = 0.0
        self._last_map: str = ''

        self._import_patterns: Dict[str, re.Pattern] = {
            '.py': re.compile(
                r'^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+(?:\s*,\s*[\w.]+)*))'
                r'|^\s*import\s+([\w.]+)'
            ),
            '.js': re.compile(r"""
                (?:import\s+(?:[\w*{},\s]+\s+from\s+)?['"]([^'"]+)['"]
                |require\(['"]([^'"]+)['"]\))
            """, re.VERBOSE),
            '.ts': re.compile(r"""
                (?:import\s+(?:[\w*{},\s]+\s+from\s+)?['"]([^'"]+)['"]
                |require\(['"]([^'"]+)['"]\)
                |import\s+type\s+(?:[\w*{},\s]+\s+from\s+)?['"]([^'"]+)['"])
            """, re.VERBOSE),
            '.go': re.compile(r'^\s*import\s+["]([^"]+)["]|^\s*import\s+\(\s*$'),
            '.rs': re.compile(r'^\s*use\s+([\w:]+)'),
        }

    # ── 公开接口 ──────────────────────────────────────────

    def build(self) -> 'CodeMap':
        """构建/重建仓库地图"""
        start = time.time()
        self._files.clear()
        self._walk_directory(self.root_path)
        self._dirty = False
        self._build_time = time.time() - start
        self._last_map = ''  # 下次请求时重新生成
        logger.info(f"CodeMap: built index with {len(self._files)} files in {self._build_time:.2f}s")
        return self

    def refresh(self) -> 'CodeMap':
        """增量刷新（仅重新扫描已修改文件）"""
        changed = 0
        for rel_path, rf in list(self._files.items()):
            if not os.path.isfile(rf.path):
                del self._files[rel_path]
                changed += 1
                continue
            mtime = os.path.getmtime(rf.path)
            if mtime > rf.modified:
                self._index_file(rf)
                changed += 1
        if changed > 0:
            self._dirty = True
        logger.debug(f"CodeMap: refreshed {changed} changed files")
        return self

    def generate_map(self, max_tokens: Optional[int] = None) -> str:
        """生成紧凑的仓库地图字符串"""
        if max_tokens is None:
            max_tokens = self.max_map_tokens

        if self._dirty or not self._last_map:
            self._last_map = self._build_map(max_tokens)
        return self._last_map

    def search_symbol(self, name: str, kind: Optional[str] = None) -> List[SymbolInfo]:
        """搜索符号"""
        results = []
        for rf in self._files.values():
            for sym in rf.symbols:
                if name.lower() in sym.name.lower():
                    if kind and sym.kind != kind:
                        continue
                    results.append(sym)
        return results

    def find_references(self, symbol_name: str) -> List[Tuple[str, int]]:
        """查找符号引用位置"""
        refs = []
        pattern = re.compile(re.escape(symbol_name))
        for rel_path, rf in self._files.items():
            try:
                with open(rf.path, 'r', encoding='utf-8', errors='ignore') as f:
                    for i, line in enumerate(f, 1):
                        if pattern.search(line):
                            refs.append((rel_path, i))
            except Exception:
                continue
        return refs

    def get_dependency_graph(self) -> Dict[str, List[str]]:
        """获取依赖图 {rel_path: [依赖的 rel_path]}"""
        graph = {}
        for rel_path, rf in self._files.items():
            deps = []
            for imp in rf.imports:
                resolved = self._resolve_import(imp, rf)
                if resolved:
                    deps.append(resolved)
            graph[rel_path] = deps
        return graph

    def get_file_tree(self, prefix: str = '') -> str:
        """获取文件树结构"""
        return self._build_file_tree(self.root_path, prefix)

    @property
    def stats(self) -> dict:
        total_lines = sum(rf.lines for rf in self._files.values())
        total_syms = sum(len(rf.symbols) for rf in self._files.values())
        return {
            'files': len(self._files),
            'lines': total_lines,
            'symbols': total_syms,
            'build_time_s': round(self._build_time, 3),
        }

    # ── 内部方法 ──────────────────────────────────────────

    def _walk_directory(self, directory: str) -> None:
        """递归遍历目录"""
        for root, dirs, files in os.walk(directory):
            # 忽略不想要的目录
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS
                       and not d.startswith('.') or d == '.lingshu']

            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext not in self.CODE_EXT:
                    continue
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, self.root_path)
                try:
                    rf = RepoFile(full_path, rel_path)
                    rf.modified = os.path.getmtime(full_path)
                    self._index_file(rf)
                    self._files[rel_path] = rf
                except Exception as e:
                    logger.debug(f"CodeMap: skip {rel_path}: {e}")

    def _index_file(self, rf: RepoFile) -> None:
        """索引单个文件"""
        try:
            with open(rf.path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception:
            return

        rf.size = len(content)
        lines = content.splitlines()
        rf.lines = len(lines)

        # 提取符号
        rf.symbols = self._extract_symbols(content, rf.ext)

        # 提取 imports
        rf.imports = self._extract_imports(content, rf.ext)

    def _extract_symbols(self, content: str, ext: str) -> List[SymbolInfo]:
        """用正则提取符号（通用方法，不依赖 tree-sitter）"""
        symbols = []

        if ext == '.py':
            # class
            for m in re.finditer(r'^class\s+(\w+)\s*(?:\([^)]*\))?\s*:', content, re.MULTILINE):
                parent = self._find_parent_class(content, m.start())
                symbols.append(SymbolInfo(m.group(1), 'class', content[:m.start()].count('\n') + 1, parent))
            # function
            for m in re.finditer(r'^\s*(?:async\s+)?def\s+(\w+)\s*\(', content, re.MULTILINE):
                parent = self._find_parent_symbol(content, m.start())
                # 提取签名
                sig_end = self._find_block_end(content, m.start())
                sig = content[m.start():sig_end].strip().split('\n')[0][:120]
                symbols.append(SymbolInfo(m.group(1), 'function' if not parent else 'method',
                                          content[:m.start()].count('\n') + 1, parent, sig))
            # 顶级变量 / 常量
            for m in re.finditer(r'^([A-Z_][A-Z0-9_]*)\s*=', content, re.MULTILINE):
                if content[:m.start()].count('\n') < 3 or not content[m.start()-3:m.start()].strip():
                    symbols.append(SymbolInfo(m.group(1), 'const', content[:m.start()].count('\n') + 1))

        elif ext in ('.js', '.ts', '.jsx', '.tsx'):
            for m in re.finditer(r'(?:export\s+)?(?:class|function|const|let|var|interface|type|enum)\s+(\w+)', content):
                kw = m.group(0).split()[0] if not m.group(0).startswith('export') else m.group(0).split()[1]
                kind_map = {'class': 'class', 'function': 'function', 'const': 'const',
                            'let': 'variable', 'var': 'variable', 'interface': 'interface',
                            'type': 'type', 'enum': 'enum'}
                kind = kind_map.get(kw, 'symbol')
                symbols.append(SymbolInfo(m.group(1), kind, content[:m.start()].count('\n') + 1))

        elif ext in ('.go',):
            for m in re.finditer(r'^\s*type\s+(\w+)\s+(struct|interface)\b', content, re.MULTILINE):
                symbols.append(SymbolInfo(m.group(1), m.group(2), content[:m.start()].count('\n') + 1))
            for m in re.finditer(r'^\s*func\s+(?:\w+\.)?(\w+)\s*\(', content, re.MULTILINE):
                symbols.append(SymbolInfo(m.group(1), 'function', content[:m.start()].count('\n') + 1))

        elif ext in ('.rs',):
            for m in re.finditer(r'^\s*(?:pub\s+)?(?:struct|enum|trait|fn|const|type)\s+(\w+)', content, re.MULTILINE):
                kw = m.group(0).strip().split()[-2]
                kind_map = {'struct': 'struct', 'enum': 'enum', 'trait': 'trait',
                            'fn': 'function', 'const': 'const', 'type': 'type'}
                kind = kind_map.get(kw, 'symbol')
                symbols.append(SymbolInfo(m.group(1), kind, content[:m.start()].count('\n') + 1))

        return symbols

    def _extract_imports(self, content: str, ext: str) -> List[str]:
        """提取导入语句"""
        pattern = self._import_patterns.get(ext)
        if not pattern:
            return []
        imports = []
        for m in pattern.finditer(content):
            for g in m.groups():
                if g:
                    # 处理多导入: import os, sys
                    for part in re.split(r'[,\s]+', g.strip()):
                        part = part.strip().strip(',').strip("'\"")
                        if part and not part.startswith('.'):
                            imports.append(part)
        return imports

    def _find_parent_class(self, content: str, pos: int) -> str:
        """查找最近的父类"""
        before = content[:pos]
        for m in re.finditer(r'^class\s+(\w+)', before, re.MULTILINE):
            parent = m.group(1)
        return ''

    def _find_parent_symbol(self, content: str, pos: int) -> str:
        """查找所在的父符号"""
        before = content[:pos]
        lines = before.splitlines()
        nesting = 0
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if stripped.startswith('class ') or stripped.startswith('def ') or stripped.startswith('async def '):
                if nesting <= 1:
                    m = re.match(r'^(?:class|(?:async\s+)?def)\s+(\w+)', stripped)
                    if m:
                        return m.group(1)
                nesting -= 1
            elif stripped.endswith(':') and not stripped.startswith(('#', '"', "'")):
                nesting += 1
        return ''

    def _find_block_end(self, content: str, start: int) -> int:
        """查找代码块结束位置"""
        lines = content[start:].splitlines()
        if not lines:
            return start + 100
        indent = len(lines[0]) - len(lines[0].lstrip())
        for i, line in enumerate(lines[1:], 1):
            if line.strip() and len(line) - len(line.lstrip()) <= indent and not line.strip().startswith(('#', ')', ']', '}')):
                return start + sum(len(l) + 1 for l in lines[:i])
        return len(content)

    def _resolve_import(self, imp: str, rf: RepoFile) -> Optional[str]:
        """将 import 解析为相对路径"""
        # 简单解析：将 dot path 转为文件路径
        parts = imp.replace('-', '_').split('.')
        # 尝试多个可能路径
        base_dir = os.path.dirname(rf.path) if rf.path else self.root_path
        candidates = []

        # 同级
        p = os.path.join(base_dir, *parts) + '.py'
        candidates.append(p)
        p = os.path.join(base_dir, *parts, '__init__.py')
        candidates.append(p)

        # 从项目根
        p = os.path.join(self.root_path, *parts) + '.py'
        candidates.append(p)
        p = os.path.join(self.root_path, *parts, '__init__.py')
        candidates.append(p)

        for c in candidates:
            normalized = os.path.normpath(c)
            if os.path.isfile(normalized):
                return os.path.relpath(normalized, self.root_path)
        return None

    def _build_map(self, max_tokens: int) -> str:
        """构建紧凑地图"""
        parts = []
        parts.append(f"# Repository Map ({self.stats['files']} files, {self.stats['lines']} lines)\n")

        # 1. 文件树（紧凑形式）
        parts.append("## File Tree\n")
        parts.append(self._build_compact_tree())
        parts.append('\n')

        if max_tokens > 500:
            # 2. 关键符号摘要（按文件列出顶级符号）
            parts.append("## Symbols\n")
            total_syms = 0
            for rel_path in sorted(self._files.keys()):
                rf = self._files[rel_path]
                if not rf.symbols:
                    continue
                sym_line = ', '.join(
                    f"{s.kind[0]}:{s.name}" for s in rf.symbols[:8]
                    if s.kind in ('class', 'function', 'interface', 'struct', 'enum', 'trait')
                )
                if sym_line:
                    parts.append(f"  {rel_path}: [{sym_line}]\n")
                    total_syms += 1
                    if total_syms > 60:
                        parts.append("  ... (more symbols)\n")
                        break

        # token 估算
        result = ''.join(parts)
        estimated_tokens = self._estimate_tokens(result)

        # 如果超出预算，只保留文件树
        if estimated_tokens > max_tokens:
            parts = [f"# Repository Map ({self.stats['files']} files, {self.stats['lines']} lines)\n\n## File Tree\n"]
            parts.append(self._build_compact_tree())
            parts.append('\n')
            result = ''.join(parts)

        return result

    def _build_compact_tree(self) -> str:
        """构建紧凑文件树"""
        tree: Dict[str, dict] = {}
        for rel_path in sorted(self._files.keys()):
            parts = rel_path.replace('\\', '/').split('/')
            current = tree
            for p in parts:
                if p not in current:
                    current[p] = {}
                current = current[p]

        lines = []
        self._render_tree(tree, lines, '')
        return ''.join(lines)

    def _render_tree(self, tree: dict, lines: list, prefix: str) -> None:
        """递归渲染树"""
        items = sorted(tree.items())
        for i, (name, subtree) in enumerate(items):
            is_last = i == len(items) - 1
            connector = '└── ' if is_last else '├── '
            ext = os.path.splitext(name)[1]
            # 给目录加 /
            display = name + '/' if subtree else name
            # 添加文件行数
            if not subtree:
                rel = name  # 查找 rel_path
                for k, v in self._files.items():
                    if k.endswith(name):
                        rel = k
                        if v.lines:
                            display += f'  ({v.lines}L)'
                        break
            lines.append(f"{prefix}{connector}{display}\n")
            if subtree:
                extension = '    ' if is_last else '│   '
                self._render_tree(subtree, lines, prefix + extension)

    def _build_file_tree(self, directory: str, prefix: str = '') -> str:
        """构建标准文件树"""
        lines = []
        entries = sorted(os.listdir(directory))
        entries = [e for e in entries if not e.startswith('.') or e == '.lingshu']
        for i, entry in enumerate(entries):
            full = os.path.join(directory, entry)
            is_last = i == len(entries) - 1
            connector = '└── ' if is_last else '├── '
            if os.path.isdir(full) and os.path.basename(full) not in self.IGNORE_DIRS:
                lines.append(f"{prefix}{connector}{entry}/\n")
                ext = '    ' if is_last else '│   '
                lines.append(self._build_file_tree(full, prefix + ext))
            elif os.path.isfile(full) and os.path.splitext(entry)[1] in self.CODE_EXT:
                lines.append(f"{prefix}{connector}{entry}\n")
        return ''.join(lines)

    def _estimate_tokens(self, text: str) -> int:
        """粗略 token 估算"""
        # CJK ≈ 1.8 token/字, 英文 ≈ 0.35 token/字符
        cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f')
        ascii_chars = sum(1 for c in text if c.isascii() and c.isprintable())
        return int(cjk * 1.8 + ascii_chars * 0.35 + len(text.splitlines()) * 0.3)


async def build_repo_map(root_path: str, max_tokens: int = 2000) -> str:
    """快捷函数：构建仓库地图"""
    cm = CodeMap(root_path, max_tokens=max_tokens)
    cm.build()
    return cm.generate_map()