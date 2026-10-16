"""The dashboard's critical paths, end to end in a real browser. Kept few and broad: each one would
catch a broken module import, a renamed API field or a dead button that no backend test can see."""

import re

import pytest
from playwright.sync_api import expect

SECTIONS = [("today", "Сегодня"), ("projects", "Проекты"), ("explore", "Обзор"), ("chat", "Чат"),
            ("settings", "Настройки")]


def test_today_shows_the_fresh_items_for_your_projects(page):
    page.go("today")
    expect(page.get_by_role("heading", name="Сегодня", exact=True)).to_be_visible()
    cards = page.locator(".card")
    expect(cards.first).to_be_visible()
    expect(page.get_by_text("whisper.cpp").first).to_be_visible()


@pytest.mark.parametrize("analyzed", [1, 3])
def test_ai_coverage_is_separate_from_the_last_collection(page, analyzed):
    def last_collection(route):
        data = route.fetch().json()
        data["health"].update({
            "ai_silent": True, "ai_read_last_run": 0,
            "last_run": {"status": "completed", "started_at": "2026-10-02T16:00:00",
                         "finished_at": "2026-10-02T16:01:00", "items_found": 3, "items_new": 3,
                         "stages": [{"name": "enrich", "ai": 0, "heuristic": 3, "duration_s": 1}]},
        })
        route.fulfill(json=data)

    def coverage(route):
        data = route.fetch().json()
        data.update(items=3, ai_analyzed=analyzed)
        route.fulfill(json=data)

    page.route("**/api/today", last_collection)
    page.route("**/api/stats", coverage)
    page.route("**/api/health", lambda route: route.fulfill(json={
        "status": "ok", "llm_enabled": True, "ai_gateway": {"ready": True, "code": "ready"}}))
    page.go("today")
    expect(page.locator(".analysis-coverage")).to_contain_text(f"AI прочитал {analyzed} из 3 находок")
    expect(page.locator(".runline")).to_contain_text("0AI за этот сбор")
    warning = page.get_by_text("В последнем сборе AI не прочитал ни одной находки", exact=True)
    if analyzed == 3:
        expect(warning).to_have_count(0)
        expect(page.locator(".analysis-coverage")).to_contain_text("Все текущие находки прочитаны AI.")
    else:
        expect(warning).to_be_visible()
        expect(page.locator(".analysis-coverage")).to_contain_text("Ждут AI-анализа: 2.")

    page.go("settings/collection")
    expect(page.locator(".analysis-coverage")).to_contain_text(f"AI прочитал {analyzed} из 3 находок")


@pytest.mark.parametrize("route,title", SECTIONS)
def test_every_section_renders_from_the_navigation(page, route, title):
    page.go("today")
    page.locator(f'#nav a[data-view="{route}"]').click()
    expect(page).to_have_url(re.compile(f"#/{route}"))
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page).to_have_title(re.compile(title))


@pytest.mark.parametrize("tab", ["finds", "capabilities", "briefings", "radar"])
def test_every_explore_tab_loads(page, tab):
    page.go(f"explore/{tab}")
    page.wait_for_load_state("networkidle")
    expect(page.locator(".alert-error")).to_have_count(0)


def test_a_russian_project_can_be_created_edited_and_deleted(page):
    page.go("projects")
    page.get_by_role("button", name="Новый проект").click()
    page.get_by_placeholder("например, Мой проект").fill("Игра со спрайтами 2")
    page.get_by_placeholder("local inference, ocr, speech to text").fill("unity, casual")
    page.get_by_role("button", name="Создать проект").click()
    expect(page.get_by_text("Проект создан")).to_be_visible()

    page.get_by_text("Игра со спрайтами 2").click()
    expect(page).to_have_url(re.compile(r"#/projects/igra-so-spraytami-2"))

    page.get_by_role("button", name="Изменить", exact=True).click()
    page.get_by_placeholder("например, Мой проект").fill("Игра со спрайтами 3")
    page.get_by_role("button", name="Сохранить").click()
    expect(page.get_by_role("heading", name="Игра со спрайтами 3")).to_be_visible()

    page.go("projects/igra-so-spraytami-2/profile")
    page.get_by_role("button", name="Удалить проект").click()
    page.locator(".modal").get_by_role("button", name=re.compile("Удалить")).click()
    expect(page.get_by_text("Проект удалён")).to_be_visible()


def test_the_item_drawer_takes_a_vote_and_a_decision(page):
    page.go("projects/chitalka/relevant")
    page.locator(".card", has_text="whisper.cpp").first.click()
    drawer = page.locator(".drawer")
    expect(drawer.get_by_role("heading", name="whisper.cpp")).to_be_visible()

    up = drawer.get_by_role("button", name="Полезно: Читалка")
    up.click()
    expect(up).to_have_attribute("aria-pressed", "true")

    drawer.get_by_role("button", name="Решение для проекта").click()
    drawer.get_by_placeholder("что хотите попробовать или проверить").fill("offline STT prototype")
    drawer.get_by_role("button", name="Сохранить").click()
    expect(page.get_by_text("Решение сохранено")).to_be_visible()
    expect(drawer.locator(".decision-line", has_text="offline STT prototype")).to_be_visible()

    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()


def test_chat_answers_without_the_ai(page):
    page.go("chat")
    page.locator("textarea.chat-input").fill("что есть для распознавания речи?")
    page.keyboard.press("Enter")
    expect(page.locator(".msg-user")).to_have_count(1)
    answer = page.locator(".msg-ai").last
    expect(answer.locator(".msg-note")).to_be_visible(timeout=15000)  # "AI недоступен — ключевые слова"


def test_the_add_url_bar_validates_before_calling_the_server(page):
    page.go("explore/finds")
    add = page.get_by_role("button", name="Добавить на радар")
    add.click()  # empty field: focuses it instead of sending anything
    expect(page.get_by_label("Ссылка")).to_be_focused()


def test_project_dependencies_show_declarations_without_inventing_resolved_versions(page):
    project = page.request.post(f"{page.base}/api/projects", data={"name": "Dependency demo"}).json()
    try:
        response = page.request.post(f"{page.base}/api/projects/{project['id']}/apply-scan", data={
            "path": "demo", "packages": [{"name": "FastAPI", "ecosystem": "pypi", "package_key": "fastapi",
                                          "declared_spec": ">=0.115", "manifest_path": "requirements.txt"}]})
        assert response.ok
        page.go(f"projects/{project['slug']}/profile")
        expect(page.get_by_role("heading", name="Зависимости проекта")).to_be_visible()
        row = page.locator(".change-row", has_text="fastapi")
        expect(row).to_contain_text("Заявлено: >=0.115")
        expect(row).to_contain_text("requirements.txt")
        expect(row).not_to_contain_text("Зафиксировано:")
    finally:
        page.request.delete(f"{page.base}/api/projects/{project['id']}")
