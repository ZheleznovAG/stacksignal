"""Portable StackSignal entry point. Requires Python 3.11+; no shell activation is needed."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
ENVIRONMENT = ROOT / ".venv"
PROFILES = {"runtime": "requirements.txt", "dev": "requirements-dev.txt", "browser": "requirements-e2e.txt"}
SCRIPTS = {path.stem.replace("_", "-"): path for path in (ROOT / "backend/scripts").glob("*.py")}


class LauncherError(ValueError):
    pass


def say(message: str) -> None:
    if sys.stdout is not None:
        print(message, flush=True)


def environment_python() -> Path:
    # venv defines these two layouts. Only this boundary needs to know their names.
    for relative in ("bin/python", "Scripts/python.exe"):
        candidate = ENVIRONMENT / relative
        if candidate.is_file():
            return candidate
    raise LauncherError("No usable .venv interpreter. Run 'python stacksignal.py setup'; rename a broken .venv first.")


def execute(arguments: list[str], *, cwd: Path = ROOT, log_file: Path | None = None) -> int:
    """Forward arguments as an argv list and own the child until it exits."""
    output = None
    try:
        if log_file is not None:
            log_file = log_file.expanduser().resolve()
            log_file.parent.mkdir(parents=True, exist_ok=True)
            output = log_file.open("ab")
        child = subprocess.Popen(arguments, cwd=cwd,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
            stdin=None if sys.stdin is not None else subprocess.DEVNULL,
            stdout=output if output is not None else None if sys.stdout is not None else subprocess.DEVNULL,
            stderr=subprocess.STDOUT if output is not None else None if sys.stderr is not None else subprocess.DEVNULL)
        try:
            return child.wait()
        except KeyboardInterrupt:
            # Terminal Ctrl+C reaches the child too. Allow its normal shutdown first.
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            return 130
    finally:
        if output is not None:
            output.close()


def dependency_fingerprint(profile: str, python_version: str) -> str:
    names = ["requirements.txt", "constraints.txt"]
    if profile in ("dev", "browser"):
        names.append("requirements-dev.txt")
    if profile == "browser":
        names.append("requirements-e2e.txt")
    value = hashlib.sha256(python_version.encode())
    for name in names:
        value.update(name.encode())
        value.update((ROOT / "backend" / name).read_bytes().replace(b"\r\n", b"\n"))
    return value.hexdigest()


def setup(profile: str = "runtime", *, force: bool = False, install_browser: bool = False) -> Path:
    if sys.version_info < (3, 11):
        raise LauncherError("Python 3.11 or newer is required.")
    if not ENVIRONMENT.exists():
        say("Creating .venv...")
        venv.EnvBuilder(with_pip=True).create(ENVIRONMENT)
    python = environment_python()
    version = subprocess.check_output([str(python), "-c", "import sys; print('%s.%s' % sys.version_info[:2])"],
                                      cwd=ROOT, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8").strip()
    if tuple(map(int, version.split("."))) < (3, 11):
        raise LauncherError("The existing .venv uses Python older than 3.11; rename it and run setup with a supported Python.")
    stamp = ENVIRONMENT / ".stacksignal-deps.json"
    try:
        installed = json.loads(stamp.read_text(encoding="utf-8"))
        if not isinstance(installed, dict):
            installed = {}
    except (OSError, ValueError):
        installed = {}
    wanted = dependency_fingerprint(profile, version)
    if force or installed.get(profile) != wanted:
        say(f"Installing {profile} dependencies constrained by backend/constraints.txt...")
        code = execute([str(python), "-m", "pip", "--disable-pip-version-check", "install", "--no-input", "-r", str(ROOT / "backend" / PROFILES[profile]),
                        "-c", str(ROOT / "backend/constraints.txt")])
        if code:
            raise LauncherError(f"Dependency installation failed (exit {code}); the setup stamp was not updated.")
        covered = ["runtime"] + (["dev"] if profile in ("dev", "browser") else []) + (["browser"] if profile == "browser" else [])
        for name in covered:
            installed[name] = dependency_fingerprint(name, version)
        stamp.write_text(json.dumps(installed, indent=2) + "\n", encoding="utf-8")
    if not (ROOT / ".env").exists():
        # Exclusive creation also protects an .env created by another launcher.
        try:
            with (ROOT / ".env").open("xb") as output, (ROOT / ".env.example").open("rb") as source:
                shutil.copyfileobj(source, output)
        except FileExistsError:
            pass
    if install_browser:
        code = execute([str(python), "-m", "playwright", "install", "chromium"])
        if code:
            raise LauncherError(f"Chromium installation failed (exit {code}).")
    return python


def remainder(arguments: list[str]) -> list[str]:
    return arguments[1:] if arguments[:1] == ["--"] else arguments


def port(value: str) -> int:
    result = int(value)
    if not 1 <= result <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1 and 65535.")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("setup", help="Create .venv and install pinned dependencies")
    install.add_argument("--dev", action="store_true", help="Include backend test dependencies")
    install.add_argument("--browser", action="store_true", help="Include browser tests and install Chromium")
    install.add_argument("--force", action="store_true", help="Reinstall even when the dependency fingerprint matches")
    run = commands.add_parser("run", help="Run the local service in the foreground")
    run.add_argument("--port", type=port, default=8000)
    run.add_argument("--reload", action="store_true", help="Development only: reload on source changes")
    run.add_argument("--log-file", type=Path, help="Append server output to this file (for an OS service manager)")
    commands.add_parser("doctor", help="Check configuration, installed dependencies and the database without network calls")
    backup = commands.add_parser("backup", help="Create a consistent snapshot of the configured SQLite database")
    backup.add_argument("target", nargs="?", type=Path)
    test = commands.add_parser("test", help="Run isolated backend tests, or --browser for browser tests")
    test.add_argument("--browser", action="store_true")
    test.add_argument("args", nargs=argparse.REMAINDER, help="Additional pytest arguments after --")
    script = commands.add_parser("script", help="Run a backend maintenance/agent script using .venv")
    script.add_argument("name", choices=sorted(SCRIPTS))
    script.add_argument("args", nargs=argparse.REMAINDER)
    extra = commands.add_parser("exec", help="Run the .venv Python; put interpreter arguments after --")
    extra.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            setup("browser" if args.browser else "dev" if args.dev else "runtime", install_browser=args.browser, force=args.force)
            say("Setup complete. Run: python stacksignal.py run")
            return 0
        # Operational commands use the existing environment; only run bootstraps automatically.
        python = setup() if args.command == "run" else environment_python()
        if args.command == "run":
            say(f"StackSignal: http://localhost:{args.port}")
            arguments = [str(python), "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "backend"),
                         "--host", "127.0.0.1", "--port", str(args.port)]
            if args.reload:
                arguments.append("--reload")
            return execute(arguments, log_file=args.log_file)
        if args.command == "doctor":
            return execute([str(python), str(SCRIPTS["doctor"])])
        if args.command == "backup":
            return execute([str(python), str(SCRIPTS["backup"]), *([str(args.target.resolve())] if args.target else [])])
        if args.command == "test":
            installed = ENVIRONMENT / ".stacksignal-deps.json"
            if not installed.exists():
                say("Tip: python stacksignal.py setup --dev (or --browser) installs test dependencies.")
            return execute([str(python), "-m", "pytest", *(["e2e"] if args.browser else []), "-q", *remainder(args.args)], cwd=ROOT / "backend")
        if args.command == "script":
            return execute([str(python), str(SCRIPTS[args.name]), *remainder(args.args)])
        arguments = remainder(args.args)
        if not arguments:
            raise LauncherError("Supply Python arguments, for example: exec -- -m pip check")
        return execute([str(python), *arguments])
    except (LauncherError, OSError, subprocess.CalledProcessError) as error:
        if sys.stderr is not None:
            print(f"StackSignal: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
