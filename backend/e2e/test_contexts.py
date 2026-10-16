"""The context editor persists taxonomy and search independently without changing URLs."""

from playwright.sync_api import expect


def test_a_lab_can_be_active_without_search_and_filtered_by_type(page):
    project = page.request.post(f"{page.base}/api/projects", data={"name": "Policy lab test"}).json()
    path = f"{page.base}/api/projects/{project['id']}"
    try:
        page.go(f"projects/{project['slug']}")
        page.get_by_role("button", name="Изменить", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("Тип контекста", exact=True).select_option("lab")
        modal.get_by_label("Состояние", exact=True).select_option("active")
        modal.get_by_role("switch", name="Искать новые материалы").uncheck()
        modal.get_by_role("button", name="Сохранить", exact=True).click()
        expect(modal).to_have_count(0)
        expect(page.get_by_role("heading", name="Policy lab test")).to_be_visible()
        saved = next(p for p in page.request.get(f"{page.base}/api/projects").json() if p["id"] == project["id"])
        assert (saved["kind"], saved["lifecycle_status"], saved["discovery_enabled"], saved["slug"]) == ("lab", "active", False, project["slug"])
        page.go("projects")
        page.get_by_label("Тип контекста", exact=True).select_option("lab")
        expect(page.locator(".tile", has_text="Policy lab test")).to_be_visible()
        expect(page.locator(".tile", has_text="Policy lab test")).to_contain_text("Поиск выключен")
        page.get_by_label("Тип контекста", exact=True).select_option("project")
        expect(page.locator(".tile", has_text="Policy lab test")).to_have_count(0)
    finally:
        page.request.delete(path)


def test_archiving_temporarily_disables_the_search_control(page):
    project = page.request.post(f"{page.base}/api/projects", data={"name": "Archived context test"}).json()
    try:
        page.go(f"projects/{project['slug']}")
        page.get_by_role("button", name="Изменить", exact=True).click()
        modal = page.get_by_role("dialog")
        state = modal.get_by_label("Состояние", exact=True)
        search = modal.get_by_role("switch", name="Искать новые материалы")
        state.select_option("archived")
        expect(search).to_be_disabled()
        state.select_option("incubate")
        expect(search).to_be_enabled()
        expect(search).to_be_checked()
        modal.get_by_role("button", name="Сохранить", exact=True).click()
        expect(modal).to_have_count(0)
        saved = next(p for p in page.request.get(f"{page.base}/api/projects").json() if p["id"] == project["id"])
        assert saved["lifecycle_status"] == "incubate" and saved["discovery_allowed"]
    finally:
        page.request.delete(f"{page.base}/api/projects/{project['id']}")
