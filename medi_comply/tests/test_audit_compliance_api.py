import datetime
from fastapi import FastAPI
from fastapi.testclient import TestClient

from medi_comply.api.routes.audit import audit_router, compliance_router, RiskLevel, WorkflowType

app = FastAPI()
app.include_router(audit_router)
app.include_router(compliance_router)
client = TestClient(app)


def _get_sample_id():
    resp = client.get("/api/v1/audit/", params={"limit": 1})
    body = resp.json()
    results = body.get("results", [])
    return results[0]["audit_id"] if results else None


def _get_all_records(limit: int = 500):
    resp = client.get("/api/v1/audit/", params={"limit": limit})
    resp.raise_for_status()
    return resp.json().get("results", [])


class TestGetAuditRecord:
    def test_get_existing_record(self):
        audit_id = _get_sample_id()
        resp = client.get(f"/api/v1/audit/{audit_id}")
        assert resp.status_code == 200
        assert resp.json().get("audit_id") == audit_id

    def test_get_nonexistent_record(self):
        resp = client.get("/api/v1/audit/AUD-DOES-NOT-EXIST")
        assert resp.status_code == 404

    def test_record_has_audit_id(self):
        audit_id = _get_sample_id()
        resp = client.get(f"/api/v1/audit/{audit_id}")
        assert resp.json().get("audit_id")

    def test_record_has_workflow_type(self):
        audit_id = _get_sample_id()
        resp = client.get(f"/api/v1/audit/{audit_id}")
        assert resp.json().get("workflow_type") in {wf.value for wf in WorkflowType}

    def test_record_has_digital_signature(self):
        audit_id = _get_sample_id()
        resp = client.get(f"/api/v1/audit/{audit_id}")
        assert resp.json().get("digital_signature")


class TestExplainAuditRecord:
    def test_explain_existing(self):
        audit_id = _get_sample_id()
        resp = client.get(f"/api/v1/audit/{audit_id}/explain")
        assert resp.status_code == 200

    def test_explain_nonexistent(self):
        resp = client.get("/api/v1/audit/AUD-NOPE/explain")
        assert resp.status_code == 404

    def test_explain_has_summary(self):
        audit_id = _get_sample_id()
        body = client.get(f"/api/v1/audit/{audit_id}/explain").json()
        assert body.get("summary")

    def test_explain_has_formatted_text(self):
        audit_id = _get_sample_id()
        body = client.get(f"/api/v1/audit/{audit_id}/explain").json()
        assert "DECISION EXPLANATION" in body.get("formatted_explanation", "")

    def test_explain_has_compliance_summary(self):
        audit_id = _get_sample_id()
        body = client.get(f"/api/v1/audit/{audit_id}/explain").json()
        assert body.get("compliance_summary") is not None


class TestSearchAuditRecords:
    def test_search_all(self):
        resp = client.post("/api/v1/audit/search", json={})
        body = resp.json()
        assert resp.status_code == 200
        assert body.get("total_count", 0) >= body.get("returned_count", 0) > 0

    def test_search_by_workflow(self):
        resp = client.post(
            "/api/v1/audit/search",
            json={"workflow_type": WorkflowType.MEDICAL_CODING.value},
        )
        body = resp.json()
        assert body.get("returned_count", 0) > 0
        assert all(r.get("workflow_type") == WorkflowType.MEDICAL_CODING.value for r in body.get("results", []))

    def test_search_by_risk_level(self):
        resp = client.post(
            "/api/v1/audit/search",
            json={"risk_level": RiskLevel.HIGH.value},
        )
        body = resp.json()
        assert body.get("returned_count", 0) > 0
        assert all(r.get("risk_level") == RiskLevel.HIGH.value for r in body.get("results", []))

    def test_search_by_min_risk_score(self):
        resp = client.post(
            "/api/v1/audit/search",
            json={"min_risk_score": 0.2},
        )
        body = resp.json()
        assert all(r.get("overall_risk_score", 0) >= 0.2 for r in body.get("results", []))

    def test_search_with_limit(self):
        resp = client.post(
            "/api/v1/audit/search",
            json={"limit": 5},
        )
        body = resp.json()
        assert len(body.get("results", [])) <= 5

    def test_search_with_offset(self):
        first = client.post(
            "/api/v1/audit/search",
            json={"limit": 2, "offset": 0},
        ).json()
        second = client.post(
            "/api/v1/audit/search",
            json={"limit": 1, "offset": 1},
        ).json()
        if first.get("returned_count", 0) > 1 and second.get("returned_count", 0) == 1:
            assert first["results"][1]["audit_id"] == second["results"][0]["audit_id"]

    def test_search_has_more(self):
        body = client.post(
            "/api/v1/audit/search",
            json={"limit": 1, "offset": 0},
        ).json()
        assert body.get("has_more") is True

    def test_search_total_count(self):
        body = client.post(
            "/api/v1/audit/search",
            json={"limit": 3},
        ).json()
        assert body.get("total_count", 0) >= len(body.get("results", []))


