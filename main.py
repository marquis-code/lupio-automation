import time
import requests
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException, Body, Header
from loopio_bulk_create_projects import get_access_token, create_project, DELAY_BETWEEN_CALLS_SECONDS

app = FastAPI(title="Loopio Bulk Create Projects API")

@app.post("/api/v1/projects/bulk-create")
async def bulk_create_projects(
    rows: List[Dict[str, Any]] = Body(...),
    loopio_client_id: str = Header(..., alias="X-Loopio-Client-Id"),
    loopio_client_secret: str = Header(..., alias="X-Loopio-Client-Secret"),
    loopio_base_url: str = Header("https://api.loopio.com", alias="X-Loopio-Base-Url")
):
    if not rows:
        raise HTTPException(status_code=400, detail="Input JSON has no projects.")
        
    required_cols = {"name", "projectType", "companyName", "dueDate"}
    for idx, row in enumerate(rows):
        missing = required_cols - set(row.keys())
        if missing:
            raise HTTPException(status_code=400, detail=f"Project at index {idx} is missing required field(s): {', '.join(sorted(missing))}")
        
    try:
        token = get_access_token(loopio_client_id, loopio_client_secret, loopio_base_url)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    session = requests.Session()
    results = []
    
    for i, row in enumerate(rows, 1):
        name = row.get("name", "<unnamed>")
        
        payload = row
            
        ok, response = create_project(session, token, payload, loopio_base_url)
        
        if not ok and response.get("refresh_token"):
            try:
                token = get_access_token(loopio_client_id, loopio_client_secret, loopio_base_url)
                ok, response = create_project(session, token, payload, loopio_base_url)
            except Exception as e:
                results.append({"row": i, "name": name, "status": "failed", "detail": f"Token refresh failed: {str(e)}"})
                continue
                
        if ok:
            results.append({
                "row": i, "name": name, "status": "success",
                "project_id": response.get("id"), "detail": "",
            })
        else:
            results.append({
                "row": i, "name": name, "status": "failed",
                "project_id": "", "detail": str(response),
            })
            
        time.sleep(DELAY_BETWEEN_CALLS_SECONDS)
        
    succeeded = sum(1 for r in results if r["status"] == "success")
    return {
        "summary": {
            "total": len(rows),
            "succeeded": succeeded,
            "failed": len(rows) - succeeded
        },
        "results": results
    }
