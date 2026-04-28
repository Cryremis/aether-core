# backend/app/main.py
from app.api.routes.auth import router as auth_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.agent import router as agent_router
from app.api.routes.files import router as files_router
from app.api.routes.health import router as health_router
from app.api.routes.host import router as host_router
from app.api.routes.llm import router as llm_router
from app.api.routes.platforms import router as platforms_router
from app.api.routes.prompts import router as prompts_router
from app.api.routes.sessions import router as sessions_router
from app.api.routes.skills import router as skills_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.services.skill_service import skill_service
from app.services.store import store_service

configure_logging()
skill_service.ensure_built_in_layout()
store_service.initialize()

app = FastAPI(title=settings.app_name, debug=settings.app_debug)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(llm_router)
app.include_router(prompts_router)
app.include_router(platforms_router)
app.include_router(host_router)
app.include_router(agent_router)
app.include_router(files_router)
app.include_router(skills_router)
app.include_router(sessions_router)


@app.get("/")
def root() -> dict[str, str]:
    """服务入口。"""

    return {"name": settings.app_name, "status": "ok"}
