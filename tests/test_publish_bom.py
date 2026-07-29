# tests/test_publish_bom.py
"""Unit tests for the BOM-driven deliverable publisher.

Parsing runs against the real production export
(``tests/fixtures/CD-001608-bom.xlsx``) plus synthetic exports built in-test
for the BOM Structure values that file happens not to contain. Vault access is
faked — nothing here touches the network.
"""
import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import publish_bom  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
REAL_BOM = os.path.join(FIXTURES, "CD-001608-bom.xlsx")


def test_scanrow_counts_one_job_per_resolved_file():
    row = publish_bom.ScanRow(stem="CD-001578")
    assert row.job_count == 0

    row.model_version_id = "124814"
    assert row.job_count == 1

    row.drawing_version_id = "124815"
    assert row.job_count == 2


# --------------------------------------------------------------------------- parsing

MAKE_STEMS = {
    "CD-001613", "CD-001612", "CD-001577", "CD-001578", "CD-001621",
    "CD-001623", "CD-001620", "CD-001660", "CD-001364",
}


def _write_bom(tmp_path, records, columns=None):
    """Build a synthetic Inventor-shaped export and return its path."""
    df = pd.DataFrame(records, columns=columns)
    path = tmp_path / "synthetic BOM.xlsx"
    df.to_excel(path, index=False)
    return str(path)


def test_the_real_bom_yields_exactly_its_nine_normal_rows():
    rows, error = publish_bom.load_publish_rows(REAL_BOM)
    assert error is None
    assert {r.stem for r in rows} == MAKE_STEMS


def test_purchased_rows_are_excluded_even_with_an_in_house_number():
    """CD-001366.ipt carries a CD number but is marked Purchased.

    BOM Structure is authoritative — a part that needs deliverables but is
    marked Purchased is a BOM error to fix in Inventor, not something this
    tool second-guesses.
    """
    rows, _ = publish_bom.load_publish_rows(REAL_BOM)
    assert "CD-001366" not in {r.stem for r in rows}


def test_reference_rows_are_excluded(tmp_path):
    """coerce_bom_dataframe maps Reference onto Make, so this only passes if
    the raw BOM Structure column was captured before coercion."""
    path = _write_bom(tmp_path, [
        {"Item": "1", "Filename": "CD-000001.ipt", "BOM Structure": "Normal",
         "QTY": "1", "Description": "keep"},
        {"Item": "2", "Filename": "CD-000002.ipt", "BOM Structure": "Reference",
         "QTY": "1", "Description": "drop"},
    ])
    rows, error = publish_bom.load_publish_rows(path)
    assert error is None
    assert {r.stem for r in rows} == {"CD-000001"}


def test_phantom_and_inseparable_and_unknown_structures_are_kept(tmp_path):
    path = _write_bom(tmp_path, [
        {"Item": "1", "Filename": "CD-000001.ipt", "BOM Structure": "Phantom",
         "QTY": "1", "Description": "a"},
        {"Item": "2", "Filename": "CD-000002.ipt", "BOM Structure": "Inseparable",
         "QTY": "1", "Description": "b"},
        {"Item": "3", "Filename": "CD-000003.ipt", "BOM Structure": "",
         "QTY": "1", "Description": "c"},
        {"Item": "4", "Filename": "CD-000004.ipt", "BOM Structure": "Whatever",
         "QTY": "1", "Description": "d"},
    ])
    rows, error = publish_bom.load_publish_rows(path)
    assert error is None
    assert {r.stem for r in rows} == {
        "CD-000001", "CD-000002", "CD-000003", "CD-000004"}


def test_the_description_is_carried_through_for_the_results_table():
    """The scan table shows it — a stem alone is hard to sanity-check."""
    rows, _ = publish_bom.load_publish_rows(REAL_BOM)
    by_stem = {r.stem: r for r in rows}
    assert by_stem["CD-001613"].description == "bmw kft 90 vacuum insert assembly"


