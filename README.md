# AetherCore/README.md

# AetherCore

AetherCore 是一个独立于业务平台的通用 Agent Runtime 平台。
当前仓库包含三个子项目：

- `backend`：独立后端服务，负责会话、宿主注入、文件与技能管理、运行时编排、沙箱执行。
- `frontend`：独立工作台前端，面向用户呈现 Agent 对话、工具调用、思考过程、文件与技能。
- `host-adapters/dash`：面向 `ascend-compete-dash` 的宿主接入适配层。

## 设计原则

- 与宿主平台解耦：平台通过宿主注入协议传递上下文、工具、技能与 API 描述。
- 执行强隔离：文件、技能与脚本执行都必须落在会话沙箱目录与容器沙箱中。
- 协议优先：前后端、宿主与运行时之间统一走结构化协议，便于扩展与治理。
- 失败关闭：容器沙箱不可用时直接失败，不能回退到宿主机直跑。

## 沙箱说明

- 默认执行器为 `docker`，配置项位于 `backend/.env`。
- 需要预先构建专用镜像：`docker build -t aethercore-sandbox:latest -f docker/sandbox/Dockerfile .`
- 容器执行默认开启：
  - 只挂载会话沙箱目录
  - 只读根文件系统
  - `--network none`
  - 非 root 用户
  - CPU / 内存 / PIDs 限制
- `local` 执行器仅供开发排障使用，且必须显式开启 `SANDBOX_LOCAL_ENABLED=true`。

## 当前阶段

当前版本已完成首轮工程化骨架，并具备以下能力：

- 独立后端服务与 `/api/v1` 路由结构
- 宿主注册与会话绑定协议
- 会话工作区、文件上传、产物下载与技能注入
- 基于模型原生工具调用的运行时循环
- 工作台聊天界面与工具/思考过程展示
- 基于 Docker 的受限执行沙箱
