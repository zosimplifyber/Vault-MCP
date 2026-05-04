"""
Probe autodesk.vault.pdf.create.idw with several param-shapes to find what the
JP handler actually accepts.

Run this on any machine that can reach the Vault server (uses config.json).
Then go to the JP / Vault Explorer Job Server Queue and look at the Status
column for the resulting job IDs. Whichever one shows Success names the
correct shape; the rest will be Error with "Job param error".
"""

import asyncio
import json
from pathlib import Path

import httpx

CONFIG = json.loads(
    (Path(__file__).resolve().parent.parent / "config.json").read_text("utf-8")
)
SERVER = CONFIG["vault"]["servername"].rstrip("/")
DB = CONFIG["vault"]["database"]
USER = CONFIG["vault"]["username"]
PWD = CONFIG["vault"]["password"]
BASE = f"{SERVER}/AutodeskDM/Services/api/vault/v2"

FILE_MASTER_ID = "106163"   # CD-001289.idw master
FILE_VERSION_ID = "110523"  # CD-001289.idw latest version
JOB_TYPE = "Autodesk.Vault.PDF.Create.idw"  # PascalCase per Vault Explorer canonical


async def sign_in(client):
    r = await client.post(
        f"{BASE}/sessions",
        json={
            "input": {"vault": DB, "userName": USER, "password": PWD, "appCode": ""}
        },
    )
    r.raise_for_status()
    data = r.json()
    return (
        data["accessToken"],
        str((data.get("vaultInformation") or {}).get("id", "")),
    )


async def submit(client, token, vault_id, label, body):
    headers = {
        "Authorization": (
            token if token.startswith("Bearer ") else f"Bearer {token}"
        ),
        "Content-Type": "application/json",
    }
    r = await client.post(
        f"{BASE}/vaults/{vault_id}/jobs", headers=headers, json=body
    )
    try:
        payload = r.json()
    except Exception:
        payload = {"text": r.text[:300]}
    print(f"\n--- {label} ---")
    print(f"sent: {json.dumps(body)[:200]}")
    print(f"status: {r.status_code}")
    if r.status_code == 200:
        job_id = (payload.get("data") or {}).get("id", "?")
        stored = (payload.get("data") or {}).get("params", {})
        print(f"job_id: {job_id}")
        print(f"stored params: {stored}")
    else:
        print(f"response: {json.dumps(payload, indent=2)[:400]}")


async def main():
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        token, vault_id = await sign_in(client)
        print(f"Signed in. vault_id={vault_id}")

        # Per Autodesk docs example, body uses ALL PascalCase keys:
        # JobType / Description / Priority / Params / FileVersionId
        shapes = [
            ("PASCAL-all-FileVersionId", {
                "JobType": JOB_TYPE,
                "Description": "PROBE PDF Create: CD-001289.idw",
                "Priority": 10,
                "Params": {"FileVersionId": FILE_VERSION_ID},
            }),
            ("PASCAL-all-FileMasterId", {
                "JobType": JOB_TYPE,
                "Description": "PROBE Master PDF Create: CD-001289.idw",
                "Priority": 10,
                "Params": {"FileMasterId": FILE_MASTER_ID},
            }),
            ("PASCAL-all-with-id-and-url", {
                "Id": "",
                "JobType": JOB_TYPE,
                "Description": "PROBE Full PDF Create: CD-001289.idw",
                "Priority": 10,
                "Url": "",
                "Params": {"FileVersionId": FILE_VERSION_ID},
                "IsOnSite": "",
            }),
        ]

        for label, body in shapes:
            try:
                await submit(client, token, vault_id, label, body)
            except Exception as exc:
                print(f"\n--- {label} ---  EXCEPTION: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