def test_duplicate_filenames_collapse_to_one_stem(tmp_path):
    path = _write_bom(tmp_path, [
        {"Item": "1", "Filename": "CD-000001.ipt", "BOM Structure": "Normal",
         "QTY": "1", "Description": "a"},
        {"Item": "2.1", "Filename": "CD-000001.ipt", "BOM Structure": "Normal",
         "QTY": "4", "Description": "a again"},
    ])
    rows, _ = publish_bom.load_publish_rows(path)
    assert [r.stem for r in rows] == ["CD-000001"]


def test_a_bom_without_a_filename_column_returns_an_error(tmp_path):
    path = _write_bom(tmp_path, [
        {"Item": "1", "Part Number": "SF-001580", "BOM Structure": "Normal",
         "QTY": "1", "Description": "no filename here"},
    ])
    rows, error = publish_bom.load_publish_rows(path)
    assert rows == []
    assert error is not None
    assert "Filename" in error


def test_a_bom_without_a_structure_column_falls_back_to_source(tmp_path):
    """A Vault-canonical BOM already carries Source as Make/Buy."""
    path = _write_bom(tmp_path, [
        {"Filename": "CD-000001.ipt", "Source": "Make",
         "Item Qty": "1", "Number": "CD-000001"},
        {"Filename": "CD-000002.ipt", "Source": "Buy",
         "Item Qty": "1", "Number": "CD-000002"},
    ])
    rows, error = publish_bom.load_publish_rows(path)
    assert error is None
    assert {r.stem for r in rows} == {"CD-000001"}


def test_an_unsupported_extension_returns_an_error_not_an_exception(tmp_path):
    path = tmp_path / "bom.docx"
    path.write_text("not a bom", encoding="utf-8")
    rows, error = publish_bom.load_publish_rows(str(path))
    assert rows == []
    assert error is not None


# --------------------------------------------------------------------------- top assembly

@pytest.mark.parametrize("filename,expected", [
    ("CD-001608 BOM.xlsx", "CD-001608"),
    ("CD-001608 MFG BOM.xlsx", "CD-001608"),
    ("CD-001608.xlsx", "CD-001608"),
    ("cd-001608 bom.xlsx", "cd-001608"),
    ("SF-001922 BOM.csv", "SF-001922"),
    ("bom export.xlsx", ""),
    ("", ""),
])
def test_top_assembly_stem_is_parsed_from_the_file_name(filename, expected):
    assert publish_bom.top_assembly_stem(filename) == expected


def test_top_assembly_stem_ignores_the_directory():
    path = r"C:\Vault Workspace\DESIGNS\PRODUCTION EQUIPMENT\CD-001608 BOM.xlsx"
    assert publish_bom.top_assembly_stem(path) == "CD-001608"


# --------------------------------------------------------------------------- fake api

class FakeAPI:
    """Records calls and replays canned responses.

    ``search_map`` maps a query stem to the ``results`` list the Vault search
    should return. ``search_errors`` holds stems whose search should fail.
    """

    def __init__(self, search_map=None, search_errors=(), submit_errors=(),
                 queue_enabled=True, search_raises=(), queue_raises=False):
        self.search_map = search_map or {}
        self.search_errors = set(search_errors)
        self.submit_errors = set(submit_errors)
        self.queue_enabled = queue_enabled
        self.search_raises = set(search_raises)
        self.queue_raises = queue_raises
        self.submitted = []
        self._next_job_id = 1000

    async def search_files(self, vault_id, query, **kwargs):
        if query in self.search_raises:
            raise RuntimeError(f"search blew up for {query}")
        if query in self.search_errors:
            return {"error": True, "status_code": 500,
                    "data": {"message": "boom"}}
        return {"error": False, "status_code": 200,
                "data": {"results": self.search_map.get(query, [])}}

    async def get_job_queue_enabled(self, vault_id):
        if self.queue_raises:
            raise RuntimeError("queue check blew up")
        return {"error": False, "status_code": 200,
                "data": {"value": self.queue_enabled}}

    async def submit_job(self, vault_id, job_type, *, params=None,
                         description=None, priority=None):
        self.submitted.append({
            "job_type": job_type, "params": dict(params or {}),
            "description": description, "priority": priority,
        })
        fvid = (params or {}).get("FileVersionId", "")
        if fvid in self.submit_errors:
            return {"error": True, "status_code": 400,
                    "data": {"message": "Job param error"}}
        self._next_job_id += 1
        return {"error": False, "status_code": 200,
                "data": {"id": str(self._next_job_id)}}


