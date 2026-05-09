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

        frontend_authenticated = self._build_frontend_authenticated_snippet(
            platform_key=platform_key,
            script_url=script_url,
        )
        frontend_browser_guest = self._build_frontend_browser_guest_snippet(
            platform_key=platform_key,
            script_url=script_url,
        )
        backend_env = self._build_backend_env_snippet(
            platform_key=platform_key,
            host_secret=host_secret,
            display_name=display_name,
        )
        backend_fastapi_authenticated = self._build_backend_fastapi_authenticated_snippet(display_name=display_name)
        backend_fastapi_guest = self._build_backend_fastapi_guest_snippet(display_name=display_name)
        backend_express_authenticated = self._build_backend_express_authenticated_snippet(display_name=display_name)
        backend_express_guest = self._build_backend_express_guest_snippet(display_name=display_name)

        return PlatformIntegrationGuide(
            platform_key=platform_key,
            display_name=display_name,
            bind_api_path=self.bind_api_path,
            frontend_script_path=self.frontend_script_path,
            frontend_script_url=script_url,
            recommended_mode_id="production_authenticated",
            prerequisites=[
                "先在 AetherCore 注册平台，并保管 host_secret。",
                "宿主后端需要提供 bind 代理，不要把 host_secret 放到浏览器。",
                "先确认你们是否有稳定用户体系；没有也可以直接走匿名访客生产模式。",
            ],
            capabilities=[
                "官方托管前端加载器",
                "生产级登录用户接入",
                "生产级匿名访客接入",
                "多后端模板",
                "浏览器级匿名访客会话",
                "渐进式启用宿主工具与回调",
            ],
            placeholders=[
                PlatformIntegrationPlaceholder(
                    key="YOUR_PLATFORM_BASE_URL",
                    label="宿主平台公网根地址",
                    value="{{YOUR_PLATFORM_BASE_URL}}",
                    description="给 AetherCore 回调宿主工具接口使用，例如 https://your-app.example.com",
                ),
                PlatformIntegrationPlaceholder(
                    key="YOUR_SETTINGS_IMPORT",
                    label="宿主配置对象导入",
                    value="{{YOUR_SETTINGS_IMPORT}}",
                    description="替换成你们自己的配置读取方式，例如 from your_project.settings import settings",
                ),
                PlatformIntegrationPlaceholder(
                    key="YOUR_USER_RESOLVER",
                    label="宿主当前用户获取逻辑",
                    value="{{YOUR_USER_RESOLVER}}",
                    description="替换成你们当前框架下的用户身份解析逻辑；仅登录用户模式需要。",
                ),
                PlatformIntegrationPlaceholder(
                    key="YOUR_GUEST_ID_KEY",
                    label="浏览器访客 ID 存储键名",
                    value="{{YOUR_GUEST_ID_KEY}}",
                    description="例如 aethercore_guest_id，用于同一浏览器复用匿名访客会话。",
                ),
            ],
            notes=[
                "不要使用浏览器指纹做用户识别，匿名访客模式应使用前端随机生成并本地持久化的 guest_id。",
                "无用户系统不等于不能接入；匿名访客生产模式即可覆盖正式上线场景。",
                "workbench_url 优先使用后端 bind 返回值，便于后续切换前台域名、CDN 或网关。",
            ],
            modes=[
                PlatformIntegrationMode(
                    mode_id="production_browser_guest",
                    title="匿名访客生产接入",
                    summary="宿主没有登录体系，但有后端。使用浏览器级 guest_id 区分访客，同一浏览器可复用会话。",
                    access_stage="production",
                    identity_scenario="browser_guest",
                    use_when="站点不要求登录，但希望同一浏览器能持续使用 agent，比如自然语言填表、页面辅助操作。",
                    recommended=False,
                    backend_requirement="需要宿主后端提供 bind 代理并保管 host_secret。",
                    identity_requirement="前端生成并持久化 guest_id，不依赖登录用户。",
                    capabilities=[
                        "浏览器级会话续接",
                        "生产环境安全 bind",
                        "可限制性启用宿主工具",
                    ],
                    steps=[
                        "前端首次生成 guest_id，写入 localStorage 或 cookie。",
                        "宿主后端按 guest_id 作为匿名 external_user_id 调用 bind。",
                        "先只开放低风险宿主能力，再按需扩展。",
                    ],
                    warnings=[
                        "不要用浏览器指纹替代 guest_id。",
                        "默认不建议开放高权限、强账户归属的宿主工具。",
                    ],
                    snippets=[
                        PlatformIntegrationSnippet(
                            snippet_id="frontend_browser_guest",
                            title="前端嵌入代码（匿名访客）",
                            language="html",
                            summary="前端自动生成并持久化 guest_id，适合无登录体系的正式接入。",
                            content=frontend_browser_guest,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_env",
                            title="后端环境变量示例",
                            language="dotenv",
                            summary="匿名访客生产接入与登录用户生产接入共用这组环境变量。",
                            content=backend_env,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_fastapi_guest",
                            title="后端 Bind 示例（FastAPI，匿名访客）",
                            language="python",
                            summary="无登录体系时的 FastAPI 模板。",
                            content=backend_fastapi_guest,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_express_guest",
                            title="后端 Bind 示例（Express，匿名访客）",
                            language="javascript",
                            summary="无登录体系时的 Express 模板。",
                            content=backend_express_guest,
                        ),
                    ],
                ),
                PlatformIntegrationMode(
                    mode_id="production_authenticated",
                    title="登录用户生产接入",
                    summary="宿主有稳定用户体系，后端保管密钥并代理 bind，适合真实生产环境和完整能力扩展。",
                    access_stage="production",
                    identity_scenario="authenticated_user",
                    use_when="有用户系统、需要稳定历史、后续可能接入高权限宿主工具和个性化会话。",
                    recommended=True,
                    backend_requirement="需要宿主后端提供 bind 代理并保管 host_secret。",
                    identity_requirement="需要宿主提供稳定 external_user_id。",
                    capabilities=[
                        "稳定用户会话",
                        "跨会话复用",
                        "更完整的宿主能力扩展",
                    ],
                    steps=[
                        "前端从宿主用户体系读取稳定 userId。",
                        "宿主后端按用户身份调用 bind，并返回 workbench_url。",
                        "确认基础对话可用后，再逐步开放 files、tools、skills 和 host callback。",
                    ],
                    warnings=[
                        "不要把 host_secret 暴露给浏览器。",
                        "用户解析逻辑和配置导入需要替换成你们自己的写法。",
                    ],
                    snippets=[
                        PlatformIntegrationSnippet(
                            snippet_id="frontend_authenticated",
                            title="前端嵌入代码（登录用户）",
                            language="html",
                            summary="宿主已有用户体系时的标准前端初始化方式。",
                            content=frontend_authenticated,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_env",
                            title="后端环境变量示例",
                            language="dotenv",
                            summary="登录用户生产接入与匿名访客生产接入共用这组环境变量。",
                            content=backend_env,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_fastapi_authenticated",
                            title="后端 Bind 示例（FastAPI，登录用户）",
                            language="python",
                            summary="有登录体系时的 FastAPI 模板。",
                            content=backend_fastapi_authenticated,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_express_authenticated",
                            title="后端 Bind 示例（Express，登录用户）",
                            language="javascript",
                            summary="有登录体系时的 Express 模板。",
                            content=backend_express_authenticated,
                        ),
                    ],
                ),
            ],
            snippets=PlatformIntegrationGuideSnippets(
                frontend=frontend_authenticated,
                backend_env=backend_env,
                backend_fastapi=backend_fastapi_authenticated,
            ),
        )

    def _build_frontend_script_url(self) -> str:
        return f"{settings.resolved_app_public_base_url}{self.frontend_script_path}"

    def _build_frontend_authenticated_snippet(self, *, platform_key: str, script_url: str) -> str:
        return dedent(
            f"""\
            <script src="{script_url}" defer></script>
            <script>
              window.mountAetherCore({{
                platformKey: "{platform_key}",
                bindUrl: "{self.bind_api_path}",
                workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                title: "AetherCore",
                subtitle: "嵌入式工作台",
                getUserId: function () {{
                  return {{{{YOUR_USER_ID_RESOLVER}}}};
                }}
              }});
            </script>
            """
        ).strip()

    def _build_frontend_browser_guest_snippet(self, *, platform_key: str, script_url: str) -> str:
        return dedent(
            f"""\
            <script src="{script_url}" defer></script>
            <script>
              (function () {{
                var guestKey = "{{{{YOUR_GUEST_ID_KEY}}}}";
                var guestId = window.localStorage.getItem(guestKey);
                if (!guestId) {{
                  guestId =
                    (window.crypto && typeof window.crypto.randomUUID === "function"
                      ? window.crypto.randomUUID()
                      : "guest-" + Math.random().toString(36).slice(2));
                  window.localStorage.setItem(guestKey, guestId);
                }}

                window.mountAetherCore({{
                  platformKey: "{platform_key}",
                  bindUrl: "{self.bind_api_path}",
                  workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                  title: "AetherCore",
                  subtitle: "匿名访客工作台",
                  getUserId: function () {{
                    return guestId;
                  }}
                }});
              }})();
            </script>
            """
        ).strip()

    def _build_frontend_authenticated_quick_snippet(self, *, platform_key: str, script_url: str) -> str:
        return dedent(
            f"""\
            <script src="{script_url}" defer></script>
            <script>
              var currentUserId = {{{{YOUR_USER_ID_RESOLVER}}}};
              window.mountAetherCore({{
                platformKey: "{platform_key}",
                workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                title: "AetherCore",
                subtitle: "登录用户快速验证",
                getUserId: function () {{
                  return currentUserId;
                }},
                getBindRequest: function () {{
                  throw new Error("当前是登录用户快速验证模式；正式上线请切换到生产 bind 模式。");
                }}
              }});
            </script>
            """
        ).strip()

    def _build_frontend_browser_guest_quick_snippet(self, *, platform_key: str, script_url: str) -> str:
        return dedent(
            f"""\
            <script src="{script_url}" defer></script>
            <script>
              (function () {{
                var guestKey = "{{{{YOUR_GUEST_ID_KEY}}}}";
                var guestId = window.localStorage.getItem(guestKey);
                if (!guestId) {{
                  guestId =
                    (window.crypto && typeof window.crypto.randomUUID === "function"
                      ? window.crypto.randomUUID()
                      : "guest-" + Math.random().toString(36).slice(2));
                  window.localStorage.setItem(guestKey, guestId);
                }}

                window.mountAetherCore({{
                  platformKey: "{platform_key}",
                  workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                  title: "AetherCore",
                  subtitle: "匿名访客快速验证",
                  getUserId: function () {{
                    return guestId;
                  }},
                  getBindRequest: function () {{
                    throw new Error("当前是匿名访客快速验证模式；正式上线请切换到生产 bind 模式。");
                  }}
                }});
              }})();
            </script>
            """
        ).strip()

    def _build_frontend_ephemeral_snippet(self, *, platform_key: str, script_url: str) -> str:
        return dedent(
            f"""\
            <script src="{script_url}" defer></script>
            <script>
              (function () {{
                var sessionGuestId =
                  (window.crypto && typeof window.crypto.randomUUID === "function"
                    ? "ephemeral-" + window.crypto.randomUUID()
                    : "ephemeral-" + Math.random().toString(36).slice(2));

                window.mountAetherCore({{
                  platformKey: "{platform_key}",
                  workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                  title: "AetherCore",
                  subtitle: "临时验证模式",
                  getUserId: function () {{
                    return sessionGuestId;
                  }},
                  getBindRequest: function () {{
                    throw new Error("临时快速验证模式仅用于 UI 接入演示，正式接入请切换到后端 bind 模式。");
                  }}
                }});
              }})();
            </script>
            """
        ).strip()

    def _build_backend_env_snippet(self, *, platform_key: str, host_secret: str, display_name: str) -> str:
        return dedent(
            f"""\
            AETHERCORE_API_BASE_URL={settings.resolved_app_public_base_url}
            AETHERCORE_WORKBENCH_URL={settings.resolved_manage_frontend_public_base_url}
            AETHERCORE_PLATFORM_KEY={platform_key}
            AETHERCORE_PLATFORM_SECRET={host_secret}
            AETHERCORE_HOST_NAME={display_name}
            AETHERCORE_HOST_CALLBACK_BASE_URL={{{{YOUR_PLATFORM_BASE_URL}}}}
            """
        ).strip()

    def _build_backend_fastapi_authenticated_snippet(self, *, display_name: str) -> str:
        return dedent(
            f"""\
            from fastapi import APIRouter, HTTPException, Request
            from pydantic import BaseModel
            import httpx
            {{{{YOUR_SETTINGS_IMPORT}}}}

            router = APIRouter()


            class AetherCoreBindRequest(BaseModel):
                conversation_key: str | None = None
                conversation_id: str | None = None


            @router.post("{self.bind_api_path}")
            async def bind_aethercore(payload: AetherCoreBindRequest, request: Request):
                user = {{{{YOUR_USER_RESOLVER}}}}
                if user is None:
                    raise HTTPException(status_code=401, detail="用户未登录")

                body = {{
                    "platform_key": settings.AETHERCORE_PLATFORM_KEY,
                    "host_name": settings.AETHERCORE_HOST_NAME or "{display_name}",
                    "conversation_key": payload.conversation_key,
                    "conversation_id": payload.conversation_id,
                    "context": {{
                        "user": {{
                            "id": str(getattr(user, "id", None) or getattr(user, "user_id", None)),
                            "name": getattr(user, "name", None) or getattr(user, "username", None) or "anonymous",
                        }},
                        "page": {{}},
                        "extras": {{
                            "host_callback_base_url": settings.AETHERCORE_HOST_CALLBACK_BASE_URL,
                        }},
                    }},
                    "tools": [],
                    "skills": [],
                    "apis": [],
                }}

                async with httpx.AsyncClient(timeout=30, verify=getattr(settings, "http_client_ssl_verify", True)) as client:
                    response = await client.post(
                        f"{{settings.AETHERCORE_API_BASE_URL.rstrip('/')}}/api/v1/host/bind",
                        headers={{"X-Aether-Platform-Secret": settings.AETHERCORE_PLATFORM_SECRET}},
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

    def _build_backend_fastapi_guest_snippet(self, *, display_name: str) -> str:
        return dedent(
            f"""\
            from fastapi import APIRouter, HTTPException
            from pydantic import BaseModel
            import httpx
            {{{{YOUR_SETTINGS_IMPORT}}}}

            router = APIRouter()


            class AetherCoreBindRequest(BaseModel):
                guest_id: str
                conversation_key: str | None = None
                conversation_id: str | None = None


            @router.post("{self.bind_api_path}")
            async def bind_aethercore(payload: AetherCoreBindRequest):
                if not payload.guest_id:
                    raise HTTPException(status_code=400, detail="缺少 guest_id")

                body = {{
                    "platform_key": settings.AETHERCORE_PLATFORM_KEY,
                    "host_name": settings.AETHERCORE_HOST_NAME or "{display_name}",
                    "conversation_key": payload.conversation_key or f"guest-{{payload.guest_id}}",
                    "conversation_id": payload.conversation_id,
                    "context": {{
                        "user": {{
                            "id": payload.guest_id,
                            "name": "Guest Visitor",
                        }},
                        "page": {{}},
                        "extras": {{
                            "host_callback_base_url": settings.AETHERCORE_HOST_CALLBACK_BASE_URL,
                            "identity_mode": "browser_guest",
                        }},
                    }},
                    "tools": [],
                    "skills": [],
                    "apis": [],
                }}

                async with httpx.AsyncClient(timeout=30, verify=getattr(settings, "http_client_ssl_verify", True)) as client:
                    response = await client.post(
                        f"{{settings.AETHERCORE_API_BASE_URL.rstrip('/')}}/api/v1/host/bind",
                        headers={{"X-Aether-Platform-Secret": settings.AETHERCORE_PLATFORM_SECRET}},
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

    def _build_backend_express_authenticated_snippet(self, *, display_name: str) -> str:
        return dedent(
            f"""\
            import express from "express";
            import fetch from "node-fetch";
            import {{ settings }} from "{{{{YOUR_SETTINGS_IMPORT}}}}";

            const router = express.Router();

            router.post("{self.bind_api_path}", async (req, res) => {{
              const user = {{{{YOUR_USER_RESOLVER}}}};
              if (!user) {{
                return res.status(401).json({{ detail: "用户未登录" }});
              }}

              const upstream = await fetch(
                `${{settings.AETHERCORE_API_BASE_URL.replace(/\\/$/, "")}}/api/v1/host/bind`,
                {{
                  method: "POST",
                  headers: {{
                    "Content-Type": "application/json",
                    "X-Aether-Platform-Secret": settings.AETHERCORE_PLATFORM_SECRET,
                  }},
                  body: JSON.stringify({{
                    platform_key: settings.AETHERCORE_PLATFORM_KEY,
                    host_name: settings.AETHERCORE_HOST_NAME || "{display_name}",
                    conversation_key: req.body?.conversation_key ?? null,
                    conversation_id: req.body?.conversation_id ?? null,
                    context: {{
                      user: {{
                        id: String(user.id ?? user.userId),
                        name: user.name ?? user.username ?? "anonymous",
                      }},
                      page: {{}},
                      extras: {{
                        host_callback_base_url: settings.AETHERCORE_HOST_CALLBACK_BASE_URL,
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

    def _build_backend_express_guest_snippet(self, *, display_name: str) -> str:
        return dedent(
            f"""\
            import express from "express";
            import fetch from "node-fetch";
            import {{ settings }} from "{{{{YOUR_SETTINGS_IMPORT}}}}";

            const router = express.Router();

            router.post("{self.bind_api_path}", async (req, res) => {{
              const guestId = String(req.body?.guest_id || "");
              if (!guestId) {{
                return res.status(400).json({{ detail: "缺少 guest_id" }});
              }}

              const upstream = await fetch(
                `${{settings.AETHERCORE_API_BASE_URL.replace(/\\/$/, "")}}/api/v1/host/bind`,
                {{
                  method: "POST",
                  headers: {{
                    "Content-Type": "application/json",
                    "X-Aether-Platform-Secret": settings.AETHERCORE_PLATFORM_SECRET,
                  }},
                  body: JSON.stringify({{
                    platform_key: settings.AETHERCORE_PLATFORM_KEY,
                    host_name: settings.AETHERCORE_HOST_NAME || "{display_name}",
                    conversation_key: req.body?.conversation_key || `guest-${{guestId}}`,
                    conversation_id: req.body?.conversation_id ?? null,
                    context: {{
                      user: {{
                        id: guestId,
                        name: "Guest Visitor",
                      }},
                      page: {{}},
                      extras: {{
                        host_callback_base_url: settings.AETHERCORE_HOST_CALLBACK_BASE_URL,
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
