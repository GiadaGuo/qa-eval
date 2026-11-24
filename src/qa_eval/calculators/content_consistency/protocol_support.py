"""Source identity shared by the loader and local caches."""

import hashlib
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def source_paths():
    parser = ROOT.parent / "policy_structure_compliance"
    return (
        *ROOT.glob("*.py"),
        *(parser / name for name in ("parsers.py", "html_source_map.py", "schema.py")),
    )


@cache
def implementation_fingerprint():
    digest = hashlib.sha256()
    for path in sorted(source_paths()):
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
