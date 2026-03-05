"""
PanelLab 后端入口 — 阶段 0/1：Hello 面板 + 登录认证
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine, Base
from routers import auth, monitor

# 建表（不插入数据，初始用户请运行 python -m init_db）
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="PanelLab", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(monitor.router)


@app.get("/api/hello")
def api_hello():
    """供前端调用的测试接口，用于验证前后端环境与联通。"""
    return {
        "status": "ok",
        "message": "PanelLab",
        "service": "backend",
    }


@app.get("/api/health")
def api_health():
    """健康检查，可用于部署与监控。"""
    return {"status": "ok"}
