"""
Standalone equivalent of:

    curl -v '{VaultServerAddress}/AutodeskDM/Services/api/vault/v2/vaults/3/jobs' \
        -X 'POST' \
        -H 'Content-Type: application/json' \
        -H 'Authorization: Bearer AuIPTf4KYLTYGVnOHQ0cuolwCW2a...' \
        -d '{
              "JobType": "Autodesk.Vault.DWF.Create.iam",
              "Description": "DWF Create: 144001000-001.iam",
              "Priority": 10,
              "Params": { "FileVersionId": "11462" }
            }'

Reads server URL + credentials from ../config.json, signs in to get a fresh
bearer token, then POSTs the body verbatim to vault 3's /jobs endpoint.

Run from anywhere:
    python scripts/submit_dwf_job.py
"""

import json
import sys
import time
from pathlib import Path

import httpx


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
CFG = json.loads(CONFIG_PATH.read_text("utf-8"))

SERVER = CFG["vault"]["servername"].rstrip("/")
DATABASE = CFG["vault"]["database"]
USERNAME = CFG["vault"]["username"]
PASSWORD = CFG["vault"]["password"]

API_BASE = f"{SERVER}/AutodeskDM/Services/api/vault/v2"

VAULT_ID = "1"
FILE_VERSION_ID = "112793"
JOB_BODY = {
    "JobType": "Autodesk.Vault.PDF.Create.idw",
    "Description": f"Test PDF Creation [{int(time.time())}]",
    "Priority": 10,
    "Params": {
        "FileVersionId": FILE_VERSION_ID,
        "UpdateViewOption": "False",
    },
}


def sign_in() -> str:
    r = httpx.post(
        f"{API_BASE}/sessions",
        json={
            "input": {
                "vault": DATABASE,
                "userName": USERNAME,
                "password": PASSWORD,
                "appCode": "",
            }
        },
        verify=False,
        timeout=30.0,
    )
    r.raise_for_status()
    token = r.json()["accessToken"]
    return token if token.startswith("Bearer ") else f"Bearer {token}"


def submit_job(bearer: str) -> httpx.Response:
    return httpx.post(
        f"{API_BASE}/vaults/{VAULT_ID}/jobs",
        headers={
            "Content-Type": "application/json",
            "Authorization": bearer,
        },
        json=JOB_BODY,
        verify=False,
        timeout=30.0,
    )


def main() -> int:
    bearer = sign_in()
    print(f"Signed in. Token: {bearer[:20]}...")

    resp = submit_job(bearer)
    print(f"\nPOST {API_BASE}/vaults/{VAULT_ID}/jobs")
    print(f"Body sent: {json.dumps(JOB_BODY, indent=2)}")
    print(f"\nHTTP {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2))
    except ValueError:
        print(resp.text)

    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
