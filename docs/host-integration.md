<!-- docs/host-integration.md -->

# 宿主集成协议

本文档描述业务平台如何将自身能力注入到 AetherCore。

## 1. 设计目标

- AetherCore 完全独立部署。
- 宿主平台不侵入 AetherCore 内核。
- 宿主平台通过结构化协议动态注入上下文、工具、技能与 API。

## 2. 基本流程

1. 宿主平台创建或恢复用户会话。
2. 宿主平台调用 `POST /api/v1/host/bind`。
3. AetherCore 返回 `session_id`。
4. 前端工作台基于该 `session_id` 进行对话、文件上传、技能浏览与后续任务执行。

## 3. 宿主绑定请求结构

宿主绑定请求由以下部分构成：

- `host_name`
- `host_type`
- `session_id`
- `context`
- `tools`
- `skills`
- `apis`

### 3.1 context

用于注入当前用户与页面上下文，例如：

```json
{
  "user": {
    "id": "u_001",
    "display_name": "张三"
  },
  "page": {
    "title": "训练看板",
    "pathname": "/pages/llm_training.html"
  },
  "extras": {
    "filters": {
      "model": "Qwen"
    }
  }
}
```

### 3.2 tools

宿主工具只传递描述与调用端点，不直接把宿主代码暴露给 AetherCore。

### 3.3 skills

宿主技能可以是业务领域提示词、流程约束或能力声明，供 AetherCore 动态合并。

### 3.4 apis

宿主 API 描述用于后续生成统一的 Host API Adapter。

## 4. 文件策略

- 用户文件先上传至 AetherCore。
- AetherCore 将文件放入会话沙箱工作区的 `input/`。
- 产物统一生成到 `output/` 并由 AetherCore 提供下载。
- 宿主平台只需要负责触发上传与展示下载入口。

## 5. 后续演进

后续将继续补充：

- Host Tool Proxy 执行协议
- 宿主签名与鉴权
- 技能上传与校验协议
- 沙箱任务状态回调协议
