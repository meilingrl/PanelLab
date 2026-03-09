"""
PanelLab 后端入口 — 阶段 0/1：Hello 面板 + 登录认证
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine, Base
from models import MonitorRemoteConfig, Server, SiteConfig, User  # 确保所有模型注册，create_all 会建齐表
from routers import auth, db_admin, monitor, sites, terminal

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
app.include_router(sites.router)
app.include_router(db_admin.router)
app.include_router(terminal.router)


@app.get("/api/health")
def api_health():
    """健康检查，可用于部署与监控。"""
    return {"status": "ok"}


# 生产部署：若存在 static 目录（如 Docker 中拷贝的前端 dist），则托管 SPA
if os.path.isdir(os.path.join(os.path.dirname(__file__), "static")):
    from starlette.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True))
