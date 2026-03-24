import pytest
from typing import cast

from medi_comply.nlp.coref_resolver import (
    CoreferenceResolver,
    MedicalAbbreviationResolver,
    PronounResolver,
    SectionAwareResolver,
    Mention,
    MentionType,
)


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def make_mention(text: str, mtype: MentionType, start: int, entity_type: str | None = None, section: str | None = None, sentence_index: int = 0) -> Mention:
    return Mention(
        text=text,
        mention_type=mtype,
        start_offset=start,
        end_offset=start + len(text),
        sentence_index=sentence_index,
        section=section,
        entity_type=entity_type,
    )


def make_entity_dict(text: str, start: int, entity_type: str = "CONDITION", section: str | None = None, sentence_index: int = 0) -> dict:
    return {
        "text": text,
        "start_offset": start,
        "end_offset": start + len(text),
        "sentence_index": sentence_index,
        "section": section,
        "entity_type": entity_type,
    }


# ---------------------------------------------------------------------------
# MedicalAbbreviationResolver tests
# ---------------------------------------------------------------------------


def test_resolve_common_abbreviations():
    resolver = MedicalAbbreviationResolver()
    assert resolver.resolve("DM") == "diabetes mellitus"
    assert resolver.resolve("HTN") == "hypertension"
    assert resolver.resolve("CKD") == "chronic kidney disease"


def test_resolve_cardiac_abbreviations():
    resolver = MedicalAbbreviationResolver()
    assert resolver.resolve("MI") == "myocardial infarction"
    assert resolver.resolve("NSTEMI") == "non-ST-elevation myocardial infarction"
    assert resolver.resolve("CHF") == "congestive heart failure"


def test_unknown_abbreviation_returns_none():
    resolver = MedicalAbbreviationResolver()
    assert resolver.resolve("XYZ123") is None


def test_is_abbreviation():
    resolver = MedicalAbbreviationResolver()
    assert resolver.is_abbreviation("DM") is True
    assert resolver.is_abbreviation("diabetes") is False


def test_find_abbreviations_in_text():
    resolver = MedicalAbbreviationResolver()
    text = "Patient has DM and HTN"
    matches = resolver.find_abbreviations(text)
    assert ("DM", "diabetes mellitus", 12, 14) in matches
    assert ("HTN", "hypertension", 19, 22) in matches


def test_context_aware_pe_pulmonary():
    resolver = MedicalAbbreviationResolver()
    text = "Concern for PE due to possible embolism"
    matches = resolver.find_abbreviations(text)
    assert any(full == "pulmonary embolism" for _, full, _, _ in matches)


def test_context_aware_pe_physical():
    resolver = MedicalAbbreviationResolver()
    text = "Pre-op PE exam completed"
    matches = resolver.find_abbreviations(text)
    assert any(full == "physical exam" for _, full, _, _ in matches)


def test_case_insensitive():
    resolver = MedicalAbbreviationResolver()
    assert resolver.resolve("dm") == "diabetes mellitus"
    assert resolver.resolve("Dm") == "diabetes mellitus"


def test_abbreviation_count():
    resolver = MedicalAbbreviationResolver()
    assert len(resolver._abbr_map) >= 50


# ---------------------------------------------------------------------------
# PronounResolver tests
# ---------------------------------------------------------------------------


def test_he_resolves_to_patient():
    pronoun_resolver = PronounResolver()
    patient = make_mention("patient", MentionType.PROPER_NOUN, 0, entity_type="PATIENT")
    he = make_mention("He", MentionType.PRONOUN, 18, entity_type=None)
    pairs = pronoun_resolver.resolve_pronouns([], [patient, he])
    assert pairs[0][0] == he and pairs[0][1] == patient


def test_it_resolves_to_condition():
    pronoun_resolver = PronounResolver()
    condition = make_mention("diabetes", MentionType.PROPER_NOUN, 15, entity_type="CONDITION")
    it = make_mention("It", MentionType.PRONOUN, 25, entity_type=None)
    pairs = pronoun_resolver.resolve_pronouns([], [condition, it])
    assert pairs[0][1] == condition


