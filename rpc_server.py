#!/usr/bin/env python3
"""
RPC Server - FastAPI 服务端

提供 HTTP API 接口:
  POST /submit_task    - 提交新任务
  GET  /task_status/:id - 查询任务状态
  POST /resume/:id     - 恢复失败任务
  GET  /list_tasks     - 列出所有任务
  GET  /engine/status  - 引擎运行状态
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from core.agent_chain import AgentChain, Agent
from core.pipeline import Pipeline
from core.model_router import ModelRouter
from core.error_recovery import ErrorRecovery
from core.resource_monitor import ResourceMonitor
from core.engine import Engine
from workflows.task_workflow import Task, TaskStep

logger = logging.getLogger(__name__)

# 全局组件实例
_components = {}
_engine_task: Optional[asyncio.Task] = None


class SubmitTaskRequest(BaseModel):
    task_id: str
    description: str
    type: str = "generic"
    steps: list[dict] = []


class SubmitTaskResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[str] = None
    error: Optional[str] = None


class EngineStatusResponse(BaseModel):
    running: bool
    loop: int = 0
    completed: int = 0
    failed: int = 0
    in_progress: int = 0
    elapsed: str = ""
    remaining: str = ""


def init_components(db_path: str = "lingshu.db", tasks_dir: str = "tasks"):
    """初始化所有子系统"""
    global _components
    model_router = ModelRouter()
    model_router.register_default_models()

    resource_monitor = ResourceMonitor(max_concurrent=5)
    error_recovery = ErrorRecovery(model_router=model_router)
    agent_chain = AgentChain()
    pipeline = Pipeline(agent_chain=agent_chain, db_path=db_path)

    agent_chain.register_agents(
        light=Agent(
            name="light-executor",
            model_type="light",
            role="editor/executor",
            system_prompt="You are a fast, precise executor. Complete code tasks efficiently.",
        ),
        elite=Agent(
            name="elite-analyzer",
            model_type="elite",
            role="analyst/architect",
            system_prompt="You are a senior engineer. Analyze problems deeply and plan solutions.",
        ),
        strong=Agent(
            name="strong-reviewer",
            model_type="strong",
            role="reviewer/approver",
            system_prompt="You are a tech lead. Review work for quality, correctness, and completeness.",
        ),
    )

    engine = Engine(
        pipeline=pipeline,
        agent_chain=agent_chain,
        error_recovery=error_recovery,
        resource_monitor=resource_monitor,
        tasks_dir=tasks_dir,
    )

    _components = {
        "model_router": model_router,
        "resource_monitor": resource_monitor,
        "error_recovery": error_recovery,
        "agent_chain": agent_chain,
        "pipeline": pipeline,
        "engine": engine,
    }
    return _components


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    init_components()
    logger.info("RPC Server started, components initialized")
    yield
    # 关闭引擎
    if _components.get("engine") and _components["engine"].is_running:
        _components["engine"].stop()
    logger.info("RPC Server stopped")


app = FastAPI(
    title="LingShu RPC Server",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/submit_task", response_model=SubmitTaskResponse)
async def submit_task(req: SubmitTaskRequest):
    """提交并执行一个任务"""
    pipeline = _components.get("pipeline")
    if not pipeline:
        raise HTTPException(503, "Server not initialized")

    task = Task(
        task_id=req.task_id,
        description=req.description,
        type=req.type,
    )
    for s in req.steps:
        task.steps.append(TaskStep(
            step_id=s.get("step_id", f"S_{len(task.steps)+1:03d}"),
            description=s.get("description", ""),
            assigned_agent=s.get("assigned_agent", "light"),
        ))

    pipeline.add_task(task)
    result = await pipeline.run_task(task)

    return SubmitTaskResponse(
        task_id=result.task_id,
        status=result.status,
        result=result.result,
        error=result.error,
    )


@app.get("/task_status/{task_id}")
async def task_status(task_id: str):
    """查询任务状态"""
    pipeline = _components.get("pipeline")
    if not pipeline:
        raise HTTPException(503, "Server not initialized")

    task = pipeline.get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id} not found")

    return {
        "task_id": task.task_id,
        "description": task.description,
        "type": task.type,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "steps": [
            {
                "step_id": s.step_id,
                "description": s.description,
                "assigned_agent": s.assigned_agent,
                "status": s.status,
            }
            for s in task.steps
        ],
    }


@app.post("/resume/{task_id}")
async def resume_task(task_id: str):
    """从检查点恢复任务"""
    pipeline = _components.get("pipeline")
    if not pipeline:
        raise HTTPException(503, "Server not initialized")

    result = await pipeline.resume(task_id)
    if not result:
        raise HTTPException(404, f"Task {task_id} not found or cannot resume")

    return {
        "task_id": result.task_id,
        "status": result.status,
        "result": result.result,
        "error": result.error,
    }


@app.get("/list_tasks")
async def list_tasks(status: Optional[str] = None):
    """列出所有任务"""
    pipeline = _components.get("pipeline")
    if not pipeline:
        raise HTTPException(503, "Server not initialized")

    return {"tasks": pipeline.list_tasks(status=status)}


@app.get("/engine/status", response_model=EngineStatusResponse)
async def engine_status():
    """查询引擎运行状态"""
    engine = _components.get("engine")
    if not engine:
        raise HTTPException(503, "Server not initialized")

    progress = engine.progress
    return EngineStatusResponse(
        running=progress["running"],
        loop=progress["loop"],
        completed=progress["completed"],
        failed=progress["failed"],
        in_progress=progress["in_progress"],
        elapsed=progress["elapsed"],
        remaining=progress["remaining"],
    )


@app.post("/engine/start")
async def engine_start(max_hours: int = 10):
    """启动持续运行引擎"""
    global _engine_task
    engine = _components.get("engine")
    if not engine:
        raise HTTPException(503, "Server not initialized")

    if engine.is_running:
        return {"status": "already_running", "progress": engine.progress}

    engine.max_duration = __import__("datetime").timedelta(hours=max_hours)

    async def run_engine():
        try:
            await engine.start()
        except Exception as e:
            logger.error(f"Engine task failed: {e}")

    _engine_task = asyncio.create_task(run_engine())
    return {"status": "started", "max_hours": max_hours}


@app.post("/engine/stop")
async def engine_stop():
    """停止引擎"""
    global _engine_task
    engine = _components.get("engine")
    if not engine:
        raise HTTPException(503, "Server not initialized")

    engine.stop()
    if _engine_task:
        _engine_task.cancel()
        _engine_task = None
    return {"status": "stopped"}


async def start_server(host: str = "127.0.0.1", port: int = 8000, db_path: str = "lingshu.db"):
    """启动服务（供 main.py server 命令调用）"""
    import uvicorn
    init_components(db_path=db_path)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    import uvicorn
    init_components()
    uvicorn.run(app, host="127.0.0.1", port=8000)