# backend/README.md

AetherCore 后端服务。

## 本地启动

1. 配置 `backend/.env`
2. 安装依赖
3. 构建沙箱镜像：

```bash
docker build -t aethercore-sandbox:latest -f docker/sandbox/Dockerfile .
```

4. 启动后端：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

## 沙箱要求

- 默认执行器为 `docker`
- 默认 `fail-closed`
- 沙箱不可用时拒绝执行，不允许自动回退到宿主机
- 会话 runtime 默认保留工作区与 `home/cache` 目录，容器重建后文件与 pip 用户安装缓存仍可复用
- 容器命令默认以 `sandbox` 用户在 `/workspace/work` 下执行；`/workspace` 及其常用子目录应保持可写
