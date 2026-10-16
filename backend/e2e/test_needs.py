from playwright.sync_api import expect


def test_create_need_record_outcome_and_preserve_it_after_profile_edit(page):
    project = page.request.post(f"{page.base}/api/projects", data={"name": "Needs browser test"}).json()
    try:
        page.go(f"projects/{project['slug']}/needs")
        page.get_by_role("button", name="Добавить вручную", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Задача", exact=True).fill("Offline speech")
        dialog.get_by_label("Ограничения", exact=True).fill("Android, no network")
        dialog.get_by_label("Критерий проверки", exact=True).fill("Run ten recitations")
        dialog.get_by_label("Поисковые слова", exact=False).fill("speech")
        dialog.get_by_role("button", name="Сохранить", exact=True).click()
        expect(dialog).to_have_count(0)
        card = page.locator(".need-card", has_text="Offline speech")
        expect(card).to_contain_text("Android, no network")
        card.get_by_role("button", name="Изменить / записать результат").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Состояние потребности").select_option("satisfied")
        dialog.get_by_label("Результат проверки").fill("10 recitations tested offline; latency 120 ms")
        dialog.get_by_role("button", name="Сохранить", exact=True).click()
        expect(dialog).to_have_count(0)
        expect(page.locator(".need-card")).to_contain_text("Есть подтверждённый результат")
        page.reload()
        expect(page.locator(".need-card")).to_contain_text("latency 120 ms")
        page.get_by_role("button", name="Изменить", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="Сохранить", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        expect(page.locator(".need-card")).to_contain_text("latency 120 ms")
    finally:
        page.request.delete(f"{page.base}/api/projects/{project['id']}")


def test_projects_survive_an_old_api_policy_shape(page):
    def legacy(route):
        projects = route.fetch().json()
        for project in projects:
            for key in ("kind", "lifecycle_status", "discovery_enabled", "discovery_allowed"):
                project.pop(key, None)
        route.fulfill(json=projects)
    page.route("**/api/projects", legacy)
    page.go("projects")
    expect(page.locator(".tile").first).to_be_visible()
