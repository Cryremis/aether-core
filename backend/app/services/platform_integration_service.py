from __future__ import annotations

from textwrap import dedent

from app.core.config import settings
from app.schemas.platform import PlatformIntegrationGuide, PlatformIntegrationGuideSnippets


class PlatformIntegrationService:
    """生成平台注册后的标准接入教程。"""

    bind_api_path = "/api/v1/aethercore/embed/bind"
    frontend_script_path = "/static/aethercore-embed.js"

    def build_guide(self, platform: dict) -> PlatformIntegrationGuide:
        platform_key = str(platform["platform_key"])
        display_name = str(platform["display_name"])
        host_secret = str(platform["host_secret"])

        return PlatformIntegrationGuide(
            platform_key=platform_key,
            display_name=display_name,
            bind_api_path=self.bind_api_path,
            frontend_script_path=self.frontend_script_path,
            snippets=PlatformIntegrationGuideSnippets(
                frontend=self._build_frontend_snippet(platform_key=platform_key),
                backend_env=self._build_backend_env_snippet(
                    platform_key=platform_key,
                    host_secret=host_secret,
                    display_name=display_name,
                ),
                backend_fastapi=self._build_backend_fastapi_snippet(
                    platform_key=platform_key,
                    display_name=display_name,
                ),
            ),
        )

    def _build_frontend_snippet(self, *, platform_key: str) -> str:
        return dedent(
            f"""\
            <script src="{self.frontend_script_path}"></script>
            <script>
              window.mountAetherCore({{
                platformKey: "{platform_key}",
                bindUrl: "{self.bind_api_path}",
                workbenchUrl: "{settings.resolved_manage_frontend_public_base_url}",
                title: "AetherCore",
                subtitle: "嵌入式工作台",
                getUserId: function () {{
                  return window.currentUser?.id || window.__USER_ID__ || "anonymous";
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

    def _build_backend_fastapi_snippet(self, *, platform_key: str, display_name: str) -> str:
        return dedent(
            f"""\
            from fastapi import APIRouter, HTTPException, Request
            from pydantic import BaseModel
            import httpx
            from your_project.settings import settings

            router = APIRouter()


            class AetherCoreBindRequest(BaseModel):
                conversation_key: str | None = None
                conversation_id: str | None = None


            @router.post("{self.bind_api_path}")
            async def bind_aethercore(payload: AetherCoreBindRequest, request: Request):
                # 按你们平台自己的登录体系替换这里的用户获取逻辑。
                user = request.state.user
                if user is None:
                    raise HTTPException(status_code=401, detail="用户未登录")

                body = {{
                    "platform_key": settings.AETHERCORE_PLATFORM_KEY,
                    "host_name": settings.AETHERCORE_HOST_NAME or "{display_name}",
                    "host_type": "custom",
                    "conversation_key": payload.conversation_key,
                    "conversation_id": payload.conversation_id,
                    "context": {{
                        "user": {{
                            "id": str(user.id),
                            "name": getattr(user, "name", None) or getattr(user, "username", None) or str(user.id),
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

                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"{{settings.AETHERCORE_API_BASE_URL.rstrip('/')}}/api/v1/host/bind",
                        headers={{"X-Aether-Platform-Secret": settings.AETHERCORE_PLATFORM_SECRET}},
                        json=body,
                    )

                if response.status_code >= 400:
                    raise HTTPException(status_code=response.status_code, detail=response.text)

                data = response.json()["data"]
                workbench_url = (
                    f"{{settings.AETHERCORE_WORKBENCH_URL}}"
                    f"?embed_token={{data['token']}}&session_id={{data['session_id']}}"
                )
                return {{
                    "data": {{
                        "token": data["token"],
                        "session_id": data["session_id"],
                        "conversation_id": data.get("conversation_id"),
                        "workbench_url": workbench_url,
                    }}
                }}
            """
        ).strip()


platform_integration_service = PlatformIntegrationService()
