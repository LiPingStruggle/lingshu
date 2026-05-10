"""
CostAwareRouter - 成本感知模型路由

增强 ModelRouter，加入成本感知路由逻辑：
- 根据任务复杂度、预算限制和当前成本，选择性价比最优模型
- 自动降级到低成本模型当预算超限
- 成本预测 + 预算控制
"""
from __future__ import annotations
import logging
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass, field
from enum import Enum
from core.model_router import ModelRouter, ModelConfig, ROLE_MIN_TIER, TIER_NAMES
from core.cost_tracker import CostTracker

logger = logging.getLogger(__name__)


class BudgetTier(Enum):
    SAVINGS = "savings"       # 极低成本模式
    STANDARD = "standard"     # 标准成本
    PERFORMANCE = "performance"  # 性能优先
    UNLIMITED = "unlimited"   # 不计成本


@dataclass
class ModelCostProfile:
    """模型成本画像"""
    model: str
    provider: str
    tier: int
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    avg_latency_ms: float = 0.0
    reliability_score: float = 1.0  # 0~1
    capability_score: float = 1.0   # 0~1 (代码/推理/理解能力)

    @property
    def cost_effectiveness(self) -> float:
        """性价比分 = 能力 / (成本 + epsilon)"""
        avg_cost = (self.cost_per_1k_input + self.cost_per_1k_output) / 2
        if avg_cost <= 0:
            return 10.0
        return round(self.capability_score / avg_cost, 4)


@dataclass
class TaskComplexity:
    """任务复杂度评估"""
    estimated_tokens: int = 0
    technical_depth: int = 3  # 1~5
    context_files: int = 1
    requires_reasoning: bool = True
    has_ambiguity: bool = False
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0


@dataclass
class RoutingDecision:
    """路由决策记录"""
    task_type: str = ''
    selected_model: str = ''
    budget_tier: BudgetTier = BudgetTier.STANDARD
    estimated_cost: float = 0.0
    reason: str = ''
    fallback_chain: List[str] = field(default_factory=list)


DEFAULT_COST_PROFILES: Dict[str, tuple] = {
    'claude-sonnet-4': (15.0, 75.0, 3500, 0.95, 0.98),
    'gpt-4.1': (10.0, 40.0, 2000, 0.97, 0.95),
    'deepseek-chat': (0.50, 2.0, 3000, 0.93, 0.90),
    'gpt-4o-mini': (0.15, 0.60, 800, 0.98, 0.85),
    'gemini-2.0-flash': (0.10, 0.40, 1500, 0.94, 0.88),
    'qwen2': (0.0, 0.0, 5000, 0.85, 0.70),
    'mock-model': (0.0, 0.0, 10, 1.0, 0.10),
}


