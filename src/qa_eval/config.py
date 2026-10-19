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

    model_config = SettingsConfigDict(
        env_prefix="QA_EVAL_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

