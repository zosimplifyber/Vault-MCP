"""
Standalone STEP-publish tester.

Submits one Autodesk.Vault.STEP.Create.ipt job for a target part, polls
until terminal, then verifies whether a sibling .stp file appeared in the
vault.

Use this to iterate on the STEP `Params` shape — the JP `StepCreateJob..ctor`
rejects the job up-front with `Job param error` until the right keys are
present. Edit JOB_PARAMS below and rerun.

Optional: pass --copy-from <jobId> to first fetch an existing job's params
(e.g. one queued from Vault Explorer's GUI), echo them, and then submit a
new job using EXACTLY those params. This is the canonical way to crack the
required-key shape — mirror what Vault Explorer does, then strip back.

Run:
    # plain submit using the params hard-coded in the script
    python scripts/submit_step_job.py

    # use a different part by file name
    python scripts/submit_step_job.py --part CD-001141.ipt

    # mirror the param shape of an existing GUI-queued job
    python scripts/submit_step_job.py --copy-from 24830
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx


# ---------------------------------------------------------------------------
# Config + connection helpers
# ---------------------------------------------------------------------------

CFG = json.loads(
    (Path(__file__).resolve().parent.parent / "config.json").read_text("utf-8")
)["vault"]
SERVER = CFG["servername"].rstrip("/")
BASE = f"{SERVER}/AutodeskDM/Services/api/vault/v2"
VAULT_ID = "1"

DEFAULT_PART_NAME = "CD-001107.ipt"
LBL = {0: "Ready", 1: "Running", 2: "Success", 3: "Failure"}

# >>> EDIT ME <<<
# Canonical params for STEP.Create.ipt|.iam, verified by mirroring a
# successful Vault Explorer-queued job (24843 — CD-001141.ipt).
# PascalCase keys; the ctor reads BOTH UpdatePdfOption and UpdateViewOption
# (despite the name, neither is the STEP-specific UpdateStpOption).
JOB_PARAMS = {
    # "FileVersionId" is filled in at runtime once we resolve the part.
    "UpdatePdfOption": "False",
    "UpdateViewOption": "False",
}


# ---------------------------------------------------------------------------
# REST helpers
# ---------------------------------------------------------------------------

def sign_in(client: httpx.Client) -> dict:
    r = client.post(f"{BASE}/sessions", json={"input": {
        "vault": CFG["database"], "userName": CFG["username"],
        "password": CFG["password"], "appCode": ""}})
    r.raise_for_status()
    tok = r.json()["accessToken"]
    if not tok.startswith("Bearer "):
        tok = f"Bearer {tok}"
    return {"Authorization": tok}


def search_part(client, headers, name) -> dict | None:
    """Resolve a .ipt or .iam by name. Returns the FileVersion record."""
    stem = name.rsplit(".", 1)[0]
    r = client.get(f"{BASE}/vaults/{VAULT_ID}/search-results", headers=headers,
                   params={"q": stem, "limit": 20,
                           "option.latestOnly": "true",
                           "option.searchSubFolders": "true"})
    if r.status_code != 200:
        return None
    for rec in r.json().get("results", []):
        if rec.get("entityType") != "FileVersion":
            continue
        if rec.get("name") == name:
            return rec
    return None


def get_job(client, headers, job_id):
    return client.get(f"{BASE}/vaults/{VAULT_ID}/jobs/{job_id}",
                      headers=headers)


def submit_job(client, headers, file_version_id, ext, params):
    body = {
        "JobType": f"Autodesk.Vault.STEP.Create.{ext}",
        "Description": f"STEP test [{int(time.time())}]",
        "Priority": 10,
        "Params": {"FileVersionId": str(file_version_id), **params},
    }
    print(f"\nPOST {BASE}/vaults/{VAULT_ID}/jobs")
    print(f"Body sent: {json.dumps(body, indent=2)}")
    r = client.post(f"{BASE}/vaults/{VAULT_ID}/jobs",
                    headers={**headers, "Content-Type": "application/json"},
                    json=body)
    print(f"\nHTTP {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2))
    except ValueError:
        print(r.text)
    return r.status_code, r.json() if r.headers.get("content-type","").startswith("application/json") else {}


def poll_until_done(client, headers, job_id, timeout_s=180):
    print(f"\n--- Polling job {job_id} (every 3s, timeout {timeout_s}s) ---")
    last = None
    start = time.time()
    while time.time() - start < timeout_s:
        r = get_job(client, headers, job_id)
        elapsed = int(time.time() - start)
        if r.status_code == 404:
            print(f"  t+{elapsed:>3}s -> 404 (deleted/done)")
            return "Deleted"
        if r.status_code == 200:
            st = r.json().get("status")
            lbl = LBL.get(st, f"?({st})")
            if lbl != last:
                print(f"  t+{elapsed:>3}s -> status={st} ({lbl})")
                last = lbl
            if st in (2, 3):
                return lbl
        time.sleep(3)
    return "Timeout"


def find_step_for(client, headers, part_name):
    """Look for a sibling .stp/.step file matching the part stem."""
    stem = part_name.rsplit(".", 1)[0]
    r = client.get(f"{BASE}/vaults/{VAULT_ID}/search-results", headers=headers,
                   params={"q": stem, "limit": 20,
                           "option.latestOnly": "true",
                           "option.searchSubFolders": "true"})
    if r.status_code != 200:
        return None
    for rec in r.json().get("results", []):
        if rec.get("entityType") != "FileVersion":
            continue
        nm = rec.get("name") or ""
        if not nm.startswith(stem + "."):
            continue
        ext = nm.rsplit(".", 1)[-1].lower()
        if ext in ("stp", "step"):
            return rec
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--part", default=DEFAULT_PART_NAME,
                    help=f"Part file name. Default: {DEFAULT_PART_NAME}")
    ap.add_argument("--copy-from", type=int, default=None, metavar="JOB_ID",
                    help="Mirror the params of this existing job (e.g. one "
                         "queued from Vault Explorer) instead of using "
                         "JOB_PARAMS.")
    args = ap.parse_args()

    with httpx.Client(verify=False, timeout=60.0) as client:
        h = sign_in(client)

        if args.copy_from:
            print(f"=== Fetching reference job {args.copy_from} ===")
            r = get_job(client, h, args.copy_from)
            print(f"HTTP {r.status_code}")
            print(json.dumps(r.json(), indent=2))
            if r.status_code != 200:
                print("\n[abort] reference job not found — was it already swept?")
                return 1
            ref = r.json()
            # The REST API echoes params with camelCased keys but the JP
            # accepts (and requires) PascalCase. Convert first-letter-up.
            ref_params = {
                (k[:1].upper() + k[1:]): v
                for k, v in (ref.get("params") or {}).items()
                if k.lower() != "fileversionid"
            }
            print("\n--- Mirrored params (PascalCase) ---")
            print(json.dumps(ref_params, indent=2))
            params = ref_params
        else:
            params = dict(JOB_PARAMS)

        print(f"\n=== Resolving part {args.part!r} ===")
        rec = search_part(client, h, args.part)
        if not rec:
            print(f"[abort] could not find a FileVersion for {args.part!r}")
            return 1
        fvid = rec["id"]
        ext = rec["name"].rsplit(".", 1)[-1].lower()
        print(f"  {rec['name']}: fileVersionId={fvid} state={rec.get('state')}")

        sc, payload = submit_job(client, h, fvid, ext, params)
        if sc != 200:
            print(f"\n[abort] submit failed http {sc}")
            return 1
        job_id = payload.get("id")
        if not job_id:
            print(f"\n[abort] no job id in response")
            return 1

        state = poll_until_done(client, h, job_id, timeout_s=180)
        print(f"\n=== Job {job_id} terminal state: {state} ===")

        # Verify whether a .stp landed in vault
        print("\n--- Looking for sibling .stp/.step in vault ---")
        time.sleep(2)
        stp = find_step_for(client, h, args.part)
        if stp:
            print(f"  PASS: {stp['name']} (verId={stp['id']}) state={stp.get('state')}")
            return 0
        print("  FAIL: no .stp/.step appeared — the JP either errored after the "
              "ctor or wrote the file with an unexpected name.")
        print("        Check JP log near the timestamp above. If the ctor still "
              "rejected, edit JOB_PARAMS and rerun, or use --copy-from after "
              "queueing a STEP job manually from Vault Explorer.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
