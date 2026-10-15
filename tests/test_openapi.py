"""Tests for OpenAPI 3.1 compliance conforming to Integration Standard #2."""
from pathlib import Path
import yaml
from app.main import app

def test_openapi_yaml_exists_and_version():
    spec_path = Path("openapi.yaml")
    assert spec_path.exists(), "openapi.yaml must exist in repository root"
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    assert spec.get("openapi") == "3.1.0", "OpenAPI version must be 3.1.0"
    assert "paths" in spec
    assert "/health" in spec["paths"]
    assert "/ready" in spec["paths"]
    assert "/api/v1/broll/jobs" in spec["paths"]
    assert "/api/v1/jobs" in spec["paths"]

def test_all_api_routes_under_v1():
    """All application routes must use the /api/v1/ prefix, except /health, /ready, and /docs."""
    allowed_roots = {"/health", "/ready", "/openapi.yaml", "/", "/docs", "/redoc", "/api/v1/openapi.json", "/docs/oauth2-redirect"}
    for route in app.routes:
        path = getattr(route, "path", None)
        if path and path not in allowed_roots and not path.startswith("/static"):
            assert path.startswith("/api/v1/"), f"Route '{path}' must be under /api/v1/"
