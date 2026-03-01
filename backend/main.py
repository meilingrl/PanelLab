"""
PanelLab 后端入口 — 阶段 0：Hello 面板与接口测试
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="PanelLab", version="0.1.0")

# 允许前端开发服务器跨域访问（接口测试 / 环境检验）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
