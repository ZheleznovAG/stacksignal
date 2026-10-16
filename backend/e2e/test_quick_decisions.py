"""The daily inbox records real per-project decisions and can undo them without changing global status."""

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("label,status", [
    ("Изучить", "assess"), ("Пробую", "trial"), ("Использую", "adopt"),
    ("Не подходит", "ignore"), ("Позже", "hold"),
])
def test_today_saves_and_undoes_a_decision_in_the_visible_project(page, label, status):
    fresh = page.request.get(f"{page.base}/api/today").json()["fresh"]
    item = next(i for i in fresh if i["name"] == "sherpa-onnx")
    path = f"{page.base}/api/items/{item['id']}"
    try:
        page.go("today")
        card = page.locator(f'.card[data-item-id="{item["id"]}"]')
        expect(card.get_by_role("group", name="Решение для Читалка")).to_be_visible()
        card.get_by_role("button", name=label, exact=True).click()
        expect(card).to_have_count(0)
        expect(page.locator(f'.inbox-candidate[data-item-id="{item["id"]}"]')).to_have_count(0)
        updated = page.request.get(path).json()
        assert updated["status"] == item["status"]
        assert [(d["project_slug"], d["status"]) for d in updated["decisions"]] == [("chitalka", status)]
        page.locator(".toast").get_by_role("button", name="Отменить", exact=True).click()
        expect(card).to_be_visible()
        assert page.request.get(path).json()["decisions"] == []
    finally:
        page.request.delete(f"{path}/decisions/chitalka")


def test_inbox_action_does_not_change_a_decision_for_another_project(page):
    fresh = page.request.get(f"{page.base}/api/today").json()["fresh"]
    item = next(i for i in fresh if i["name"] == "sherpa-onnx")
    path = f"{page.base}/api/items/{item['id']}"
    project = page.request.post(f"{page.base}/api/projects", data={"name": "Other decision context"}).json()
    try:
        assert page.request.put(f"{path}/decisions/{project['slug']}", data={"status": "adopt", "verdict": "Works here"}).ok
        page.go("today")
        row = page.locator(f'.inbox-candidate[data-item-id="{item["id"]}"][data-project-slug="chitalka"]')
        row.get_by_role("button", name="Не подходит", exact=True).click()
        expect(row).to_have_count(0)
        decisions = {d["project_slug"]: d for d in page.request.get(path).json()["decisions"]}
        assert decisions["chitalka"]["status"] == "ignore"
        assert decisions[project["slug"]]["status"] == "adopt"
        assert decisions[project["slug"]]["verdict"] == "Works here"
    finally:
        page.request.delete(f"{path}/decisions/chitalka")
        page.request.delete(f"{page.base}/api/projects/{project['id']}")
