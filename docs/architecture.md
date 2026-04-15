<!-- docs/architecture.md -->

# AetherCore 架构概览

## 1. 总体边界

AetherCore 是独立于业务平台的 Agent Runtime 平台。

业务平台只负责：

- 用户入口
- 页面上下文
- 宿主工具描述
- 宿主技能描述
- 宿主 API 描述

AetherCore 负责：

- 会话生命周期
- LLM 编排
- 技能装载
- 工具执行
- 文件上传与产物管理
- 沙箱工作区
- 工作台前端

## 2. 当前目录结构

```text
AetherCore/
  backend/          独立后端
  frontend/         独立工作台前端
  host-adapters/    宿主侧适配器
  docs/             架构与协议文档
```

## 3. 后端分层

### 3.1 api

面向前端与宿主平台的 HTTP 接口。

### 3.2 services

承载会话、文件、产物、技能等应用层能力。

### 3.3 runtime

承载推理与执行循环，是后续接入真实 LLM 和 Tool Executor 的核心区域。

### 3.4 sandbox

负责为每个会话生成隔离工作区，后续将继续接入容器化执行器。

### 3.5 host

负责宿主平台绑定与能力注入。

## 4. 前端结构

当前前端统一为工作台模式，默认包含四块：

- 对话
- 时间线
- 文件
- 技能

后续将继续接入：

- 宿主页面上下文展示
- 沙箱任务状态
- 产物下载卡片
- Tool 与 Skill 可视化轨迹

## 5. 演进方向

下一阶段重点包括：

1. 接入真实 LLM Provider。
2. 建立 Tool Registry 与 Host Tool Proxy。
3. 增强 Skill 元数据与用户上传技能能力。
4. 引入沙箱命令执行器与产物化输出。
5. 让 Dash 宿主适配器真正把工作台嵌入现有平台。
