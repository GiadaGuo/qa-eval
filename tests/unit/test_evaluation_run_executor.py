from __future__ import annotations

from uuid import uuid4

import apps.api.routes.evaluation_runs as module


class _FakeExecutor:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.submissions = []
        self.shutdown_calls = []

    def submit(self, function, *args):
        self.submissions.append((function, args))

    def shutdown(self, **kwargs):
        self.shutdown_calls.append(kwargs)


def test_executor_is_lazy_and_recreated_after_shutdown(monkeypatch):
    executors = []

    def factory(**kwargs):
        executor = _FakeExecutor(**kwargs)
        executors.append(executor)
        return executor

    monkeypatch.setattr(module, "ThreadPoolExecutor", factory)
    monkeypatch.setattr(module, "_executor", None)

    module.dispatch_evaluation_run(uuid4())
    module.shutdown_evaluation_executor()
    module.dispatch_evaluation_run(uuid4())
    module.shutdown_evaluation_executor()

    assert len(executors) == 2
    assert all(item.kwargs["max_workers"] == 1 for item in executors)
    assert all(
        item.shutdown_calls == [{"wait": True, "cancel_futures": True}]
        for item in executors
    )
