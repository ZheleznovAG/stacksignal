"""Project names in Russian: the slug used to keep only [a-z0-9], so every Cyrillic name became
"item" and the second Russian project was refused as a duplicate. Also the request size limits."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module
from app.main import app
from app.utils import slugify, unique_slug


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db_module.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_module.get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_slugify_transliterates_cyrillic_and_leaves_ascii_as_it_was():
    assert slugify("Читалка") == "chitalka"
    assert slugify("Игра со спрайтами") == "igra-so-spraytami"
    assert slugify("Щука и Ёж") == "shchuka-i-ezh"
    assert slugify("Dialogue Demo") == "dialogue-demo"
    assert slugify("日本語") == "item"


def test_unique_slug_adds_a_counter():
    taken = {"chitalka", "chitalka-2"}
    assert unique_slug("chitalka", taken.__contains__) == "chitalka-3"
    assert unique_slug("softbody", taken.__contains__) == "softbody"


def test_several_russian_projects_get_distinct_readable_slugs(client):
    slugs = [client.post("/api/projects", json={"name": name}).json()["slug"]
             for name in ["Читалка", "Игра со спрайтами", "Мой проект", "Мой проект!"]]
    assert slugs == ["chitalka", "igra-so-spraytami", "moy-proekt", "moy-proekt-2"]


def test_the_same_name_in_another_case_is_still_a_duplicate(client):
    assert client.post("/api/projects", json={"name": "Читалка"}).status_code == 200
    response = client.post("/api/projects", json={"name": "читалка"})
    assert response.status_code == 400


def test_a_name_without_letters_we_can_slug_still_gets_a_project(client):
    first = client.post("/api/projects", json={"name": "日本語"}).json()["slug"]
    second = client.post("/api/projects", json={"name": "中文"}).json()["slug"]
    assert (first, second) == ("project", "project-2")


def test_rename_cannot_duplicate_another_unicode_name(client):
    first = client.post("/api/projects", json={"name": "Читалка"}).json()
    second = client.post("/api/projects", json={"name": "Softbody Demo"}).json()
    assert client.put(f"/api/projects/{second['id']}", json={"name": " читалка "}).status_code == 400
    # Changing only the current project's case is allowed, and rejection did not rename Softbody Demo.
    assert client.put(f"/api/projects/{first['id']}", json={"name": "читалка"}).status_code == 200
    assert {p["name"] for p in client.get("/api/projects").json()} == {"читалка", "Softbody Demo"}


@pytest.mark.parametrize("body", [
    {"name": ""},
    {"name": "   "},
    {"name": "x" * 201},
    {"name": "ok", "description": "x" * 10_001},
    {"name": "ok", "keywords": ["k"] * 201},
])
def test_oversized_or_empty_project_input_is_rejected(client, body):
    assert client.post("/api/projects", json=body).status_code == 422


def test_chat_message_size_is_limited(client):
    assert client.post("/api/chat", json={"message": "x" * 4001}).status_code == 422
    assert client.post("/api/chat", json={"message": "hi", "history": [{}] * 41}).status_code == 422
