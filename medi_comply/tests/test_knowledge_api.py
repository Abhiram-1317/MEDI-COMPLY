import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from medi_comply.api.routes.knowledge import router, _store, KnowledgeUpdateRequest

app = FastAPI()
app.include_router(router)
client = TestClient(app)


# ---------------------------------------------------------------------------
# ICD-10 search
# ---------------------------------------------------------------------------
class TestICD10Search:
    def test_search_by_code(self):
        resp = client.get("/api/v1/knowledge/icd10/search", params={"q": "E11.22"})
        assert resp.status_code == 200
        body = resp.json()
        assert any(item["code"] == "E11.22" for item in body["results"])

    def test_search_by_prefix(self):
        resp = client.get("/api/v1/knowledge/icd10/search", params={"q": "E11"})
        assert resp.status_code == 200
        body = resp.json()
        codes = {item["code"] for item in body["results"]}
        assert any(code.startswith("E11") for code in codes)
        assert len(body["results"]) > 1

    def test_search_by_description(self):
        resp = client.get("/api/v1/knowledge/icd10/search", params={"q": "diabetes"})
        assert resp.status_code == 200
        body = resp.json()
        assert any("diabetes" in item["description"].lower() for item in body["results"])

    def test_search_by_symptom(self):
        resp = client.get("/api/v1/knowledge/icd10/search", params={"q": "chest pain"})
        assert resp.status_code == 200
        body = resp.json()
        assert any(item["code"] == "R07.9" for item in body["results"])

    def test_search_billable_only(self):
        resp = client.get(
            "/api/v1/knowledge/icd10/search",
            params={"q": "E11", "billable_only": True, "max_results": 50},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(item["is_billable"] for item in body["results"])

    def test_search_by_chapter(self):
        resp = client.get(
            "/api/v1/knowledge/icd10/search",
            params={"q": "hypertension", "chapter": "9", "max_results": 50},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"]
        assert all(item.get("chapter") == "9" for item in body["results"])

    def test_search_relevance_order(self):
        resp = client.get("/api/v1/knowledge/icd10/search", params={"q": "E11.22", "max_results": 5})
        body = resp.json()
        assert body["results"]
        assert body["results"][0]["code"] == "E11.22"

    def test_search_max_results(self):
        resp = client.get(
            "/api/v1/knowledge/icd10/search",
            params={"q": "pain", "max_results": 2},
        )
        body = resp.json()
        assert len(body["results"]) <= 2

    def test_search_no_results(self):
        resp = client.get(
            "/api/v1/knowledge/icd10/search",
            params={"q": "unknownterm", "max_results": 5},
        )
        body = resp.json()
        assert body["result_count"] == 0
        assert body["suggestions"]

    def test_search_case_insensitive(self):
        lower = client.get("/api/v1/knowledge/icd10/search", params={"q": "diabetes"}).json()
        upper = client.get("/api/v1/knowledge/icd10/search", params={"q": "DIABETES"}).json()
        assert lower["result_count"] == upper["result_count"]


# ---------------------------------------------------------------------------
# CPT search
# ---------------------------------------------------------------------------
class TestCPTSearch:
    def test_search_by_code(self):
        resp = client.get("/api/v1/knowledge/cpt/search", params={"q": "99214"})
        assert resp.status_code == 200
        body = resp.json()
        assert any(item["code"] == "99214" for item in body["results"])

    def test_search_by_description(self):
        resp = client.get("/api/v1/knowledge/cpt/search", params={"q": "office visit"})
        body = resp.json()
        assert any("office" in item["description"].lower() for item in body["results"])

    def test_search_by_procedure(self):
        resp = client.get("/api/v1/knowledge/cpt/search", params={"q": "MRI"})
        body = resp.json()
        assert any("mri" in item["description"].lower() for item in body["results"])

    def test_search_by_category(self):
        resp = client.get(
            "/api/v1/knowledge/cpt/search",
            params={"q": "MRI", "category": "Radiology"},
        )
        body = resp.json()
        assert body["results"]
        assert all(item.get("category") == "Radiology" for item in body["results"])

    def test_search_includes_rvu(self):
        body = client.get("/api/v1/knowledge/cpt/search", params={"q": "99214"}).json()
        target = next(item for item in body["results"] if item["code"] == "99214")
        assert target.get("rvu_total") is not None

    def test_search_includes_modifiers(self):
        body = client.get("/api/v1/knowledge/cpt/search", params={"q": "99213"}).json()
        target = next(item for item in body["results"] if item["code"] == "99213")
        assert isinstance(target.get("common_modifiers"), list)

    def test_search_max_results(self):
        body = client.get("/api/v1/knowledge/cpt/search", params={"q": "x", "max_results": 3}).json()
        assert len(body["results"]) <= 3

    def test_search_no_results(self):
        body = client.get("/api/v1/knowledge/cpt/search", params={"q": "unknown-proc"}).json()
        assert body["result_count"] == 0
        assert body["suggestions"]


# ---------------------------------------------------------------------------
# NCCI checks
# ---------------------------------------------------------------------------
class TestNCCICheck:
    def test_bundled_pair(self):
        body = client.get(
            "/api/v1/knowledge/ncci/check", params={"cpt1": "80053", "cpt2": "82565"}
        ).json()
        assert body["has_any_conflict"] is True

    def test_mutually_exclusive(self):
        body = client.get(
            "/api/v1/knowledge/ncci/check", params={"cpt1": "99223", "cpt2": "99232"}
        ).json()
        assert any(edit.get("edit_type") == "mutually_exclusive" for edit in body["edits_found"])

    def test_no_conflict(self):
        body = client.get(
            "/api/v1/knowledge/ncci/check", params={"cpt1": "99214", "cpt2": "71046"}
        ).json()
        assert body["has_any_conflict"] is False

    def test_modifier_allowed(self):
        body = client.get(
            "/api/v1/knowledge/ncci/check", params={"cpt1": "99214", "cpt2": "93000"}
        ).json()
        assert any(edit.get("allowed_with_modifier") for edit in body["edits_found"])

    def test_modifier_not_allowed(self):
        body = client.get(
            "/api/v1/knowledge/ncci/check", params={"cpt1": "93000", "cpt2": "93005"}
        ).json()
        assert any(edit.get("modifier_indicator") == "0" for edit in body["edits_found"])

    def test_reverse_check(self):
        body = client.get(
            "/api/v1/knowledge/ncci/check", params={"cpt1": "82565", "cpt2": "80053"}
        ).json()
        assert body["has_any_conflict"] is True

    def test_has_recommendation(self):
        body = client.get(
            "/api/v1/knowledge/ncci/check", params={"cpt1": "80053", "cpt2": "82565"}
        ).json()
        assert any(edit.get("recommendation") for edit in body["edits_found"])

    def test_summary_present(self):
        body = client.get(
            "/api/v1/knowledge/ncci/check", params={"cpt1": "99214", "cpt2": "71046"}
        ).json()
        assert isinstance(body.get("summary"), str)


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
class TestKBVersion:
    def test_version_returns_data(self):
        resp = client.get("/api/v1/knowledge/version")
        assert resp.status_code == 200
        assert resp.json().get("version")

    def test_version_has_code_counts(self):
        body = client.get("/api/v1/knowledge/version").json()
        assert body.get("icd10_code_count", 0) > 0
        assert body.get("cpt_code_count", 0) > 0

    def test_version_has_ncci_count(self):
        body = client.get("/api/v1/knowledge/version").json()
        assert body.get("ncci_edit_pairs", 0) > 0

    def test_version_has_schedule(self):
        body = client.get("/api/v1/knowledge/version").json()
        schedule = body.get("update_schedule", {})
        assert "ICD-10" in schedule and "CPT" in schedule and "NCCI" in schedule

    def test_version_has_integrity_hash(self):
        body = client.get("/api/v1/knowledge/version").json()
        assert body.get("integrity_hash")


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------
class TestKBUpdate:
    def test_update_dry_run(self):
        resp = client.post(
            "/api/v1/knowledge/update",
            json={"component": "icd10", "dry_run": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "success"
        assert body.get("dry_run") is True

    def test_update_dry_run_shows_changes(self):
        body = client.post(
            "/api/v1/knowledge/update",
            json={"component": "cpt", "dry_run": True},
        ).json()
        assert body.get("changes_summary")

    def test_update_real(self):
        body = client.post(
            "/api/v1/knowledge/update",
            json={"component": "ncci", "dry_run": False},
        ).json()
        assert body.get("status") in {"success", "failed"}

    def test_update_has_regression_test(self):
        body = client.post(
            "/api/v1/knowledge/update",
            json={"component": "ncci", "dry_run": False},
        ).json()
        assert body.get("regression_test_result") is not None

    def test_update_invalid_component(self):
        resp = client.post(
            "/api/v1/knowledge/update",
            json={"component": "unknown", "dry_run": True},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# MUE checks (store-level)
# ---------------------------------------------------------------------------
class TestMUECheck:
    def test_mue_within_limit(self):
        result = _store.check_mue("99213", units=1)
        assert result.exceeds_mue is False

    def test_mue_at_limit(self):
        result = _store.check_mue("93000", units=3)
        assert result.exceeds_mue is False

    def test_mue_exceeds(self):
        result = _store.check_mue("93000", units=4)
        assert result.exceeds_mue is True


# ---------------------------------------------------------------------------
# Code hierarchy (store-level)
# ---------------------------------------------------------------------------
class TestCodeHierarchy:
    def test_hierarchy_has_parent(self):
        hierarchy = _store.get_code_hierarchy("E11.22")
        assert hierarchy.get("parent") in {"E11.2", "E11"}

    def test_hierarchy_has_children(self):
        hierarchy = _store.get_code_hierarchy("N18.3")
        assert set(hierarchy.get("children", [])) >= {"N18.31", "N18.32"}

    def test_category_has_children(self):
        hierarchy = _store.get_code_hierarchy("E11")
        assert hierarchy.get("children")


if __name__ == "__main__":
    pytest.main([__file__])
