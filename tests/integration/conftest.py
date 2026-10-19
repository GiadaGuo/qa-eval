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

from qa_eval.config import get_settings


@pytest.fixture
def db_session():
    settings = get_settings()
    database_url = settings.test_database_url

    if not database_url:
        pytest.fail(
            "QA_EVAL_TEST_DATABASE_URL is not configured"
        )

    parsed_url = make_url(database_url)

    if parsed_url.database != "qa_eval_test":
        pytest.fail(
            "Integration tests must use qa_eval_test"
        )

    engine = create_engine(
        database_url,
        pool_pre_ping=True,
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
