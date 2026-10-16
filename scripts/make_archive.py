"""Filtered working tree + Git bundle + status + staged patch; never raw .git.

Symlinks, junctions and submodules are refused. Every exported history object is
checked for known .env values and common credentials. Detection is best-effort:
arbitrary unknown secrets in source/history cannot be guaranteed absent.

Usage: python scripts/make_archive.py [output.zip] [--include-data]
With --include-data, also exports local data and a consistent SQLite snapshot.
"""
import argparse
from contextlib import closing
import datetime
import fnmatch
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = [".env", ".env.*", "client.json", "*.pem", "*.key", "*.p12", "*.pfx",
                   "id_rsa*", "id_ed25519*", "*secret*", "data/*", "*.zip"]
SECRET_ENV_KEY = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$", re.IGNORECASE)
PRIVATE_KEY = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")
URL_CREDENTIALS = re.compile(rb"https?://[^\s/@:]+:([^\s/@]+)@")
# Test fixtures that exercise credential rejection use obvious placeholders.
PLACEHOLDER_PASSWORDS = {b"pass", b"password", b"secret", b"x", b"***", b"xxx", b"changeme"}
METADATA = "_projectpack"
TEMP_EXPORT_PREFIX = "projectpack-"


class ArchiveError(ValueError):
    pass


def git(*args: str, cwd=None, input=None) -> bytes:
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", *args], cwd=cwd or ROOT, env=env, input=input,
                          check=True, capture_output=True).stdout


