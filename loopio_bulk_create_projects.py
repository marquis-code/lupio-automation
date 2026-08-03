#!/usr/bin/env python3
"""
loopio_bulk_create_projects.py

Bulk-create Loopio Projects from a CSV file, using the single-project
POST /data/v2/projects endpoint under the hood (Loopio doesn't offer a
native bulk endpoint, so this script loops + rate-limits for you).

WHAT IT DOES
------------
1. Authenticates once via OAuth2 Client Credentials flow (client_id +
   client_secret -> access token), matching the "Client Credentials
   OAuth Flow" shown in Loopio's docs (scope: project:write).
2. Reads a CSV of projects to create.
3. POSTs each row to /data/v2/projects, respecting a delay between
   calls to avoid hammering the API.
4. Retries on 401 (token refresh) and 429 (rate limit, honors
   Retry-After if present) with exponential backoff on 5xx.
5. Writes a results CSV (results_<timestamp>.csv) with the outcome of
   every row: success + new project id, or the error returned.

SETUP
-----
1. pip install requests python-dotenv
2. Create a .env file next to this script (or export env vars):

     LOOPIO_CLIENT_ID=your_client_id
     LOOPIO_CLIENT_SECRET=your_client_secret
     LOOPIO_BASE_URL=https://api.loopio.com   # American DC shown in docs;
                                               # swap for your datacenter's
                                               # base URL if different

3. Build your input CSV (see SAMPLE CSV COLUMNS below). Only name,
   projectType, companyName, and dueDate are required by the API;
   description and owner_id are optional. Any extra columns whose
   names start with "cf_" are packed automatically into
   customProjectFieldValues (e.g. a column "cf_Region" becomes
   customProjectFieldValues["Region"]).

4. Run:
     python loopio_bulk_create_projects.py --input projects.csv

   Add --dry-run first to validate the CSV and preview payloads
   without actually calling the API.

SAMPLE CSV COLUMNS
-------------------
name,projectType,description,companyName,dueDate,createdType,owner_id,cf_Region,cf_Priority
Project ABC,RFP,Some description,Acme Co,2026-09-08T15:39:04Z,BLANK,0,EMEA,High
Project XYZ,RFI,,Beta Inc,2026-10-01T12:00:00Z,BLANK,,APAC,

  - dueDate must be ISO-8601 (e.g. 2026-09-08T15:39:04Z). Loopio
    converts it to UTC and pushes to end of day.
  - projectType allowed values: RFP, RFI, DDQ, SQ, PP, OTHER
  - createdType allowed values: BLANK, TEMPLATE, COPY_OUTLINE,
    CHROME_EXTENSION, SALESFORCE, MS_DYNAMICS, MS_EXCEL_ADDIN
    (defaults to BLANK if the column is missing/empty)
  - owner_id is optional; if blank, the authenticated user becomes owner.
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("LOOPIO_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOOPIO_CLIENT_SECRET")
BASE_URL = os.getenv("LOOPIO_BASE_URL", "https://api.loopio.com").rstrip("/")

TOKEN_URL = f"{BASE_URL}/oauth2/access_token"
PROJECTS_URL = f"{BASE_URL}/data/v2/projects"

DELAY_BETWEEN_CALLS_SECONDS = 0.5  # be polite to the API; tune as needed
MAX_RETRIES = 5


def get_access_token():
    """Client Credentials OAuth flow -> bearer token with project:write scope."""
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError(
            "Missing LOOPIO_CLIENT_ID / LOOPIO_CLIENT_SECRET. "
            "Set them in a .env file or as environment variables."
        )

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "project:write",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def row_to_payload(row):
    """Convert one CSV row (dict) into the JSON body /data/v2/projects expects."""
    payload = {
        "name": row["name"].strip(),
        "projectType": row["projectType"].strip(),
        "companyName": row["companyName"].strip(),
        "dueDate": row["dueDate"].strip(),
        "createdType": (row.get("createdType") or "BLANK").strip(),
    }

    description = (row.get("description") or "").strip()
    if description:
        payload["description"] = description

    owner_id = (row.get("owner_id") or "").strip()
    if owner_id:
        payload["owner"] = {"id": int(owner_id)}

    custom_fields = {
        key[3:]: value
        for key, value in row.items()
        if key.startswith("cf_") and value not in (None, "")
    }
    if custom_fields:
        payload["customProjectFieldValues"] = custom_fields

    return payload


def create_project(session, token, payload):
    """POST a single project, with retry/backoff. Returns (ok, response_json_or_error)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    backoff = 2
    for attempt in range(1, MAX_RETRIES + 1):
        resp = session.post(PROJECTS_URL, json=payload, headers=headers, timeout=30)

        if resp.status_code in (200, 201):
            return True, resp.json()

        if resp.status_code == 401:
            # token expired mid-run -> caller should refresh and retry
            return False, {"error": "unauthorized", "status": 401, "refresh_token": True}

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", backoff))
            print(f"    Rate limited, waiting {wait}s (attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(wait)
            backoff *= 2
            continue

        if 500 <= resp.status_code < 600:
            print(f"    Server error {resp.status_code}, retrying in {backoff}s "
                  f"(attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(backoff)
            backoff *= 2
            continue

        # 4xx validation errors etc. - don't retry, just report
        try:
            return False, resp.json()
        except ValueError:
            return False, {"error": resp.text, "status": resp.status_code}

    return False, {"error": "max retries exceeded"}


def main():
    parser = argparse.ArgumentParser(description="Bulk-create Loopio projects from a CSV.")
    parser.add_argument("--input", required=True, help="Path to input CSV of projects.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Validate and print payloads without calling the API.")
    parser.add_argument("--delay", type=float, default=DELAY_BETWEEN_CALLS_SECONDS,
                         help="Seconds to wait between API calls (default: 0.5).")
    args = parser.parse_args()

    with open(args.input, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        sys.exit("Input CSV has no rows.")

    required_cols = {"name", "projectType", "companyName", "dueDate"}
    missing = required_cols - set(rows[0].keys())
    if missing:
        sys.exit(f"CSV is missing required column(s): {', '.join(sorted(missing))}")

    print(f"Loaded {len(rows)} project(s) from {args.input}")

    if args.dry_run:
        for i, row in enumerate(rows, 1):
            print(f"\n--- Row {i} payload preview ---")
            print(row_to_payload(row))
        print("\nDry run complete. No API calls were made.")
        return

    token = get_access_token()
    session = requests.Session()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = f"results_{timestamp}.csv"
    results = []

    for i, row in enumerate(rows, 1):
        name = row.get("name", "<unnamed>")
        print(f"[{i}/{len(rows)}] Creating '{name}'...")

        try:
            payload = row_to_payload(row)
        except Exception as e:
            print(f"    Skipped: invalid row data ({e})")
            results.append({"row": i, "name": name, "status": "invalid_row", "detail": str(e)})
            continue

        ok, response = create_project(session, token, payload)

        if not ok and response.get("refresh_token"):
            print("    Token expired mid-run, refreshing and retrying once...")
            token = get_access_token()
            ok, response = create_project(session, token, payload)

        if ok:
            print(f"    Created (id={response.get('id')})")
            results.append({
                "row": i, "name": name, "status": "success",
                "project_id": response.get("id"), "detail": "",
            })
        else:
            print(f"    Failed: {response}")
            results.append({
                "row": i, "name": name, "status": "failed",
                "project_id": "", "detail": str(response),
            })

        time.sleep(args.delay)

    with open(results_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["row", "name", "status", "project_id", "detail"])
        writer.writeheader()
        writer.writerows(results)

    succeeded = sum(1 for r in results if r["status"] == "success")
    print(f"\nDone. {succeeded}/{len(rows)} succeeded. Full results written to {results_path}")


if __name__ == "__main__":
    main()