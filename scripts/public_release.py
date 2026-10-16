"""Check and export public source without personal state or Git history.

Uses only the standard library. Diagnostics never include matched credential values.
The checks are a baseline, not a guarantee that arbitrary personal data is absent.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_archive

ROOT = make_archive.ROOT
ROOT_FILES = {
    ".env.example", ".gitattributes", ".gitignore", "README.md", "LICENSE",
    "CONTRIBUTING.md", "SECURITY.md", "run.ps1", "run.sh",
}
SOURCE_DIRS = {"backend", "frontend", "scripts", "docs", ".github"}
BLOCKED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules",
                 "data", "github-export", "dist", ".test-tmp"}
ALLOWED_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".txt", ".ini", ".mako",
                    ".yml", ".yaml", ".ps1", ".sh", ".vbs", ".png"}
TOKEN_PATTERNS = {
    "github-token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    "provider-key": re.compile(rb"\bsk-(?:ant-[A-Za-z0-9_-]{30,}|proj-[A-Za-z0-9_-]{30,}|[A-Za-z0-9]{40,})"),
    "telegram-token": re.compile(rb"\b[0-9]{8,12}:[A-Za-z0-9_-]{35}\b"),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "google-api-key": re.compile(rb"\bAIza[A-Za-z0-9_-]{35}\b"),
    "slack-token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}


def is_public(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if (not parts or PurePosixPath(path).is_absolute() or ".." in parts or "\\" in path
            or any(p.lower() in BLOCKED_PARTS for p in parts)):
        return False
    if make_archive.is_secret(path) or path.endswith(".sha256"):
        return False
    if path in ROOT_FILES or path == ".claude/skills/analyze-backlog/SKILL.md":
        return True
    return (parts[0] in SOURCE_DIRS and
            (PurePosixPath(path).suffix in ALLOWED_SUFFIXES or path == "backend/alembic/README"))


def credential_rules(data: bytes, known: list[bytes]) -> list[str]:
    rules = [name for name, pattern in TOKEN_PATTERNS.items() if pattern.search(data)]
    if any(value in data for value in known):
        rules.append("local-credential-value")
    if make_archive.PRIVATE_KEY.search(data):
        rules.append("private-key")
    if any(m[1].lower() not in make_archive.PLACEHOLDER_PASSWORDS
           for m in make_archive.URL_CREDENTIALS.finditer(data)):
        rules.append("url-credentials")
    return rules


def source_contents() -> dict[str, bytes]:
    files, _ = make_archive.project_files()
    known = make_archive.secret_values()
    contents = {}
    for name in files:
        if not is_public(name):
            continue
        data = make_archive.read_regular(name)
        rules = credential_rules(data, known)
        if rules:
            raise make_archive.ArchiveError(f"{name}: {', '.join(rules)}")
        contents[name] = data if name.endswith(".png") else data.replace(b"\r\n", b"\n")
    missing = ROOT_FILES - contents.keys()
    if missing:
        raise make_archive.ArchiveError("Missing public files: " + ", ".join(sorted(missing)))
    # A forced git add of runtime state must fail CI even though exports exclude it.
    for raw in make_archive.git("ls-files", "-z").decode().split("\0"):
        if raw and (ROOT / raw).exists() and make_archive.is_secret(raw):
            raise make_archive.ArchiveError(f"Private file tracked by Git: {raw}")
    return contents


def audit_history() -> dict:
    """Inspect each reachable Git object once; never print the object contents."""
    import subprocess
    entries = make_archive.git("rev-list", "--objects", "--all").splitlines()
    known = make_archive.secret_values()
    findings = []
    bootstrap_objects = 0
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT,
                               env=dict(os.environ, GIT_NO_REPLACE_OBJECTS="1"),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for entry in entries:
            oid, _, path = entry.partition(b" ")
            process.stdin.write(oid + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3:
                raise make_archive.ArchiveError("Cannot read history object")
            data = process.stdout.read(int(header[2]))
            process.stdout.read(1)
            rules = credential_rules(data, known)
            if path and make_archive.is_secret(path.decode("utf-8", errors="replace")):
                rules.append("private-history-path")
            if header[1] == b"tree":
                rows = make_archive.git("ls-tree", "-z", oid.decode()).split(b"\0")
                if any(row and make_archive.is_secret(row.split(b"\t", 1)[1].decode("utf-8")) for row in rows):
                    rules.append("private-history-tree-entry")
            if path.endswith(b"/seed.py") and b"PROJECT_PROFILES = [" in data and b'"slug":' in data:
                bootstrap_objects += 1
            if rules:
                findings.append({"object": oid.decode(), "rules": rules})
    finally:
        process.stdin.close()
        process.wait(timeout=10)
        process.stdout.close()
    return {"reachable_objects": len(entries), "credential_or_private_path_findings": findings,
            "objects_with_embedded_bootstrap_profiles": bootstrap_objects,
            "recommendation": "Publish a clean source snapshot with a new Git history."}


def write_public_zip(target: Path, contents: dict[str, bytes]) -> dict:
    target = target.absolute()
    checksum = target.with_suffix(target.suffix + ".sha256")
    manifest = target.with_suffix(target.suffix + ".manifest.json")
    if any(p.exists() or p.is_symlink() for p in (target, checksum, manifest)):
        raise make_archive.ArchiveError("Output already exists; choose a new archive name")
    target.parent.mkdir(parents=True, exist_ok=True)
    files = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo("StackSignal/" + name)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if name == "run.sh" else 0o100644) << 16
            archive.writestr(info, data)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    manifest.write_text(json.dumps({"format": 1, "files": files, "archive_sha256": digest},
                                   ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return {"archive": str(target), "files": len(files), "sha256": digest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check public working files only")
    parser.add_argument("--audit-history", action="store_true", help="report on reachable local history")
    parser.add_argument("--output", type=Path, help="new public source ZIP (never contains history)")
    args = parser.parse_args()
    try:
        if args.audit_history:
            report = audit_history()
            print(json.dumps(report, ensure_ascii=True, indent=2))
            return int(bool(report["credential_or_private_path_findings"] or
                            report["objects_with_embedded_bootstrap_profiles"]))
        contents = source_contents()
        if args.check:
            print(f"Public source check passed: {len(contents)} files; no credential matches.")
        else:
            target = args.output or ROOT / "dist" / (
                "stacksignal-public-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip")
            print(json.dumps(write_public_zip(target, contents), ensure_ascii=True, indent=2))
    except (make_archive.ArchiveError, OSError, ValueError) as exc:
        print(f"Public release refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
