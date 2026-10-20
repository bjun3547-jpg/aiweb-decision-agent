# syntax=docker/dockerfile:1
#
# 大模型驱动的智能运筹决策 Agent —— 生产镜像
#
# 构建（在仓库根目录执行，注意最后那个点）：
#     docker build -t aiweb-agent .
#
# 运行（密钥从环境变量注入，绝不写进镜像）：
#     docker run -p 8000:8000 -e DEEPSEEK_API_KEY=sk-xxxx aiweb-agent
#
# 国内网络慢可以换源：
#     docker build -t aiweb-agent --build-arg PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple .

FROM python:3.13-slim

# 国内拉依赖默认走清华源；要用官方源就覆盖 PIP_INDEX
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

# 依赖单独一层：只要 lock 文件不变，改代码不会触发重新安装
COPY backend/requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir -i "${PIP_INDEX}" -r /app/requirements.lock.txt

# 只拷后端（前端是 backend/app/static 下的静态文件，一起进来）
COPY backend /app/backend

# 非 root 运行：容器被攻破时拿不到 root
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

WORKDIR /app/backend

EXPOSE 8000

# 健康检查走 skip_balance=true：避免每 30 秒打一次模型厂商的余额接口（慢且费额度）
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health?skip_balance=true' % os.getenv('PORT','8000'), timeout=4)"

# 用 sh -c 是为了让 ${PORT} 能在运行时展开（云平台通常动态分配端口）
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
