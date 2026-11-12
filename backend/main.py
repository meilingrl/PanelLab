"""
PanelLab 后端入口 — 阶段 0/1：Hello 面板 + 登录认证
"""
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from database import engine, Base
from models import MonitorRemoteConfig, Server, SiteConfig, User, Feedback  # 确保所有模型注册，create_all 会建齐表
from routers import auth, db_admin, docs, feedback, monitor, sites, site_changes, terminal, servers, remote, workloads, agent

# 建表（不插入数据，初始用户请运行 python -m init_db）
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield
    from services.remote_connection import reset_pool
    reset_pool()  # close pooled SSH transports on shutdown


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
app.include_router(site_changes.router)
app.include_router(db_admin.router)
app.include_router(terminal.router)
app.include_router(docs.router)
app.include_router(feedback.router)
app.include_router(servers.router)
app.include_router(remote.router)
app.include_router(workloads.router)
app.include_router(agent.router)


@app.get("/api/health")
def api_health():
    """健康检查，可用于部署与监控。"""
    return {"status": "ok"}


# Every top-level path in frontend/src/router/index.js; test_workspace_spa checks they match.
FRONTEND_ROUTES = ('login', 'register', 'workspaces', 'agent', 'monitor', 'sites', 'databases',
                   'terminal', 'manual', 'feedback', 'settings')


def mount_frontend(application: FastAPI, directory: Path):
    def frontend_shell():
        return FileResponse(directory / 'index.html')

    # Vue history routes also need to work when opened from a bookmark.
    for route in FRONTEND_ROUTES:
        for path in ('/' + route, '/' + route + '/'):
            application.add_api_route(path, frontend_shell, methods=['GET', 'HEAD'], include_in_schema=False)
    application.mount('/', StaticFiles(directory=directory, html=True))


static_directory = Path(__file__).with_name('static')
if static_directory.is_dir():
    mount_frontend(app, static_directory)
