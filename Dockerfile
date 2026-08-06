# 数据探索镜像：FastAPI(API) + 探索 SPA + 原静态图谱站点
# 构建： docker build -t goty-knowledge-graph .
# 运行： docker run -d -p 8080:8080 --name goty-graph goty-knowledge-graph
# 访问：
#   探索 SPA：  http://localhost:8080/
#   原图谱页：  http://localhost:8080/graph/
#   API：       http://localhost:8080/api/meta
FROM python:3.11-slim

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制全部源码（api/ 复用 analysis/ml/ 计算模块）
COPY . .

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=8s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/meta').status==200 else 1)" || exit 1

CMD ["sh", "-c", "python -m uvicorn api.app:app --host 0.0.0.0 --port 8080"]