def test_the_medication_resolves():
    pronoun_resolver = PronounResolver()
    med = make_mention("metformin", MentionType.PROPER_NOUN, 10, entity_type="MEDICATION")
    phrase = make_mention("The medication", MentionType.DEFINITE_NP, 25, entity_type=None)
    pairs = pronoun_resolver.resolve_pronouns([], [med, phrase])
    assert pairs[0][1] == med


def test_possessive_his():
    pronoun_resolver = PronounResolver()
    patient_knee = make_mention("patient's left knee", MentionType.PROPER_NOUN, 0, entity_type="BODY_SITE")
    his = make_mention("His knee", MentionType.POSSESSIVE, 25, entity_type=None)
    pairs = pronoun_resolver.resolve_pronouns([], [patient_knee, his])
    assert pairs[0][1] == patient_knee


def test_recency_bias():
    pronoun_resolver = PronounResolver()
    old = make_mention("hypertension", MentionType.PROPER_NOUN, 5, entity_type="CONDITION")
    recent = make_mention("diabetes", MentionType.PROPER_NOUN, 30, entity_type="CONDITION")
    it = make_mention("It", MentionType.PRONOUN, 40, entity_type=None)
    pairs = pronoun_resolver.resolve_pronouns([], [old, recent, it])
    assert pairs[0][1] == recent


def test_the_patient_always_resolves():
    pronoun_resolver = PronounResolver()
    patient = make_mention("patient", MentionType.PROPER_NOUN, 0, entity_type="PATIENT")
    phrase = make_mention("the patient", MentionType.DEFINITE_NP, 15, entity_type=None)
    pairs = pronoun_resolver.resolve_pronouns([], [patient, phrase])
    assert pairs[0][1] == patient


# ---------------------------------------------------------------------------
# SectionAwareResolver tests
# ---------------------------------------------------------------------------


def test_same_section_preference():
    resolver = SectionAwareResolver()
    cand_same = make_mention("diabetes", MentionType.PROPER_NOUN, 5, entity_type="CONDITION", section="HPI")
    cand_other = make_mention("hypertension", MentionType.PROPER_NOUN, 10, entity_type="CONDITION", section="ROS")
    pronoun = make_mention("this", MentionType.PRONOUN, 20, section="HPI")
    result = resolver.resolve_cross_section(pronoun, [cand_same, cand_other], {0: "HPI"})
    assert result == cand_same


def test_assessment_references_hpi():
    resolver = SectionAwareResolver()
    hpi_ent = make_mention("chest pain", MentionType.PROPER_NOUN, 5, entity_type="CONDITION", section="HPI")
    assess_pron = make_mention("the above", MentionType.RELATIVE, 30, section="Assessment")
    result = resolver.resolve_cross_section(assess_pron, [hpi_ent], {0: "HPI", 30: "Assessment"})
    assert result == hpi_ent


def test_plan_references_assessment():
    resolver = SectionAwareResolver()
    assess_ent = make_mention("NSTEMI", MentionType.PROPER_NOUN, 10, entity_type="CONDITION", section="Assessment")
    plan_pron = make_mention("this condition", MentionType.DEFINITE_NP, 40, section="Plan")
    result = resolver.resolve_cross_section(plan_pron, [assess_ent], {10: "Assessment", 40: "Plan"})
    assert result == assess_ent


# ---------------------------------------------------------------------------
# CoreferenceResolver full pipeline tests
# ---------------------------------------------------------------------------


def test_simple_pronoun_resolution():
    resolver = CoreferenceResolver()
    text = "Patient has diabetes. It is uncontrolled."
    entities = [make_entity_dict("diabetes", 12, entity_type="CONDITION")]
    result = resolver.resolve(text, entities=entities)
    assert len(result.chains) >= 1
    assert "It" in result.resolved_text


