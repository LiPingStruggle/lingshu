#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
ModelsManager — 多供应商模型配置管理器

从 .env 加载多供应商配置，动态注册到 ModelRouter。

支持供应商:
  - OpenAI (OPENAI_API_KEY)
  - Anthropic (ANTHROPIC_API_KEY)
  - DeepSeek (DEEPSEEK_API_KEY)
  - LongCat (LONGCAT_API_KEY)
  - Google/Gemini (GEMINI_API_KEY)
  - Ollama (本机，无需 key)
"""
from __future__ import annotations
import logging
import os
import re
from typing import Optional, Any

logger = logging.getLogger("lingshu.models")


# 供应商默认端点
PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "longcat": "https://api.longcat.chat/openai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "ollama": "http://localhost:11434/v1",
}

# 默认层级映射（当没有 LINGSHU_MAP_* 时使用）
DEFAULT_TIER_MAP = {
    "elite": ("claude-sonnet-4", "anthropic", 5, {"context_window": 200000}),
    "strong": ("gpt-4.1", "openai", 4, {"context_window": 128000}),
    "medium": ("gpt-4o-mini", "openai", 3, {"context_window": 128000}),
    "light": ("LongCat-Flash-Lite", "longcat", 2, {"context_window": 128000}),
    "local": ("qwen2.5-coder:7b", "ollama", 1, {"context_window": 32768}),
}


class ModelsManager:
    """
    多供应商模型配置管理器

    从 .env 加载 API key / base_url，
    按 LINGSHU_MAP_* 映射注册模型到 ModelRouter。
    """

    def __init__(self, env_path: str = ".env"):
        self._env_path = env_path
        self._providers: dict[str, dict] = {}  # provider -> {api_key, base_url, models}
        self._tier_map: dict[str, tuple] = {}  # tier -> (name, provider, tier_num, kwargs)
        self._loaded = False

    def load_env(self) -> dict[str, dict]:
        """读取 .env 文件，解析所有供应商配置"""
        if self._loaded:
            return self._providers

        # 手动解析 .env
        env_vars = {}
        if os.path.exists(self._env_path):
            with open(self._env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    env_vars[key.strip()] = value.strip()

        # 也读系统环境变量（优先级更高）
        for key in os.environ:
            if any(key.startswith(p) for p in ["OPENAI_", "ANTHROPIC_", "DEEPSEEK_",
                                                "LONGCAT_", "GEMINI_", "OLLAMA_", "LINGSHU_MAP_"]):
                env_vars[key] = os.environ[key]

        # 解析供应商
        providers = {}

        # OpenAI
        if env_vars.get("OPENAI_API_KEY"):
            providers["openai"] = {
                "api_key": env_vars["OPENAI_API_KEY"],
                "base_url": env_vars.get("OPENAI_BASE_URL", PROVIDER_BASE_URLS["openai"]),
            }

        # Anthropic
        if env_vars.get("ANTHROPIC_API_KEY"):
            providers["anthropic"] = {
                "api_key": env_vars["ANTHROPIC_API_KEY"],
                "base_url": env_vars.get("ANTHROPIC_BASE_URL", PROVIDER_BASE_URLS["anthropic"]),
            }

        # DeepSeek
        if env_vars.get("DEEPSEEK_API_KEY"):
            providers["deepseek"] = {
                "api_key": env_vars["DEEPSEEK_API_KEY"],
                "base_url": env_vars.get("DEEPSEEK_BASE_URL", PROVIDER_BASE_URLS["deepseek"]),
            }

        # LongCat
        if env_vars.get("LONGCAT_API_KEY"):
            providers["longcat"] = {
                "api_key": env_vars["LONGCAT_API_KEY"],
                "base_url": env_vars.get("LONGCAT_BASE_URL", PROVIDER_BASE_URLS["longcat"]),
            }

        # Google
        if env_vars.get("GEMINI_API_KEY"):
            providers["google"] = {
                "api_key": env_vars["GEMINI_API_KEY"],
                "base_url": env_vars.get("GEMINI_BASE_URL", PROVIDER_BASE_URLS["google"]),
            }

        # Ollama（总是可用）
        providers["ollama"] = {
            "api_key": "",
            "base_url": env_vars.get("OLLAMA_BASE_URL", PROVIDER_BASE_URLS["ollama"]),
        }

        self._providers = providers

        # 解析层级映射
        self._parse_tier_map(env_vars)

        self._loaded = True
        logger.info(f"ModelsManager: loaded {len(providers)} providers, {len(self._tier_map)} tiers")
        for p in providers:
            logger.info(f"  Provider: {p} -> {providers[p]['base_url']}")

        return providers

    def _parse_tier_map(self, env_vars: dict) -> None:
        """从 LINGSHU_MAP_* 环境变量解析层级映射"""
        tier_map = {}
        for key, value in env_vars.items():
            m = re.match(r"^LINGSHU_MAP_(ELITE|STRONG|MEDIUM|LIGHT|LOCAL)$", key, re.IGNORECASE)
            if m and "@" in value:
                tier_name = m.group(1).lower()
                model_name, provider = value.split("@", 1)
                provider = provider.strip().lower()
                tier_num = {"elite": 5, "strong": 4, "medium": 3, "light": 2, "local": 1}.get(tier_name, 2)
                context_window = 128000
                if provider == "anthropic":
                    context_window = 200000
                elif provider == "ollama":
                    context_window = 32768
                elif provider == "google":
                    context_window = 1000000
                tier_map[tier_name] = (model_name.strip(), provider, tier_num, {"context_window": context_window})

        # 没有自定义映射则使用默认
        for tier, default in DEFAULT_TIER_MAP.items():
            if tier not in tier_map:
                tier_map[tier] = default

        # 验证模型的 provider 是否有 API key
        final_map = {}
        for tier, (name, provider, tier_num, kwargs) in tier_map.items():
            kwargs = dict(kwargs)
            if provider in self._providers:
                kwargs["api_key"] = self._providers[provider]["api_key"]
                kwargs["base_url"] = self._providers[provider]["base_url"]
                final_map[tier] = (name, provider, tier_num, kwargs)
            elif provider == "ollama":
                kwargs["api_key"] = ""
                kwargs["base_url"] = self._providers.get("ollama", {}).get("base_url", "http://localhost:11434/v1")
                final_map[tier] = (name, provider, tier_num, kwargs)
            else:
                logger.warning(f"Tier '{tier}' uses '{provider}' but no API key configured, skipping")

        self._tier_map = final_map

    def register_all(self, router) -> int:
        """将所有已配置模型注册到 ModelRouter"""
        self.load_env()
        count = 0
        for tier_name, (name, provider, tier_num, kwargs) in self._tier_map.items():
            try:
                router.register_model(name, provider, tier_num, **kwargs)
                count += 1
                logger.info(f"Registered [{tier_name}] {name} ({provider}) @ tier={tier_num}")
            except Exception as e:
                logger.error(f"Failed to register {name}: {e}")
        return count

    def get_available_providers(self) -> list[str]:
        """列出已配置了 API key 的供应商"""
        self.load_env()
        return sorted(self._providers.keys())

    def get_tier_mapping(self) -> dict[str, dict]:
        """获取层级映射摘要"""
        self.load_env()
        result = {}
        for tier, (name, provider, tier_num, kwargs) in self._tier_map.items():
            result[tier] = {
                "name": name,
                "provider": provider,
                "tier": tier_num,
                "base_url": kwargs.get("base_url", ""),
                "key_configured": bool(kwargs.get("api_key", "") or provider == "ollama"),
            }
        return result

    def to_summary(self) -> str:
        """可读的模型配置摘要"""
        self.load_env()
        lines = ["=== 模型配置摘要 ==="]
        lines.append(f"\n已检测供应商 ({len(self._providers)}):")
        for p in sorted(self._providers):
            info = self._providers[p]
            lines.append(f"  [{p.upper()}] {info['base_url']}  (key={'✓' if info.get('api_key') else '✗'})")
        lines.append(f"\n层级映射:")
        for tier in ["elite", "strong", "medium", "light", "local"]:
            if tier in self._tier_map:
                name, provider, tn, kw = self._tier_map[tier]
                key_ok = bool(kw.get("api_key", "") or provider == "ollama")
                lines.append(f"  [{tier:7}] {name:30} @ {provider:12} tier={tn}  key={'✓' if key_ok else '✗'}")
        return "\n".join(lines)

    def reload(self) -> int:
        """重新加载配置"""
        self._loaded = False
        self._providers = {}
        self._tier_map = {}
        return self.register_all(router=None) if False else 0  # placeholder

    def get_env_template(self) -> str:
        """生成 .env 模板"""
        return """# ===== LingShu 模型配置 =====
