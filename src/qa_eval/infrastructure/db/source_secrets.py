"""Local credential storage, separate from metadata and artifacts."""

import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID

from qa_eval.paths import resolve_project_path


def _password_path(dataset_id: UUID) -> Path:
    root = resolve_project_path(
        os.environ.get("QA_EVAL_SOURCE_SECRET_DIR", "data/runtime/source-secrets")
    )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError("数据库凭证目录必须仅对服务账号可访问")
    return root / str(dataset_id)


def save_source_password(dataset_id: UUID, password: str) -> None:
    path = _password_path(dataset_id)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(password)
            handle.flush()
            os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def load_source_password(dataset_id: UUID) -> str:
    path = _password_path(dataset_id)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise PermissionError("数据库凭证文件必须仅对服务账号可访问")
        return handle.read()
