"""应用配置（pydantic-settings）。所有值来自环境变量 / `.env`。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "同舟行"
    environment: str = "development"
    main_agent_v2_enabled: bool = False
    main_agent_typed_runtime_enabled: bool = False

    # CORS：前端开发服务器与容器内 nginx 来源
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]

    # —— 数据库 / Redis ——
    # 默认指向本机（host 直连容器暴露端口）；容器内由 compose 覆盖为服务名。
    database_url: str = "postgresql+asyncpg://dyflow:dyflow_dev_pw@localhost:5432/dyflow"
    redis_url: str = "redis://localhost:6379/0"

    # Agent runtime: production runs execute in ARQ; tests/local can keep the
    # synchronous path until Redis and a worker are available.
    agent_runtime_async_enabled: bool = False
    agent_run_lease_seconds: int = 120
    agent_run_max_attempts: int = 3
    langgraph_checkpoint_enabled: bool = False
    agent_runtime_max_rounds: int = 8
    agent_runtime_max_expert_calls: int = 12
    agent_runtime_max_expert_calls_per_code: int = 3
    agent_runtime_max_tool_calls: int = 20
    agent_runtime_max_tokens: int = 100_000
    agent_runtime_max_cost_usd: float = 5.0
    agent_runtime_max_elapsed_seconds: int = 900
    agent_runtime_memory_event_threshold: int = 8
    agent_runtime_memory_char_threshold: int = 12_000
    agent_runtime_context_char_budget: int = 8_000
    agent_runtime_memory_auto_compact_enabled: bool = False

    # 连接池
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # —— 认证（JWT）——
    jwt_secret: str = "dev-only-change-me-please-min-32-bytes-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720

    # —— 初始管理员种子（app/seed.py 用）——
    admin_email: str = "admin@dyflow.local"
    admin_password: str = "change-me-admin-password"
    default_org_name: str = "DyFlow"

    # —— 大模型网关（v1 默认 DeepSeek）——
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_default_model: str = "deepseek-chat"
    llm_deterministic_test_provider_enabled: bool = False

    # —— 视频生成 ——
    # 火山引擎方舟 Ark（豆包视频模型；账号实际可用 Seedance 1.0-pro；M1 E7 接入）
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_video_model: str = "doubao-seedance-1-0-pro-250528"
    # Seedance 独立 API（后期接入，与火山方舟是不同供应商）
    seedance_api_key: str = ""

    # —— 抖音开放平台 ——
    douyin_client_key: str = ""
    douyin_client_secret: str = ""
    douyin_oauth_worker_secret: str = ""
    douyin_h5_publish_enabled: bool = False
    douyin_posting_task_enabled: bool = False
    douyin_direct_publish_enabled: bool = False
    credential_encryption_key: str = ""

    # —— 对象存储（v1 本地卷，MinIO 接口预留；T2 暂不接入实际读写）——
    storage_backend: str = "local"
    storage_local_dir: str = "/data/objects"


settings = Settings()
