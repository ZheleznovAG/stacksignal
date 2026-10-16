import json
import subprocess
import sys
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path("github-export")
PER_PAGE = 100


def gh_api(endpoint: str, headers: list[str] | None = None) -> Any:
    cmd = ["gh", "api", endpoint]

    for header in headers or []:
        cmd += ["-H", header]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"gh api failed: {endpoint}")

    return json.loads(result.stdout)


def check_gh() -> None:
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:
        print("Ошибка: gh CLI не найден.")
        print("Установи GitHub CLI: https://cli.github.com/")
        sys.exit(1)

    if result.returncode != 0:
        print("GitHub CLI не авторизован.")
        print("Выполни:")
        print("  gh auth login")
        sys.exit(1)


def get_current_user() -> dict:
    return gh_api("/user")


def fetch_all_pages(
    endpoint: str,
    headers: list[str] | None = None,
) -> list[dict]:
    result: list[dict] = []
    page = 1

    while True:
        separator = "&" if "?" in endpoint else "?"
        url = (
            f"{endpoint}"
            f"{separator}per_page={PER_PAGE}"
            f"&page={page}"
        )

        items = gh_api(url, headers)

        if not isinstance(items, list):
            raise RuntimeError(
                f"Expected list from {endpoint}, got {type(items)}"
            )

        result.extend(items)

        print(
            f"{endpoint}: page {page}, "
            f"{len(items)} items, total {len(result)}"
        )

        if len(items) < PER_PAGE:
            break

        page += 1

    return result


def export_following() -> list[dict]:
    raw = fetch_all_pages("/user/following")

    following = []

    for user in raw:
        following.append(
            {
                "login": user.get("login"),
                "id": user.get("id"),
                "html_url": user.get("html_url"),
                "avatar_url": user.get("avatar_url"),
                "type": user.get("type"),
            }
        )

    return following


def export_starred() -> list[dict]:
    # Этот media type заставляет GitHub вернуть starred_at.
    raw = fetch_all_pages(
        "/user/starred",
        headers=[
            "Accept: application/vnd.github.star+json"
        ],
    )

    starred = []

    for item in raw:
        repo = item.get("repo", item)

        owner = repo.get("owner") or {}

        starred.append(
            {
                "starred_at": item.get("starred_at"),
                "full_name": repo.get("full_name"),
                "name": repo.get("name"),
                "owner": owner.get("login"),
                "html_url": repo.get("html_url"),
                "description": repo.get("description"),
                "language": repo.get("language"),
                "topics": repo.get("topics", []),
                "stargazers_count": repo.get("stargazers_count"),
                "forks_count": repo.get("forks_count"),
                "archived": repo.get("archived"),
                "fork": repo.get("fork"),
                "updated_at": repo.get("updated_at"),
            }
        )

    return starred


def save_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def make_markdown(
    user: dict,
    following: list[dict],
    starred: list[dict],
) -> str:
    lines = []

    lines.append("# GitHub export")
    lines.append("")
    lines.append(f"Account: `{user.get('login')}`")
    lines.append("")
    lines.append(f"- Following: **{len(following)}**")
    lines.append(f"- Starred repositories: **{len(starred)}**")
    lines.append("")

    lines.append("## Following")
    lines.append("")

    for item in following:
        lines.append(
            f"- [{item['login']}]({item['html_url']})"
        )

    lines.append("")
    lines.append("## Starred repositories")
    lines.append("")

    for item in starred:
        title = item.get("full_name") or "unknown"
        url = item.get("html_url") or ""
        language = item.get("language") or "—"
        starred_at = item.get("starred_at") or "—"

        lines.append(
            f"- [{title}]({url})"
            f" — `{language}`"
            f" — starred `{starred_at}`"
        )

        description = item.get("description")

        if description:
            lines.append(f"  - {description}")

        topics = item.get("topics") or []

        if topics:
            lines.append(
                "  - topics: " + ", ".join(topics)
            )

    lines.append("")

    return "\n".join(lines)


def main() -> None:
    check_gh()

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    user = get_current_user()

    print(f"GitHub account: {user.get('login')}")
    print()

    print("Fetching following...")
    following = export_following()

    print()
    print("Fetching starred repositories...")
    starred = export_starred()

    save_json(
        OUTPUT_DIR / "following.json",
        following,
    )

    save_json(
        OUTPUT_DIR / "starred.json",
        starred,
    )

    save_json(
        OUTPUT_DIR / "profile.json",
        {
            "login": user.get("login"),
            "name": user.get("name"),
            "html_url": user.get("html_url"),
        },
    )

    markdown = make_markdown(
        user,
        following,
        starred,
    )

    (OUTPUT_DIR / "github-export.md").write_text(
        markdown,
        encoding="utf-8",
    )

    print()
    print("Done.")
    print(f"Following: {len(following)}")
    print(f"Starred:   {len(starred)}")
    print()
    print(f"Output: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()