FROM node:22-alpine AS web-builder
WORKDIR /web-frontend
COPY ./web-frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY ./web-frontend ./
RUN npm run build

FROM 115bot:base
LABEL authors="qiqiandfei"

# 设置工作目录
WORKDIR /app

# 复制app下所有文件到/app
ADD ./app .

# 复制构建好的前端静态文件到 FastAPI 托管目录
COPY --from=web-builder /web-frontend/dist /app/web/static

# 设置Python模块搜索路径，包含所有需要的目录
ENV PYTHONPATH="/app:/app/utils:/app/core:/app/handlers:/app/.."

EXPOSE 8115

CMD ["python", "115bot.py"]

