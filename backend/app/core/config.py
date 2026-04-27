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
    llm_max_tokens: int = 4000
    llm_network_enabled: bool = True
    llm_network_allowed_domains: list[str] = Field(default_factory=list)
    llm_network_blocked_domains: list[str] = Field(default_factory=list)
    llm_network_max_search_results: int = 8
    llm_network_fetch_timeout_seconds: int = 30
    llm_network_fetch_max_bytes: int = 2 * 1024 * 1024
    llm_network_user_agent: str = "AetherCore/1.0"

    auth_secret_key: str = "aethercore-dev-secret-key"
    auth_algorithm: str = "HS256"
    auth_access_token_expire_minutes: int = 60 * 24 * 7
    auth_embed_token_expire_minutes: int = 60 * 24
    auth_system_admin_username: str = "admin"
    auth_system_admin_password: str = "admin123456"
    auth_oauth_providers: str = ""
    auth_oauth_config_json: str = ""

    agent_max_turns: int = 0
    agent_max_runtime_seconds: int = 1800
    agent_max_stall_rounds: int = 0

    storage_root: Path = Path("storage")
    sessions_dir_name: str = "sessions"
    built_in_skills_dir: Path = Path("storage/built_in_skills")
    metadata_db_name: str = "aethercore.db"

    sandbox_executor: str = "docker"
    sandbox_fail_closed: bool = True
    sandbox_local_enabled: bool = False
    sandbox_shell: str = "bash"
    sandbox_command_timeout_seconds: int = 120
    sandbox_output_char_limit: int = 12000
    sandbox_file_read_limit_bytes: int = 131072
    sandbox_allow_network: bool = False
    sandbox_env_whitelist: list[str] = Field(
        default_factory=lambda: ["PATH", "PATHEXT", "SYSTEMROOT", "COMSPEC"]
    )
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
    sandbox_docker_command: str = "docker"
    sandbox_docker_image: str = "aethercore-sandbox:latest"
    sandbox_docker_workspace_mount: str = "/workspace"
    sandbox_docker_work_dir: str = "/workspace/work"
    sandbox_docker_input_dir: str = "/workspace/input"
    sandbox_docker_output_dir: str = "/workspace/output"
    sandbox_docker_skills_dir: str = "/workspace/skills"
    sandbox_docker_logs_dir: str = "/workspace/logs"
    sandbox_docker_user: str = "sandbox"
    sandbox_docker_memory: str = "1g"
    sandbox_docker_cpus: str = "1.0"
    sandbox_docker_pids_limit: int = 128
    sandbox_docker_read_only_rootfs: bool = True
    sandbox_docker_tmpfs: list[str] = Field(
        default_factory=lambda: [
            "/tmp:size=256m",
            "/var/tmp:size=64m",
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
    def resolved_storage_root(self) -> Path:
        if self.storage_root.is_absolute():
            return self.storage_root
        # 存储根目录固定落在 backend/storage，避免受启动 cwd 影响读到项目根目录下的另一套运行态数据。
        return self.backend_root / self.storage_root

    @property
    def sessions_root(self) -> Path:
        return self.resolved_storage_root / self.sessions_dir_name

    @property
    def metadata_db_path(self) -> Path:
        return self.resolved_storage_root / self.metadata_db_name

    @property
    def platform_baselines_root(self) -> Path:
        return self.resolved_storage_root / "platform_baselines"


settings = Settings()
