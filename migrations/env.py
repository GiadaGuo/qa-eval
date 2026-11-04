# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 14:25:48
# @Description: 
# - 导入 qa_eval.infrastructure.db.models 是为了让 DatasetModel 注册到 Base.metadata
# ====================================================

from logging.config import fileConfig

from alembic import context

import qa_eval.infrastructure.db.models  # noqa: F401
from qa_eval.config import get_settings
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.session import (
    create_database_engine,
    normalize_database_url,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()

config.set_main_option(
    "sqlalchemy.url",
    normalize_database_url(settings.database_url).render_as_string(
        hide_password=False
    ).replace("%", "%%"),
)

# 目标数据库结构由 SQLAlchemy ORM 模型定义
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    is_sqlite = url.startswith("sqlite:")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=is_sqlite,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_database_engine(
        config.get_main_option("sqlalchemy.url")
    )

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                render_as_batch=connection.dialect.name == "sqlite",
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
