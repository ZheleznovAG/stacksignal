"""Check local Markdown links in public project documents using only the standard library."""
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def without_fences(text):
    lines, fence = [], None
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "\n".join(lines)


def headings(path):
    counts, identifiers = {}, set()
    for title in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", without_fences(path.read_text(encoding="utf-8")), re.MULTILINE):
        title = re.sub(r"[`*_]", "", title).lower()
        slug = re.sub(r"[^\w\-\s]", "", title).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        identifiers.add(slug if not count else f"{slug}-{count}")
    return identifiers


def check():
    files = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"), *(ROOT / ".claude/skills").rglob("SKILL.md")]
    failures, checked = [], 0
    for path in files:
        if path.name.endswith(".local.md"):
            continue
        text = without_fences(path.read_text(encoding="utf-8"))
        for destination in re.findall(r"!?\[[^\]\n]*\]\(([^)\n]+)\)", text):
            destination = destination.strip().strip("<>")
            url = urlsplit(destination)
            if url.scheme or url.netloc:
                continue
            target = ((ROOT if url.path.startswith("/") else path.parent) / unquote(url.path).lstrip("/")).resolve() if url.path else path
            label = f"{path.relative_to(ROOT).as_posix()}: {destination}"
            checked += 1
            if not target.is_relative_to(ROOT) or not target.exists():
                failures.append(label + " (missing or outside the public project)")
            elif url.fragment and target.suffix == ".md" and unquote(url.fragment) not in headings(target):
                failures.append(label + " (missing heading)")
    return files, checked, failures


if __name__ == "__main__":
    files, count, failures = check()
    for failure in failures:
        print(failure)
    print(f"Documentation links: {count} checked, {len(failures)} broken.")
    raise SystemExit(bool(failures))
