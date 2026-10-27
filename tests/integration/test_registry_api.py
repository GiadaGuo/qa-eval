from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_db_session
from apps.api.main import create_app
from qa_eval.infrastructure.db.base import Base


def test_registry_read_api() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    app = create_app()

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session
            session.commit()

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as client:
        metrics = client.get("/api/v1/metrics")
        calculators = client.get("/api/v1/calculators")
        bindings = client.get(
            "/api/v1/metric-calculator-bindings", params={"metric_key": "02080103"}
        )
        detail = client.get("/api/v1/metrics/02080103/versions/1.0.0")

    assert metrics.status_code == 200
    assert metrics.json()[0]["metric_key"] == "02080103"
    assert calculators.json()[0]["calculator_key"] == "text_duplicate_degree"
    assert bindings.json()[0]["enabled"] is True
    assert len(detail.json()["measurements"]) == 6
