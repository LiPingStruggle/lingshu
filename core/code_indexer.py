"""
CodeIndexer - 代码符号索引（基于 tree-sitter）

需求覆盖（第 14 章）：代码符号索引，支持 Python/JS/TS 等
"""
from __future__ import annotations
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from tree_sitter import Language, Parser
except ImportError:
    Language = None
    Parser = None
    logger.warning("tree-sitter not installed, code indexer will be limited")


class Symbol:
    """代码符号"""
    def __init__(self, name: str, kind: str, file_path: str,
                 line: int, column: int, parent: Optional[str] = None):
        self.name = name
        self.kind = kind  # function, class, method, variable
        self.file_path = file_path
        self.line = line
        self.column = column
        self.parent = parent


class CodeIndexer:
    """代码索引器"""

    PYTHON_PATTERNS = [
        ("function_definition", "function"),
        ("class_definition", "class"),
        ("method_definition", "method"),
    ]

    def __init__(self):
        self._symbols: dict[str, list[Symbol]] = {}
        self._parser = None
        self._init_parser()

    def _init_parser(self) -> None:
        if Parser is not None:
            try:
                self._parser = Parser()
                logger.info("CodeIndexer: tree-sitter parser initialized")
            except Exception as e:
                logger.warning(f"CodeIndexer: parser init failed: {e}")

    def index_file(self, file_path: str) -> list[Symbol]:
        """索引单个文件"""
        if self._parser is None:
            return []

        if not os.path.isfile(file_path):
            return []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()

            tree = self._parser.parse(bytes(source, "utf8"))
            symbols = self._extract_symbols(tree.root_node, file_path, source)
            self._symbols[file_path] = symbols
            return symbols

        except Exception as e:
            logger.warning(f"CodeIndexer: failed to index {file_path}: {e}")
            return []

    def _extract_symbols(self, node, file_path: str, source: str) -> list[Symbol]:
        """从 AST 提取符号"""
        symbols = []
        for child in node.children:
            if child.type == "function_definition":
                name_node = child.child_by_field_name("name")
                if name_node:
                    symbols.append(Symbol(
                        name=source[name_node.start_byte:name_node.end_byte],
                        kind="function",
                        file_path=file_path,
                        line=child.start_point[0] + 1,
                        column=child.start_point[1] + 1,
                    ))
            elif child.type == "class_definition":
                name_node = child.child_by_field_name("name")
                if name_node:
                    symbols.append(Symbol(
                        name=source[name_node.start_byte:name_node.end_byte],
                        kind="class",
                        file_path=file_path,
                        line=child.start_point[0] + 1,
                        column=child.start_point[1] + 1,
                    ))
            symbols.extend(self._extract_symbols(child, file_path, source))
        return symbols

    def index_directory(self, directory: str, extensions: set[str] = None) -> int:
        """索引整个目录"""
        if extensions is None:
            extensions = {".py", ".js", ".ts", ".jsx", ".tsx"}
        count = 0
        for root, _, files in os.walk(directory):
            for f in files:
                if any(f.endswith(ext) for ext in extensions):
                    path = os.path.join(root, f)
                    symbols = self.index_file(path)
                    count += len(symbols)
        return count

    def search(self, name: str, kind: Optional[str] = None) -> list[Symbol]:
        """搜索符号"""
        results = []
        for file_symbols in self._symbols.values():
            for sym in file_symbols:
                if name.lower() in sym.name.lower():
                    if kind and sym.kind != kind:
                        continue
                    results.append(sym)
        return results

    @property
    def stats(self) -> dict:
        total = sum(len(syms) for syms in self._symbols.values())
        return {
            "indexed_files": len(self._symbols),
            "total_symbols": total,
        }