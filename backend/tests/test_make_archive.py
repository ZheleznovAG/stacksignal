"""Regression tests for the repository exporter without invoking Git or creating a full bundle."""

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "make_archive.py"
spec = importlib.util.spec_from_file_location("make_archive", SCRIPT)
make_archive = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(make_archive)


def test_stale_projectpack_directory_is_always_excluded():
    assert make_archive.is_secret("projectpack-deadbeef/stacksignal.db")
    assert make_archive.is_secret("projectpack-deadbeef/nested/source.py")


def test_normal_source_and_env_example_are_not_accidentally_excluded():
    assert not make_archive.is_secret("backend/app/chat.py")
    assert not make_archive.is_secret(".env.example")
