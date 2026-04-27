# AetherCore 通用接入规范

目标：宿主平台只需要在 AetherCore 注册拿到 `platform_key` 和 `host_secret`，然后复制一段前端脚本和一个后端 bind 代理，即可获得与 POC 接近的浮球、右侧抽屉、加载动画和 iframe 工作台体验。

## 推荐接入形态

统一为两段代码：

1. 前端嵌入壳：负责浮球、抽屉、动画、iframe、会话 key 持久化。复制 `host-adapters/universal/aethercore-embed.js` 到宿主静态资源目录。
2. 后端 bind 代理：负责保存平台密钥，读取当前登录用户，调用 AetherCore `/api/v1/host/bind`，把 `token` 和 `session_id` 返回给前端。

不要在浏览器里直接保存或发送 `host_secret`。密钥只允许存在宿主后端。

## 前端复制代码

在宿主平台的全局布局页、公共模板页或 `</body>` 前加入：

```html
<script src="/static/aethercore-embed.js"></script>
<script>
  window.mountAetherCore({
    platformKey: "your-platform-key",
    bindUrl: "/api/v1/aethercore/embed/bind",
    workbenchUrl: "https://ac.example.com",
    title: "AetherCore",
    subtitle: "嵌入式工作台",
    getUserId: function () {
      return window.currentUser?.id || window.__USER_ID__ || "anonymous";
    }
  });
</script>
```

如果宿主是 Vue/React/SPA，放在主布局组件挂载后执行即可；如果是传统多页应用，放到公共脚本里即可。

## 后端 bind 代理契约

前端只调用宿主自己的接口：

```http
POST /api/v1/aethercore/embed/bind
Content-Type: application/json

{
  "conversation_key": "your-platform-user-stable-conversation-key"
}
```

宿主后端调用 AetherCore：

```http
POST {AETHERCORE_API_BASE_URL}/api/v1/host/bind
X-Aether-Platform-Secret: {host_secret}
Content-Type: application/json
```

最小请求体：

```json
{
  "platform_key": "your-platform-key",
  "host_name": "Your Platform",
  "host_type": "custom",
  "conversation_key": "stable-key-from-frontend",
  "context": {
    "user": {
      "id": "current-user-id",
      "name": "current-user-name"
    },
    "page": {},
    "extras": {
      "host_callback_base_url": "https://your-platform.example.com"
    }
  },
  "tools": [],
  "skills": [],
  "apis": []
}
```

宿主后端返回给前端：

```json
{
  "data": {
    "token": "embed-token-from-aethercore",
    "session_id": "session-id-from-aethercore"
  }
}
```

`aethercore-embed.js` 同时兼容 `{ token, session_id }`、`{ data: { token, session_id } }` 和带 `workbench_url` 的返回。

## FastAPI 后端示例

```python
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import httpx

router = APIRouter()

class AetherCoreBindRequest(BaseModel):
    conversation_key: str | None = None
    conversation_id: str | None = None

@router.post("/api/v1/aethercore/embed/bind")
async def bind_aethercore(payload: AetherCoreBindRequest, request: Request):
    user = request.state.user
    body = {
        "platform_key": settings.AETHERCORE_PLATFORM_KEY,
        "host_name": settings.AETHERCORE_HOST_NAME,
        "host_type": "custom",
        "conversation_key": payload.conversation_key,
        "conversation_id": payload.conversation_id,
        "context": {
            "user": {"id": str(user.id), "name": user.name},
            "page": {},
            "extras": {"host_callback_base_url": settings.AETHERCORE_HOST_CALLBACK_BASE_URL},
        },
        "tools": [],
        "skills": [],
        "apis": [],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            f"{settings.AETHERCORE_API_BASE_URL.rstrip('/')}/api/v1/host/bind",
            headers={"X-Aether-Platform-Secret": settings.AETHERCORE_PLATFORM_SECRET},
            json=body,
        )
    if res.status_code >= 400:
        raise HTTPException(status_code=res.status_code, detail=res.text)
    data = res.json()["data"]
    return {"data": {"token": data["token"], "session_id": data["session_id"]}}
```

## 工具注入规范

宿主如果希望 AetherCore 调宿主工具，在 `tools` 里声明工具：

```json
{
  "name": "search_projects",
  "description": "搜索当前用户可见项目",
  "endpoint": "/api/v1/agent/tools/search_projects/invoke",
  "method": "POST",
  "input_schema": {
    "type": "object",
    "properties": { "query": { "type": "string" } },
    "required": ["query"]
  },
  "requires_auth": true,
  "auth_inject": true
}
```

推荐先只接入空工具列表，确认浮球和会话工作正常后，再逐个接入工具。工具 endpoint 使用相对路径时，AetherCore 会结合 `context.extras.host_callback_base_url` 调回宿主。

## POC 和 Dash 当前差异

POC 当前实现是 Vue 组件，UI 更接近目标形态：浮球、右侧抽屉、可拖拽宽度、iframe 加载渐显。Dash 当前实现是原生 JS 组件，初始化链路更适合传统多页应用，但配置写死较多。

统一后的标准做法：

- UI 由 `aethercore-embed.js` 负责，宿主不再重复写抽屉、动画、iframe、resize 逻辑。
- 宿主只保留 `bindUrl`、`workbenchUrl`、`platformKey`、`getUserId` 这些参数。
- 后端只保留一个 bind 代理，平台密钥不进入前端。
- POC、Dash 和新平台都返回同样的 `{ data: { token, session_id } }`。

## 推荐落点

- Vue/React SPA：在 App 主布局或登录后的全局 Layout 初始化。
- 传统多页应用：在公共 `common.js` 或公共 HTML 模板底部初始化。
- 管理后台类平台：建议所有登录后页面都挂载，未登录页面不要挂载。
