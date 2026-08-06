# 数据探索镜像：FastAPI(API) + 探索 SPA + 原静态图谱站点
# 构建： docker build -t goty-knowledge-graph .
# 运行： docker run -d -p 8080:8080 --name goty-graph goty-knowledge-graph
# 访问：
#   洞察页（原始数据 + 原始洞察）： http://localhost:8080/
#   探索页（参数化数据挖掘）：     http://localhost:8080/explore/  （需 -e GOTY_ENABLE_EXPLORATION=true）
#   API：                           http://localhost:8080/api/meta
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-linuxarm64

WORKDIR /app

# 国内镜像（构建环境连 pypi.org 不稳定），海外可覆盖：--build-arg PIP_INDEX=https://pypi.org/simple
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 uv（用于按锁文件精确安装依赖）
RUN pip install --no-cache-dir \
    --index-url ${PIP_INDEX} \
    --trusted-host pypi.tuna.tsinghua.edu.cn \
    uv

# 复制依赖声明与锁文件（利用层缓存；运行时必需数据在后续 COPY . 中保留）
COPY pyproject.toml uv.lock requirements.lock.txt ./

# 依据锁文件精确安装【运行时】依赖（--system 不建虚拟环境；不含 dev 组）
RUN uv pip install --system --no-cache \
    --index-url ${PIP_INDEX} \
    -r requirements.lock.txt

# 复制全部源码（api/ 复用 analysis/ml/ 计算模块；data/graph.json 由 .dockerignore 保留）
COPY . .

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=8s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/meta').status==200 else 1)" || exit 1

# 默认只读洞察模式；开启探索 SPA 需 -e GOTY_ENABLE_EXPLORATION=true
CMD ["sh", "-c", "python -m uvicorn api.app:app --host 0.0.0.0 --port 8080"]