def _hit(name, fvid):
    return {"entityType": "FileVersion", "name": name, "id": fvid}


# --------------------------------------------------------------------------- scan

@pytest.mark.asyncio
async def test_scan_classifies_model_and_drawing():
    api = FakeAPI({"CD-001578": [
        _hit("CD-001578.ipt", "111"),
        _hit("CD-001578.idw", "222"),
    ]})
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem="CD-001578")])

    assert len(rows) == 1
    assert rows[0].model_name == "CD-001578.ipt"
    assert rows[0].model_version_id == "111"
    assert rows[0].drawing_name == "CD-001578.idw"
    assert rows[0].drawing_version_id == "222"
    assert rows[0].status == publish_bom.STATUS_BOTH


@pytest.mark.asyncio
async def test_scan_reports_a_make_part_with_no_drawing():
    """The gap this tool exists to surface."""
    api = FakeAPI({"CD-001601": [_hit("CD-001601.iam", "333")]})
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem="CD-001601")])

    assert rows[0].status == publish_bom.STATUS_MODEL_ONLY
    assert rows[0].drawing_version_id == ""
    assert rows[0].job_count == 1


@pytest.mark.asyncio
async def test_scan_reports_a_stem_that_matches_nothing():
    api = FakeAPI({})
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem="CD-001644")])

    assert rows[0].status == publish_bom.STATUS_MISSING
    assert rows[0].job_count == 0


@pytest.mark.asyncio
async def test_scan_requires_an_exact_basename_match():
    """A substring match would pull in every assembly that uses the part."""
    api = FakeAPI({"CD-001578": [
        _hit("CD-001578-BRACKET.ipt", "999"),
        _hit("CD-001578 REV A.idw", "998"),
        _hit("CD-001578.ipt", "111"),
    ]})
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem="CD-001578")])

    assert rows[0].model_version_id == "111"
    assert rows[0].drawing_version_id == ""


@pytest.mark.asyncio
async def test_scan_ignores_non_file_version_hits():
    api = FakeAPI({"CD-001578": [
        {"entityType": "Item", "name": "CD-001578.ipt", "id": "777"},
        _hit("CD-001578.ipt", "111"),
    ]})
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem="CD-001578")])

    assert rows[0].model_version_id == "111"


@pytest.mark.asyncio
async def test_a_search_failure_degrades_only_its_own_row():
    api = FakeAPI(
        {"CD-000002": [_hit("CD-000002.ipt", "111")]},
        search_errors=["CD-000001"],
    )
    rows = await publish_bom.scan_rows(api, "1", [
        publish_bom.PublishRow(stem="CD-000001"),
        publish_bom.PublishRow(stem="CD-000002"),
    ])

    assert rows[0].status == publish_bom.STATUS_FAILED
    assert rows[1].status == publish_bom.STATUS_MODEL_ONLY


@pytest.mark.asyncio
async def test_scan_preserves_input_order():
    api = FakeAPI({})
    stems = [f"CD-00{n:04d}" for n in range(20)]
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem=s) for s in stems])

    assert [r.stem for r in rows] == stems


