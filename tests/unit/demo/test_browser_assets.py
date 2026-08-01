from pathlib import Path

DEMO_DIR = Path(__file__).parents[3] / "src" / "study_agent" / "demo"


def test_cardine_assets_are_split_and_reference_each_other() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    css = (DEMO_DIR / "browser.css").read_text(encoding="utf-8")
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/browser.css">' in page
    assert '<link rel="icon" href="/icons/book-open.svg" type="image/svg+xml">' in page
    assert '<script src="/browser.js" defer></script>' in page
    assert "<style" not in page
    assert "x-dc" not in page
    assert "support.js" not in page
    assert "Claude" not in page + javascript
    assert "@media (max-width: 700px)" in css
    assert "prefers-reduced-motion" in css


def test_ai_native_primitives_are_modular_packaged_and_complete() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    primitives_css = (DEMO_DIR / "ai-primitives.css").read_text(encoding="utf-8")
    primitives_js = (DEMO_DIR / "ai-primitives.js").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/ai-primitives.css">' in page
    assert '<script src="/ai-primitives.js" defer></script>' in page
    assert page.index("/ai-primitives.js") < page.index("/browser.js")

    for marker in (
        "ai-loading",
        "ai-thinking",
        "ai-answer",
        "ai-approval",
        "ai-tool-stack",
        "ai-task-list",
        "ai-chat-panel",
        "ai-recommendation",
        "ai-context-grid",
        "ai-diff-table",
        "ai-records-table",
        "ai-filter-table",
        "ai-sidebar-search",
        "ai-command-search",
        "ai-insight-deck",
        "ai-code-block",
        "ai-fine-tune",
    ):
        assert marker in primitives_css + primitives_js

    assert "prefers-reduced-motion" in primitives_css
    assert "CardineAI" in primitives_js
    assert "innerHTML" not in primitives_js
    assert "fetch(" not in primitives_js
    assert "/api/" not in primitives_js


def test_cardine_assets_use_only_approved_v1_routes_and_semantic_markers() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    for marker in (
        '<textarea id="entry"',
        'aria-labelledby="conversation-heading"',
        'aria-labelledby="material-heading"',
        'aria-labelledby="evidence-heading"',
        'aria-labelledby="conflict-heading"',
        'aria-labelledby="review-heading"',
        'id="entry-form"',
    ):
        assert marker in page

    for route in (
        "/api/v1/bootstrap",
        "/api/v1/session",
        "/api/v1/session/turns",
        "/api/v1/session/continuations/",
        "/api/v1/materials",
        "/api/v1/artifacts",
        "/api/v1/assessments",
        "/api/v1/evidence",
        "/api/v1/recall/due",
        "/api/v1/context/conflicts",
    ):
        assert route in javascript

    assert "schema_version: SCHEMA_VERSION" in javascript
    assert "expected_sequence: state.highWaterSequence" in javascript
    assert "{ content: value }" in javascript
    assert "{ response: value }" in javascript
    assert "Anteprima completata · nessun dato personale salvato" in javascript
    assert "/api/state" not in javascript
    assert "/api/entry" not in javascript


def test_public_demo_discovers_mode_before_private_auth_probe() -> None:
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert 'fetchJson("/health")' in javascript
    assert 'healthMode && healthMode !== "private"' in javascript
    assert 'fetchJson("/api/v1/auth/session")' in javascript


def test_private_workspace_and_model_check_surfaces_are_wired() -> None:
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    for route in (
        "/api/v1/workspace",
        "/api/v1/workspace/select",
        "/api/v1/workspace/sessions",
        "/api/v1/workspace/courses",
        "/api/v1/settings/model/check",
    ):
        assert route in javascript
    for marker in (
        "data-workspace-select",
        "data-workspace-session",
        "data-workspace-course",
        "workspace-new-session-course",
        "data-settings-check",
            "Verifica decisione tutor",
    ):
        assert marker in javascript


def test_private_chat_can_open_and_confirm_course_creation() -> None:
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert "/api/v1/chat/course-creation" in javascript
    for marker in (
        "data-open-course-creation",
        "data-chat-course-creation",
        "Crea un corso",
        "confirmed: true",
        "Crea e inizia a studiare",
    ):
        assert marker in javascript


def test_collapsed_sidebar_keeps_search_and_account_controls_on_same_grid() -> None:
    css = (DEMO_DIR / "browser.css").read_text(encoding="utf-8")

    assert ".rail.is-collapsed .nav-item" in css
    assert ".rail.is-collapsed .account-control" in css
    assert "width: 40px" in css
    assert "place-items: center" in css


def test_piano_is_explicitly_unavailable_and_source_conflicts_are_read_only() -> None:
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert "Piano non disponibile" in javascript
    assert "Disaccordo tra fonti: sola lettura" in javascript
    assert "Non esiste un owner canonico" in javascript


def test_assessment_surface_renders_safe_lifecycle_history() -> None:
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert "grade_history" in javascript
    assert "assessment-lifecycle" in javascript
    assert "supersedes_grade_id" in javascript
    assert "contested_at" in javascript
    assert "evaluation_criteria" not in javascript
    assert "criterion_results" not in javascript


def test_composer_has_an_ime_safe_enter_key_contract() -> None:
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert 'event.key !== "Enter"' in javascript
    assert "event.shiftKey" in javascript
    assert "event.isComposing" in javascript
    assert "event.keyCode === 229" in javascript
    assert "event.preventDefault()" in javascript
    assert "form.requestSubmit()" in javascript
    assert 'textarea.addEventListener("input"' in javascript
    assert "syncComposerState(form, textarea)" in javascript
    assert 'form.classList.toggle("has-value", hasValue)' in javascript
    assert "state.loading || !hasValue" in javascript


def test_claude_caliber_shell_polish_remains_theme_and_motion_safe() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    css = (DEMO_DIR / "browser.css").read_text(encoding="utf-8")
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert "color-scheme: light dark" in css
    assert "@media (prefers-color-scheme: dark)" in css
    assert "--ease-out: cubic-bezier(.23, 1, .32, 1)" in css
    assert "transition: width" not in css
    assert ".composer.has-value .composer__send" in css
    assert "icon--arrow-up" in page + javascript
    assert 'data-open-tutor-info' in page + javascript


def test_primary_surface_is_a_chat_workspace_with_secondary_tools() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    css = (DEMO_DIR / "browser.css").read_text(encoding="utf-8")
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert 'class="new-chat-button' in page
    assert 'class="rail-tools"' in page
    assert 'class="chat-home"' in javascript
    assert 'class="chat-session"' in javascript
    assert 'class="conversation-composer-dock"' in javascript
    assert "Invio invia · Maiusc + Invio va a capo" in javascript
    assert 'class="thread-message thread-message--assistant"' in javascript
    assert "aiAnswer({" in javascript
    assert ".conversation-scroll" in css
    assert "position: sticky" in css


def test_ai_primitive_assets_are_included_in_the_wheel_package_data() -> None:
    project = (DEMO_DIR.parents[2] / "pyproject.toml").read_text(encoding="utf-8")

    assert '"study_agent.demo" = [' in project
    for asset in ("ai-primitives.css", "ai-primitives.js", "icons/*.svg"):
        assert asset in project
