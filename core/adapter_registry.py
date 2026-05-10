#!/usr/bin/env python3
"""
AdapterRegistry - 适配器注册中心

将所有外部适配器统一注册到核心系统：
- LSP/静态分析
- Cursor/OpenCode 接口
- ECC/外部工具接口
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """适配器注册中心"""

    def __init__(self):
        self._adapters: Dict[str, Any] = {}

    def register(self, name: str, adapter: Any) -> None:
        self._adapters[name] = adapter
        logger.info(f"AdapterRegistry: registered '{name}' ({type(adapter).__name__})")

    def get(self, name: str) -> Optional[Any]:
        return self._adapters.get(name)

    def has(self, name: str) -> bool:
        return name in self._adapters

    def list(self) -> List[str]:
        return list(self._adapters.keys())

    def remove(self, name: str) -> None:
        self._adapters.pop(name, None)

    def __getitem__(self, name: str) -> Any:
        adapter = self._adapters.get(name)
        if adapter is None:
            raise KeyError(f"Adapter '{name}' not registered")
        return adapter

    def __contains__(self, name: str) -> bool:
        return name in self._adapters


# 全局单例
_registry = AdapterRegistry()


def get_registry() -> AdapterRegistry:
    return _registry


def init_default_adapters() -> AdapterRegistry:
    """初始化默认适配器"""
    from adapters.lsp_adapter import LSPAdapter
    from adapters.cursor_adapter import CursorAdapter
    from adapters.ecc_adapter import ECCAdapter

    _registry.register('lsp', LSPAdapter())
    _registry.register('cursor', CursorAdapter())
    _registry.register('ecc', ECCAdapter())

    # 别名
    _registry.register('static_analysis', _registry.get('lsp'))
    _registry.register('context', _registry.get('cursor'))
    _registry.register('external_tools', _registry.get('ecc'))

    return _registry