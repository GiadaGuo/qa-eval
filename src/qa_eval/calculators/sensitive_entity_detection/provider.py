from __future__ import annotations

import hashlib
import json
import os
import select
import subprocess
import threading
import weakref
from pathlib import Path
from typing import ClassVar

from qa_eval.calculators.protocol import CalculatorError, CalculatorValidationError


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class IsolatedUIEProvider:
    _active: ClassVar[weakref.WeakSet[IsolatedUIEProvider]] = weakref.WeakSet()
    _active_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        *,
        python: Path,
        model_dir: Path,
        vendor_src: Path,
        checksum: str,
        timeout: float,
    ):
        self.process: subprocess.Popen[str] | None = None
        self._close_lock = threading.Lock()
        self.python = python.resolve()
        self.model_dir = model_dir.resolve()
        self.vendor_src = vendor_src.resolve()
        self.timeout = timeout
        weight = self.model_dir / "pytorch_model.bin"
        for path, kind in (
            (self.python, "ner_python"),
            (weight, "model weight"),
            (self.vendor_src, "vendor source"),
        ):
            if not path.exists():
                raise CalculatorValidationError(
                    f"{kind} is unavailable", path=str(path)
                )
        actual = file_sha256(weight)
        expected = checksum.removeprefix("sha256:")
        if actual != expected:
            raise CalculatorValidationError(
                "model checksum mismatch", expected=expected, actual=actual
            )
        worker = Path(__file__).resolve().with_name("ner_worker.py")
        env = {
            **os.environ,
            "TOKENIZERS_PARALLELISM": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        try:
            self.process = subprocess.Popen(
                [
                    str(self.python),
                    "-u",
                    str(worker),
                    str(self.model_dir),
                    str(self.vendor_src),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
                start_new_session=os.name == "posix",
            )
            with self._active_lock:
                self._active.add(self)
            ready = self._read()
            if ready.get("status") != "READY":
                raise CalculatorError(
                    "UIE worker failed to initialize",
                    code=ready.get("code", "MODEL_INIT_FAILED"),
                )
        except BaseException:
            self.close()
            raise

    def _read(self) -> dict:
        process = self.process
        if process is None or process.stdout is None:
            raise CalculatorError("UIE worker is not running")
        ready, _, _ = select.select([process.stdout], [], [], self.timeout)
        if not ready:
            raise CalculatorError("UIE worker timed out", timeout_seconds=self.timeout)
        line = process.stdout.readline()
        if not line:
            raise CalculatorError(
                "UIE worker exited unexpectedly", returncode=process.poll()
            )
        return json.loads(line)

    def infer(self, text: str, request_id: str) -> list[dict]:
        process = self.process
        if process is None or process.stdin is None:
            raise CalculatorError("UIE worker is not running")
        try:
            process.stdin.write(
                json.dumps({"request_id": request_id, "text": text}, ensure_ascii=False)
                + "\n"
            )
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise CalculatorError(
                "UIE worker exited unexpectedly", returncode=process.poll()
            ) from exc
        response = self._read()
        if response.get("status") != "OK":
            raise CalculatorError(
                "UIE inference failed", code=response.get("code", "INFERENCE_FAILED")
            )
        return response["entities"]

    def close(self) -> None:
        with self._close_lock:
            process = self.process
            if process is None:
                return
            self.process = None
            with self._active_lock:
                self._active.discard(self)
        if process.stdin is not None:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

    @classmethod
    def close_all(cls) -> None:
        with cls._active_lock:
            active = list(cls._active)
        for provider in active:
            provider.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
