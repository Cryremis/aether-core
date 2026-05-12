# AetherCore 嵌入接入（可直接复制）

这份文档只讲一件事：让你把 AetherCore 嵌入到已有系统里，并且尽量做到复制即用。

## 你需要准备什么

- 你已经在 AetherCore 里注册了平台，拿到了：
  - `platform_key`
  - `host_secret`
- 你的宿主后端可以新增一个 bind API（示例用 `/api/v1/aethercore/embed/bind`）。
- 你有一个稳定的用户 ID（已有登录体系时直接用现有用户 ID）。

## 第 1 步：前端粘贴这段

放在全局 Layout、公共模板页，或 `</body>` 前：

```html
<script src="/api/v1/host/public/embed/aethercore-embed.js"></script>
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

## 第 2 步：后端加 bind 代理

前端只调用你自己的后端：

```http
POST /api/v1/aethercore/embed/bind
Content-Type: application/json

{
  "conversation_key": "stable-key-from-frontend"
}
```

你的后端再去调用 AetherCore：

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

返回给前端：

```json
{
  "data": {
    "token": "embed-token",
    "session_id": "session-id",
    "workbench_url": "https://ac.example.com/?embed_token=...&session_id=..."
  }
}
```

## 第 3 步：环境变量

```dotenv
AETHERCORE_API_BASE_URL=https://ac-backend.example.com
AETHERCORE_PLATFORM_KEY=your-platform-key
AETHERCORE_PLATFORM_SECRET=your-platform-secret
AETHERCORE_HOST_NAME=Your Platform
AETHERCORE_HOST_CALLBACK_BASE_URL=https://your-platform.example.com

# 浏览器里 iframe 打开的工作台地址（可选；不配时默认用 AETHERCORE_API_BASE_URL）
# AETHERCORE_WORKBENCH_URL=https://ac.example.com
```

## 同域 / 跨域怎么写（人话版）

同域（页面和后端同一个域名）时：

- script 用相对路径：`/api/v1/host/public/embed/aethercore-embed.js`
- bind 用相对路径：`/api/v1/aethercore/embed/bind`

跨域（页面域名和后端域名不一样）时：

- script 用绝对地址：`https://api.example.com/api/v1/host/public/embed/aethercore-embed.js`
- bind 用绝对地址：`https://api.example.com/api/v1/aethercore/embed/bind`

跨域还需要你们后端正确配置 CORS 和认证策略。

## Cookie 模式 vs Bearer 模式

### Cookie 模式（常见于同域）

前端不手动塞 token，浏览器自动带登录 Cookie。

```js
window.mountAetherCore({
  platformKey: "your-platform-key",
  bindUrl: "/api/v1/aethercore/embed/bind",
  workbenchUrl: "https://ac.example.com",
  credentials: "include",
  getUserId: function () { return window.currentUser.id; }
});
```

### Bearer 模式（常见于前后端分离）

前端在 bind 请求里手动带 `Authorization`：

```js
window.mountAetherCore({
  platformKey: "your-platform-key",
  bindUrl: "https://api.example.com/api/v1/aethercore/embed/bind",
  workbenchUrl: "https://ac.example.com",
  credentials: "omit",
  getUserId: function () { return window.currentUser.id; },
  getBindRequest: async function (state, config) {
    return {
      url: config.bindUrl,
      method: "POST",
      credentials: "omit",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + localStorage.getItem("access_token")
      },
      body: {
        conversation_key: state.conversationKey
      }
    };
  }
});
```

## SPA（Vue/React）放在哪里

- 只在登录后全局 Layout 里初始化一次。
- 如果已存在实例，不要重复 `mountAetherCore`。
- 在登出或壳组件卸载时清理实例。

简单判断示例：

```js
if (!window.__AETHERCORE_EMBED_INSTANCE__) {
  window.__AETHERCORE_EMBED_INSTANCE__ = window.mountAetherCore({...});
}
```

## FastAPI bind 代理示例

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
    workbench_base_url = (
        getattr(settings, "AETHERCORE_WORKBENCH_URL", "") or settings.AETHERCORE_API_BASE_URL
    ).rstrip("/")
    return {
        "data": {
            "token": data["token"],
            "session_id": data["session_id"],
            "workbench_url": f"{workbench_base_url}?embed_token={data['token']}&session_id={data['session_id']}",
        }
    }
```

## 常见报错

### `failed to load aethercore embed script`

优先检查：

1. 浏览器访问 `/api/v1/host/public/embed/aethercore-embed.js` 是否返回 200。
2. 你的网关/Nginx 是否把这个路径正确转发到了后端。
3. 跨域时 `script src` 是否写成了正确的绝对地址。
4. HTTPS 页面里是否错误引用了 HTTP 脚本地址。
5. 后端是否有鉴权拦截了这个公开脚本路径。

