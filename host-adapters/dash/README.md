# host-adapters/dash/README.md

# Dash 宿主适配器

该目录用于承接 `ascend-compete-dash` 与 AetherCore 的集成。

## 目标

- 不修改 AetherCore 内核
- Dash 作为宿主平台，通过注入协议向 AetherCore 传递：
  - 用户上下文
  - 页面上下文
  - 宿主工具描述
  - 宿主技能描述
  - 宿主 API 描述

## 当前阶段

当前已提供：

- `src/aethercore-host.js`：面向 Dash 的最小注入桥接骨架

后续将继续补充：

- Dash 页面挂载入口
- 宿主工具清单生成器
- 页面上下文采集器
- 与 AetherCore 工作台的通信桥接