@pytest.mark.asyncio
async def test_a_raised_exception_degrades_only_its_own_row():
    """asyncio.gather defaults to return_exceptions=False - one raise must not
    discard every already-resolved row in a 200-row scan."""
    api = FakeAPI(
        {"CD-000002": [_hit("CD-000002.ipt", "111")],
         "CD-000003": [_hit("CD-000003.ipt", "222")]},
        search_raises=["CD-000001"],
    )
    rows = await publish_bom.scan_rows(api, "1", [
        publish_bom.PublishRow(stem="CD-000001"),
        publish_bom.PublishRow(stem="CD-000002"),
        publish_bom.PublishRow(stem="CD-000003"),
    ])

    assert rows[0].status == publish_bom.STATUS_FAILED
    assert rows[1].status == publish_bom.STATUS_MODEL_ONLY
    assert rows[1].model_version_id == "111"
    assert rows[2].status == publish_bom.STATUS_MODEL_ONLY
    assert rows[2].model_version_id == "222"


# --------------------------------------------------------------------------- submit

def _scanned(stem="CD-001578", model="CD-001578.ipt", model_id="111",
             drawing="CD-001578.idw", drawing_id="222", is_top=False):
    row = publish_bom.ScanRow(stem=stem, is_top=is_top,
                              model_name=model, model_version_id=model_id,
                              drawing_name=drawing, drawing_version_id=drawing_id)
    row.status = publish_bom._status_for(row)
    return row


@pytest.mark.asyncio
async def test_submit_uses_the_right_job_type_per_extension():
    api = FakeAPI()
    await publish_bom.submit_jobs(api, "1", [
        _scanned(model="CD-001578.ipt", drawing="CD-001578.idw"),
        _scanned(stem="CD-001613", model="CD-001613.iam", model_id="333",
                 drawing="CD-001613.dwg", drawing_id="444"),
    ])

    types = {j["job_type"] for j in api.submitted}
    assert types == {
        "Autodesk.Vault.PDF.Create.idw",
        "Autodesk.Vault.STEP.Create.ipt",
        "Autodesk.Vault.PDF.Create.dwg",
        "Autodesk.Vault.STEP.Create.iam",
    }


@pytest.mark.asyncio
async def test_step_and_pdf_params_match_the_shapes_the_job_processor_accepts():
    """PascalCase, and STEP reads UpdatePdfOption despite the name.

    The job processor's constructor rejects the job outright on wrong casing,
    and the REST response echoes params back camelCased, which makes this easy
    to get wrong twice.
    """
    api = FakeAPI()
    await publish_bom.submit_jobs(api, "1", [_scanned()])

    step = next(j for j in api.submitted if "STEP" in j["job_type"])
    pdf = next(j for j in api.submitted if "PDF" in j["job_type"])

    assert step["params"] == {
        "FileVersionId": "111",
        "UpdatePdfOption": "False",
        "UpdateViewOption": "False",
    }
    assert pdf["params"] == {
        "FileVersionId": "222",
        "UpdateViewOption": "False",
    }


@pytest.mark.asyncio
async def test_every_job_carries_a_non_empty_description():
    """Vault error 155 ("Illegal null parameter") otherwise."""
    api = FakeAPI()
    await publish_bom.submit_jobs(api, "1", [_scanned()])

    assert api.submitted
    assert all(j["description"] for j in api.submitted)


@pytest.mark.asyncio
async def test_a_row_with_no_drawing_queues_only_the_step_job():
    api = FakeAPI()
    result = await publish_bom.submit_jobs(
        api, "1", [_scanned(drawing="", drawing_id="")])

    assert len(api.submitted) == 1
    assert "STEP" in api.submitted[0]["job_type"]
    assert result["submitted"] == 1


@pytest.mark.asyncio
async def test_one_failing_submit_does_not_stop_the_rest():
    api = FakeAPI(submit_errors=["111"])
    result = await publish_bom.submit_jobs(api, "1", [
        _scanned(),
        _scanned(stem="CD-001613", model="CD-001613.iam", model_id="333",
                 drawing="CD-001613.idw", drawing_id="444"),
    ])

    assert len(api.submitted) == 4
    assert result["failed"] == 1
    assert result["submitted"] == 3


