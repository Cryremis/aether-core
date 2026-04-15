# backend/app/core/config.py
from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AetherCore 后端配置。"""

    app_name: str = "AetherCore Backend"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8100
    app_debug: bool = True

    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    llm_provider: str = "openai_compatible"
    llm_provider_name: str = "volcengine"
    llm_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    llm_model: str = "ep-20260214160206-k72dd"
    llm_api_key: str = ""
    llm_timeout_seconds: int = 180
    llm_max_steps: int = 8
    llm_max_tokens: int = 4000

    storage_root: Path = Path("storage")
    sessions_dir_name: str = "sessions"
    built_in_skills_dir: Path = Path("storage/built_in_skills")

    sandbox_shell: str = "powershell"
    sandbox_command_timeout_seconds: int = 120
    sandbox_output_char_limit: int = 12000
    sandbox_file_read_limit_bytes: int = 131072
    sandbox_allow_network: bool = False
    sandbox_blocked_command_keywords: list[str] = Field(
        default_factory=lambda: [
            "curl ",
            "wget ",
            "invoke-webrequest",
            "irm ",
            "scp ",
            "ssh ",
            "ftp ",
            "telnet ",
            "docker ",
            "kubectl ",
            "shutdown",
            "restart-computer",
            "stop-computer",
            "format ",
            "format-volume",
            "mount ",
            "dism ",
        ]
    )

    manage_backend_port: int = 8100
    manage_frontend_port: int = 5178

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @cached_property
    def backend_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @cached_property
    def project_root(self) -> Path:
        return self.backend_root.parent

    @property
    def sessions_root(self) -> Path:
        return self.storage_root / self.sessions_dir_name


settings = Settings()
