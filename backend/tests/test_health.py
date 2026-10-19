"""Tests for /health, /ready, and Correlation-ID."""

def test_health_check_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert "X-Correlation-Id" in response.headers

def test_readiness_check_returns_200(client):
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "connected"
    assert data["storage"] == "available"

def test_correlation_id_propagation(client):
    custom_corr_id = "test-corr-12345"
    response = client.get("/health", headers={"X-Correlation-Id": custom_corr_id})
    assert response.headers.get("X-Correlation-Id") == custom_corr_id
