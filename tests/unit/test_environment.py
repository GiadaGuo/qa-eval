
import sys

import fastapi
import pydantic
import pytest


def test_python_version():
    assert sys.version_info[:2] == (3, 12)


def test_core_dependencies():
    assert fastapi.__version__
    assert pydantic.__version__
    assert pytest.__version__


def test_basic_calculation():
    assert sum([1, 2, 3]) == 6
