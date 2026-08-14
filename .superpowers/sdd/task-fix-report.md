# Task Fix Report — multilingual-reply-generation

Whole-branch review findings fix. All findings fixed; full suite green.

## Commit
- `fix: 多语种话术 review 修复 (语言回退/hx-vals 转义/断言加固/迁移测试/regenerate 测试) (multilingual-reply-generation)`

## Files changed
- `app/reply/generator.py` — F1 (language fallback), F9 (SCENARIO_LIST)
- `app/storage/sqlite_store.py` — F7 (idempotency comments)
- `app/web/routes.py` — F8 (drop unused `regenerate_reply` import)
- `app/web/templates/reply_result.html` — F2 (hidden inputs + hx-include instead of hx-vals)
- `tests/reply/test_generator.py` — F1 test, F3 assertion hardening
- `tests/reply/test_worker.py` — F3 assertion hardening
- `tests/storage/test_reply_store.py` — F4 legacy-DB migration test
- `tests/web/test_reply_async.py` — F2/F5/F6 test updates
- `.superpowers/sdd/task-fix-report.md` — this report

## Fixes

### F1 (Important): unknown `language` doesn't fall back to default
`_build_system` used `LANGUAGES.get(language, "")`, emitting no language instruction for unknown codes while scenario/formality fall back correctly.
- **Fix:** `LANGUAGES.get(language, LANGUAGES["zh"])`.
- **TDD evidence:** added `test_unknown_language_falls_back_to_chinese` first → FAILED (no "用简体中文回复"); after fix → PASSED.

### F2 (Important): regenerate hx-vals message not robustly escaped
`reply_result.html` embedded message in `hx-vals` JSON; under Starlette autoescape a `"`/`\` in message could break htmx JSON parse and silently drop all params.
- **Fix:** replaced the hx-vals approach with 8 hidden inputs (`customer_id`, `chat_id`, `message`, `session_id`, `style`, `language`, `scenario`, `formality`, all `|default('', true)`) inside the card's btn-row + button `hx-post="/api/reply/regenerate" hx-include="closest div" hx-target="closest div" hx-swap="innerHTML"` with no hx-vals. Display tags kept.
- Existing `test_reply_result_has_copy_button_and_session` updated: session_id assertion now matches the hidden-input `name="session_id" value="<hex>"` form (still asserts uuid hex). `test_reply_full_lifecycle`, `test_reply_result_shows_generation_dimensions` pass unchanged.

### F3 (Important): weak scenario assertions mask regressions
`assert "砍价"` / `assert "付款"` substrings also exist in the `SCENARIOS["auto"]` fallback text, so a fallback regression would pass.
- **Fix:** bargain → `assert "让步空间"`, payment → `assert "交易安全"` in `tests/reply/test_generator.py` (`test_scenario_instruction_in_system`, `test_regenerate_preserves_dimensions`) and `tests/reply/test_worker.py` (`test_execute_reply_passes_generation_params`).

### F4 (Minor→worth fixing): legacy-DB upgrade-path migration test
The `ALTER TABLE ADD COLUMN` branch in `_init_schema` was untested (all tests build fresh DBs).
- **Test added:** `test_legacy_db_upgrade_adds_generation_columns` in `tests/storage/test_reply_store.py` — creates the OLD 12-column `reply_tasks` at `settings.sqlite_path` via raw `sqlite3`, instantiates `SqliteStore()` (runs executescript + 3 ALTERs), asserts all 3 columns present via `PRAGMA table_info`, double-init for idempotency, and `create_reply_task(..., language="ru")` round-trips.

### F5 (Minor→worth fixing): regenerate route persistence test
No test posted to `/api/reply/regenerate` with dimensions.
- **Test added:** `test_reply_regenerate_persists_generation_params` in `tests/web/test_reply_async.py` — posts form data (language=ru, scenario=payment, formality=formal, style, session_id, customer_id, chat_id, message) and asserts the created task row persists `mode="regenerate"`, `session_id`, and all three dims.

### F6 (Minor): raw-code-in-payload + legacy render
- `test_reply_result_shows_generation_dimensions` now also asserts raw codes as hidden-input values: `name="language" value="ru"`, `name="scenario" value="payment"`, `name="formality" value="formal"`.
- **Test added:** `test_legacy_result_renders_without_dimension_tags` — a done task whose result dict lacks language/scenario/formality renders without dim tags and regenerate submits empty dims (`name="language" value=""` etc.).

### F7 (Minor): scenario/formality ALTER idempotency comments
Added `# 列已存在 (新库 schema.sql 已含) — 幂等` comments to the scenario and formality `ALTER TABLE` try/except blocks, matching the language block.

### F8 (Minor): unused `regenerate_reply` import in routes.py
Removed `regenerate_reply` from `from app.reply.generator import ...` in `app/web/routes.py` (production only uses `NEXT_STYLE`). Function kept in `generator.py` (tested, module API). Verified no other call in routes.py.

### F9 (Minor): tasks.md / SCENARIO_LIST drift
Added `SCENARIO_LIST = ["询价", "砍价", "看车", "物流", "付款", "售后"]` to `app/reply/generator.py`; `SCENARIOS["auto"]` left inlined. `tasks.md` 1.1 description now matches reality, no edit needed.

## Test results
- Focused: `tests/reply/test_generator.py tests/reply/test_worker.py tests/storage/test_reply_store.py` → 23 passed
- Focused: `tests/web/test_reply_async.py` → 11 passed
- Full suite: `python -m pytest -q` → **280 passed** (1 pre-existing StarletteDeprecationWarning)
