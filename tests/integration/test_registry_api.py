from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_db_session
from apps.api.main import create_app
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.registry import CalculatorImplementationModel


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
        synced = client.post("/api/v1/registry/sync")
        readiness = client.get("/api/v1/registry/readiness")
        metrics = client.get("/api/v1/metrics")
        calculators = client.get("/api/v1/calculators")
        bindings = client.get(
            "/api/v1/metric-calculator-bindings", params={"metric_key": "02080103"}
        )
        detail = client.get("/api/v1/metrics/02080103/versions/1.0.0")

        with Session(engine) as session:
            descriptor = (
                session.query(CalculatorImplementationModel)
                .filter_by(calculator_key="text_duplicate_degree")
                .one()
            )
            descriptor.document = {
                **descriptor.document,
                "entrypoint": "qa_eval.calculators.text_duplicate_degree:calculate",
            }
            session.commit()
        stale_readiness = client.get("/api/v1/registry/readiness")

    assert synced.status_code == 200
    assert readiness.status_code == 200
    # Placeholders are registered but not executable; overall readiness only
    # reflects calculators that declare themselves AVAILABLE.
    assert readiness.json()["ready"] is True
    assert readiness.json()["calculators"][0]["error"] is None
    assert readiness.json()["calculators"][0]["availability"]["status"] == (
        "UNAVAILABLE"
    )
    assert stale_readiness.status_code == 200
    assert stale_readiness.json()["ready"] is False
    stale_full = next(
        item
        for item in stale_readiness.json()["calculators"]
        if item["calculator_key"] == "text_duplicate_degree"
    )
    assert stale_full["error"]["code"] == "CALCULATOR_LOAD_ERROR"
    assert synced.json() == {"metrics": 15, "calculators": 16, "bindings": 16}
    assert metrics.status_code == 200
    assert {item["metric_key"] for item in metrics.json()} == {
        "0201", "0202", "0203", "0204", "0205", "0206", "0207", "0208",
        "02080101", "02080102", "02080103", "02080104", "02080105", "02080106",
        "02080107",
    }
    calculator_keys = {item["calculator_key"] for item in calculators.json()}
    assert {"exact_text_duplicate", "structural_completeness", "text_duplicate_degree"} <= calculator_keys
    assert "format_compliance" in calculator_keys
    placeholders = [
        item for item in calculators.json()
        if item["availability"]["status"] != "AVAILABLE"
    ]
    assert len(placeholders) == 13
    assert all(item["entrypoint"] is None for item in placeholders)
    assert bindings.json()[0]["enabled"] is True
    assert len(detail.json()["measurements"]) == 6
