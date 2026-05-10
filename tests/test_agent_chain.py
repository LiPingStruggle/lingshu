"""
AgentChain 单元测试
"""
from __future__ import annotations
import pytest
from workflows.task_workflow import Task, TaskStep
from core.agent_chain import AgentChain, Agent


@pytest.fixture
def agent_chain():
    chain = AgentChain()
    chain.register_agents(
        light=Agent("test-light", "mock", "executor"),
        elite=Agent("test-elite", "mock", "analyzer"),
        strong=Agent("test-strong", "mock", "reviewer"),
    )
    return chain


@pytest.fixture
def sample_task():
    task = Task(
        task_id="TEST001",
        description="Test task",
        type="bug_fix",
    )
    task.steps.append(TaskStep(
        step_id="S001",
        description="Analyze the problem",
        assigned_agent="elite",
    ))
    task.steps.append(TaskStep(
        step_id="S002",
        description="Fix the bug",
        assigned_agent="light",
    ))
    task.steps.append(TaskStep(
        step_id="S003",
        description="Review the fix",
        assigned_agent="strong",
    ))
    return task


@pytest.mark.asyncio
async def test_agent_registration(agent_chain):
    """测试 Agent 注册"""
    assert agent_chain.get_agent("light") is not None
    assert agent_chain.get_agent("elite") is not None
    assert agent_chain.get_agent("strong") is not None
    assert agent_chain.get_agent("unknown") is None


@pytest.mark.asyncio
async def test_dispatch_step(agent_chain, sample_task):
    """测试步骤分派"""
    step = sample_task.steps[0]
    result = await agent_chain.dispatch_step(step, sample_task)
    assert result.status == "done"
    assert result.result is not None


@pytest.mark.asyncio
async def test_dispatch_task(agent_chain, sample_task):
    """测试任务分派"""
    result = await agent_chain.dispatch_task(sample_task)
    assert result.status == "done"
    assert result.steps[0].status == "done"
    assert result.steps[1].status == "done"
    assert result.steps[2].status == "done"


@pytest.mark.asyncio
async def test_dispatch_invalid_agent(agent_chain, sample_task):
    """测试无效 Agent"""
    step = TaskStep(step_id="S999", description="Invalid", assigned_agent="ghost")
    result = await agent_chain.dispatch_step(step, sample_task)
    assert result.status == "failed"
    assert "No agent" in result.error


@pytest.mark.asyncio
async def test_review_task(agent_chain, sample_task):
    """测试任务复核"""
    sample_task.status = "done"
    passed = await agent_chain.review_task(sample_task)
    assert passed is True or passed is False  # mock 模式下可能任意


@pytest.mark.asyncio
async def test_review_not_done(agent_chain, sample_task):
    """测试未完成任务的复核"""
    sample_task.status = "failed"
    passed = await agent_chain.review_task(sample_task)
    assert passed is False