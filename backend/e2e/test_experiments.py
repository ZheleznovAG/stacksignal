import re

from playwright.sync_api import expect


def test_candidate_brief_download_and_explicit_result_survive_reload(page):
    project = page.request.post(f"{page.base}/api/projects", data={"name": "Experiment browser test"}).json()
    try:
        need = page.request.post(f"{page.base}/api/projects/{project['id']}/needs", data={
            "title": "Offline speech import", "constraints": "Offline",
            "acceptance_criteria": "Check ten audio samples", "search_terms": ["speech"],
        }).json()
        page.go(f"projects/{project['slug']}/needs")
        card = page.locator(".need-card", has_text="Offline speech import")
        card.locator("summary", has_text="Кандидаты").click()
        card.get_by_role("button", name="Подготовить проверку", exact=True).first.click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_label("Задание для Claude")).to_have_value(re.compile("Check ten audio samples"))
        with page.expect_download() as downloaded:
            dialog.get_by_role("link", name="Скачать .md").click()
        assert downloaded.value.suggested_filename.endswith(".md")
        dialog.get_by_role("button", name="Начать проверку", exact=True).click()
        expect(dialog).to_have_count(0)
        page.locator(".experiment-card").get_by_role("button", name="Открыть проверку").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Итог проверки").select_option("useful")
        dialog.get_by_label("Результат эксперимента").fill("Ten synthetic samples checked; 90 ms offline")
        dialog.get_by_label("Свидетельства проверки").fill("results/demo.json: ten samples, 90 ms")
        dialog.get_by_role("switch", name="Подтвердить закрытие потребности этим результатом").check()
        dialog.get_by_role("button", name="Записать результат", exact=True).click()
        expect(dialog).to_have_count(0)
        expect(page.locator(".need-card")).to_contain_text("Есть подтверждённый результат")
        page.reload()
        expect(page.locator(".experiment-card")).to_contain_text("Ten synthetic samples checked")
        before = page.request.get(f"{page.base}/api/needs/{need['id']}/experiments").json()[0]
        page.request.put(f"{page.base}/api/needs/{need['id']}", data={"constraints": "Now Android only"})
        page.reload()
        expect(page.locator(".experiment-card")).to_contain_text("исходный контекст изменился")
        after = page.request.get(f"{page.base}/api/experiments/{before['id']}").json()
        assert after["brief"] == before["brief"] and after["stale"]
    finally:
        page.request.delete(f"{page.base}/api/projects/{project['id']}")
