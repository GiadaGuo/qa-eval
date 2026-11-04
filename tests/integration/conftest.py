# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 14:49:37
# @LastEditTime: 2026-10-19 15:18:07
# @Description: 
# ====================================================

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import qa_eval.infrastructure.db.models  # noqa: F401
from qa_eval.config import get_settings
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.session import create_database_engine


@pytest.fixture
def db_session(tmp_path):
    settings = get_settings()
    database_url = settings.test_database_url

    if not database_url:
        pytest.fail(
            "QA_EVAL_TEST_DATABASE_URL is not configured"
        )

    parsed_url = make_url(database_url)

    if parsed_url.get_backend_name() == "sqlite":
        engine = create_database_engine(
            f"sqlite:///{tmp_path / 'qa-eval-test.sqlite3'}"
        )
        Base.metadata.create_all(engine)
    elif parsed_url.database == "qa_eval_test":
        engine = create_engine(database_url, pool_pre_ping=True)
    else:
        pytest.fail(
            "PostgreSQL integration tests must use qa_eval_test"
        )

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            session = Session(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )

            try:
                if engine.dialect.name == "postgresql":
                    actual_db = session.execute(
                        text("SELECT current_database()")
                    ).scalar_one()
                    assert actual_db == "qa_eval_test"

                yield session

            finally:
                session.close()
                transaction.rollback()

    finally:
        engine.dispose()
