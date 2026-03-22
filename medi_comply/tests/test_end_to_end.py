"""End-to-end integration tests for MEDI-COMPLY."""

from __future__ import annotations

import asyncio

import pytest

from medi_comply.tests.conftest import SAMPLE_NOTES


@pytest.mark.asyncio
async def test_batch_processing(initialized_system):
    system = initialized_system
    documents = [
        {
            "clinical_note": SAMPLE_NOTES["cardiac_nstemi"],
            "patient_context": {"age": 62, "gender": "male", "encounter_type": "INPATIENT"},
        },
        {
            "clinical_note": SAMPLE_NOTES["pulmonary_copd"],
            "patient_context": {"age": 55, "gender": "female", "encounter_type": "INPATIENT"},
        },
        {
            "clinical_note": SAMPLE_NOTES["simple_dm_htn"],
            "patient_context": {"age": 48, "gender": "female", "encounter_type": "OUTPATIENT"},
        },
    ]

    results = await asyncio.gather(*(system.process(**doc) for doc in documents))

    assert len(results) == 3
    trace_ids = []
    for res in results:
        assert res is not None
        assert res.trace_id
        trace_ids.append(res.trace_id)
    assert len(set(trace_ids)) == 3


@pytest.mark.asyncio
async def test_idempotent_processing(initialized_system):
    system = initialized_system
    note = SAMPLE_NOTES["simple_dm_htn"]
    ctx = {"age": 48, "gender": "female", "encounter_type": "OUTPATIENT"}

    result_one = await system.process(clinical_note=note, patient_context=ctx)
    result_two = await system.process(clinical_note=note, patient_context=ctx)

    if (
        result_one.status == "SUCCESS"
        and result_two.status == "SUCCESS"
        and result_one.coding_result
        and result_two.coding_result
    ):
        codes_one = {cd.code for cd in (result_one.coding_result.diagnosis_codes or [])}
        codes_two = {cd.code for cd in (result_two.coding_result.diagnosis_codes or [])}
        assert codes_one == codes_two, f"Codes diverged: {codes_one} vs {codes_two}"


@pytest.mark.asyncio
async def test_system_stats_after_processing(initialized_system):
    system = initialized_system
    stats = await system.get_system_stats()
    assert isinstance(stats, dict)