def is_secret(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts or any(part.lower() == ".git" for part in parts):
        return True
    if parts[-1] == ".env.example":
        return False
    # A crashed old exporter could leave projectpack-*/ inside the repository. Never treat a
    # previous export scratch directory as project input, especially because it may contain a DB.
    if parts[0].lower().startswith(TEMP_EXPORT_PREFIX):
        return True
    return any(fnmatch.fnmatchcase(path.lower(), p) or fnmatch.fnmatchcase(parts[-1].lower(), p)
               for p in SECRET_PATTERNS)


def read_regular(path: str) -> bytes:
    """Check all components before opening; verify the file did not change."""
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts or "\\" in path:
        raise ArchiveError(f"Unsafe path: {path}")
    current = ROOT
    for part in relative.parts:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ArchiveError(f"Link/reparse point refused: {path}")
    if not stat.S_ISREG(info.st_mode) or not current.resolve().is_relative_to(ROOT.resolve()):
        raise ArchiveError(f"Not a regular project file: {path}")
    before = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    with current.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != before:
            raise ArchiveError(f"File changed while exporting: {path}")
        data = stream.read()
    after = current.lstat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != before or current.resolve() != ROOT.resolve() / relative:
        raise ArchiveError(f"File changed while exporting: {path}")
    return data


def project_files() -> tuple[list[str], list[str]]:
    indexed = {}
    for row in git("ls-files", "--stage", "-z").split(b"\0"):
        if not row:
            continue
        header, path = row.split(b"\t", 1)
        mode, _, stage = header.split()
        name = path.decode("utf-8")
        if stage != b"0":
            raise ArchiveError("Resolve index conflicts before exporting")
        if mode not in (b"100644", b"100755"):
            raise ArchiveError(f"Tracked link/submodule refused: {name}")
        indexed[name] = mode
    listed = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")
    paths = sorted({p for p in listed if p and not is_secret(p)})
    if any(PurePosixPath(p).parts[0] == METADATA for p in paths):
        raise ArchiveError(f"Reserved export path: {METADATA}")
    # lexists keeps broken links visible so they are refused, too.
    return [p for p in paths if os.path.lexists(ROOT / p)], sorted(p for p in indexed if not is_secret(p))


def secret_values() -> list[bytes]:
    if not os.path.lexists(ROOT / ".env"):
        return []
    values = []
    for line in read_regular(".env").decode("utf-8").splitlines():
        key, sep, value = line.partition("=")
        value = value.strip().strip("'\"")
        if sep and not line.lstrip().startswith("#") and SECRET_ENV_KEY.search(key.strip()) and value:
            values.append(value.encode("utf-8"))
    return values


def check_bytes(label: str, data: bytes, values: list[bytes]) -> None:
    url_credentials = any(m[1].lower() not in PLACEHOLDER_PASSWORDS for m in URL_CREDENTIALS.finditer(data))
    if any(value in data for value in values) or PRIVATE_KEY.search(data) or url_credentials:
        # Never include even a secret prefix in diagnostics or command arguments.
        raise ArchiveError(f"Possible credential in {label}; no archive written")


def check_bundle(bundle: Path, scratch: Path, values: list[bytes]) -> None:
    mirror = scratch / "verify.git"
    git("clone", "--mirror", "--quiet", str(bundle), str(mirror))
    for entry in git("rev-list", "--objects", "--all", cwd=mirror).splitlines():
        oid, _, path = entry.partition(b" ")
        if path and is_secret(path.decode("utf-8")):
            raise ArchiveError(f"Excluded file in exported history (object {oid.decode()})")
        kind = git("cat-file", "-t", oid.decode(), cwd=mirror).strip().decode()
        content = git("cat-file", kind, oid.decode(), cwd=mirror)
        check_bytes(f"history object {oid.decode()}", content, values)
        # rev-list gives one name per shared object. Inspect every tree to detect
        # a secret path even when the same bytes also have an allowed filename.
        if kind == "tree":
            for row in git("ls-tree", "-z", oid.decode(), cwd=mirror).split(b"\0"):
                if row and is_secret(row.split(b"\t", 1)[1].decode("utf-8")):
                    raise ArchiveError(f"Excluded filename in history tree {oid.decode()}")
    check_bytes("bundle refs", git("bundle", "list-heads", str(bundle)), values)


def data_files() -> list[str]:
    """Include ignored data, refusing links and excluding credential files and WAL sidecars."""
    directory = ROOT / "data"
    if not directory.exists():
        return []
    paths = []
    for current, directories, files in os.walk(directory, followlinks=False):
        for entry in [Path(current), *(Path(current) / name for name in directories)]:
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ArchiveError("Link/reparse point refused in data")
        for name in files:
            path = (Path(current) / name).relative_to(ROOT).as_posix()
            if not is_secret(name) and not name.endswith(("-wal", "-shm", "-journal")):
                paths.append(path)
    return sorted(paths)


def write_zip(target: Path, *, include_data: bool = False) -> int:
    target = target.absolute()
    if target.exists() or target.is_symlink():
        raise ArchiveError("Output already exists; choose a new archive name")
    files, indexed = project_files()
    local_data = data_files() if include_data else []
    values = secret_values()
    # Pack the exact checked bytes, not files reopened after the leak check.
    contents = {}
    for path in files:
        data = read_regular(path)
        check_bytes(path, data, values)
        contents[path] = data
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    patch = git("--literal-pathspecs", "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv",
                "--", *indexed) if indexed else b""
    for label, data in (("status", status), ("staged patch", patch)):
        check_bytes(label, data, values)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=TEMP_EXPORT_PREFIX) as tmp:
        scratch = Path(tmp)
        bundle = scratch / "history.bundle"
        git("bundle", "create", str(bundle), "--all")
        check_bundle(bundle, scratch, values)
        snapshot = scratch / "stacksignal.db"
        if "data/stacksignal.db" in local_data:
            # Validate the source path, then use SQLite's backup API to include committed WAL writes.
            read_regular("data/stacksignal.db")
            with closing(sqlite3.connect((ROOT / "data/stacksignal.db").as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(snapshot)) as destination:
                    source.backup(destination)
                    if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ArchiveError("Database snapshot failed integrity check")
        packed = scratch / "export.zip"
        with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path, data in contents.items():
                archive.writestr(f"{ROOT.name}/{path}", data)
            for path in local_data:
                if path == "data/stacksignal.db":
                    archive.write(snapshot, f"{ROOT.name}/{path}")
                else:
                    archive.writestr(f"{ROOT.name}/{path}", read_regular(path))
            prefix = f"{ROOT.name}/{METADATA}"
            archive.write(bundle, f"{prefix}/history.bundle")
            archive.writestr(f"{prefix}/status.txt", status)
            archive.writestr(f"{prefix}/staged.patch", patch)
            archive.writestr(f"{prefix}/README.txt", "History: git clone history.bundle restored\n"
                            "Working files capture the current tree, including unstaged changes.\n"
                            "staged.patch records allowed staged paths, including deletions.\n"
                            "Excluded files, .git metadata, links and submodules are not exported.\n"
                            "Leak detection is best-effort, not a guarantee against unknown secrets.\n"
                            + ("Local data included; stacksignal.db is a consistent SQLite backup.\n"
                               "Data may contain private content and is not credential-scanned.\n"
                               "WAL/SHM/journal sidecars are omitted. .env is excluded; configure it on restore.\n"
                               if include_data else "Local data is excluded.\n"))
        with target.open("xb") as output, packed.open("rb") as source:
            shutil.copyfileobj(source, output)
    return len(contents) + len(local_data) + 4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?")
    parser.add_argument("--include-data", action="store_true", help="Include local data and a consistent database backup")
    args = parser.parse_args()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "-with-data" if args.include_data else ""
    target = Path(args.output).absolute() if args.output else ROOT / f"{ROOT.name}{suffix}-{stamp}.zip"
    try:
        count = write_zip(target, include_data=args.include_data)
    except (ArchiveError, OSError, subprocess.CalledProcessError) as error:
        # Git stderr can contain private paths/refs. Do not dump it.
        raise SystemExit(str(error) if isinstance(error, ArchiveError) else "Export failed; no completed archive")
    print(f"{target} ({count} entries, {target.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
