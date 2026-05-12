from __future__ import annotations

from textwrap import dedent

from app.core.config import settings
from app.schemas.platform import (
    PlatformIntegrationGuide,
    PlatformIntegrationGuideSnippets,
    PlatformIntegrationMode,
    PlatformIntegrationPlaceholder,
    PlatformIntegrationSnippet,
)


class PlatformIntegrationService:
    """生成平台注册后的结构化接入教程。"""

    bind_api_path = "/api/v1/aethercore/embed/bind"
    frontend_script_path = "/api/v1/host/public/embed/aethercore-embed.js"

    def build_guide(self, platform: dict) -> PlatformIntegrationGuide:
        platform_key = str(platform["platform_key"])
        display_name = str(platform["display_name"])
        host_secret = str(platform["host_secret"])
        script_url = self._build_frontend_script_url()

        frontend_authenticated_same_origin = self._build_frontend_authenticated_same_origin_snippet(
            platform_key=platform_key,
            script_url=script_url,
        )
        frontend_authenticated_cross_origin = self._build_frontend_authenticated_cross_origin_snippet(
            platform_key=platform_key,
        )
        frontend_browser_guest_same_origin = self._build_frontend_guest_same_origin_snippet(
            platform_key=platform_key,
            script_url=script_url,
        )
        frontend_browser_guest_cross_origin = self._build_frontend_guest_cross_origin_snippet(
            platform_key=platform_key,
        )
        backend_fastapi_authenticated = self._build_backend_fastapi_authenticated_snippet(
            display_name=display_name,
            platform_key=platform_key,
            host_secret=host_secret,
        )
        backend_fastapi_guest = self._build_backend_fastapi_guest_snippet(
            display_name=display_name,
            platform_key=platform_key,
            host_secret=host_secret,
        )
        backend_express_authenticated = self._build_backend_express_authenticated_snippet(
            display_name=display_name,
            platform_key=platform_key,
            host_secret=host_secret,
        )
        backend_express_guest = self._build_backend_express_guest_snippet(
            display_name=display_name,
            platform_key=platform_key,
            host_secret=host_secret,
        )

        return PlatformIntegrationGuide(
            platform_key=platform_key,
            display_name=display_name,
            bind_api_path=self.bind_api_path,
            frontend_script_path=self.frontend_script_path,
            frontend_script_url=script_url,
            recommended_mode_id="production_authenticated",
            prerequisites=[
                "先在 AetherCore 注册平台并保存 host_secret；不要放到前端。",
                "宿主后端提供 bind 代理接口（示例：/api/v1/aethercore/embed/bind）。",
                "前端确认能访问 script URL 与 bind URL。",
            ],
            capabilities=[
                "前端可直接复制的嵌入代码（同域 / 跨域分开）",
                "FastAPI / Express 后端 bind 模板（可直接跑）",
                "已有登录体系与匿名访客两套生产模板",
                "Cookie / Bearer 两种认证方式示例",
                "SPA 防重复挂载建议",
            ],
            placeholders=[
                PlatformIntegrationPlaceholder(
                    key="YOUR_API_BASE_URL",
                    label="宿主后端公网根地址（跨域时使用）",
                    value="{{YOUR_API_BASE_URL}}",
                    description="例如 https://api.example.com，用于拼接 script URL 和 bind URL 的绝对地址。",
                ),
                PlatformIntegrationPlaceholder(
                    key="YOUR_USER_RESOLVER",
                    label="宿主当前用户获取逻辑",
                    value="{{YOUR_USER_RESOLVER}}",
                    description='替换成你们当前框架下的用户身份解析逻辑，例如 FastAPI: request.state.user，Express: req.user。',
                ),
            ],
            notes=[
                "同域部署：script URL 和 bind URL 用相对路径。",
                "跨域部署：script URL 和 bind URL 改为绝对地址，并检查 CORS。",
                "Cookie 模式适合同域（credentials: include）；Bearer 模式适合前后端分离（Authorization + credentials: omit）。",
                "匿名访客模式统一使用 localStorage 键名 aethercore_guest_id。",
                "SPA 只在全局 Layout 初始化一次；window.__AETHERCORE_EMBED_INSTANCE__ 存在时不要重复 mount。",
            ],
            modes=[
                PlatformIntegrationMode(
                    mode_id="production_authenticated",
                    title="标准接入（已有登录体系）",
                    summary="直接复制这组前后端代码即可跑通，适合 POC、Dash、管理后台等已有用户系统场景。",
                    access_stage="production",
                    identity_scenario="authenticated_user",
                    use_when="宿主已有登录体系，需要稳定用户会话和后续能力扩展。",
                    recommended=True,
                    backend_requirement="需要宿主后端提供 bind 代理并保管 host_secret。",
                    identity_requirement="需要宿主提供稳定 external_user_id。",
                    capabilities=[
                        "最小改动可上线",
                        "稳定用户会话",
                        "可扩展宿主工具回调",
                    ],
                    steps=[
                        "按部署形态复制同域或跨域前端模板。",
                        "后端按模板添加 bind 代理。",
                        "验证可打开工作台后，再逐步接入 tools / skills / apis。",
                    ],
                    warnings=[
                        "不要把 host_secret 暴露给浏览器。",
                        "跨域时请使用绝对 URL 并检查 CORS。",
                    ],
                    snippets=[
                        PlatformIntegrationSnippet(
                            snippet_id="frontend_authenticated_same_origin",
                            title="前端嵌入代码（同域）",
                            language="html",
                            summary="同域场景推荐，直接使用相对路径 + Cookie。",
                            content=frontend_authenticated_same_origin,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="frontend_authenticated_cross_origin",
                            title="前端嵌入代码（跨域）",
                            language="html",
                            summary="前后端分域场景，使用绝对地址 + Bearer。",
                            content=frontend_authenticated_cross_origin,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_fastapi_authenticated",
                            title="后端 Bind 示例（FastAPI）",
                            language="python",
                            summary="可直接运行，按注释替换用户解析逻辑即可。",
                            content=backend_fastapi_authenticated,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_express_authenticated",
                            title="后端 Bind 示例（Express）",
                            language="javascript",
                            summary="可直接运行，按注释替换用户解析逻辑即可。",
                            content=backend_express_authenticated,
                        ),
                    ],
                ),
                PlatformIntegrationMode(
                    mode_id="production_browser_guest",
                    title="标准接入（无登录体系 / 匿名访客）",
                    summary="站点没有用户登录系统时使用。通过浏览器持久化 guest_id 维持同一访客会话。",
                    access_stage="production",
                    identity_scenario="browser_guest",
                    use_when="宿主无登录体系，但希望同一浏览器里的访客会话可续接。",
                    recommended=False,
                    backend_requirement="需要宿主后端提供 bind 代理并保管 host_secret。",
                    identity_requirement="前端生成并持久化 guest_id（不使用浏览器指纹）。",
                    capabilities=[
                        "无登录可上线",
                        "浏览器级会话续接",
                        "可逐步扩展宿主工具",
                    ],
                    steps=[
                        "按部署形态复制同域或跨域匿名访客前端模板。",
                        "bind 请求携带 guest_id，后端用它作为 external_user_id。",
                        "验证可用后再按需开放宿主能力。",
                    ],
                    warnings=[
                        "不要把 host_secret 暴露给浏览器。",
                        "默认不要开放高权限、强账户归属工具。",
                    ],
                    snippets=[
                        PlatformIntegrationSnippet(
                            snippet_id="frontend_browser_guest_same_origin",
                            title="前端嵌入代码（匿名访客，同域）",
                            language="html",
                            summary="同域匿名访客模式，使用固定键名 aethercore_guest_id。",
                            content=frontend_browser_guest_same_origin,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="frontend_browser_guest_cross_origin",
                            title="前端嵌入代码（匿名访客，跨域）",
                            language="html",
                            summary="跨域匿名访客模式，使用绝对地址 + Bearer。",
                            content=frontend_browser_guest_cross_origin,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_fastapi_guest",
                            title="后端 Bind 示例（FastAPI，匿名访客）",
                            language="python",
                            summary="可直接运行的匿名访客模板。",
                            content=backend_fastapi_guest,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_express_guest",
                            title="后端 Bind 示例（Express，匿名访客）",
                            language="javascript",
                            summary="可直接运行的匿名访客模板。",
                            content=backend_express_guest,
                        ),
                    ],
                ),
            ],
            snippets=PlatformIntegrationGuideSnippets(
                frontend=frontend_authenticated_same_origin,
                backend_env="",
                backend_fastapi=backend_fastapi_authenticated,
            ),
        )

    def _build_frontend_script_url(self) -> str:
        return f"{settings.resolved_app_public_base_url}{self.frontend_script_path}"

    def _build_frontend_authenticated_same_origin_snippet(self, *, platform_key: str, script_url: str) -> str:
        return dedent(
            f"""\
            <script src="{script_url}" defer></script>
            <script>
              if (!window.__AETHERCORE_EMBED_INSTANCE__) {{
                window.__AETHERCORE_EMBED_INSTANCE__ = window.mountAetherCore({{
                  platformKey: "{platform_key}",
                  bindUrl: "{self.bind_api_path}",
                  workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                  title: "AetherCore",
                  subtitle: "嵌入式工作台",
                  credentials: "include",
                  getUserId: function () {{
                    return {{{{YOUR_USER_ID_RESOLVER}}}};
                  }}
                }});
              }}
            </script>
            """
        ).strip()

    def _build_frontend_authenticated_cross_origin_snippet(self, *, platform_key: str) -> str:
        return dedent(
            f"""\
            <script src="{{{{YOUR_API_BASE_URL}}}}{self.frontend_script_path}" defer></script>
            <script>
              if (!window.__AETHERCORE_EMBED_INSTANCE__) {{
                window.__AETHERCORE_EMBED_INSTANCE__ = window.mountAetherCore({{
                  platformKey: "{platform_key}",
                  bindUrl: "{{{{YOUR_API_BASE_URL}}}}{self.bind_api_path}",
                  workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                  title: "AetherCore",
                  subtitle: "嵌入式工作台",
                  credentials: "omit",
                  getUserId: function () {{
                    return {{{{YOUR_USER_ID_RESOLVER}}}};
                  }},
                  getBindRequest: async function (state, config) {{
                    return {{
                      url: config.bindUrl,
                      method: "POST",
                      credentials: "omit",
                      headers: {{
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + localStorage.getItem("access_token"),
                      }},
                      body: {{
                        conversation_key: state.conversationKey
                      }}
                    }};
                  }}
                }});
              }}
            </script>
            """
        ).strip()

    def _build_frontend_guest_same_origin_snippet(self, *, platform_key: str, script_url: str) -> str:
        return dedent(
            f"""\
            <script src="{script_url}" defer></script>
            <script>
              (function () {{
                var guestKey = "aethercore_guest_id";
                var guestId = window.localStorage.getItem(guestKey);
                if (!guestId) {{
                  guestId =
                    (window.crypto && typeof window.crypto.randomUUID === "function"
                      ? window.crypto.randomUUID()
                      : "guest-" + Math.random().toString(36).slice(2));
                  window.localStorage.setItem(guestKey, guestId);
                }}

                if (!window.__AETHERCORE_EMBED_INSTANCE__) {{
                  window.__AETHERCORE_EMBED_INSTANCE__ = window.mountAetherCore({{
                    platformKey: "{platform_key}",
                    bindUrl: "{self.bind_api_path}",
                    workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                    title: "AetherCore",
                    subtitle: "匿名访客工作台",
                    credentials: "include",
                    getUserId: function () {{
                      return guestId;
                    }},
                    getBindRequest: function (state, config) {{
                      return {{
                        url: config.bindUrl,
                        method: "POST",
                        credentials: "include",
                        headers: {{
                          "Content-Type": "application/json"
                        }},
                        body: {{
                          guest_id: guestId,
                          conversation_key: state.conversationKey
                        }}
                      }};
                    }}
                  }});
                }}
              }})();
            </script>
            """
        ).strip()

    def _build_frontend_guest_cross_origin_snippet(self, *, platform_key: str) -> str:
        return dedent(
            f"""\
            <script src="{{{{YOUR_API_BASE_URL}}}}{self.frontend_script_path}" defer></script>
            <script>
              (function () {{
                var guestKey = "aethercore_guest_id";
                var guestId = window.localStorage.getItem(guestKey);
                if (!guestId) {{
                  guestId =
                    (window.crypto && typeof window.crypto.randomUUID === "function"
                      ? window.crypto.randomUUID()
                      : "guest-" + Math.random().toString(36).slice(2));
                  window.localStorage.setItem(guestKey, guestId);
                }}

                if (!window.__AETHERCORE_EMBED_INSTANCE__) {{
                  window.__AETHERCORE_EMBED_INSTANCE__ = window.mountAetherCore({{
                    platformKey: "{platform_key}",
                    bindUrl: "{{{{YOUR_API_BASE_URL}}}}{self.bind_api_path}",
                    workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                    title: "AetherCore",
                    subtitle: "匿名访客工作台",
                    credentials: "omit",
                    getUserId: function () {{
                      return guestId;
                    }},
                    getBindRequest: async function (state, config) {{
                      return {{
                        url: config.bindUrl,
                        method: "POST",
                        credentials: "omit",
                        headers: {{
                          "Content-Type": "application/json",
                          "Authorization": "Bearer " + localStorage.getItem("access_token"),
                        }},
                        body: {{
                          guest_id: guestId,
                          conversation_key: state.conversationKey
                        }}
                      }};
                    }}
                  }});
                }}
              }})();
            </script>
            """
        ).strip()

    def _build_backend_fastapi_authenticated_snippet(
        self, *, display_name: str, platform_key: str, host_secret: str
    ) -> str:
        return dedent(
            f"""\
            from fastapi import APIRouter, HTTPException, Request
            from pydantic import BaseModel
            import httpx

            router = APIRouter()
            AETHERCORE_API_BASE_URL = "{settings.resolved_app_public_base_url}"
            AETHERCORE_PLATFORM_KEY = "{platform_key}"
            AETHERCORE_PLATFORM_SECRET = "{host_secret}"
            AETHERCORE_HOST_NAME = "{display_name}"
            AETHERCORE_HOST_CALLBACK_BASE_URL = "{{YOUR_PLATFORM_BASE_URL}}"


            class AetherCoreBindRequest(BaseModel):
                conversation_key: str | None = None
                conversation_id: str | None = None


            @router.post("{self.bind_api_path}")
            async def bind_aethercore(payload: AetherCoreBindRequest, request: Request):
                # TODO: 替换为你们项目自己的当前登录用户获取逻辑
                user = {{{{YOUR_USER_RESOLVER}}}}
                if user is None:
                    raise HTTPException(status_code=401, detail="用户未登录")

                body = {{
                    "platform_key": AETHERCORE_PLATFORM_KEY,
                    "host_name": AETHERCORE_HOST_NAME,
                    "conversation_key": payload.conversation_key,
                    "conversation_id": payload.conversation_id,
                    "context": {{
                        "user": {{
                            "id": str(getattr(user, "id", None) or getattr(user, "user_id", None)),
                            "name": getattr(user, "name", None) or getattr(user, "username", None) or "anonymous",
                        }},
                        "page": {{}},
                        "extras": {{
                            "host_callback_base_url": AETHERCORE_HOST_CALLBACK_BASE_URL,
                        }},
                    }},
                    "tools": [],
                    "skills": [],
                    "apis": [],
                }}

                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"{{AETHERCORE_API_BASE_URL.rstrip('/')}}/api/v1/host/bind",
                        headers={{"X-Aether-Platform-Secret": AETHERCORE_PLATFORM_SECRET}},
                        json=body,
                    )

                if response.status_code >= 400:
                    raise HTTPException(status_code=response.status_code, detail=response.text)

                data = response.json()["data"]
                return {{
                    "data": {{
                        "token": data["token"],
                        "session_id": data["session_id"],
                        "conversation_id": data.get("conversation_id"),
                        "workbench_url": data.get("workbench_url"),
                    }}
                }}
            """
        ).strip()

    def _build_backend_fastapi_guest_snippet(
        self, *, display_name: str, platform_key: str, host_secret: str
    ) -> str:
        return dedent(
            f"""\
            from fastapi import APIRouter, HTTPException
            from pydantic import BaseModel
            import httpx

            router = APIRouter()
            AETHERCORE_API_BASE_URL = "{settings.resolved_app_public_base_url}"
            AETHERCORE_PLATFORM_KEY = "{platform_key}"
            AETHERCORE_PLATFORM_SECRET = "{host_secret}"
            AETHERCORE_HOST_NAME = "{display_name}"
            AETHERCORE_HOST_CALLBACK_BASE_URL = "{{YOUR_PLATFORM_BASE_URL}}"


            class AetherCoreBindRequest(BaseModel):
                guest_id: str
                conversation_key: str | None = None
                conversation_id: str | None = None


            @router.post("{self.bind_api_path}")
            async def bind_aethercore(payload: AetherCoreBindRequest):
                guest_id = (payload.guest_id or "").strip()
                if not guest_id:
                    raise HTTPException(status_code=400, detail="缺少 guest_id")

                body = {{
                    "platform_key": AETHERCORE_PLATFORM_KEY,
                    "host_name": AETHERCORE_HOST_NAME,
                    "conversation_key": payload.conversation_key or f"guest-{{guest_id}}",
                    "conversation_id": payload.conversation_id,
                    "context": {{
                        "user": {{
                            "id": guest_id,
                            "name": "Guest Visitor",
                        }},
                        "page": {{}},
                        "extras": {{
                            "host_callback_base_url": AETHERCORE_HOST_CALLBACK_BASE_URL,
                            "identity_mode": "browser_guest",
                        }},
                    }},
                    "tools": [],
                    "skills": [],
                    "apis": [],
                }}

                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"{{AETHERCORE_API_BASE_URL.rstrip('/')}}/api/v1/host/bind",
                        headers={{"X-Aether-Platform-Secret": AETHERCORE_PLATFORM_SECRET}},
                        json=body,
                    )

                if response.status_code >= 400:
                    raise HTTPException(status_code=response.status_code, detail=response.text)

                data = response.json()["data"]
                return {{
                    "data": {{
                        "token": data["token"],
                        "session_id": data["session_id"],
                        "conversation_id": data.get("conversation_id"),
                        "workbench_url": data.get("workbench_url"),
                    }}
                }}
            """
        ).strip()

    def _build_backend_express_authenticated_snippet(
        self, *, display_name: str, platform_key: str, host_secret: str
    ) -> str:
        return dedent(
            f"""\
            import express from "express";
            import fetch from "node-fetch";

            const router = express.Router();
            const AETHERCORE_API_BASE_URL = "{settings.resolved_app_public_base_url}";
            const AETHERCORE_PLATFORM_KEY = "{platform_key}";
            const AETHERCORE_PLATFORM_SECRET = "{host_secret}";
            const AETHERCORE_HOST_NAME = "{display_name}";
            const AETHERCORE_HOST_CALLBACK_BASE_URL = "{{YOUR_PLATFORM_BASE_URL}}";

            router.post("{self.bind_api_path}", async (req, res) => {{
              // TODO: 替换为你们项目自己的当前登录用户获取逻辑
              const user = {{{{YOUR_USER_RESOLVER}}}};
              if (!user) {{
                return res.status(401).json({{ detail: "用户未登录" }});
              }}

              const upstream = await fetch(
                `${{AETHERCORE_API_BASE_URL.replace(/\\/$/, "")}}/api/v1/host/bind`,
                {{
                  method: "POST",
                  headers: {{
                    "Content-Type": "application/json",
                    "X-Aether-Platform-Secret": AETHERCORE_PLATFORM_SECRET,
                  }},
                  body: JSON.stringify({{
                    platform_key: AETHERCORE_PLATFORM_KEY,
                    host_name: AETHERCORE_HOST_NAME,
                    conversation_key: req.body?.conversation_key ?? null,
                    conversation_id: req.body?.conversation_id ?? null,
                    context: {{
                      user: {{
                        id: String(user.id ?? user.userId),
                        name: user.name ?? user.username ?? "anonymous",
                      }},
                      page: {{}},
                      extras: {{
                        host_callback_base_url: AETHERCORE_HOST_CALLBACK_BASE_URL,
                      }},
                    }},
                    tools: [],
                    skills: [],
                    apis: [],
                  }}),
                }},
              );

              const data = await upstream.json();
              return res.status(upstream.status).json({{
                data: {{
                  token: data?.data?.token,
                  session_id: data?.data?.session_id,
                  conversation_id: data?.data?.conversation_id,
                  workbench_url: data?.data?.workbench_url,
                }},
              }});
            }});

            export default router;
            """
        ).strip()

    def _build_backend_express_guest_snippet(
        self, *, display_name: str, platform_key: str, host_secret: str
    ) -> str:
        return dedent(
            f"""\
            import express from "express";
            import fetch from "node-fetch";

            const router = express.Router();
            const AETHERCORE_API_BASE_URL = "{settings.resolved_app_public_base_url}";
            const AETHERCORE_PLATFORM_KEY = "{platform_key}";
            const AETHERCORE_PLATFORM_SECRET = "{host_secret}";
            const AETHERCORE_HOST_NAME = "{display_name}";
            const AETHERCORE_HOST_CALLBACK_BASE_URL = "{{YOUR_PLATFORM_BASE_URL}}";

            router.post("{self.bind_api_path}", async (req, res) => {{
              const guestId = String(req.body?.guest_id || "").trim();
              if (!guestId) {{
                return res.status(400).json({{ detail: "缺少 guest_id" }});
              }}

              const upstream = await fetch(
                `${{AETHERCORE_API_BASE_URL.replace(/\\/$/, "")}}/api/v1/host/bind`,
                {{
                  method: "POST",
                  headers: {{
                    "Content-Type": "application/json",
                    "X-Aether-Platform-Secret": AETHERCORE_PLATFORM_SECRET,
                  }},
                  body: JSON.stringify({{
                    platform_key: AETHERCORE_PLATFORM_KEY,
                    host_name: AETHERCORE_HOST_NAME,
                    conversation_key: req.body?.conversation_key || `guest-${{guestId}}`,
                    conversation_id: req.body?.conversation_id ?? null,
                    context: {{
                      user: {{
                        id: guestId,
                        name: "Guest Visitor",
                      }},
                      page: {{}},
                      extras: {{
                        host_callback_base_url: AETHERCORE_HOST_CALLBACK_BASE_URL,
                        identity_mode: "browser_guest",
                      }},
                    }},
                    tools: [],
                    skills: [],
                    apis: [],
                  }}),
                }},
              );

              const data = await upstream.json();
              return res.status(upstream.status).json({{
                data: {{
                  token: data?.data?.token,
                  session_id: data?.data?.session_id,
                  conversation_id: data?.data?.conversation_id,
                  workbench_url: data?.data?.workbench_url,
                }},
              }});
            }});

            export default router;
            """
        ).strip()


platform_integration_service = PlatformIntegrationService()
