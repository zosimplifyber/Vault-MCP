"""
Diagnostic: probe POST /vaults/{vaultId}/jobs with several body shapes to find
what the Vault v2 REST API actually accepts. Reads creds from config.json.
"""

import asyncio
import json
import sys
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


async def sign_in(client):
    r = await client.post(
        f"{BASE}/sessions",
        json={"input": {"vault": DB, "userName": USER, "password": PWD, "appCode": ""}},
    )
    r.raise_for_status()
    data = r.json()
    return data["accessToken"], str((data.get("vaultInformation") or {}).get("id", ""))


async def probe(client, token, vault_id, label, body):
    headers = {
        "Authorization": token if token.startswith("Bearer ") else f"Bearer {token}",
        "Content-Type": "application/json",
    }
    r = await client.post(f"{BASE}/vaults/{vault_id}/jobs", headers=headers, json=body)
    try:
        payload = r.json()
    except Exception:
        payload = {"text": r.text[:300]}
    print(f"\n--- {label} ---")
    print(f"body: {json.dumps(body)}")
    print(f"status: {r.status_code}")
    print(f"response: {json.dumps(payload, indent=2)[:600]}")


async def main():
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        token, vault_id = await sign_in(client)
        print(f"Signed in. vault_id={vault_id}")

        shapes = [
            ("flat object params",
             {"jobType": "Autodesk.Vault.SyncProperties",
              "params": {"FileMasterId": "107271"}}),

            ("flat empty params",
             {"jobType": "Autodesk.Vault.SyncProperties", "params": {}}),

            ("flat no params field",
             {"jobType": "Autodesk.Vault.SyncProperties"}),

            ("flat with description",
             {"jobType": "Autodesk.Vault.SyncProperties",
              "description": "probe",
              "params": {"FileMasterId": "107271"}}),

            ("input wrapper",
             {"input": {"jobType": "Autodesk.Vault.SyncProperties",
                        "params": {"FileMasterId": "107271"}}}),

            ("input wrapper, no params",
             {"input": {"jobType": "Autodesk.Vault.SyncProperties"}}),

            ("array-of-keyvalue params",
             {"jobType": "Autodesk.Vault.SyncProperties",
              "params": [{"key": "FileMasterId", "value": "107271"}]}),

            ("array params named name/val",
             {"jobType": "Autodesk.Vault.SyncProperties",
              "params": [{"name": "FileMasterId", "val": "107271"}]}),

            ("priority required (1)",
             {"jobType": "Autodesk.Vault.SyncProperties",
              "priority": 1, "params": {"FileMasterId": "107271"}}),

            ("nonsense jobType to compare error",
             {"jobType": "ThisDoesNotExist.Foo.Bar",
              "params": {"FileMasterId": "107271"}}),

            ("missing jobType",
             {"params": {"FileMasterId": "107271"}}),

            ("empty body",
             {}),

            ("with url field",
             {"jobType": "Autodesk.Vault.SyncProperties",
              "url": "",
              "params": {"FileMasterId": "107271"}}),

            ("full Job all fields",
             {"id": "",
              "jobType": "Autodesk.Vault.SyncProperties",
              "priority": 1,
              "description": "probe",
              "url": "",
              "params": {"FileMasterId": "107271"},
              "isOnSite": ""}),

            ("flat with empty isOnSite only",
             {"jobType": "Autodesk.Vault.SyncProperties",
              "isOnSite": "",
              "params": {"FileMasterId": "107271"}}),

            ("status defaulted to Ready",
             {"jobType": "Autodesk.Vault.SyncProperties",
              "status": "Ready",
              "params": {"FileMasterId": "107271"}}),

            ("array params w/ Key/Val capitalized",
             {"jobType": "Autodesk.Vault.SyncProperties",
              "params": [{"Key": "FileMasterId", "Val": "107271"}]}),

            ("known-good Inventor jobtype",
             {"jobType": "Autodesk.Vault.DWFCreate",
              "params": {"FileMasterId": "107271"}}),

            ("priority only no params",
             {"jobType": "Autodesk.Vault.SyncProperties",
              "priority": 1}),
        ]

        for label, body in shapes:
            try:
                await probe(client, token, vault_id, label, body)
            except Exception as exc:
                print(f"\n--- {label} ---  EXCEPTION: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
