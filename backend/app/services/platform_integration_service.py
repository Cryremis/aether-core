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

        frontend_hosted = self._build_frontend_hosted_snippet(
            platform_key=platform_key,
            script_url=script_url,
        )
        backend_env = self._build_backend_env_snippet(
            platform_key=platform_key,
            host_secret=host_secret,
            display_name=display_name,
        )
        backend_fastapi = self._build_backend_fastapi_snippet(display_name=display_name)
        backend_express = self._build_backend_express_snippet(display_name=display_name)
        guest_frontend = self._build_guest_frontend_snippet(platform_key=platform_key, script_url=script_url)

        return PlatformIntegrationGuide(
            platform_key=platform_key,
            display_name=display_name,
            bind_api_path=self.bind_api_path,
            frontend_script_path=self.frontend_script_path,
            frontend_script_url=script_url,
            recommended_mode_id="standard_bind_hosted",
            prerequisites=[
                "先在 AetherCore 注册平台，并保管 host_secret。",
                "生产环境推荐宿主后端提供 bind 代理，不要把 host_secret 放到浏览器。",
                "优先先接通最小对话能力，再逐步开启 tools、skills、files 和回调。",
            ],
            capabilities=[
                "官方托管前端加载器",
                "标准 bind 代理",
                "多后端模板",
                "最小匿名 / guest 验证路径",
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
                    description="替换成你们当前框架下的用户身份解析逻辑。",
                ),
            ],
            notes=[
                "如果宿主没有统一用户系统，可以先用 guest 模式完成体验验证，按需补 bind 代理和鉴权。",
                "workbench_url 优先使用后端 bind 返回值，便于后续切换前台域名、CDN 或网关。",
            ],
            modes=[
                PlatformIntegrationMode(
                    mode_id="standard_bind_hosted",
                    title="标准接入",
                    summary="推荐给绝大多数生产宿主。前端加载官方脚本，后端提供一个 bind 代理。",
                    use_when="有任意后端服务，但不希望被绑定到某个框架实现。",
                    recommended=True,
                    backend_requirement="需要一个能发 HTTP 请求的后端接口。",
                    identity_requirement="推荐有稳定用户 ID；没有用户系统也可以先用匿名 ID。",
                    capabilities=[
                        "聊天工作台",
                        "会话复用",
                        "后续可扩展 tools / files / skills / host callback",
                    ],
                    steps=[
                        "在宿主全局布局中加载官方托管的 embed loader。",
                        "宿主后端实现 bind 代理，安全保存 host_secret。",
                        "先只返回 token、session_id、workbench_url，确认对话可用。",
                        "后续按需补充 tools、skills、apis 和 host auth。",
                    ],
                    warnings=[
                        "不要把 host_secret 暴露给浏览器。",
                        "用户解析逻辑和配置导入需要替换成你们自己的写法。",
                    ],
                    snippets=[
                        PlatformIntegrationSnippet(
                            snippet_id="frontend_hosted",
                            title="前端嵌入代码",
                            language="html",
                            summary="放到全局布局或登录后主布局，直接加载官方托管脚本。",
                            content=frontend_hosted,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_env",
                            title="后端环境变量示例",
                            language="dotenv",
                            summary="适用于任意后端，只要能读取环境变量即可。",
                            content=backend_env,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_fastapi",
                            title="后端 Bind 示例（FastAPI）",
                            language="python",
                            summary="如果你们后端是 FastAPI，可以直接从这里改起。",
                            content=backend_fastapi,
                        ),
                        PlatformIntegrationSnippet(
                            snippet_id="backend_express",
                            title="后端 Bind 示例（Express）",
                            language="javascript",
                            summary="Node / Express 场景的最小生产接入模板。",
                            content=backend_express,
                        ),
                    ],
                ),
                PlatformIntegrationMode(
                    mode_id="quick_guest",
                    title="最小验证",
                    summary="适合先验证嵌入体验，不依赖宿主后端，不调用宿主工具。",
                    use_when="宿主暂时没有后端、没有用户体系，或者只想先看对话工作台效果。",
                    recommended=False,
                    backend_requirement="无需宿主后端。",
                    identity_requirement="可以使用 guest 用户 ID。",
                    capabilities=[
                        "快速嵌入验证",
                        "基础对话能力",
                        "零宿主回调依赖",
                    ],
                    steps=[
                        "直接加载官方托管脚本。",
                        "传入固定 platformKey 和 guest userId。",
                        "只做前端侧体验验证，后续再切换到标准 bind 模式。",
                    ],
                    warnings=[
                        "仅适合 PoC / Demo，不适合生产。",
                        "该模式不支持安全地注入宿主工具和敏感身份。",
                    ],
                    snippets=[
                        PlatformIntegrationSnippet(
                            snippet_id="frontend_guest",
                            title="最小 Guest 嵌入代码",
                            language="html",
                            summary="最快速的体验验证入口，适合先看 UI 和对话工作流。",
                            content=guest_frontend,
                        ),
                    ],
                ),
            ],
            snippets=PlatformIntegrationGuideSnippets(
                frontend=frontend_hosted,
                backend_env=backend_env,
                backend_fastapi=backend_fastapi,
            ),
        )

    def _build_frontend_script_url(self) -> str:
        return f"{settings.resolved_app_public_base_url}{self.frontend_script_path}"

    def _build_frontend_hosted_snippet(self, *, platform_key: str, script_url: str) -> str:
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
                  return (
                    window.__AETHERCORE_USER_ID__ ||
                    window.__USER_IDENTIFIER__ ||
                    window.__USER_ID__ ||
                    "anonymous"
                  );
                }}
              }});
            </script>
            """
        ).strip()

    def _build_guest_frontend_snippet(self, *, platform_key: str, script_url: str) -> str:
        return dedent(
            f"""\
            <script src="{script_url}" defer></script>
            <script>
              window.mountAetherCore({{
                platformKey: "{platform_key}",
                workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                autoOpen: false,
                getUserId: function () {{
                  return "guest-demo-user";
                }},
                getBindRequest: function () {{
                  return {{
                    url: "{settings.resolved_app_public_base_url}/api/v1/host/bind",
                    method: "POST",
                    headers: {{
                      "X-Aether-Platform-Secret": "{{{{DO_NOT_USE_IN_PRODUCTION}}}}"
                    }},
                    body: {{
                      platform_key: "{platform_key}",
                      host_name: "{platform_key}",
                      conversation_key: "guest-demo-conversation",
                      context: {{
                        user: {{ id: "guest-demo-user", name: "Guest Demo User" }},
                        page: {{}},
                        extras: {{}}
                      }},
                      tools: [],
                      skills: [],
                      apis: []
                    }}
                  }};
                }}
              }});
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

    def _build_backend_fastapi_snippet(self, *, display_name: str) -> str:
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
                # 用你们自己的登录体系替换这里。
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
                            "id": str(getattr(user, "id", None) or getattr(user, "user_id", None) or "anonymous"),
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

    def _build_backend_express_snippet(self, *, display_name: str) -> str:
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
                        id: String(user.id ?? user.userId ?? "anonymous"),
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


platform_integration_service = PlatformIntegrationService()
