# LionClaw — Claude Notes

## Python environment

Never use `source .venv/bin/activate`. Instead, always invoke the venv directly by full path:
- Python: `.venv/bin/python`
- pip: `.venv/bin/pip`
- pytest: `.venv/bin/pytest`
- Any other tool: `.venv/bin/<tool>`

This way Claude writes `.venv/bin/pytest tests/` instead of `source .venv/bin/activate && pytest tests/`, which:
- Never triggers a "source" permission prompt
- Actually works correctly (the activate step was a no-op between calls anyway)
- Runs every command with the right interpreter automatically

## Running the test suite

Run all tests from the repo root:

```
.venv/bin/pytest tests/
```

Or use the convenience script (same thing with nicer output):

```
./tests/run_tests.sh
```

Run a single category:

```
.venv/bin/pytest tests/test_db/          # database model tests
.venv/bin/pytest tests/test_api/         # HTTP endpoint tests
.venv/bin/pytest tests/test_docx/        # docx parse + export tests
.venv/bin/pytest tests/test_integration/ # full cutting workflow (uses real test docx)
```

Run one specific test by name:

```
.venv/bin/pytest -k test_full_cutting_workflow
```

### What the tests cover

| Directory | What it tests |
|-----------|---------------|
| `test_db/` | All three ORM models (Job, Project, Card): field persistence, JSON round-trips, status transitions, null cite fields |
| `test_api/` | Every API endpoint via FastAPI TestClient: jobs, projects, cards, export, cite-verbatim, bulk approve/trash |
| `test_docx/` | `strip_cutting`, `extract_text_from_xml`, `parse_cards` against the real debate docx; `export_cards_to_docx` XML structure |
| `test_integration/` | Full pipeline: upload real docx → AI cutting (mocked) → verify output.docx has underline formatting |

### How isolation works

- Tests use an **in-memory SQLite database** (`StaticPool`) — the production `lionclaw.db` is never touched.
- The `.env` auth tokens are blanked before import so all test requests auto-pass as admin.
- Integration tests also patch `tasks.SessionLocal` and `tasks.DATA_DIR` so background jobs write to a temp directory.
- AI model calls in integration tests are mocked — no live model server is needed.

### Test files

The real debate docx used for parse and integration tests lives at:

```
tests/test-files/Emory-Gazmararian-Forman-Neg-Coast 1-Doubles.docx
```
