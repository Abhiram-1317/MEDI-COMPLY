"""Streamlit demo app for the MEDI-COMPLY system."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Iterable

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from medi_comply.demo_scenarios import DEMO_SCENARIOS
from medi_comply.system import MediComplySystem, ProcessingError, SystemInitializationError


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def safe_get(obj: Any, path: str | Iterable[str] | None = None, default: Any = "N/A") -> Any:
    """Safely traverse nested attributes/dicts with graceful fallbacks."""
    if obj is None:
        return default
    if path is None:
        return obj

    parts: list[str]
    if isinstance(path, str):
        parts = path.split(".") if path else []
    else:
        parts = list(path)

    value: Any = obj
    for part in parts:
        if value is None:
            return default
        try:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = getattr(value, part)
        except AttributeError:
            return default
    return default if value is None else value


def safe_list(value: Any) -> list[Any]:
    """Convert arbitrary iterables to a list while tolerating None."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        return list(value)
    except TypeError:
        return [value]


def format_percent(value: Any, fallback: str = "N/A") -> str:
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return fallback


def format_float(value: Any, digits: int = 2, fallback: str = "N/A") -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return fallback


def run_async(awaitable: Awaitable[Any]) -> Any:
    """Run an async coroutine on a short-lived event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(awaitable)
    finally:
        loop.close()


@st.cache_resource(show_spinner=False)
def load_system() -> MediComplySystem:
    system = MediComplySystem()
    run_async(system.initialize())
    return system


# ---------------------------------------------------------------------------
# Streamlit layout helpers
# ---------------------------------------------------------------------------


def render_health_panel(system: MediComplySystem | None) -> dict[str, Any]:
    """Render the system health check inside the sidebar."""
    health_snapshot: dict[str, Any] = {"status": "unavailable"}
    if not system:
        st.error("System initialization failed; processing disabled.")
        return health_snapshot

    try:
        health_snapshot = run_async(system.health_check())
    except Exception as exc:  # pragma: no cover - UI only
        st.error(f"Health check error: {exc}")
        return health_snapshot

    is_healthy = bool(health_snapshot.get("is_healthy"))
    icon = "🟢" if is_healthy else "🟡"
    overall_label = "Healthy" if is_healthy else "Degraded"
    st.metric("Overall", f"{icon} {overall_label}")

    checks = health_snapshot.get("checks", {})
    for name, detail in checks.items():
        status = detail.get("status", "N/A")
        st.caption(f"• {name.replace('_', ' ').title()}: {status}")
    return health_snapshot


def render_result(
    result: Any,
    elapsed: float,
    *,
    show_reasoning: bool,
    show_evidence: bool,
    show_compliance: bool,
    show_audit: bool,
) -> None:
    trace_value = safe_get(result, "trace_id", "")
    trace_label = f"`{str(trace_value)[:12]}...`" if trace_value else ""
    status = safe_get(result, "status", "UNKNOWN")

    if status == "SUCCESS":
        st.success(f"✅ Coding Complete | {elapsed:.1f}s | Trace: {trace_label}")
    elif status == "ESCALATED":
        st.warning(f"⚠️ Escalated to Human Review | {elapsed:.1f}s | Trace: {trace_label}")
    elif status == "BLOCKED":
        st.error(f"🚫 Blocked — Security Issue | Trace: {trace_label}")
    else:
        st.error(f"❌ Error | {status} | Trace: {trace_label}")

    st.markdown("---")
    metric_cols = st.columns(5)
    coding_result = safe_get(result, "coding_result", None)
    compliance_report = safe_get(result, "compliance_report", None)
    risk_assessment = safe_get(result, "risk_assessment", None)

    total_codes = safe_get(coding_result, "total_codes_assigned", 0) or 0
    confidence = safe_get(coding_result, "overall_confidence", 0.0) or 0.0
    risk_score = safe_get(risk_assessment, "overall_score", 0.0) or 0.0
    risk_level = safe_get(risk_assessment, "risk_level", "N/A") or "N/A"
    comp_passed = safe_get(compliance_report, "checks_passed", 0) or 0
    comp_total = safe_get(compliance_report, "total_checks_run", 0) or 0

    metric_cols[0].metric("Codes Assigned", total_codes)
    metric_cols[1].metric("Confidence", format_percent(confidence))
    metric_cols[2].metric("Risk Score", f"{format_float(risk_score)} ({risk_level})")
    metric_cols[3].metric("Compliance", f"{comp_passed}/{comp_total}")
    metric_cols[4].metric("Processing", f"{elapsed:.1f}s")

    st.markdown("---")
    st.markdown("### 🏷️ Assigned Diagnosis Codes")
    diagnosis_codes = safe_list(safe_get(coding_result, "diagnosis_codes", []))
    if diagnosis_codes:
        for cd in diagnosis_codes:
            position = safe_get(cd, "sequence_position", "SECONDARY")
            badge = {
                "PRIMARY": "🔴 PRIMARY",
                "SECONDARY": "🔵 SECONDARY",
                "ADDITIONAL": "🟢 ADDITIONAL",
            }.get(position, "🔵 SECONDARY")

            code_value = safe_get(cd, "code", "N/A")
            description = safe_get(cd, "description", "Not available")
            confidence_score = safe_get(cd, "confidence_score", 0.0)
            header = (
                f"{badge} | **{code_value}** — {description} | Confidence: {format_percent(confidence_score)}"
            )
            with st.expander(header, expanded=(position == "PRIMARY")):
                col_a, col_b = st.columns([3, 1])
                if show_reasoning:
                    reasoning_chain = safe_list(safe_get(cd, "reasoning_chain", []))
                    if reasoning_chain:
                        with col_a:
                            st.markdown("**🧠 Reasoning Chain:**")
                            for step in reasoning_chain:
                                icon = "📋" if safe_get(step, "guideline_ref", None) else "🔍"
                                st.markdown(
                                    f"{icon} **Step {safe_get(step, 'step_number', '?')}:** "
                                    f"{safe_get(step, 'detail', 'Not available')}"
                                )
                                if safe_get(step, "guideline_ref", None):
                                    st.caption(f"📖 Guideline: {safe_get(step, 'guideline_ref', 'N/A')}")
                if show_evidence:
                    evidence_links = safe_list(safe_get(cd, "clinical_evidence", []))
                    if evidence_links:
                        with col_a:
                            st.markdown("**📍 Clinical Evidence:**")
                            for ev in evidence_links:
                                snippet = safe_get(ev, "source_text", "Evidence reference")
                                section = safe_get(ev, "section", "Unknown section")
                                page = safe_get(ev, "page", "?")
                                line = safe_get(ev, "line", "?")
                                st.info(f'"{snippet}" — *{section}, page {page}, line {line}*')
                with col_b:
                    st.metric("Confidence", format_percent(confidence_score))
                    guidelines = safe_list(safe_get(cd, "guidelines_cited", []))
                    if guidelines:
                        st.markdown("**Guidelines:**")
                        for guideline in guidelines:
                            st.caption(f"📖 {guideline}")
                    if safe_get(cd, "requires_human_review", False):
                        st.warning("⚠️ Needs Review")
                    combo_note = safe_get(cd, "combination_code_note", None)
                    if combo_note not in (None, "", "N/A"):
                        st.info(f"🔗 {combo_note}")

    procedure_codes = safe_list(safe_get(coding_result, "procedure_codes", []))
    if procedure_codes:
        st.markdown("### 🛠️ Procedure Codes")
        for proc in procedure_codes:
            header = f"🛠️ **{safe_get(proc, 'code', 'N/A')}** — {safe_get(proc, 'description', 'Not available')}"
            with st.expander(header):
                st.metric("Confidence", format_percent(safe_get(proc, "confidence_score", 0.0)))
                if show_reasoning:
                    reasoning_chain = safe_list(safe_get(proc, "reasoning_chain", []))
                    if reasoning_chain:
                        st.markdown("**🧠 Reasoning Chain:**")
                        for step in reasoning_chain:
                            st.markdown(
                                f"🔍 **Step {safe_get(step, 'step_number', '?')}:** "
                                f"{safe_get(step, 'detail', 'Not available')}"
                            )

    coding_summary = safe_get(coding_result, "coding_summary", "Not available")
    if coding_summary not in (None, "", "Not available"):
        st.info(f"📋 Summary: {coding_summary}")

    if show_compliance and compliance_report:
        st.markdown("---")
        st.markdown("### 🛡️ Compliance Guardrails")
        decision = safe_get(compliance_report, "overall_decision", "Not available")
        if decision == "PASS":
            st.markdown('<span class="pass-badge">ALL CHECKS PASSED ✅</span>', unsafe_allow_html=True)
        else:
            st.markdown(
                f'<span class="fail-badge">{decision} ❌</span>',
                unsafe_allow_html=True,
            )

        layer3_pass = all(
            safe_get(r, "passed", True) for r in safe_list(safe_get(compliance_report, "layer3_results", []))
        )
        layer4_pass = all(
            safe_get(r, "passed", True) for r in safe_list(safe_get(compliance_report, "layer4_results", []))
        )
        layer5_pass = all(
            safe_get(r, "passed", True) for r in safe_list(safe_get(compliance_report, "layer5_results", []))
        )
        comp_cols = st.columns(3)
        comp_cols[0].metric("Layer 3 (Structural)", "✅" if layer3_pass else "❌")
        comp_cols[1].metric("Layer 4 (Semantic)", "✅" if layer4_pass else "❌")
        comp_cols[2].metric("Layer 5 (Output)", "✅" if layer5_pass else "❌")

    audit_summary = safe_get(result, "audit_report_summary", "Not available")
    if show_audit and audit_summary not in ("Not available", "N/A", ""):
        st.markdown("---")
        st.markdown("### 📜 Audit Trail")
        st.code(audit_summary)

    warnings = safe_list(safe_get(result, "warnings", []))
    if warnings:
        st.markdown("---")
        st.markdown("### ⚠️ Warnings")
        for warning in warnings:
            st.warning(warning)

    errors = safe_list(safe_get(result, "errors", []))
    if errors:
        st.markdown("### ❌ Errors")
        for err in errors:
            message = safe_get(err, "error_message", None)
            st.error(message if message not in (None, "N/A") else str(err))

    with st.expander("📦 Raw JSON Output"):
        payload: Any = {"detail": "Not available"}
        try:
            if hasattr(result, "model_dump"):
                payload = result.model_dump()
            elif hasattr(result, "__dict__"):
                payload = dict(result.__dict__)
        except Exception as serialization_error:  # pragma: no cover - UI only
            payload = {"error": f"Serialization failed: {serialization_error}"}
        st.json(payload)


def render_footer() -> None:
    st.markdown("---")
    st.markdown(
        "<center><small>🏥 MEDI-COMPLY v1.0.0 | Gen AI Hackathon | Domain-Specialized AI Agent with Compliance Guardrails</small></center>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main Streamlit workflow
# ---------------------------------------------------------------------------


st.set_page_config(page_title="MEDI-COMPLY Demo", page_icon="🏥", layout="wide")
st.markdown(
    """
    <style>
        .pass-badge {background:#0f996d; color:white; padding:6px 10px; border-radius:6px; font-weight:600;}
        .fail-badge {background:#a82a2a; color:white; padding:6px 10px; border-radius:6px; font-weight:600;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("MEDI-COMPLY Clinical Coding Workbench")
st.caption("Multi-agent clinical coding with compliance guardrails and full audit trail.")

try:
    system_instance = load_system()
    system_error: str | None = None
except (SystemInitializationError, RuntimeError) as exc:  # pragma: no cover - UI only
    system_instance = None
    system_error = str(exc)

with st.sidebar:
    st.header("Scenario Controls")
    scenario_names = list(DEMO_SCENARIOS.keys())
    default_index = scenario_names.index("🫀 Cardiac NSTEMI (Complex)") if "🫀 Cardiac NSTEMI (Complex)" in scenario_names else 0
    scenario_name = st.selectbox("Choose demo scenario", scenario_names, index=default_index)
    scenario = DEMO_SCENARIOS.get(scenario_name) or next(iter(DEMO_SCENARIOS.values()))
    st.caption(scenario.get("description", ""))

    patient_defaults = scenario.get("patient", {"age": 50, "gender": "MALE", "encounter_type": "INPATIENT"})
    age = int(
        st.number_input(
            "Patient age",
            min_value=0,
            max_value=120,
            value=int(patient_defaults.get("age", 50)),
        )
    )
    gender_options = ["FEMALE", "MALE", "OTHER"]
    gender_default = str(patient_defaults.get("gender", "FEMALE")).upper()
    gender_index = gender_options.index(gender_default) if gender_default in gender_options else 0
    gender = st.selectbox("Gender", gender_options, index=gender_index)

    encounter_options = ["INPATIENT", "OUTPATIENT", "EMERGENCY"]
    encounter_default = str(patient_defaults.get("encounter_type", "INPATIENT")).upper()
    encounter_index = encounter_options.index(encounter_default) if encounter_default in encounter_options else 0
    encounter = st.selectbox("Encounter type", options=encounter_options, index=encounter_index)

    st.markdown("---")
    st.subheader("Display options")
    show_reasoning = st.checkbox("Show reasoning chain", value=True)
    show_evidence = st.checkbox("Show evidence links", value=True)
    show_compliance = st.checkbox("Show compliance layers", value=True)
    show_audit = st.checkbox("Show audit trail", value=False)

    st.markdown("---")
    st.subheader("System health")
    health_snapshot = render_health_panel(system_instance)
    if system_error:
        st.error(system_error)

note_label = "Enter clinical note" if scenario_name == "✍️ Custom Note" else "Clinical note (editable)"
default_note = scenario.get("note", "")
clinical_note = st.text_area(
    note_label,
    value="" if scenario_name == "✍️ Custom Note" else default_note.strip(),
    height=320,
    placeholder="Paste or enter a clinical document...",
)

col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    process_clicked = st.button("🚀 Process Document", type="primary", use_container_width=True)

if process_clicked:
    if not system_instance:
        st.error("System is not available. Please check initialization logs.")
    elif not clinical_note.strip():
        st.warning("Please enter a clinical note first.")
    else:
        try:
            with st.spinner("🧠 AI Agent Processing..."):
                start_time = time.time()
                result_payload = run_async(
                    system_instance.process(
                        clinical_note=clinical_note,
                        patient_context={"age": age, "gender": gender, "encounter_type": encounter},
                    )
                )
                elapsed = time.time() - start_time

            render_result(
                result_payload,
                elapsed,
                show_reasoning=show_reasoning,
                show_evidence=show_evidence,
                show_compliance=show_compliance,
                show_audit=show_audit,
            )
        except (ProcessingError, Exception) as exc:  # pragma: no cover - UI only
            st.error("🚫 Unable to process or display results. Please try again.")
            with st.expander("View error details"):
                st.write(str(exc))

render_footer()
