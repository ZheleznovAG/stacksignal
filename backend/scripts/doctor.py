"""Local configuration and SQLite diagnostics. Does not contact collectors, AI or Telegram."""
import importlib.metadata
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def diagnose():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app import config
    from app.storage import database_path

    report = {"python": sys.version.split()[0], "data_directory": str(config.DATA_DIR),
              "private_storage_outside_checkout": config.PRIVATE_DIR != config.ROOT_DIR,
              "ai_enabled": config.LLM_ENABLED, "scheduler_enabled": config.SCHEDULER_ENABLED,
              "embedding_model_download_allowed": config.EMBEDDINGS_ENABLED, "dependencies": {}, "ok": True}
    for name in ("fastapi", "uvicorn", "SQLAlchemy", "alembic", "onnxruntime", "tokenizers"):
        try:
            report["dependencies"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["dependencies"][name] = "missing"
            report["ok"] = False
    path = database_path()
    report["database_path"] = str(path)
    if not path.is_file():
        report["database"] = {"status": "not_created", "next_step": "python stacksignal.py run"}
        return report
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA quick_check").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        versions = [row[0] for row in connection.execute("SELECT version_num FROM alembic_version")] if "alembic_version" in tables else []
        alembic_config = Config(str(config.ROOT_DIR / "backend/alembic.ini"))
        alembic_config.set_main_option("script_location", str(config.ROOT_DIR / "backend/alembic"))
        heads = ScriptDirectory.from_config(alembic_config).get_heads()
        current = set(versions) == set(heads)
        report["database"] = {"status": "ok" if integrity == [("ok",)] and not foreign_keys else "needs_attention",
                              "integrity": "ok" if integrity == [("ok",)] else "failed",
                              "foreign_key_violations": len(foreign_keys), "schema_versions": versions,
                              "schema_current": current, "expected_schema_versions": heads}
        report["ok"] = report["ok"] and report["database"]["status"] == "ok" and current
    finally:
        connection.close()
    return report


def main():
    try:
        result = diagnose()
    except Exception as error:
        # Configuration can contain credentials: do not print exception messages or tracebacks.
        result = {"ok": False, "error_type": type(error).__name__,
                  "next_step": "Check local configuration and run python stacksignal.py setup."}
    if sys.stdout is not None:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
