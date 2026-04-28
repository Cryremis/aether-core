# docker/sandbox/Dockerfile
FROM mcr.microsoft.com/dotnet/sdk:8.0-jammy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        locales \
        python3 \
        python3-pip \
        ripgrep \
        unzip \
    && rm -rf /var/lib/apt/lists/*

RUN locale-gen C.UTF-8 \
    && useradd --create-home --uid 10001 --shell /bin/bash sandbox \
    && mkdir -p /workspace /tmp/aether \
    && chown -R sandbox:sandbox /workspace /tmp/aether /home/sandbox

ENV HOME=/home/sandbox
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

USER sandbox
WORKDIR /workspace/work
