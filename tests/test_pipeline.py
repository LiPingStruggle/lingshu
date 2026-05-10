"""
Pipeline 单元测试
"""
from __future__ import annotations
import os
import tempfile
import pytest
from workflows.task_workflow import Task, TaskStep
from core.pipeline import Pipeline
from core.agent_chain import AgentChain, Agent


@pytest.fixture
def pipeline_with_temp_db():
    """使用临时数据库的 Pipeline"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    chain = AgentChain()
    chain.register_agents(
        light=Agent("test-light", "mock", "executor"),
        elite=Agent("test-elite", "mock", "analyzer"),
        strong=Agent("test-strong", "mock", "reviewer"),
    )
    pl = Pipeline(agent_chain=chain, db_path=tmp.name)
    yield pl
    os.unlink(tmp.name)


@pytest.fixture
def sample_task():
    task = Task(
        task_id="PIPE001",
        description="Pipeline test task",
        type="generic",
    )
    task.steps.append(TaskStep(
        step_id="S001",
        description="Execute step",
        assigned_agent="light",
    ))
    return task


@pytest.mark.asyncio
async def test_add_and_get_task(pipeline_with_temp_db, sample_task):
    """测试添加和获取任务"""
    pipeline_with_temp_db.add_task(sample_task)
    retrieved = pipeline_with_temp_db.get_task("PIPE001")
    assert retrieved is not None
    assert retrieved.task_id == "PIPE001"
    assert retrieved.description == "Pipeline test task"
    assert retrieved.status == "pending"


@pytest.mark.asyncio
async def test_update_task(pipeline_with_temp_db, sample_task):
    """测试更新任务"""
    pipeline_with_temp_db.add_task(sample_task)
    sample_task.status = "done"
    sample_task.result = "All good"
    pipeline_with_temp_db.update_task(sample_task)

    retrieved = pipeline_with_temp_db.get_task("PIPE001")
    assert retrieved.status == "done"
    assert retrieved.result == "All good"


@pytest.mark.asyncio
async def test_get_nonexistent_task(pipeline_with_temp_db):
    """测试获取不存在任务"""
    assert pipeline_with_temp_db.get_task("GHOST") is None


@pytest.mark.asyncio
async def test_list_tasks(pipeline_with_temp_db, sample_task):
    """测试列出任务"""
    pipeline_with_temp_db.add_task(sample_task)
    tasks = pipeline_with_temp_db.list_tasks()
    assert len(tasks) >= 1
    assert any(t["task_id"] == "PIPE001" for t in tasks)


@pytest.mark.asyncio
async def test_list_tasks_by_status(pipeline_with_temp_db, sample_task):
    """测试按状态列出任务"""
    pipeline_with_temp_db.add_task(sample_task)
    pending = pipeline_with_temp_db.list_tasks(status="pending")
    done = pipeline_with_temp_db.list_tasks(status="done")
    assert len(pending) >= 1
    assert len(done) == 0


@pytest.mark.asyncio
async def test_run_task(pipeline_with_temp_db, sample_task):
    """测试执行任务"""
    pipeline_with_temp_db.add_task(sample_task)
    result = await pipeline_with_temp_db.run_task(sample_task)
    assert result.status == "done" or result.status == "failed"


@pytest.mark.asyncio
async def test_checkpoints(pipeline_with_temp_db, sample_task):
    """测试检查点"""
    pipeline_with_temp_db.add_task(sample_task)
    pipeline_with_temp_db.save_checkpoint("PIPE001", "S001", "execute", "done", "ok")
    checkpoints = pipeline_with_temp_db.get_checkpoints("PIPE001")
    assert len(checkpoints) >= 1
    assert checkpoints[0]["step_id"] == "S001"
    assert checkpoints[0]["phase"] == "execute"


@pytest.mark.asyncio
async def test_topological_sort(pipeline_with_temp_db):
    """测试拓扑排序"""
    task_a = Task(task_id="A", description="Task A", dependencies=[])
    task_b = Task(task_id="B", description="Task B", dependencies=["A"])
    task_c = Task(task_id="C", description="Task C", dependencies=["B"])

    sorted_tasks = pipeline_with_temp_db._topological_sort([task_c, task_b, task_a])
    ids = [t.task_id for t in sorted_tasks]
    assert ids.index("A") < ids.index("B") < ids.index("C")


@pytest.mark.asyncio
async def test_resume_task(pipeline_with_temp_db, sample_task):
    """测试任务恢复"""
    pipeline_with_temp_db.add_task(sample_task)
    # 模拟一个 failed 步骤
    sample_task.steps[0].status = "failed"
    pipeline_with_temp_db.update_task(sample_task)

    result = await pipeline_with_temp_db.resume("PIPE001")
    assert result is not None