ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG DEBIAN_MIRROR=mirrors.aliyun.com
WORKDIR /app

RUN if [ "$DEBIAN_MIRROR" != "deb.debian.org" ]; then \
        sed -i "s|deb.debian.org|$DEBIAN_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" .

EXPOSE 8090
CMD ["virt-report", "--config", "/app/config.yaml", "serve", "--host", "0.0.0.0", "--port", "8090"]