def test_multiple_chains():
    resolver = CoreferenceResolver()
    text = "Patient has HTN. It is controlled. Patient also has DM. It is uncontrolled."
    result = resolver.resolve(text)
    assert len(result.chains) >= 2


def test_abbreviation_expansion():
    resolver = CoreferenceResolver()
    text = "Patient has DM. It was treated."
    result = resolver.resolve(text)
    assert any("diabetes mellitus" in chain.canonical_text for chain in result.chains)


def test_resolved_text_format():
    resolver = CoreferenceResolver()
    text = "He has DM."
    result = resolver.resolve(text, entities=[make_entity_dict("He", 0, entity_type="PATIENT")])
    assert "[" in result.resolved_text and "]" in result.resolved_text


def test_confidence_scores():
    resolver = CoreferenceResolver()
    text = "Patient has DM. It is controlled."
    result = resolver.resolve(text)
    for chain in result.chains:
        assert 0.0 <= chain.confidence <= 1.0


def test_canonical_mention_selection():
    resolver = CoreferenceResolver()
    text = "Type 2 diabetes mellitus (T2DM). It is uncontrolled."
    entities = [make_entity_dict("Type 2 diabetes mellitus", 0, entity_type="CONDITION")]
    result = resolver.resolve(text, entities=entities)
    assert any(chain.canonical_text.startswith("Type 2 diabetes") for chain in result.chains)


def test_unresolved_mentions_tracked():
    resolver = CoreferenceResolver()
    text = "It was noted."
    result = resolver.resolve(text)
    assert len(result.unresolved_mentions) >= 1


def test_diabetes_with_complications():
    resolver = CoreferenceResolver()
    text = "Patient has T2DM with nephropathy. It was diagnosed 5 years ago. The condition is managed with metformin."
    result = resolver.resolve(text)
    assert "type 2 diabetes mellitus" in result.resolved_text
    assert any("condition" in m.text.lower() for chain in result.chains for m in chain.mentions)


def test_multiple_conditions():
    resolver = CoreferenceResolver()
    text = "Patient has HTN and DM. The hypertension is controlled. The diabetes is not."
    result = resolver.resolve(text)
    assert len(result.chains) >= 2


def test_negated_pronoun():
    resolver = CoreferenceResolver()
    text = "Patient was evaluated for PE. It was ruled out."
    result = resolver.resolve(text)
    assert "pulmonary embolism" in result.resolved_text


def test_cross_section_reference():
    resolver = CoreferenceResolver()
    text = "HPI: chest pain. Assessment: the above symptom persists."
    sections_map = {0: "HPI", 16: "Assessment"}
    entities = [make_entity_dict("chest pain", 5, entity_type="CONDITION", section="HPI")]
    result = resolver.resolve(text, entities=entities, sections=cast(dict[object, str], sections_map))
    assert len(result.chains) >= 1


def test_no_false_chains():
    resolver = CoreferenceResolver()
    text = "Patient has hypertension and diabetes."
    result = resolver.resolve(text)
    assert result.chains == []


def test_empty_text():
    resolver = CoreferenceResolver()
    result = resolver.resolve("")
    assert result.chains == []
    assert result.resolved_text == ""


def test_long_clinical_note():
    resolver = CoreferenceResolver()
    sentence = "Patient has HTN and DM. It is controlled."
    text = " ".join([sentence] * 60)  # ~500+ words
    result = resolver.resolve(text)
    assert result.resolved_text


def test_result_serialization():
    resolver = CoreferenceResolver()
    text = "Patient has DM. It is treated."
    result = resolver.resolve(text)
    assert result.model_dump()
    assert result.model_dump_json()


def test_abbreviation_count_full():
    resolver = MedicalAbbreviationResolver()
    assert len(resolver._abbr_map) >= 50


def test_context_aware_pe_full_text():
    resolver = CoreferenceResolver()
    text = "PE noted in assessment; exam focused on PE findings."
    result = resolver.resolve(text)
    assert any("pulmonary embolism" in chain.canonical_text or "physical exam" in chain.canonical_text for chain in result.chains)