class CostAwareRouter:
    """成本感知模型路由"""

    # 各预算等级的成本乘数（相对于标准）
    BUDGET_MULTIPLIERS = {
        BudgetTier.SAVINGS: 0.3,
        BudgetTier.STANDARD: 1.0,
        BudgetTier.PERFORMANCE: 2.5,
        BudgetTier.UNLIMITED: 10.0,
    }

    def __init__(
        self,
        model_router: ModelRouter,
        cost_tracker: CostTracker,
        daily_budget_usd: float = 5.0,
        default_budget_tier: BudgetTier = BudgetTier.STANDARD,
    ):
        self._router = model_router
        self._tracker = cost_tracker
        self._daily_budget = daily_budget_usd
        self._budget_tier = default_budget_tier
        self._profiles: Dict[str, ModelCostProfile] = {}
        self._decisions: List[RoutingDecision] = []
        self._budget_reset_day = 0
        self._init_profiles()

    def _init_profiles(self) -> None:
        """初始化成本画像"""
        for name, (inp, out, lat, rel, cap) in DEFAULT_COST_PROFILES.items():
            config = self._router.get_model_config(name)
            tier = config.tier if config else 0
            self._profiles[name] = ModelCostProfile(
                model=name,
                provider=config.provider if config else 'unknown',
                tier=tier,
                cost_per_1k_input=inp,
                cost_per_1k_output=out,
                avg_latency_ms=lat,
                reliability_score=rel,
                capability_score=cap,
            )

    def set_budget(self, daily_usd: float) -> None:
        self._daily_budget = daily_usd
        logger.info(f"CostAwareRouter: daily budget set to ${daily_usd}")

    def set_budget_tier(self, tier: BudgetTier) -> None:
        self._budget_tier = tier
        logger.info(f"CostAwareRouter: budget tier set to {tier.value}")

    def select_model(
        self,
        role: str,
        task_type: str = 'generic',
        complexity: Optional[TaskComplexity] = None,
        preferred_model: Optional[str] = None,
    ) -> RoutingDecision:
        """成本感知模型选择"""
        decision = RoutingDecision(task_type=task_type)
        complexity = complexity or TaskComplexity()

        # 1. 如果指定了首选模型，直接用
        if preferred_model and preferred_model in self._profiles:
            decision.selected_model = preferred_model
            decision.reason = 'user_preferred'
            self._record_decision(decision)
            return decision

        # 2. 计算当前已消费
        today_spent = self._tracker.total_cost()
        budget_remaining = self._daily_budget - today_spent
        budget_mult = self.BUDGET_MULTIPLIERS.get(self._budget_tier, 1.0)

        # 3. 根据 budget 决定能否选更高 tier
        affordable_cost = (budget_remaining / (self._daily_budget or 1)) * budget_mult
        can_afford_high = affordable_cost > 0.3  # >30% 预算剩余才能用高级模型

        # 4. 候选模型列表
        min_tier = ROLE_MIN_TIER.get(role, 2)
        candidates = self._get_candidates(min_tier, can_afford_high, complexity)

        if not candidates:
            # fallback：用标准路由
            fallback_model = self._router.select_model(role, task_type)
            if fallback_model:
                decision.selected_model = fallback_model
                decision.reason = 'no_cost_optimal_fallback'
                decision.budget_tier = self._budget_tier
            else:
                decision.reason = 'no_model_available'
            self._record_decision(decision)
            return decision

        # 5. 按性价比排序
        sorted_candidates = sorted(
            candidates,
            key=lambda p: (p.cost_effectiveness * p.reliability_score),
            reverse=True,
        )

        best = sorted_candidates[0]
        decision.selected_model = best.model
        decision.budget_tier = self._budget_tier

        # 估算成本
        est_input = complexity.estimated_tokens * 0.75 or 2000
        est_output = complexity.estimated_tokens * 0.25 or 500
        decision.estimated_cost = round(
            (est_input / 1000) * best.cost_per_1k_input +
            (est_output / 1000) * best.cost_per_1k_output,
            6,
        )

        # fallback 链
        for p in sorted_candidates[1:3]:
            decision.fallback_chain.append(p.model)

        decision.reason = (
            f"tier={TIER_NAMES.get(best.tier, '?')}, "
            f"cost_eff={best.cost_effectiveness}, "
            f"budget_tier={self._budget_tier.value}"
        )

        self._record_decision(decision)
        return decision

    def get_profile(self, model: str) -> Optional[ModelCostProfile]:
        return self._profiles.get(model)

    def register_profile(self, profile: ModelCostProfile) -> None:
        self._profiles[profile.model] = profile

    def summary(self) -> dict:
        """路由摘要"""
        model_usage: Dict[str, int] = {}
        for d in self._decisions:
            model_usage[d.selected_model] = model_usage.get(d.selected_model, 0) + 1

        return {
            'total_routes': len(self._decisions),
            'daily_budget_usd': self._daily_budget,
            'spent_today_usd': round(self._tracker.total_cost(), 4),
            'budget_tier': self._budget_tier.value,
            'model_usage': dict(sorted(model_usage.items(), key=lambda x: -x[1])),
        }

    # ── 内部 ──

    def _get_candidates(
        self,
        min_tier: int,
        can_afford_high: bool,
        complexity: TaskComplexity,
    ) -> List[ModelCostProfile]:
        """获取候选模型"""
        candidates = []
        for name, profile in self._profiles.items():
            if profile.tier < min_tier:
                continue
            if not can_afford_high and profile.tier >= 4:
                if complexity.technical_depth < 4:
                    continue  # 预算不足且复杂度不够，跳过高级模型
            candidates.append(profile)
        return candidates

    def _record_decision(self, decision: RoutingDecision) -> None:
        self._decisions.append(decision)
        logger.debug(f"CostAwareRouter: {decision.selected_model} for {decision.task_type} ({decision.reason})")


async def auto_select_model(
    router: CostAwareRouter,
    task_type: str,
    description: str,
    estimated_tokens: int = 0,
) -> str:
    """快捷函数：自动选择最优模型"""
    role_map = {
        'bug_fix': 'root_cause',
        'refactor': 'architect',
        'test_generation': 'analyzer',
        'code_review': 'reviewer',
        'generate': 'worker',
        'edit': 'editor',
        'question': 'advisor',
    }
    role = role_map.get(task_type, 'worker')
    complexity = TaskComplexity(
        estimated_tokens=estimated_tokens,
        technical_depth=4 if task_type in ('bug_fix', 'refactor') else 2,
    )
    decision = router.select_model(role=role, task_type=task_type, complexity=complexity)
    return decision.selected_model