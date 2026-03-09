# PanelLab 生产镜像：前端构建 + 后端运行，单镜像托管
# 构建：docker build -t panellab .
# 运行需挂载 .env 或传入 MYSQL_*、SECRET_KEY 等，并先执行 init_db（见 docs/deploy/deploy.md）

# 阶段 1：构建前端
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 阶段 2：后端 + 静态
FROM python:3.11-slim
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=frontend /app/frontend/dist ./static
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
