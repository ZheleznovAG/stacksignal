"""Import a chosen folder through the real scanner and dashboard, leaving other folders untouched."""

from playwright.sync_api import expect


def test_scan_review_and_import_project_folders(page, tmp_path):
    for name in ["Chosen App", "Unselected App", "Already Known"]:
        folder = tmp_path / name
        folder.mkdir()
        (folder / "README.md").write_text(
            "# Demo\n\nA local application for turning photos of pages into learning material.\n", encoding="utf-8")
        (folder / "requirements.txt").write_text("fastapi>=0.115\n", encoding="utf-8")
    existing = page.request.post(f"{page.base}/api/projects", data={"name": "Existing profile"}).json()
    page.request.post(f"{page.base}/api/projects/{existing['id']}/apply-scan", data={"path": str(tmp_path / "Already Known")})
    imported = None
    try:
        page.go("projects")
        page.get_by_role("button", name="Сканировать папку", exact=True).click()
        modal = page.get_by_role("dialog", name="Добавить проекты из папки")
        modal.get_by_label("Общая папка проектов").fill(str(tmp_path))
        modal.get_by_role("button", name="Сканировать", exact=True).click()
        expect(modal.get_by_text("Уже добавлен: Existing profile", exact=True)).to_be_visible()
        expect(modal.get_by_text("Найдено: 3. Новых: 2. Уже добавлено: 1.")).to_be_visible()
        expect(modal.get_by_role("button", name="Добавить выбранные (0)")).to_be_disabled()
        assert len(page.request.get(f"{page.base}/api/projects").json()) >= 1

        modal.get_by_role("checkbox", name="Выбрать все новые проекты").check()
        expect(modal.get_by_role("button", name="Добавить выбранные (2)")).to_be_enabled()
        modal.get_by_role("checkbox", name="Добавить Unselected App", exact=True).uncheck()
        card = modal.locator(".directory-candidate", has_text="Chosen App")
        card.get_by_text("Проверить профиль", exact=True).click()
        card.get_by_label("Название проекта", exact=True).fill("Reviewed Directory App")
        card.get_by_label("Ключевые слова", exact=True).fill("handwriting OCR, offline learning")
        card.get_by_label("Потребности", exact=True).fill("Offline handwriting recognition")
        modal.get_by_role("switch", name="Искать новые материалы для добавленных проектов").uncheck()
        modal.get_by_role("button", name="Добавить выбранные (1)").click()
        expect(modal).to_have_count(0)
        expect(page.get_by_role("link", name="Reviewed Directory App", exact=False)).to_be_visible()

        projects = page.request.get(f"{page.base}/api/projects").json()
        imported = next(p for p in projects if p["name"] == "Reviewed Directory App")
        assert not any(p["name"] in {"Chosen App", "Unselected App", "Already Known"} for p in projects)
        assert imported["keywords"] == ["handwriting OCR", "offline learning"]
        assert imported["needs"] == ["Offline handwriting recognition"]
        assert imported["stack_path"] == str(tmp_path / "Chosen App")
        assert not imported["discovery_enabled"]
        assert imported["dependencies"][0]["package_key"] == "fastapi"

        page.get_by_role("button", name="Сканировать папку", exact=True).click()
        page.get_by_role("button", name="Сканировать", exact=True).click()
        expect(page.get_by_text("Уже добавлен: Reviewed Directory App", exact=True)).to_be_visible()
    finally:
        if imported:
            page.request.delete(f"{page.base}/api/projects/{imported['id']}")
        page.request.delete(f"{page.base}/api/projects/{existing['id']}")
