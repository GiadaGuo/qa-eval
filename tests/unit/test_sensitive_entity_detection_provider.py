from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

import qa_eval.calculators.sensitive_entity_detection.provider as module
from qa_eval.calculators.protocol import CalculatorError
from qa_eval.calculators.sensitive_entity_detection.provider import (
    IsolatedUIEProvider,
)


class _FakeProcess:
    def __init__(self, *, wait_timeouts: int = 0):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.wait_timeouts = wait_timeouts
        self.wait_calls = 0
        self.terminated = 0
        self.killed = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls <= self.wait_timeouts:
            raise subprocess.TimeoutExpired("worker", timeout)
        return 0

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1

    def poll(self):
        return None


def _assets(tmp_path: Path) -> tuple[Path, Path, Path]:
    python = tmp_path / "python"
    python.touch()
    model = tmp_path / "model"
    model.mkdir()
    (model / "pytorch_model.bin").write_bytes(b"weight")
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    return python, model, vendor


def test_constructor_failure_reaps_worker_with_kill_fallback(monkeypatch, tmp_path):
    process = _FakeProcess(wait_timeouts=2)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        IsolatedUIEProvider,
        "_read",
        lambda self: (_ for _ in ()).throw(CalculatorError("init timeout")),
    )
    python, model, vendor = _assets(tmp_path)

    with pytest.raises(CalculatorError, match="init timeout"):
        IsolatedUIEProvider(
            python=python,
            model_dir=model,
            vendor_src=vendor,
            checksum=f"sha256:{module.file_sha256(model / 'pytorch_model.bin')}",
            timeout=1,
        )

    assert process.stdin.closed
    assert process.terminated == 1
    assert process.killed == 1
    assert process.wait_calls == 3
    assert not list(IsolatedUIEProvider._active)


def test_close_is_idempotent(monkeypatch, tmp_path):
    process = _FakeProcess()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(IsolatedUIEProvider, "_read", lambda self: {"status": "READY"})
    python, model, vendor = _assets(tmp_path)
    provider = IsolatedUIEProvider(
        python=python,
        model_dir=model,
        vendor_src=vendor,
        checksum=f"sha256:{module.file_sha256(model / 'pytorch_model.bin')}",
        timeout=1,
    )

    provider.close()
    provider.close()

    assert process.wait_calls == 1
    assert provider.process is None
    assert not list(IsolatedUIEProvider._active)
