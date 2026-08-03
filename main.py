import csv
import io
import time
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException
from loopio_bulk_create_projects import get_access_token, row_to_payload, create_project, DELAY_BETWEEN_CALLS_SECONDS

app = FastAPI(title="Loopio Bulk Create Projects API")

@app.post("/api/v1/projects/bulk-create")
async def bulk_create_projects(file: UploadFile = File(...)):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are allowed.")
    
    contents = await file.read()
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = contents.decode("utf-8")
        
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    
    if not rows:
        raise HTTPException(status_code=400, detail="Input CSV has no rows.")
        
    required_cols = {"name", "projectType", "companyName", "dueDate"}
    missing = required_cols - set(rows[0].keys())
    if missing:
        raise HTTPException(status_code=400, detail=f"CSV is missing required column(s): {', '.join(sorted(missing))}")
        
    try:
        token = get_access_token()
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    session = requests.Session()
    results = []
    
    for i, row in enumerate(rows, 1):
        name = row.get("name", "<unnamed>")
        
        try:
            payload = row_to_payload(row)
        except Exception as e:
            results.append({"row": i, "name": name, "status": "invalid_row", "detail": str(e)})
            continue
            
        ok, response = create_project(session, token, payload)
        
        if not ok and response.get("refresh_token"):
            try:
                token = get_access_token()
                ok, response = create_project(session, token, payload)
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