class TestComplianceDashboard:
    def test_dashboard_returns_data(self):
        resp = client.get("/api/v1/compliance/dashboard")
        assert resp.status_code == 200

    def test_dashboard_has_total_decisions(self):
        body = client.get("/api/v1/compliance/dashboard").json()
        assert body.get("total_decisions", 0) >= 0

    def test_dashboard_has_compliance_rate(self):
        body = client.get("/api/v1/compliance/dashboard").json()
        assert 0.0 <= body.get("compliance_rate", 0) <= 100.0

    def test_dashboard_has_risk_distribution(self):
        body = client.get("/api/v1/compliance/dashboard").json()
        dist = body.get("risk_distribution", {})
        for level in [r.value for r in RiskLevel]:
            assert level in dist

    def test_dashboard_has_workflow_breakdown(self):
        body = client.get("/api/v1/compliance/dashboard").json()
        assert isinstance(body.get("workflow_breakdown"), dict)

    def test_dashboard_has_trend(self):
        body = client.get("/api/v1/compliance/dashboard").json()
        assert len(body.get("compliance_trend", [])) > 0

    def test_dashboard_custom_period(self):
        body = client.get("/api/v1/compliance/dashboard", params={"period_days": 7}).json()
        assert body.get("period") == "last_7_days"


class TestComplianceReport:
    def test_report_monthly(self):
        resp = client.get("/api/v1/compliance/report", params={"period": "monthly"})
        assert resp.status_code == 200

    def test_report_weekly(self):
        resp = client.get("/api/v1/compliance/report", params={"period": "weekly"})
        assert resp.status_code == 200

    def test_report_quarterly(self):
        resp = client.get("/api/v1/compliance/report", params={"period": "quarterly"})
        assert resp.status_code == 200

    def test_report_has_summary(self):
        body = client.get("/api/v1/compliance/report").json()
        assert body.get("executive_summary")

    def test_report_has_recommendations(self):
        body = client.get("/api/v1/compliance/report").json()
        assert len(body.get("recommendations", [])) > 0

    def test_report_has_metrics(self):
        body = client.get("/api/v1/compliance/report").json()
        assert "total_transactions" in body and "compliance_rate" in body

    def test_report_has_hash(self):
        body = client.get("/api/v1/compliance/report").json()
        assert body.get("report_hash")


class TestAuditDataIntegrity:
    def test_records_have_timestamps(self):
        records = _get_all_records()
        for rec in records:
            datetime.datetime.fromisoformat(rec["timestamp"].replace("Z", ""))

    def test_records_have_risk_scores(self):
        records = _get_all_records()
        assert all(0.0 <= rec.get("overall_risk_score", 0) <= 1.0 for rec in records)

    def test_risk_level_matches_score(self):
        records = _get_all_records()
        for rec in records:
            score = rec.get("overall_risk_score", 0)
            level = rec.get("risk_level")
            if score < 0.15:
                expected = RiskLevel.LOW.value
            elif score < 0.3:
                expected = RiskLevel.MODERATE.value
            elif score < 0.5:
                expected = RiskLevel.HIGH.value
            else:
                expected = RiskLevel.CRITICAL.value
            assert level == expected
