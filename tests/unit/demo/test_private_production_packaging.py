from pathlib import Path

ROOT = Path(__file__).parents[3]


def test_private_production_image_has_a_non_public_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile.production").read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker" / "production-entrypoint.sh").read_text(
        encoding="utf-8"
    )
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "ENTRYPOINT [\"/usr/local/bin/cardine-production-entrypoint\"]" in dockerfile
    assert "--private" in entrypoint
    assert "--production" in entrypoint
    assert "--public-demo" not in entrypoint
    assert 'cardine-private-password-hash = "study_agent.demo.private_access:main"' in project
    for variable in (
        "CARDINE_OWNER_PASSWORD_HASH",
        "CARDINE_PUBLIC_ORIGIN",
        "CARDINE_COURSE_ID",
        "CARDINE_SESSION_ID",
        "CARDINE_REPOSITORY",
    ):
        assert variable in entrypoint


def test_private_production_compose_persists_repository_and_drops_capabilities() -> None:
    compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")

    assert "Dockerfile.production" in compose
    assert "read_only: true" in compose
    assert "cap_drop:" in compose
    assert "CARDINE_REPOSITORY_PATH" in compose
    assert ":/cardine/repository" in compose
    assert '"127.0.0.1:8080:8080"' in compose