# 取消注释并填入你的 API key 即可启用对应供应商

# OpenAI
# OPENAI_API_KEY=sk-...
# OPENAI_BASE_URL=https://api.openai.com/v1

# Anthropic
# ANTHROPIC_API_KEY=sk-ant-...
# ANTHROPIC_BASE_URL=https://api.anthropic.com/v1

# DeepSeek
# DEEPSEEK_API_KEY=sk-...
# DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# LongCat
LONGCAT_API_KEY=ak_2Y39CK78A6DI6ry4El89o29P6Hb65
LONGCAT_BASE_URL=https://api.longcat.chat/openai/v1
LONGCAT_MODEL=LongCat-Flash-Lite

# Google Gemini
# GEMINI_API_KEY=...
# GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta

# Ollama (本地，无需 key)
# OLLAMA_BASE_URL=http://localhost:11434/v1

# 自定义层级映射（可选）
# 格式: LINGSHU_MAP_<TIER>=<model_name>@<provider>
# LINGSHU_MAP_ELITE=claude-sonnet-4@anthropic
# LINGSHU_MAP_STRONG=gpt-4.1@openai
# LINGSHU_MAP_MEDIUM=gpt-4o-mini@openai
# LINGSHU_MAP_LIGHT=LongCat-Flash-Lite@longcat
# LINGSHU_MAP_LOCAL=qwen2.5-coder:7b@ollama
"""


# ====== 快捷函数 ======


def apply_models_to_router(router, env_path: str = ".env") -> int:
    """快捷函数：从 .env 加载所有模型并注册到 router"""
    mgr = ModelsManager(env_path)
    return mgr.register_all(router)


def print_model_summary(env_path: str = ".env") -> None:
    """打印模型配置摘要"""
    mgr = ModelsManager(env_path)
    print(mgr.to_summary())