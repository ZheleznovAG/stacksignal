from app.collectors import github, huggingface


def test_github_normalize_maps_fields_without_network():
    raw_repo = {
        "full_name": "acme/widget",
        "name": "widget",
        "owner": {"login": "acme"},
        "html_url": "https://github.com/acme/widget",
        "description": "A widget",
        "topics": ["cli", "tools"],
        "stargazers_count": 1234,
        "forks_count": 56,
        "created_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-02-01T00:00:00Z",
        "language": "Rust",
        "license": {"spdx_id": "MIT"},
        "open_issues_count": 3,
        "archived": False,
    }
    item = github.normalize(raw_repo)

    assert item["source"] == "github"
    assert item["external_id"] == "acme/widget"
    assert item["owner"] == "acme"
    assert item["stars"] == 1234
    assert item["forks"] == 56
    assert item["downloads"] == 0
    assert item["likes"] == 0
    assert item["raw"]["license"] == "MIT"
    assert item["raw"]["archived"] is False


def test_github_normalize_handles_missing_optional_fields():
    raw_repo = {
        "full_name": "acme/widget",
        "name": "widget",
        "owner": {"login": "acme"},
        "html_url": "https://github.com/acme/widget",
        "description": None,
        "topics": None,
        "license": None,
    }
    item = github.normalize(raw_repo)

    assert item["description"] == ""
    assert item["topics"] == []
    assert item["raw"]["license"] is None


def test_huggingface_normalize_maps_fields_without_network():
    raw_model = {
        "id": "acme/tiny-vlm",
        "tags": ["vision", "vlm", "onnx"],
        "downloads": 5000,
        "likes": 42,
        "createdAt": "2026-03-01T00:00:00Z",
        "lastModified": "2026-03-15T00:00:00Z",
        "pipeline_tag": "image-to-text",
        "library_name": "transformers",
    }
    item = huggingface.normalize(raw_model)

    assert item["source"] == "huggingface"
    assert item["external_id"] == "acme/tiny-vlm"
    assert item["owner"] == "acme"
    assert item["name"] == "tiny-vlm"
    assert item["stars"] == 0
    assert item["downloads"] == 5000
    assert item["likes"] == 42
    assert item["raw"]["pipeline_tag"] == "image-to-text"


def test_huggingface_normalize_handles_id_without_owner():
    raw_model = {"id": "standalone-model", "tags": []}
    item = huggingface.normalize(raw_model)

    assert item["owner"] == ""
    assert item["name"] == "standalone-model"
