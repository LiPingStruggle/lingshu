"""
ModelRouter 单元测试
"""
from __future__ import annotations
import pytest
from core.model_router import ModelRouter


@pytest.fixture
def router():
    r = ModelRouter()
    r.register_default_models()
    return r


def test_register_and_list(router):
    """测试注册和列出模型"""
    models = router.list_models()
    assert len(models) >= 6  # 6 个默认模型
    assert "claude-sonnet-4" in models
    assert "gpt-4.1" in models
    assert "mock-model" in models


def test_select_by_tier(router):
    """测试按层级选择"""
    tier5 = router.list_models(tier=5)
    assert "claude-sonnet-4" in tier5


def test_select_model_by_role(router):
    """测试按角色选择模型"""
    # root_cause 需要 elite (tier 5)
    model = router.select_model("root_cause")
    assert model == "claude-sonnet-4"

    # reviewer 需要 strong (tier 4)
    model = router.select_model("reviewer")
    assert model == "gpt-4.1"

    # worker 需要 light (tier 2)
    model = router.select_model("worker")
    assert model == "gemini-2.0-flash"


def test_get_fallback(router):
    """测试 fallback 链"""
    # light → medium → strong → elite
    fallback = router.get_fallback("gemini-2.0-flash")
    assert fallback is not None
    assert fallback == "gpt-4o-mini"  # medium 比 light 高一级

    # elite 没有更高层级
    fallback = router.get_fallback("claude-sonnet-4")
    assert fallback is None


def test_get_model_config(router):
    """测试获取模型配置"""
    config = router.get_model_config("gpt-4.1")
    assert config is not None
    assert config.provider == "openai"
    assert config.tier == 4


def test_get_invalid_model(router):
    """测试获取不存在的模型"""
    assert router.get_model_config("nonexistent") is None
    assert router.select_model("unknown_role") is not None  # 会 fallback 到最底层


def test_register_custom_model(router):
    """测试注册自定义模型"""
    router.register_model("my-local-model", "ollama", 1, context_window=4096)
    config = router.get_model_config("my-local-model")
    assert config is not None
    assert config.provider == "ollama"