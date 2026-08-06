# 数据探索镜像：FastAPI(API) + 探索 SPA + 原静态图谱站点
# 构建： docker build -t goty-knowledge-graph .
# 运行： docker run -d -p 8080:8080 --name goty-graph goty-knowledge-graph
# 访问：
#   探索 SPA：  http://localhost:8080/
#   原图谱页：  http://localhost:8080/graph/
#   API：       http://localhost:8080/api/meta
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-linuxarm64

WORKDIR /app

# 先装依赖（利用层缓存）
# 注：构建环境连 pypi.org 不稳定（SSL EOF），改用国内镜像并加重试/超时。
# 如在海外构建，可把 PIP_INDEX 传回官方源：docker build --build-arg PIP_INDEX=https://pypi.org/simple .
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --index-url ${PIP_INDEX} \
    --trusted-host pypi.tuna.tsinghua.edu.cn \
    --retries 5 --timeout 60 \
    -r requirements.txt

# 复制全部源码（api/ 复用 analysis/ml/ 计算模块）
COPY . .

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=8s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/meta').status==200 else 1)" || exit 1

CMD ["sh", "-c", "python -m uvicorn api.app:app --host 0.0.0.0 --port 8080"]
