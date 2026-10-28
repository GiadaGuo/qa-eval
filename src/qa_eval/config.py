# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 10:43:04
# @Description:
# 1. 加载优先级：系统环境变量 > .env 文件内变量 > 代码里写的默认值
# 2. @lru_cache：全局单例，只实例化一次 Settings，避免重复读取 `.env`、重复解析，FastAPI 标准最佳实践
# ====================================================


from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "QA Evaluation System"
    app_env: str = "development"
    api_prefix: str = "/api/v1"

    database_url: str = (
        "postgresql+psycopg://qa_eval:"
        "change_me_local_only@127.0.0.1:5432/qa_eval"
    )

    test_database_url: str | None = None

    # Artifact storage configuration
    artifact_store_type: str = "local"  # "local" or "s3"
    artifact_store_local_root: str = "data/artifacts"
    artifact_store_s3_bucket: str | None = None
    artifact_store_s3_prefix: str = "qa-eval"
    artifact_store_s3_endpoint_url: str | None = None
    artifact_store_s3_region_name: str | None = None
    artifact_store_s3_access_key_id: str | None = None
    artifact_store_s3_secret_access_key: str | None = None

    # LLM（OpenAI 兼容网关）与说明文档评测配置
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = ""
    llm_temperature: float = 0.1
    llm_timeout_seconds: float = 60.0
    llm_transport_retries: int = 1
    llm_max_output_tokens: int = 4096
    document_rubric_path: str = (
        "configs/document-evaluation/rubric/1.0.0.json"
    )
    document_max_validation_retries: int = 2
    document_max_prompt_lines: int = 4000
    document_max_prompt_chars: int = 400_000
    document_executor_workers: int = 2

    # Agent V1 bounded runtime
    agent_tool_timeout_seconds: float = 10.0
    agent_max_tool_calls_per_turn: int = 4
    agent_llm_enabled: bool = False

    model_config = SettingsConfigDict(
        env_prefix="QA_EVAL_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

