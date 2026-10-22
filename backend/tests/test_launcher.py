"""The portable entry point must preserve configuration and forward argv without a shell."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "stacksignal.py"
spec = importlib.util.spec_from_file_location("stacksignal_launcher", SOURCE)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.fixture()
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout with spaces"
    (root / "backend").mkdir(parents=True)
    for name in [*launcher.PROFILES.values(), "constraints.txt"]:
        (root / "backend" / name).write_text("# fixture\n", encoding="utf-8")
    (root / ".env.example").write_text("AI_ENABLED=0\n", encoding="utf-8")
    environment = root / ".venv"
    python = environment / "bin/python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    monkeypatch.setattr(launcher, "ROOT", root)
    monkeypatch.setattr(launcher, "ENVIRONMENT", environment)
    monkeypatch.setattr(launcher.subprocess, "check_output", lambda *a, **k: "3.12\n")
    calls = []
    monkeypatch.setattr(launcher, "execute", lambda args, **kwargs: calls.append((args, kwargs)) or 0)
    return root, python, calls


@pytest.mark.parametrize("relative", ["bin/python", "Scripts/python.exe"])
def test_both_venv_layouts_are_found_without_activating_a_shell(tmp_path, monkeypatch, relative):
    python = tmp_path / relative
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    monkeypatch.setattr(launcher, "ENVIRONMENT", tmp_path)
    assert launcher.environment_python() == python


def test_setup_is_cached_but_requirements_changes_trigger_install_and_env_is_preserved(checkout):
    root, python, calls = checkout
    (root / ".env").write_text("STACKSIGNAL_PRIVATE_DIR=../personal\n", encoding="utf-8")
    launcher.setup("dev")
    assert len(calls) == 1 and calls[0][0][0] == str(python)
    launcher.setup("dev")
    launcher.setup("runtime")
    assert len(calls) == 1
    (root / "backend/constraints.txt").write_text("# changed\n", encoding="utf-8")
    launcher.setup("dev")
    assert len(calls) == 2
    assert (root / ".env").read_text(encoding="utf-8") == "STACKSIGNAL_PRIVATE_DIR=../personal\n"


def test_failed_install_cannot_stamp_an_environment_as_ready(checkout, monkeypatch):
    root, _, _ = checkout
    monkeypatch.setattr(launcher, "execute", lambda *a, **k: 1)
    with pytest.raises(launcher.LauncherError, match="stamp was not updated"):
        launcher.setup()
    assert not (root / ".venv/.stacksignal-deps.json").exists()
    assert not (root / ".env").exists()


def test_browser_setup_installs_all_profiles_and_chromium(checkout):
    root, _, calls = checkout
    launcher.setup("browser", install_browser=True)
    assert len(calls) == 2 and calls[1][0][-3:] == ["playwright", "install", "chromium"]
    assert set(json.loads((root / ".venv/.stacksignal-deps.json").read_text())) == set(launcher.PROFILES)
    assert (root / ".env").read_text() == (root / ".env.example").read_text()


def test_run_uses_loopback_and_absolute_paths(checkout, monkeypatch):
    root, python, calls = checkout
    monkeypatch.setattr(launcher, "setup", lambda: python)
    assert launcher.main(["run", "--port", "8123"]) == 0
    arguments = calls[0][0]
    assert arguments[arguments.index("--host") + 1] == "127.0.0.1"
    assert arguments[arguments.index("--app-dir") + 1] == str(root / "backend")
    assert arguments[-2:] == ["--port", "8123"]


def test_pytest_and_python_arguments_keep_their_order(checkout):
    root, python, calls = checkout
    assert launcher.main(["test", "--browser", "--", "-k", "name with spaces"]) == 0
    assert calls[-1] == ([str(python), "-m", "pytest", "e2e", "-q", "-k", "name with spaces"], {"cwd": root / "backend"})
    assert launcher.main(["exec", "--", "-c", "print('literal $value')"]) == 0
    assert calls[-1][0] == [str(python), "-c", "print('literal $value')"]


def test_dependency_fingerprint_is_identical_for_lf_and_crlf(checkout):
    root, _, _ = checkout
    before = launcher.dependency_fingerprint("dev", "3.12")
    (root / "backend/requirements.txt").write_bytes(b"# fixture\r\n")
    assert launcher.dependency_fingerprint("dev", "3.12") == before


def test_keyboard_interrupt_waits_for_and_then_terminates_owned_child(tmp_path, monkeypatch):
    class Child:
        waits = 0
        terminated = False

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            if self.waits == 2:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return 0

        def terminate(self):
            self.terminated = True

    child = Child()
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: child)
    # The fixture above normally mocks execute, so this test uses its real lifecycle.
    assert launcher.execute(["unused"], cwd=tmp_path) == 130
    assert child.terminated and child.waits == 3


def test_headless_interpreter_supplies_valid_standard_streams(tmp_path, monkeypatch):
    captured = {}

    class Child:
        def wait(self):
            return 0

    def spawn(*args, **kwargs):
        captured.update(kwargs)
        return Child()

    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    with monkeypatch.context() as patch:
        for name in ("stdin", "stdout", "stderr"):
            patch.setattr(launcher.sys, name, None)
        code = launcher.execute(["unused"], cwd=tmp_path)
    assert code == 0
    assert all(captured[name] == subprocess.DEVNULL for name in ("stdin", "stdout", "stderr"))
