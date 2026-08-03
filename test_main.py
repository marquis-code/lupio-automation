from fastapi.testclient import TestClient
from main import app
import unittest.mock as mock

client = TestClient(app)

@mock.patch("main.get_access_token")
@mock.patch("main.create_project")
def test_bulk_create_success(mock_create_project, mock_get_access_token):
    mock_get_access_token.return_value = "fake_token"
    mock_create_project.return_value = (True, {"id": 123})
    
    json_payload = [
        {
            "name": "Test Project",
            "projectType": "RFP",
            "companyName": "Test Co",
            "dueDate": "2026-09-08T15:39:04Z",
            "description": "Test Desc"
        }
    ]
    
    response = client.post(
        "/api/v1/projects/bulk-create",
        json=json_payload,
        headers={
            "X-Loopio-Client-Id": "test_id",
            "X-Loopio-Client-Secret": "test_secret"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total"] == 1
    assert data["summary"]["succeeded"] == 1
    assert data["results"][0]["project_id"] == 123

def test_bulk_create_missing_fields():
    json_payload = [
        {
            "name": "Test Project",
            "projectType": "RFP"
            # Missing companyName and dueDate
        }
    ]
    response = client.post(
        "/api/v1/projects/bulk-create",
        json=json_payload,
        headers={
            "X-Loopio-Client-Id": "test_id",
            "X-Loopio-Client-Secret": "test_secret"
        }
    )
    assert response.status_code == 400
    assert "missing required field(s)" in response.json()["detail"]
