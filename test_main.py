from fastapi.testclient import TestClient
from main import app
import unittest.mock as mock

client = TestClient(app)

@mock.patch("main.get_access_token")
@mock.patch("main.create_project")
def test_bulk_create_success(mock_create_project, mock_get_access_token):
    mock_get_access_token.return_value = "fake_token"
    mock_create_project.return_value = (True, {"id": 123})
    
    csv_content = """name,projectType,companyName,dueDate,description
Test Project,RFP,Test Co,2026-09-08T15:39:04Z,Test Desc"""
    
    response = client.post(
        "/api/v1/projects/bulk-create",
        files={"file": ("test.csv", csv_content, "text/csv")}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total"] == 1
    assert data["summary"]["succeeded"] == 1
    assert data["results"][0]["project_id"] == 123

def test_bulk_create_invalid_file_extension():
    response = client.post(
        "/api/v1/projects/bulk-create",
        files={"file": ("test.txt", "some content", "text/plain")}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Only CSV files are allowed."
