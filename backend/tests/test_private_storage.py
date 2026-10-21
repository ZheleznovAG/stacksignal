"""Private state can live outside the checkout without changing environment precedence."""
import os
import runpy
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "app/config.py"


def configure(tmp_path, *, pointer=None, root_env="", private_env="", environment=None):
    project = tmp_path / "project"
    config_file = project / "backend/app/config.py"
    config_file.parent.mkdir(parents=True)
    config_file.write_bytes(SOURCE.read_bytes())
    private = tmp_path / "private"
    private.mkdir()
    (private / ".env").write_text(private_env, encoding="utf-8")
    if pointer is not None:
        root_env = f'STACKSIGNAL_PRIVATE_DIR="{pointer}"\n' + root_env
    (project / ".env").write_text(root_env, encoding="utf-8")
    with patch.dict(os.environ, {"AI_BASE_URL": "http://127.0.0.1:1", **(environment or {})}, clear=True):
        result = runpy.run_path(str(config_file))
    return result, project, private


def test_default_storage_remains_local_to_the_checkout(tmp_path):
    config, project, _ = configure(tmp_path)
    assert config["DATA_DIR"] == project / "data"
    assert config["DB_PATH"].exists() is False
    assert config["DATA_DIR"].is_dir()


def test_external_storage_loads_private_configuration_and_keeps_checkout_clean(tmp_path):
    config, project, private = configure(tmp_path, pointer="../private",
                                          private_env="GITHUB_TOKEN=private-placeholder\n")
    assert config["DATA_DIR"] == private / "data"
    assert config["DB_PATH"] == private / "data/stacksignal.db"
    assert config["GITHUB_TOKEN"] == "private-placeholder"
    assert not (project / "data").exists()


def test_process_and_checkout_settings_override_private_environment(tmp_path):
    config, _, private = configure(tmp_path, pointer="../private",
        root_env="GITHUB_LOGIN=checkout-account\nGITHUB_TOKEN=checkout-placeholder\n",
        private_env="GITHUB_LOGIN=private-account\nGITHUB_TOKEN=private-placeholder\n",
        environment={"GITHUB_TOKEN": "process-placeholder"})
    assert config["DATA_DIR"] == private / "data"
    assert config["GITHUB_LOGIN"] == "checkout-account"
    assert config["GITHUB_TOKEN"] == "process-placeholder"


def test_empty_process_setting_disables_external_storage_for_tests(tmp_path):
    config, project, _ = configure(tmp_path, pointer="../private",
                                  environment={"STACKSIGNAL_PRIVATE_DIR": ""})
    assert config["DATA_DIR"] == project / "data"
