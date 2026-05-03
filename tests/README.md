# LionClaw Test Suite

## Adding a new test

### Pick the right directory

| Directory | Add a test here when you're testing… |
|-----------|--------------------------------------|
| `test_db/` | A new ORM model, field, or database behaviour |
| `test_api/` | A new or changed API endpoint |
| `test_docx/` | A change to docx parsing (`docx_utils.py`) or export (`card_export.py`) |
| `test_integration/` | A full workflow that spans multiple layers (upload → task → download) |

### Minimal example

Add a function named `test_<what_you_are_checking>` to any existing file in the right directory, or create a new file named `test_<topic>.py`.

```python
# tests/test_api/test_projects.py  (existing file — just append)

def test_archived_project_excluded_from_list(test_client):
    project_id = test_client.post("/api/projects", json={"name": "Archive Me"}).json()["id"]
    test_client.patch(f"/api/projects/{project_id}", json={"status": "archived"})

    names = [p["name"] for p in test_client.get("/api/projects").json()]
    assert "Archive Me" not in names
```

### Available fixtures (from `conftest.py`)

| Fixture | Type | What you get |
|---------|------|--------------|
| `test_client` | `TestClient` | FastAPI test client wired to an isolated in-memory DB; no auth token needed |
| `db_engine` | `Engine` | The same in-memory SQLAlchemy engine the test client uses — insert rows directly |
| `db_session` | `Session` | A SQLAlchemy session bound to the test engine |
| `test_docx_bytes` | `bytes` | The real debate docx from `test-files/` |
| `test_docx_path` | `Path` | Path to that file |
| `integration_client` | `TestClient` | Like `test_client` but also patches `tasks.SessionLocal` and `tasks.DATA_DIR` — use this when your test triggers a background job |

Declare what you need as function parameters — pytest injects them automatically:

```python
def test_something(test_client, db_engine):
    ...
```

### Inserting rows directly (bypassing the API)

Use `db_engine` when you need to set up state that has no API endpoint:

```python
from sqlalchemy.orm import sessionmaker
from database import Card
import uuid
from datetime import datetime

def test_cut_card_appears_in_cut_tab(test_client, db_engine):
    project_id = test_client.post("/api/projects", json={"name": "P"}).json()["id"]

    Session = sessionmaker(bind=db_engine)
    s = Session()
    s.add(Card(
        id=str(uuid.uuid4()),
        project_id=project_id,
        tag="My card",
        card_status="cut",
        created_at=datetime.utcnow(),
    ))
    s.commit()
    s.close()

    cards = test_client.get(f"/api/projects/{project_id}/cards?card_status=cut").json()
    assert len(cards) == 1
```

### Mocking AI calls

AI functions live in `ai.py` and are called from `tasks.py`. Patch them at the `tasks` module level:

```python
from unittest.mock import patch

def test_something_with_ai(integration_client, test_docx_bytes):
    async def mock_underline(card, topic, prompt):
        return {"relevant": True, "underlined": ["key phrase"]}, "mock", {}

    async def mock_highlight(card, underlined, prompt):
        return {"highlighted": ["phrase"]}, "mock", {}

    async def mock_refine(card, topic, underlined, highlighted, ul_prompt, hl_prompt):
        return {"satisfied": True}, "mock", {}

    with patch("tasks.underline_card", side_effect=mock_underline), \
         patch("tasks.highlight_card", side_effect=mock_highlight), \
         patch("tasks.review_and_refine_cutting", side_effect=mock_refine):
        resp = integration_client.post(
            "/api/jobs",
            files={"file": ("test.docx", test_docx_bytes, "application/octet-stream")},
            data={"settings": '{"mode": "all"}'},
        )
    assert resp.json()["status"] == "queued"
```

For endpoints that call AI directly (cite, cite-verbatim, add-from-url), patch at the `main` level:

```python
from unittest.mock import AsyncMock, patch

def test_cite_endpoint(test_client, db_engine):
    ...
    with patch("main.generate_cite", new=AsyncMock(return_value=({"author": "Doe, Jane"}, "mock", {}))):
        resp = test_client.post(f"/api/cards/{card_id}/cite", json={"article_text": "..."})
    assert resp.json()["author"] == "Doe, Jane"
```

### Running your new test

```
.venv/bin/pytest tests/test_api/test_projects.py::test_archived_project_excluded_from_list -v
```

Or run the whole suite to make sure nothing regressed:

```
.venv/bin/pytest tests/
```
