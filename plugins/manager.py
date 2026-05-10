"""
PluginManager - 插件管理器

职责：
- 根据配置加载已启用的插件
- 按阶段（phase）编排插件执行
- 记录每个插件执行前后的日志

配置格式（lingshu.yaml）：
    plugins:
      enabled: true
      phases:
        evidence_collection:
          - trailmark_callgraph
          - codebadger_inspect
        post_execution:
          - diting_verify
"""
from __future__ import annotations
import importlib
import logging
import sys
from typing import Any, Callable

from plugins.base import BasePlugin

logger = logging.getLogger("lingshu.plugins")


class PluginManager:
    """插件管理器"""

    def __init__(self, config: dict) -> None:
        """
        Args:
            config: 完整配置字典（含 plugins 字段）。
        """
        self._config: dict = config.get("plugins", {})
        self._plugins: dict[str, BasePlugin] = {}         # name -> instance
        self._phase_order: dict[str, list[str]] = {}      # phase -> [plugin_name]
        self._global_enabled: bool = self._config.get("enabled", True)

    # ── 加载 ──────────────────────────────────────────────

    async def load_plugins(self) -> int:
        """根据配置，动态导入并实例化所有启用的插件。

        Returns:
            成功加载的插件数量。
        """
        if not self._global_enabled:
            logger.info("PluginManager: plugins globally disabled via config")
            return 0

        phase_config = self._config.get("phases", {})
        count = 0

        for phase_name, plugin_names in phase_config.items():
            if not isinstance(plugin_names, list):
                continue

            for plugin_name in plugin_names:
                if not isinstance(plugin_name, str) or not plugin_name.strip():
                    continue

                plugin_name = plugin_name.strip()
                if plugin_name in self._plugins:
                    # 已在其他阶段加载过，只需记录阶段映射
                    self._phase_order.setdefault(phase_name, []).append(plugin_name)
                    continue

                instance = await self._load_single_plugin(plugin_name)
                if instance is None:
                    continue

                # 检查该插件的配置是否启用
                plugin_cfg = self._config.get(instance.name, {})
                if isinstance(plugin_cfg, dict) and plugin_cfg.get("enabled") is False:
                    logger.debug(
                        "PluginManager: %s disabled by config, skipped", instance.name
                    )
                    continue

                self._plugins[plugin_name] = instance
                self._phase_order.setdefault(phase_name, []).append(plugin_name)
                count += 1

        logger.info(
            "PluginManager: loaded %d plugin(s) across %d phase(s)",
            count,
            len(self._phase_order),
        )
        return count

    async def _load_single_plugin(self, name: str) -> BasePlugin | None:
        """尝试导入并实例化单个插件。

        查找策略：
          1. 按 name 在 plugins.xxx 中找同名模块下的 Plugin 类
          2. 若找不到，尝试导入 plugins.{name}.{name.capitalize()}Plugin
        """
        # 策略 1: 从 plugins.{name} 找 class Plugin
        module = None
        candidates = [
            f"plugins.{name}",
            f"plugins.{name}." + name.capitalize() + "Plugin",
            f"plugins.{name}.Plugin",
        ]

        for mod_path in candidates:
            try:
                module = importlib.import_module(mod_path)
                break
            except ImportError:
                continue

        if module is None:
            logger.warning("PluginManager: plugin %r not found (tried plugins.%s)", name, name)
            return None

        # 2. 在模块中查找 BasePlugin 子类
        instance = None
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BasePlugin)
                and obj is not BasePlugin
            ):
                try:
                    instance = obj()
                    logger.debug(
                        "PluginManager: instantiated %s.%s", module.__name__, attr_name
                    )
                    break
                except Exception as e:
                    logger.error(
                        "PluginManager: failed to instantiate %s.%s: %s",
                        module.__name__, attr_name, e,
                    )
                    return None

        if instance is None:
            logger.warning(
                "PluginManager: no BasePlugin subclass found in %s", module.__name__
            )
            return None

        # 3. 调用 init 钩子
        try:
            await instance.init()
        except Exception as e:
            logger.error("PluginManager: %s.init() failed: %s", name, e)

        return instance

    # ── 执行 ──────────────────────────────────────────────

    async def execute_phase(self, phase: str, state: dict) -> dict:
        """执行某个阶段的所有已启用插件。

        插件按注册顺序依次执行，每个的结果会传递到下一个插件。
        如果某个插件的 validate() 返回 False，则跳过该插件。

        Args:
            phase: 阶段名称，如 "evidence_collection"。
            state: 初始状态字典。

        Returns:
            所有插件执行完毕后的最终状态字典。
        """
        if not self._global_enabled:
            return state

        current = dict(state)
        plugin_names = self._phase_order.get(phase, [])

        logger.debug("PluginManager: executing phase %r (%d plugin(s))", phase, len(plugin_names))

        for plug_name in plugin_names:
            plug = self._plugins.get(plug_name)
            if plug is None:
                continue
            if not plug.enabled:
                logger.debug("PluginManager: %s disabled, skipped", plug_name)
                continue

            # 验证
            try:
                valid = await plug.validate()
            except Exception:
                logger.exception("PluginManager: %s.validate() crashed", plug_name)
                continue

            if not valid:
                logger.warning("PluginManager: %s validate() returned False, skipped", plug_name)
                current.setdefault("plugin_skipped", []).append(plug_name)
                continue

            # 执行
            logger.info("PluginManager: running plugin %s (phase=%s)", plug_name, phase)
            try:
                current = await plug.run(current)
                current.setdefault("plugin_results", {})[plug_name] = "ok"
                logger.debug("PluginManager: plugin %s completed", plug_name)
            except Exception:
                logger.exception("PluginManager: plugin %s crashed during run()", plug_name)
                current.setdefault("plugin_errors", {})[plug_name] = {
                    "phase": phase,
                    "error": "run() crashed",
                }

        return current

    # ── 查询 ──────────────────────────────────────────────

    def get_plugin(self, name: str) -> BasePlugin | None:
        return self._plugins.get(name)

    def list_plugins(self) -> list[dict]:
        return [p.to_dict() for p in self._plugins.values()]

    @property
    def stats(self) -> dict:
        return {
            "total": len(self._plugins),
            "phases": {p: len(ns) for p, ns in self._phase_order.items()},
            "global_enabled": self._global_enabled,
        }


# ── 便捷函数 ──────────────────────────────────────────────

async def run_phase(config: dict, phase: str, state: dict) -> dict:
    """单次快捷调用：加载插件 → 执行指定阶段。

    Args:
        config: 完整配置字典。
        phase: 阶段名称。
        state: 初始状态。

    Returns:
        执行后的状态。
    """
    mgr = PluginManager(config)
    await mgr.load_plugins()
    return await mgr.execute_phase(phase, state)