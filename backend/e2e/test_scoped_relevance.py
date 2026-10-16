"""An AI match must not turn a keyword summary into an AI-written summary."""

from playwright.sync_api import expect


def test_project_scoped_claude_match_has_its_own_badge(page):
    def scoped_match(route):
        items = route.fetch().json()
        assert items
        item = items[0]
        item["ai_analyzed"] = False
        item["matched_relevance"]["analysis"] = {
            "mode": "ai", "scope": "project_relevance", "provider": "claude-code", "model": "test-model"}
        route.fulfill(json=[item])

    page.route("**/api/relevant?*", scoped_match)
    page.go("projects/chitalka/relevant")
    card = page.locator(".card").first
    expect(card.locator(".badge-kw")).to_be_visible()
    expect(card.get_by_text("Релевантность: Claude", exact=True)).to_be_visible()