@pytest.mark.asyncio
async def test_submit_reports_job_ids():
    api = FakeAPI()
    result = await publish_bom.submit_jobs(api, "1", [_scanned()])

    assert len(result["jobs"]) == 2
    assert all(j["job_id"] for j in result["jobs"])


@pytest.mark.asyncio
async def test_rows_with_nothing_found_queue_nothing():
    api = FakeAPI()
    result = await publish_bom.submit_jobs(
        api, "1", [_scanned(model="", model_id="", drawing="", drawing_id="")])

    assert api.submitted == []
    assert result["submitted"] == 0


@pytest.mark.asyncio
async def test_a_disabled_queue_warns_but_still_submits():
    messages = []
    api = FakeAPI(queue_enabled=False)
    result = await publish_bom.submit_jobs(
        api, "1", [_scanned()], on_progress=messages.append)

    assert any("disabled" in m.lower() for m in messages)
    assert result["submitted"] == 2


@pytest.mark.asyncio
async def test_a_raised_queue_check_does_not_block_submission():
    """The queue check is advisory - an exception from it must never block
    the jobs it was only meant to warn about."""
    api = FakeAPI(queue_raises=True)
    result = await publish_bom.submit_jobs(api, "1", [_scanned()])

    assert result["submitted"] == 2
    assert result["failed"] == 0


# --------------------------------------------------------------------------- scan_bom

@pytest.mark.asyncio
async def test_scan_bom_appends_the_top_assembly_row():
    api = FakeAPI({
        "CD-001608": [_hit("CD-001608.iam", "900"), _hit("CD-001608.idw", "901")],
    })
    rows, error = await publish_bom.scan_bom(
        api, "1", REAL_BOM, top_assembly="CD-001608")

    assert error is None
    assert len(rows) == 10          # 9 Make rows + the top assembly
    top = [r for r in rows if r.is_top]
    assert len(top) == 1
    assert top[0].stem == "CD-001608"
    assert top[0].job_count == 2    # the top assembly gets both


@pytest.mark.asyncio
async def test_scan_bom_with_a_blank_top_assembly_scans_only_the_bom():
    api = FakeAPI({})
    rows, error = await publish_bom.scan_bom(api, "1", REAL_BOM, top_assembly="")

    assert error is None
    assert len(rows) == 9
    assert not any(r.is_top for r in rows)


@pytest.mark.asyncio
async def test_scan_bom_does_not_duplicate_a_top_assembly_already_in_the_bom():
    api = FakeAPI({})
    rows, _ = await publish_bom.scan_bom(
        api, "1", REAL_BOM, top_assembly="CD-001613")

    assert [r.stem for r in rows].count("CD-001613") == 1


@pytest.mark.asyncio
async def test_scan_bom_surfaces_a_parse_error_without_calling_vault(tmp_path):
    path = _write_bom(tmp_path, [
        {"Item": "1", "Part Number": "SF-001580", "BOM Structure": "Normal",
         "QTY": "1", "Description": "no filename"},
    ])
    api = FakeAPI({})
    rows, error = await publish_bom.scan_bom(api, "1", path, top_assembly="")

    assert rows == []
    assert error is not None
    assert api.submitted == []


def test_summarize_counts_models_drawings_jobs_and_gaps():
    rows = [
        _scanned(),                                              # both
        _scanned(stem="CD-2", drawing="", drawing_id=""),        # no drawing
        _scanned(stem="CD-3", model="", model_id="",
                 drawing="", drawing_id=""),                     # nothing
    ]
    summary = publish_bom.summarize(rows)

    assert summary["rows"] == 3
    assert summary["models"] == 2
    assert summary["drawings"] == 1
    assert summary["jobs"] == 3
    assert summary["missing_drawing"] == 1
    assert summary["not_found"] == 1
