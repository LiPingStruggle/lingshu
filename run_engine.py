#!/usr/bin/env python3
"""Run engine for 10 hours directly"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import asyncio
from core.agent_chain import AgentChain, Agent
from core.pipeline import Pipeline
from core.model_router import ModelRouter
from core.error_recovery import ErrorRecovery
from core.resource_monitor import ResourceMonitor
from core.inverse_verifier import InverseVerifier, InverseVerifierConfig
from core.engine import Engine

async def main():
    mr = ModelRouter(); mr.register_default_models()
    rm = ResourceMonitor(max_concurrent=5)
    er = ErrorRecovery(model_router=mr)
    ac = AgentChain()
    ac.register_agents(
        light=Agent('light-executor', 'light', 'editor/executor'),
        elite=Agent('elite-analyzer', 'elite', 'analyst/architect'),
        strong=Agent('strong-reviewer', 'strong', 'reviewer/approver'),
    )
    pl = Pipeline(agent_chain=ac, db_path='lingshu.db')
    # 初始化反向验证器（信任持久化到 .lingshu/trust.db）
    vc = InverseVerifierConfig(
        enabled=True, default_intensity='medium',
        consecutive_pass_required=10,
        trust_db_path=os.path.join(os.path.dirname(os.path.abspath('lingshu.db')) or '.', '.lingshu', 'trust.db'),
    )
    iv = InverseVerifier(agent_chain=ac, config=vc)
    pl.inverse_verifier = iv
    eng = Engine(pipeline=pl, agent_chain=ac, error_recovery=er, resource_monitor=rm, inverse_verifier=iv, tasks_dir='tasks', max_duration_hours=10)
    eng.on_task_complete(lambda t: print(f'DONE: {t.task_id}'))
    eng.on_task_fail(lambda tid: print(f'FAILED: {tid}'))
    stats = await eng.start()
    print(f'FINAL STATS: completed={stats["completed"]}, failed={stats["failed"]}, loop={stats["loop_count"]}')
    trust_stats = iv.trust_statistics
    print(f'TRUST: trusted={trust_stats["trusted"]}, consecutive={trust_stats["consecutive_pass"]}/{trust_stats["required"]}, pass_rate={trust_stats["pass_rate"]}%')
    return stats

if __name__ == '__main__':
    asyncio.run(main())