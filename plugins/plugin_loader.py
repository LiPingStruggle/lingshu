#!/usr/bin/env python3
"""
PluginLoader - 完整插件系统

支持 4 类资源注册:
- agents: 自定义 Agent
- models: 自定义模型配置
- prompts: 自定义提示词模板
- tools: 自定义工具函数

生命周期钩子:
- onLoad: 插件加载时
- onUnload: 插件卸载时
- onRunStart: 执行开始时
- onRunComplete: 执行完成时
- onTaskComplete: 任务完成时
"""
from __future__ import annotations
import importlib
import inspect
import logging
import os
import sys
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class PluginResource:
    """插件注册的资源"""
    def __init__(self, resource_type: str, name: str, value: Any, metadata: dict = None):
        self.resource_type = resource_type  # agent | model | prompt | tool
        self.name = name
        self.value = value
        self.metadata = metadata or {}


class PluginContext:
    """插件上下文"""
    def __init__(self):
        self.resources: dict[str, list[PluginResource]] = {
            "agents": [],
            "models": [],
            "prompts": [],
            "tools": [],
        }
        self._hooks: dict[str, list[Callable]] = {}

    def register_resource(self, resource: PluginResource) -> None:
        rt = resource.resource_type + "s"  # 复数
        if rt in self.resources:
            self.resources[rt].append(resource)
            logger.info(f"Plugin resource registered: {resource.resource_type}/{resource.name}")

    def add_hook(self, event: str, handler: Callable) -> None:
        if event not in self._hooks:
            self._hooks[event] = []
        self._hooks[event].append(handler)

    def trigger_hook(self, event: str, *args, **kwargs) -> None:
        for handler in self._hooks.get(event, []):
            try:
                handler(*args, **kwargs)
            except Exception as e:
                logger.error(f"Hook {event} failed: {e}")


class PluginInfo:
    """插件元信息"""
    def __init__(self, name: str, version: str = "1.0.0",
                 description: str = "", module_path: str = ""):
        self.name = name
        self.version = version
        self.description = description
        self.module_path = module_path
        self.enabled = True


class PluginLoader:
    """插件加载器"""

    def __init__(self, plugin_dirs: list[str] = None):
        self.plugin_dirs = plugin_dirs or ["plugins"]
        self._plugins: dict[str, PluginInfo] = {}
        self._instances: dict[str, Any] = {}
        self._context = PluginContext()

    @property
    def context(self) -> PluginContext:
        return self._context

    def discover_plugins(self) -> list[PluginInfo]:
        """发现可用插件"""
        found = []
        for plugin_dir in self.plugin_dirs:
            if not os.path.isdir(plugin_dir):
                continue
            for f in os.listdir(plugin_dir):
                if f.endswith(".py") and not f.startswith("_"):
                    module_name = f[:-3]
                    info = PluginInfo(
                        name=module_name,
                        module_path=os.path.join(plugin_dir, f),
                    )
                    found.append(info)
        return found

    def load_plugin(self, info: PluginInfo) -> bool:
        """加载单个插件"""
        try:
            # Add plugin dir to path
            plugin_dir = os.path.dirname(info.module_path)
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            # Import module
            module_name = info.name
            if module_name in sys.modules:
                module = importlib.reload(sys.modules[module_name])
            else:
                module = importlib.import_module(module_name)

            # Find plugin class
            for name, cls in inspect.getmembers(module, inspect.isclass):
                if name.lower() == info.name.lower() or (
                    hasattr(cls, "name") and getattr(cls, "name") == info.name
                ):
                    instance = cls()
                    self._register_from_instance(instance)
                    self._instances[info.name] = instance

                    # Update info from class
                    if hasattr(cls, "version"):
                        info.version = getattr(cls, "version")
                    if hasattr(cls, "description"):
                        info.description = getattr(cls, "description", "")

                    info.enabled = True
                    self._plugins[info.name] = info

                    # Trigger onLoad
                    if hasattr(instance, "init"):
                        import asyncio
                        try:
                            asyncio.get_event_loop().run_until_complete(
                                instance.init(self._context)
                            )
                        except RuntimeError:
                            pass

                    self._context.trigger_hook("onLoad", info)
                    logger.info(f"Plugin loaded: {info.name} v{info.version}")
                    return True

            logger.warning(f"No valid plugin class found in {info.module_path}")
            return False

        except Exception as e:
            logger.error(f"Failed to load plugin {info.name}: {e}")
            return False

    def load_all(self) -> int:
        """加载所有发现的插件"""
        count = 0
        for info in self.discover_plugins():
            if self.load_plugin(info):
                count += 1
        return count

    def unload_plugin(self, name: str) -> bool:
        """卸载插件"""
        if name in self._instances:
            instance = self._instances[name]
            if hasattr(instance, "destroy"):
                import asyncio
                try:
                    asyncio.get_event_loop().run_until_complete(instance.destroy())
                except RuntimeError:
                    pass
            del self._instances[name]
        if name in self._plugins:
            self._plugins[name].enabled = False
            self._context.trigger_hook("onUnload", name)
            logger.info(f"Plugin unloaded: {name}")
            return True
        return False

    def _register_from_instance(self, instance: Any) -> None:
        """从插件实例注册资源"""
        # agents
        for agent in getattr(instance, "agents", []):
            self._context.register_resource(PluginResource(
                resource_type="agent", name=agent.get("name", "unknown"), value=agent
            ))
        # models
        for model in getattr(instance, "models", []):
            self._context.register_resource(PluginResource(
                resource_type="model", name=model.get("name", "unknown"), value=model
            ))
        # prompts
        for prompt in getattr(instance, "prompts", []):
            self._context.register_resource(PluginResource(
                resource_type="prompt", name=prompt.get("id", "unknown"), value=prompt
            ))
        # tools
        for tool in getattr(instance, "tools", []):
            self._context.register_resource(PluginResource(
                resource_type="tool", name=tool.get("id", "unknown"), value=tool
            ))
        # hooks
        for hook_name, handler in getattr(instance, "hooks", {}).items():
            self._context.add_hook(hook_name, handler)

    def get_resource(self, resource_type: str, name: str) -> Optional[PluginResource]:
        rt = resource_type + "s"
        for r in self._context.resources.get(rt, []):
            if r.name == name:
                return r
        return None

    def get_resources(self, resource_type: str) -> list[PluginResource]:
        rt = resource_type + "s"
        return self._context.resources.get(rt, [])

    @property
    def stats(self) -> dict:
        return {
            "total_plugins": len(self._plugins),
            "loaded_plugins": [
                {"name": p.name, "version": p.version}
                for p in self._plugins.values() if p.enabled
            ],
            "resources": {
                rt: len(resources)
                for rt, resources in self._context.resources.items()
            },
        }