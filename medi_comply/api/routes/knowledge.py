"""Knowledge base API routes for MEDI-COMPLY.

This router exposes ICD-10 search, CPT search, NCCI edit checks, version info,
and knowledge base update simulation for the healthcare coding knowledge base.
The implementation is intentionally self-contained to mirror other API layers
and to provide rich seeded data for demos and testing.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# Router setup -------------------------------------------------------------
router = APIRouter(prefix="/api/v1/knowledge", tags=["Knowledge Base"])


# Enums --------------------------------------------------------------------
class CodeType(str, Enum):
    ICD10 = "ICD-10-CM"
    CPT = "CPT"
    HCPCS = "HCPCS"


class NCCIEditType(str, Enum):
    COLUMN_1_2 = "column_1_2"  # Column 1/Column 2 bundling
    MUTUALLY_EXCLUSIVE = "mutually_exclusive"  # Cannot be billed together
    MUE = "mue"  # Medically Unlikely Edit (unit limit)


class NCCIModifierIndicator(str, Enum):
    NOT_ALLOWED = "0"  # Modifier NOT allowed to bypass edit
    ALLOWED = "1"  # Modifier allowed to bypass edit
    NOT_APPLICABLE = "9"  # Not applicable


class UpdateStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"
    SCHEDULED = "scheduled"


# Response models ----------------------------------------------------------
class ICD10CodeResult(BaseModel):
    code: str
    description: str
    long_description: Optional[str] = None
    category: Optional[str] = None
    chapter: Optional[str] = None
    is_billable: bool = True
    parent_code: Optional[str] = None
    child_codes: List[str] = Field(default_factory=list)
    excludes1: List[str] = Field(default_factory=list)
    excludes2: List[str] = Field(default_factory=list)
    includes_notes: List[str] = Field(default_factory=list)
    code_first: Optional[str] = None
    use_additional: Optional[str] = None
    seventh_character: Optional[Dict[str, str]] = None
    age_range: Optional[str] = None
    sex_specific: Optional[str] = None
    manifestation_code: bool = False
    relevance_score: float = 1.0


class ICD10SearchResponse(BaseModel):
    query: str
    result_count: int = 0
    results: List[ICD10CodeResult] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    search_time_ms: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class CPTCodeResult(BaseModel):
    code: str
    description: str
    long_description: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    rvu_work: Optional[float] = None
    rvu_pe: Optional[float] = None
    rvu_mp: Optional[float] = None
    rvu_total: Optional[float] = None
    global_period: Optional[str] = None
    is_add_on: bool = False
    requires_primary: bool = False
    separate_procedure: bool = False
    modifier_info: Optional[str] = None
    common_modifiers: List[str] = Field(default_factory=list)
    common_diagnosis_codes: List[str] = Field(default_factory=list)
    relevance_score: float = 1.0


class CPTSearchResponse(BaseModel):
    query: str
    result_count: int = 0
    results: List[CPTCodeResult] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    search_time_ms: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class NCCIEditResult(BaseModel):
    cpt1: str
    cpt2: str
    has_conflict: bool = False
    edit_type: Optional[str] = None
    modifier_indicator: Optional[str] = None
    modifier_indicator_description: Optional[str] = None
    effective_date: Optional[str] = None
    deletion_date: Optional[str] = None
    rationale: Optional[str] = None
    recommendation: str = ""
    allowed_with_modifier: bool = False
    suggested_resolution: Optional[str] = None


class NCCICheckResponse(BaseModel):
    cpt1: str
    cpt2: str
    edits_found: List[NCCIEditResult] = Field(default_factory=list)
    has_any_conflict: bool = False
    summary: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class MUECheckResult(BaseModel):
    cpt_code: str
    mue_value: Optional[int] = None
    mue_adjudication_indicator: Optional[str] = None
    mue_rationale: Optional[str] = None
    requested_units: int = 1
    exceeds_mue: bool = False
    message: str = ""


class KnowledgeVersionResponse(BaseModel):
    version: str = "KB-2025-Q1-v3"
    build_date: str = ""
    effective_date: str = ""

    icd10_version: str = "FY2025"
    icd10_code_count: int = 0
    icd10_last_updated: str = ""

    cpt_version: str = "2025"
    cpt_code_count: int = 0
    cpt_last_updated: str = ""

    ncci_version: str = "Q1-2025"
    ncci_edit_pairs: int = 0
    ncci_last_updated: str = ""

    lcd_ncd_count: int = 0
    payer_policy_count: int = 0
    guideline_version: str = "OCG-FY2025"

    components: List[Dict[str, Any]] = Field(default_factory=list)
    update_schedule: Dict[str, str] = Field(
        default_factory=lambda: {
            "ICD-10": "Annual (October 1)",
            "CPT": "Annual (January 1)",
            "NCCI": "Quarterly",
            "LCD/NCD": "As published",
            "Payer Policies": "Monitored daily",
        }
    )

    data_sources: List[Dict[str, str]] = Field(default_factory=list)
    integrity_hash: str = ""


class KnowledgeUpdateRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"component": "icd10", "force": False}})

    component: str = Field(
        ..., description="Component to update: icd10, cpt, ncci, lcd_ncd, payer_policies, all"
    )
    force: bool = Field(default=False, description="Force update even if current version is latest")
    dry_run: bool = Field(default=False, description="Preview changes without applying")


class KnowledgeUpdateResponse(BaseModel):
    update_id: str = Field(default_factory=lambda: f"UPD-{uuid.uuid4().hex[:8].upper()}")
    component: str
    status: UpdateStatus
    previous_version: str = ""
    new_version: str = ""
    changes_summary: Dict[str, Any] = Field(default_factory=dict)
    codes_added: int = 0
    codes_removed: int = 0
    codes_modified: int = 0
    regression_test_result: Optional[Dict[str, Any]] = None
    dry_run: bool = False
    message: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


# Knowledge store ----------------------------------------------------------
class KnowledgeStore:
    """Self-contained knowledge base for the API layer."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self.ICD10_DATABASE: Dict[str, Dict[str, Any]] = self._build_icd10_database()
        self.CPT_DATABASE: Dict[str, Dict[str, Any]] = self._build_cpt_database()
        self.NCCI_EDITS: List[Dict[str, Any]] = self._build_ncci_edits()
        self.MUE_LIMITS: Dict[str, Dict[str, Any]] = self._build_mue_limits()
        self._version_info = self._build_version_info()
        self._update_history: List[KnowledgeUpdateResponse] = []

    # -- Seed data ---------------------------------------------------------
    def _build_icd10_database(self) -> Dict[str, Dict[str, Any]]:
        # Core chapter coverage; billable and category codes.
        data: Dict[str, Dict[str, Any]] = {
            # Chapter 1
            "A41.9": {"desc": "Sepsis, unspecified organism", "chapter": "1", "category": "A40-A41", "billable": True},
            "B20": {"desc": "HIV disease", "chapter": "1", "category": "B20-B24", "billable": True},
            "B34.9": {"desc": "Viral infection, unspecified", "chapter": "1", "billable": True},
            # Chapter 3
            "D50.9": {"desc": "Iron deficiency anemia, unspecified", "chapter": "3", "billable": True},
            "D64.9": {"desc": "Anemia, unspecified", "chapter": "3", "billable": True},
            # Chapter 4
            "E03.9": {"desc": "Hypothyroidism, unspecified", "chapter": "4", "billable": True},
            "E04.2": {"desc": "Nontoxic multinodular goiter", "chapter": "4", "billable": True},
            "E10.9": {"desc": "Type 1 diabetes mellitus without complications", "chapter": "4", "billable": True},
            "E11": {"desc": "Type 2 diabetes mellitus", "chapter": "4", "billable": False, "child_codes": ["E11.9", "E11.22", "E11.65"]},
            "E11.9": {"desc": "Type 2 DM without complications", "chapter": "4", "category": "E08-E13", "billable": True, "parent": "E11"},
            "E11.21": {"desc": "Type 2 DM with diabetic nephropathy", "chapter": "4", "billable": True, "parent": "E11"},
            "E11.22": {
                "desc": "Type 2 DM with diabetic CKD",
                "chapter": "4",
                "category": "E08-E13",
                "billable": True,
                "parent": "E11.2",
                "use_additional": "N18.- for stage of CKD",
                "excludes1": ["E13.-"],
                "child_codes": [],
            },
            "E11.29": {"desc": "Type 2 DM with other kidney complication", "chapter": "4", "billable": True, "parent": "E11"},
            "E11.65": {"desc": "Type 2 DM with hyperglycemia", "chapter": "4", "category": "E08-E13", "billable": True, "parent": "E11.6"},
            "E11.8": {"desc": "Type 2 DM with unspecified complications", "chapter": "4", "billable": True, "parent": "E11"},
            "E13.9": {"desc": "Other specified diabetes mellitus without complications", "chapter": "4", "billable": True},
            "E66.01": {"desc": "Morbid obesity due to excess calories", "chapter": "4", "billable": True},
            "E66.9": {"desc": "Obesity, unspecified", "chapter": "4", "billable": True},
            "E78.0": {"desc": "Pure hypercholesterolemia", "chapter": "4", "billable": True},
            "E78.5": {"desc": "Hyperlipidemia, unspecified", "chapter": "4", "billable": True},
            "E55.9": {"desc": "Vitamin D deficiency, unspecified", "chapter": "4", "billable": True},
            # Chapter 5
            "F01.50": {"desc": "Vascular dementia without behavioral disturbance", "chapter": "5", "billable": True},
            "F32.9": {"desc": "Major depressive disorder, single episode, unspecified", "chapter": "5", "billable": True},
            "F33.1": {"desc": "Major depressive disorder, recurrent, moderate", "chapter": "5", "billable": True},
            "F41.1": {"desc": "Generalized anxiety disorder", "chapter": "5", "billable": True},
            "F43.23": {"desc": "Adjustment disorder with mixed anxiety and depressed mood", "chapter": "5", "billable": True},
            # Chapter 6
            "G20": {"desc": "Parkinson's disease", "chapter": "6", "billable": True},
            "G30.9": {"desc": "Alzheimer's disease, unspecified", "chapter": "6", "billable": True},
            "G47.33": {"desc": "Obstructive sleep apnea", "chapter": "6", "billable": True},
            "G89.29": {"desc": "Other chronic pain", "chapter": "6", "billable": True},
            # Chapter 7
            "H25.9": {"desc": "Age-related cataract, unspecified", "chapter": "7", "billable": True},
            "H52.4": {"desc": "Presbyopia", "chapter": "7", "billable": True},
            # Chapter 9
            "I10": {"desc": "Essential (primary) hypertension", "chapter": "9", "billable": True, "excludes1": ["I11.-", "I12.-", "I13.-"]},
            "I11": {"desc": "Hypertensive heart disease", "billable": False, "child_codes": ["I11.0", "I11.9"]},
            "I11.0": {"desc": "Hypertensive heart disease with HF", "chapter": "9", "billable": True, "parent": "I11"},
            "I11.9": {"desc": "Hypertensive heart disease without HF", "chapter": "9", "billable": True, "parent": "I11"},
            "I12.9": {"desc": "Hypertensive CKD with stage 1-4", "chapter": "9", "billable": True},
            "I13.0": {"desc": "Hypertensive heart and CKD with HF", "chapter": "9", "billable": True},
            "I21": {"desc": "Acute myocardial infarction", "billable": False, "child_codes": ["I21.0", "I21.1", "I21.3", "I21.4"]},
            "I21.3": {"desc": "ST elevation MI of unspecified site", "chapter": "9", "billable": True, "parent": "I21"},
            "I21.4": {"desc": "Non-ST elevation MI", "chapter": "9", "billable": True, "parent": "I21"},
            "I25.10": {"desc": "Atherosclerotic heart disease without angina", "chapter": "9", "billable": True},
            "I48.91": {"desc": "Unspecified atrial fibrillation", "chapter": "9", "billable": True},
            "I49.9": {"desc": "Cardiac arrhythmia, unspecified", "chapter": "9", "billable": True},
            "I50": {"desc": "Heart failure", "billable": False, "child_codes": ["I50.9"]},
            "I50.9": {"desc": "Heart failure, unspecified", "chapter": "9", "billable": True, "child_codes": ["I50.20", "I50.30", "I50.40"]},
            "I50.20": {"desc": "Unspecified systolic HF", "chapter": "9", "billable": True, "parent": "I50.9"},
            "I50.30": {"desc": "Unspecified diastolic HF", "chapter": "9", "billable": True, "parent": "I50.9"},
            "I50.40": {"desc": "Unspecified combined systolic and diastolic HF", "chapter": "9", "billable": True, "parent": "I50.9"},
            "I63.9": {"desc": "Cerebral infarction, unspecified", "chapter": "9", "billable": True},
            "I65.29": {"desc": "Occlusion and stenosis of carotid artery", "chapter": "9", "billable": True},
            "I70.201": {"desc": "Unspecified atherosclerosis of native arteries of extremities", "chapter": "9", "billable": True},
            "I73.9": {"desc": "Peripheral vascular disease, unspecified", "chapter": "9", "billable": True},
            "I82.40": {"desc": "Acute DVT of unspecified deep veins of lower extremity", "chapter": "9", "billable": True},
            "I26.99": {"desc": "Other pulmonary embolism without acute cor pulmonale", "chapter": "9", "billable": True},
            # Chapter 10
            "J18.9": {"desc": "Pneumonia, unspecified organism", "chapter": "10", "billable": True},
            "J18.1": {"desc": "Lobar pneumonia, unspecified organism", "chapter": "10", "billable": True},
            "J44.1": {"desc": "COPD with acute exacerbation", "chapter": "10", "billable": True},
            "J45.20": {"desc": "Mild intermittent asthma, uncomplicated", "chapter": "10", "billable": True},
            "J45.40": {"desc": "Moderate persistent asthma", "chapter": "10", "billable": True},
            "J96.00": {"desc": "Acute respiratory failure, unspecified", "chapter": "10", "billable": True},
            # Chapter 11
            "K21.0": {"desc": "GERD with esophagitis", "chapter": "11", "billable": True},
            "K21.9": {"desc": "GERD without esophagitis", "chapter": "11", "billable": True},
            "K35.80": {"desc": "Unspecified acute appendicitis", "chapter": "11", "billable": True},
            "K52.9": {"desc": "Noninfective gastroenteritis and colitis, unspecified", "chapter": "11", "billable": True},
            "K57.30": {"desc": "Diverticulosis large intestine without perforation", "chapter": "11", "billable": True},
            "K74.60": {"desc": "Unspecified cirrhosis of liver", "chapter": "11", "billable": True},
            "K76.0": {"desc": "Fatty (change of) liver, not elsewhere classified", "chapter": "11", "billable": True},
            "K92.2": {"desc": "Gastrointestinal hemorrhage, unspecified", "chapter": "11", "billable": True},
            # Chapter 13
            "M16.11": {"desc": "Unilateral primary osteoarthritis, right hip", "chapter": "13", "billable": True},
            "M16.12": {"desc": "Unilateral primary osteoarthritis, left hip", "chapter": "13", "billable": True},
            "M17.11": {"desc": "Primary osteoarthritis, right knee", "chapter": "13", "billable": True},
            "M17.12": {"desc": "Primary osteoarthritis, left knee", "chapter": "13", "billable": True},
            "M23.21": {"desc": "Derangement of meniscus due to old tear, right knee", "chapter": "13", "billable": True},
            "M23.22": {"desc": "Derangement of meniscus due to old tear, left knee", "chapter": "13", "billable": True},
            "M25.561": {"desc": "Pain in right knee", "chapter": "13", "billable": True},
            "M25.562": {"desc": "Pain in left knee", "chapter": "13", "billable": True},
            "M46.96": {"desc": "Inflammatory spondylopathy, lumbar region", "chapter": "13", "billable": True},
            "M51.36": {"desc": "DDD, lumbar region", "chapter": "13", "billable": True},
            "M54.2": {"desc": "Cervicalgia", "chapter": "13", "billable": True},
            "M54.5": {"desc": "Low back pain", "chapter": "13", "billable": True},
            "M62.830": {"desc": "Muscle spasm of back", "chapter": "13", "billable": True},
            "M65.4": {"desc": "Radial styloid tenosynovitis", "chapter": "13", "billable": True},
            "M70.50": {"desc": "Trochanteric bursitis, unspecified hip", "chapter": "13", "billable": True},
            "M75.41": {"desc": "Impingement syndrome of right shoulder", "chapter": "13", "billable": True},
            "M75.42": {"desc": "Impingement syndrome of left shoulder", "chapter": "13", "billable": True},
            "M79.1": {"desc": "Myalgia", "chapter": "13", "billable": True},
            # Chapter 14
            "N17.9": {"desc": "Acute kidney failure, unspecified", "chapter": "14", "billable": True},
            "N18": {"desc": "Chronic kidney disease", "billable": False, "child_codes": ["N18.1", "N18.2", "N18.3", "N18.4", "N18.5"]},
            "N18.2": {"desc": "CKD stage 2", "chapter": "14", "billable": True, "parent": "N18"},
            "N18.3": {"desc": "CKD stage 3 (moderate)", "chapter": "14", "billable": True, "child_codes": ["N18.31", "N18.32"], "parent": "N18"},
            "N18.31": {"desc": "CKD stage 3a", "chapter": "14", "billable": True, "parent": "N18.3"},
            "N18.32": {"desc": "CKD stage 3b", "chapter": "14", "billable": True, "parent": "N18.3"},
            "N18.4": {"desc": "CKD stage 4 (severe)", "chapter": "14", "billable": True, "parent": "N18"},
            "N18.5": {"desc": "CKD stage 5", "chapter": "14", "billable": True, "parent": "N18"},
            "N39.0": {"desc": "UTI, site not specified", "chapter": "14", "billable": True},
            "N40.0": {"desc": "Enlarged prostate without LUTS", "chapter": "14", "billable": True},
            # Chapter 15 (sex-specific)
            "O09.529": {"desc": "Elderly multigravida, unspecified trimester", "chapter": "15", "billable": True, "sex_specific": "F"},
            # Chapter 18
            "R06.02": {"desc": "Shortness of breath", "chapter": "18", "billable": True},
            "R07.9": {"desc": "Chest pain, unspecified", "chapter": "18", "billable": True},
            "R50.9": {"desc": "Fever, unspecified", "chapter": "18", "billable": True},
            "R53.83": {"desc": "Other fatigue", "chapter": "18", "billable": True},
            "R73.9": {"desc": "Hyperglycemia, unspecified", "chapter": "18", "billable": True},
            "R79.89": {"desc": "Other specified abnormal findings of blood chemistry", "chapter": "18", "billable": True},
            "R91.1": {"desc": "Solitary pulmonary nodule", "chapter": "18", "billable": True},
            # Chapter 19
            "S06.0X0A": {"desc": "Concussion without loss of consciousness", "chapter": "19", "billable": True},
            "S72.001A": {"desc": "Fracture of unspecified part of neck of right femur, initial", "chapter": "19", "billable": True, "seventh": {"A": "initial"}},
            "S72.002A": {"desc": "Fracture of unspecified part of neck of left femur, initial", "chapter": "19", "billable": True, "seventh": {"A": "initial"}},
            # Chapter 21
            "Z00.00": {"desc": "Encounter for general adult exam without abnormal findings", "chapter": "21", "billable": True},
            "Z01.818": {"desc": "Preprocedural exam, other", "chapter": "21", "billable": True},
            "Z11.52": {"desc": "Encounter for screening for COVID-19", "chapter": "21", "billable": True},
            "Z23": {"desc": "Encounter for immunization", "chapter": "21", "billable": True},
            "Z68.41": {"desc": "BMI 40.0-44.9, adult", "chapter": "21", "billable": True},
            "Z79.4": {"desc": "Long term (current) insulin use", "chapter": "21", "billable": True},
            "Z79.899": {"desc": "Other long term drug therapy", "chapter": "21", "billable": True},
            "Z86.73": {"desc": "Personal history of TIA and stroke without residual deficits", "chapter": "21", "billable": True},
            "Z91.19": {"desc": "Patient's noncompliance with medical treatment", "chapter": "21", "billable": True},
            "Z95.5": {"desc": "Presence of coronary angioplasty implant and graft", "chapter": "21", "billable": True},
        }

        # Non-billable roll-ups and additional breadth to push code coverage beyond 60.
        data.update(
            {
                "E11.2": {"desc": "Type 2 diabetes with kidney complications", "billable": False, "child_codes": ["E11.22", "E11.29"]},
                "E11.6": {"desc": "Type 2 diabetes with other specified complications", "billable": False, "child_codes": ["E11.65", "E11.8"]},
                "I21": {"desc": "Acute myocardial infarction", "billable": False, "child_codes": ["I21.3", "I21.4"]},
                "N18": {"desc": "Chronic kidney disease", "billable": False, "child_codes": ["N18.2", "N18.3", "N18.4", "N18.5"]},
                "I50": {"desc": "Heart failure", "billable": False, "child_codes": ["I50.9"]},
            }
        )

        # Additional codes to surpass 60 entries and broaden chapter variety.
        extras: Dict[str, Dict[str, Any]] = {
            "A09": {"desc": "Infectious gastroenteritis and colitis", "chapter": "1", "billable": True},
            "B18.1": {"desc": "Chronic viral hepatitis B", "chapter": "1", "billable": True},
            "C34.90": {"desc": "Malignant neoplasm of unspecified part of unspecified bronchus or lung", "chapter": "2", "billable": True},
            "D68.9": {"desc": "Coagulation defect, unspecified", "chapter": "3", "billable": True},
            "E87.1": {"desc": "Hypo-osmolality and hyponatremia", "chapter": "4", "billable": True},
            "E87.6": {"desc": "Hypokalemia", "chapter": "4", "billable": True},
            "F11.20": {"desc": "Opioid dependence, uncomplicated", "chapter": "5", "billable": True},
            "G62.9": {"desc": "Polyneuropathy, unspecified", "chapter": "6", "billable": True},
            "H40.9": {"desc": "Unspecified glaucoma", "chapter": "7", "billable": True},
            "I31.3": {"desc": "Pericardial effusion (noninflammatory)", "chapter": "9", "billable": True},
            "I95.9": {"desc": "Hypotension, unspecified", "chapter": "9", "billable": True},
            "J20.9": {"desc": "Acute bronchitis, unspecified", "chapter": "10", "billable": True},
            "J30.1": {"desc": "Allergic rhinitis due to pollen", "chapter": "10", "billable": True},
            "J98.4": {"desc": "Other disorders of lung", "chapter": "10", "billable": True},
            "K59.00": {"desc": "Constipation, unspecified", "chapter": "11", "billable": True},
            "K64.9": {"desc": "Unspecified hemorrhoids", "chapter": "11", "billable": True},
            "M05.79": {"desc": "Rheumatoid arthritis with rheumatoid factor, multiple sites", "chapter": "13", "billable": True},
            "M06.9": {"desc": "Rheumatoid arthritis, unspecified", "chapter": "13", "billable": True},
            "M79.7": {"desc": "Fibromyalgia", "chapter": "13", "billable": True},
            "N30.00": {"desc": "Acute cystitis without hematuria", "chapter": "14", "billable": True},
            "N30.01": {"desc": "Acute cystitis with hematuria", "chapter": "14", "billable": True},
            "N39.41": {"desc": "Urge incontinence", "chapter": "14", "billable": True},
            "O24.419": {"desc": "Gestational diabetes mellitus in pregnancy", "chapter": "15", "billable": True},
            "R10.13": {"desc": "Epigastric pain", "chapter": "18", "billable": True},
            "R19.7": {"desc": "Diarrhea, unspecified", "chapter": "18", "billable": True},
            "R42": {"desc": "Dizziness and giddiness", "chapter": "18", "billable": True},
            "R82.90": {"desc": "Abnormal findings in urine", "chapter": "18", "billable": True},
            "S93.401A": {"desc": "Sprain of unspecified ligament of right ankle", "chapter": "19", "billable": True},
            "T81.4XXA": {"desc": "Infection following a procedure", "chapter": "19", "billable": True},
            "Z72.0": {"desc": "Tobacco use", "chapter": "21", "billable": True},
            "Z72.4": {"desc": "Inappropriate diet and eating habits", "chapter": "21", "billable": True},
            "Z85.3": {"desc": "Personal history of malignant neoplasm of breast", "chapter": "21", "billable": True},
            "Z87.891": {"desc": "Personal history of nicotine dependence", "chapter": "21", "billable": True},
        }
        data.update(extras)
        return data

    def _build_cpt_database(self) -> Dict[str, Dict[str, Any]]:
        data: Dict[str, Dict[str, Any]] = {
            # E&M
            "99211": {"desc": "Office visit, established, minimal", "category": "E&M", "rvu_total": 0.57},
            "99212": {"desc": "Office visit, established, straightforward", "category": "E&M", "rvu_total": 1.28},
            "99213": {"desc": "Office visit, established, low complexity", "category": "E&M", "rvu_total": 1.92, "common_modifiers": ["25"], "common_dx": ["R07.9", "I10", "E11.9"]},
            "99214": {"desc": "Office visit, established, moderate", "category": "E&M", "rvu_total": 2.86, "common_modifiers": ["25"]},
            "99215": {"desc": "Office visit, established, high", "category": "E&M", "rvu_total": 3.99, "common_modifiers": ["25"]},
            "99203": {"desc": "Office visit, new patient, low", "category": "E&M", "rvu_total": 2.09},
            "99204": {"desc": "Office visit, new patient, moderate", "category": "E&M", "rvu_total": 3.18},
            "99205": {"desc": "Office visit, new patient, high", "category": "E&M", "rvu_total": 4.21},
            "99221": {"desc": "Initial hospital care, straightforward/low", "category": "E&M", "rvu_total": 3.23},
            "99222": {"desc": "Initial hospital care, moderate", "category": "E&M", "rvu_total": 4.30},
            "99223": {"desc": "Initial hospital care, high complexity", "category": "E&M", "rvu_total": 5.98, "common_modifiers": ["25", "57"]},
            "99231": {"desc": "Subsequent hospital care, low", "category": "E&M", "rvu_total": 1.59},
            "99232": {"desc": "Subsequent hospital care, moderate", "category": "E&M", "rvu_total": 2.31},
            "99233": {"desc": "Subsequent hospital care, high", "category": "E&M", "rvu_total": 3.23},
            "99238": {"desc": "Hospital discharge, 30 min or less", "category": "E&M", "rvu_total": 2.15},
            "99239": {"desc": "Hospital discharge, more than 30 min", "category": "E&M", "rvu_total": 3.17},
            # Radiology
            "70450": {"desc": "CT head/brain without contrast", "category": "Radiology", "rvu_total": 1.28},
            "70551": {"desc": "MRI brain without contrast", "category": "Radiology", "rvu_total": 1.52},
            "70553": {"desc": "MRI brain without then with contrast", "category": "Radiology", "rvu_total": 2.19},
            "71045": {"desc": "Chest X-ray, single view", "category": "Radiology", "rvu_total": 0.22},
            "71046": {"desc": "Chest X-ray, 2 views", "category": "Radiology", "rvu_total": 0.31},
            "73721": {"desc": "MRI lower extremity joint without contrast", "category": "Radiology", "rvu_total": 1.44},
            "73723": {"desc": "MRI lower extremity joint with/without contrast", "category": "Radiology", "rvu_total": 1.80},
            "74176": {"desc": "CT abdomen and pelvis without contrast", "category": "Radiology", "rvu_total": 1.62},
            "74177": {"desc": "CT abdomen/pelvis with contrast", "category": "Radiology", "rvu_total": 1.82},
            "70460": {"desc": "CT head with contrast", "category": "Radiology", "rvu_total": 1.40},
            # Cardiology
            "93000": {"desc": "Electrocardiogram, routine with interpretation", "category": "Cardiology", "rvu_total": 0.33},
            "93005": {"desc": "EKG tracing only", "category": "Cardiology", "rvu_total": 0.15},
            "93010": {"desc": "EKG interpretation and report", "category": "Cardiology", "rvu_total": 0.18},
            "93306": {"desc": "Echocardiography, transthoracic", "category": "Cardiology", "rvu_total": 1.88},
            "93452": {"desc": "Left heart catheterization", "category": "Cardiology", "rvu_total": 6.41},
            "93458": {"desc": "Left heart cath with coronary angiography", "category": "Cardiology", "rvu_total": 7.12},
            # Laboratory
            "80048": {"desc": "Basic metabolic panel", "category": "Laboratory", "rvu_total": 0.0},
            "80053": {"desc": "Comprehensive metabolic panel", "category": "Laboratory", "rvu_total": 0.0},
            "80061": {"desc": "Lipid panel", "category": "Laboratory", "rvu_total": 0.0},
            "81003": {"desc": "Urinalysis, automated", "category": "Laboratory", "rvu_total": 0.0},
            "82565": {"desc": "Creatinine", "category": "Laboratory", "rvu_total": 0.0},
            "83036": {"desc": "Hemoglobin A1c", "category": "Laboratory", "rvu_total": 0.0},
            "84443": {"desc": "TSH", "category": "Laboratory", "rvu_total": 0.0},
            "84484": {"desc": "Troponin, quantitative", "category": "Laboratory", "rvu_total": 0.0},
            "85025": {"desc": "Complete blood count with differential", "category": "Laboratory", "rvu_total": 0.0},
            "85027": {"desc": "Complete blood count, automated", "category": "Laboratory", "rvu_total": 0.0},
            # Surgery
            "27130": {"desc": "Total hip arthroplasty", "category": "Surgery", "rvu_total": 20.84, "global_period": "090"},
            "27446": {"desc": "Partial knee arthroplasty", "category": "Surgery", "rvu_total": 16.50, "global_period": "090"},
            "27447": {"desc": "Total knee arthroplasty", "category": "Surgery", "rvu_total": 21.59, "global_period": "090"},
            "29877": {"desc": "Arthroscopy, debridement/shaving of articular cartilage", "category": "Surgery", "rvu_total": 4.50, "global_period": "090"},
            "29881": {"desc": "Arthroscopy, knee, surgical; meniscectomy", "category": "Surgery", "rvu_total": 6.43, "global_period": "090"},
            "31500": {"desc": "Intubation, endotracheal", "category": "Surgery", "rvu_total": 2.48},
            "33405": {"desc": "Replacement of aortic valve", "category": "Surgery", "rvu_total": 40.0, "global_period": "090"},
            # Medicine / Therapy
            "94002": {"desc": "Ventilation assist and management, initial day", "category": "Medicine", "rvu_total": 1.99},
            "96372": {"desc": "Therapeutic injection, SC or IM", "category": "Medicine", "rvu_total": 0.36},
            "97110": {"desc": "Therapeutic exercises", "category": "Physical Therapy", "rvu_total": 0.72},
            "97140": {"desc": "Manual therapy techniques", "category": "Physical Therapy", "rvu_total": 0.69},
            "97530": {"desc": "Therapeutic activities", "category": "Physical Therapy", "rvu_total": 0.72},
            "97542": {"desc": "Wheelchair management training", "category": "Physical Therapy", "rvu_total": 0.70},
        }
        return data

    def _build_ncci_edits(self) -> List[Dict[str, Any]]:
        return [
            {"cpt1": "99223", "cpt2": "99232", "type": "mutually_exclusive", "modifier": "0", "rationale": "Cannot bill initial and subsequent hospital care on same date"},
            {"cpt1": "99213", "cpt2": "99214", "type": "mutually_exclusive", "modifier": "0", "rationale": "Cannot bill two E&M codes same level same encounter"},
            {"cpt1": "93000", "cpt2": "93005", "type": "column_1_2", "modifier": "0", "rationale": "93000 includes tracing (93005)"},
            {"cpt1": "93000", "cpt2": "93010", "type": "column_1_2", "modifier": "0", "rationale": "93000 includes interpretation (93010)"},
            {"cpt1": "71046", "cpt2": "71045", "type": "column_1_2", "modifier": "0", "rationale": "2-view chest X-ray includes single view"},
            {"cpt1": "80053", "cpt2": "80048", "type": "column_1_2", "modifier": "0", "rationale": "Comprehensive panel includes basic panel"},
            {"cpt1": "80053", "cpt2": "82565", "type": "column_1_2", "modifier": "0", "rationale": "CMP includes creatinine"},
            {"cpt1": "80053", "cpt2": "84443", "type": "column_1_2", "modifier": "0", "rationale": "CMP includes TSH"},
            {"cpt1": "85025", "cpt2": "85027", "type": "column_1_2", "modifier": "0", "rationale": "CBC with diff includes CBC without"},
            {"cpt1": "93306", "cpt2": "93307", "type": "column_1_2", "modifier": "0", "rationale": "Complete echo includes limited echo"},
            {"cpt1": "93306", "cpt2": "93308", "type": "column_1_2", "modifier": "0", "rationale": "Complete echo includes follow-up echo"},
            {"cpt1": "99223", "cpt2": "99213", "type": "column_1_2", "modifier": "1", "rationale": "E&M bundled unless separate and identifiable service with modifier 25"},
            {"cpt1": "99214", "cpt2": "93000", "type": "column_1_2", "modifier": "1", "rationale": "E&M may bundle with EKG unless modifier 25"},
            {"cpt1": "29881", "cpt2": "29877", "type": "column_1_2", "modifier": "0", "rationale": "Meniscectomy includes debridement"},
            {"cpt1": "27447", "cpt2": "27446", "type": "column_1_2", "modifier": "0", "rationale": "Total knee includes partial knee"},
            {"cpt1": "70551", "cpt2": "70553", "type": "mutually_exclusive", "modifier": "0", "rationale": "MRI without contrast and MRI with/without contrast are mutually exclusive"},
            {"cpt1": "74177", "cpt2": "74176", "type": "column_1_2", "modifier": "0", "rationale": "CT with contrast includes CT without"},
            {"cpt1": "97110", "cpt2": "97530", "type": "column_1_2", "modifier": "1", "rationale": "Therapeutic exercise may bundle with therapeutic activities"},
            {"cpt1": "96372", "cpt2": "99213", "type": "column_1_2", "modifier": "1", "rationale": "Injection may bundle with E&M unless separately identifiable"},
            {"cpt1": "93452", "cpt2": "93458", "type": "column_1_2", "modifier": "0", "rationale": "Left cath included in combined cath with angiography"},
        ]

    def _build_mue_limits(self) -> Dict[str, Dict[str, Any]]:
        return {
            "99213": {"mue": 1, "indicator": "2", "rationale": "1 per date of service"},
            "99214": {"mue": 1, "indicator": "2", "rationale": "1 per date of service"},
            "99223": {"mue": 1, "indicator": "2", "rationale": "1 per admission"},
            "93000": {"mue": 3, "indicator": "3", "rationale": "Up to 3 per date of service"},
            "71046": {"mue": 2, "indicator": "3", "rationale": "Up to 2 per date of service"},
            "80053": {"mue": 1, "indicator": "2", "rationale": "1 per date of service"},
            "85025": {"mue": 2, "indicator": "3", "rationale": "Up to 2 per date of service"},
            "84484": {"mue": 3, "indicator": "3", "rationale": "Up to 3 per date of service for serial troponins"},
            "97110": {"mue": 4, "indicator": "3", "rationale": "Up to 4 units per date of service"},
            "27447": {"mue": 1, "indicator": "1", "rationale": "1 per lifetime per anatomic site"},
        }

    def _build_version_info(self) -> KnowledgeVersionResponse:
        info = KnowledgeVersionResponse()
        info.icd10_code_count = len(self.ICD10_DATABASE)
        info.cpt_code_count = len(self.CPT_DATABASE)
        info.ncci_edit_pairs = len(self.NCCI_EDITS)
        info.build_date = "2025-01-05"
        info.effective_date = "2025-01-15"
        info.icd10_last_updated = "2025-01-01"
        info.cpt_last_updated = "2025-01-01"
        info.ncci_last_updated = "2025-01-01"
        info.lcd_ncd_count = 75
        info.payer_policy_count = 120
        info.components = [
            {"component": "ICD-10", "version": info.icd10_version, "codes": info.icd10_code_count},
            {"component": "CPT", "version": info.cpt_version, "codes": info.cpt_code_count},
            {"component": "NCCI", "version": info.ncci_version, "pairs": info.ncci_edit_pairs},
        ]
        info.data_sources = [
            {"name": "CMS", "type": "NCCI, MUE, LCD/NCD"},
            {"name": "AMA", "type": "CPT"},
            {"name": "WHO", "type": "ICD-10"},
        ]
        raw = f"{info.version}:{info.icd10_code_count}:{info.cpt_code_count}:{info.ncci_edit_pairs}"
        info.integrity_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return info

    # -- Helpers -----------------------------------------------------------
    def _normalize_query(self, query: str) -> str:
        return query.strip().lower()

    def _score_description(self, query_words: List[str], description: str) -> float:
        desc_lower = description.lower()
        if not query_words:
            return 0.0
        if desc_lower == " ".join(query_words):
            return 1.0
        hits = sum(1 for word in query_words if word in desc_lower)
        if hits == len(query_words):
            return 0.9
        if hits == 0:
            return 0.0
        return 0.5 + 0.3 * (hits / len(query_words))

    def _icd10_to_result(self, code: str, meta: Dict[str, Any], score: float) -> ICD10CodeResult:
        return ICD10CodeResult(
            code=code,
            description=meta.get("desc", ""),
            long_description=meta.get("long_desc"),
            category=meta.get("category"),
            chapter=meta.get("chapter"),
            is_billable=meta.get("billable", True),
            parent_code=meta.get("parent"),
            child_codes=meta.get("child_codes", []),
            excludes1=meta.get("excludes1", []),
            excludes2=meta.get("excludes2", []),
            includes_notes=meta.get("includes", []),
            code_first=meta.get("code_first"),
            use_additional=meta.get("use_additional"),
            seventh_character=meta.get("seventh"),
            age_range=meta.get("age_range"),
            sex_specific=meta.get("sex_specific"),
            manifestation_code=meta.get("manifestation", False),
            relevance_score=score,
        )

    def _cpt_to_result(self, code: str, meta: Dict[str, Any], score: float) -> CPTCodeResult:
        return CPTCodeResult(
            code=code,
            description=meta.get("desc", ""),
            long_description=meta.get("long_desc"),
            category=meta.get("category"),
            subcategory=meta.get("subcategory"),
            rvu_work=meta.get("rvu_work"),
            rvu_pe=meta.get("rvu_pe"),
            rvu_mp=meta.get("rvu_mp"),
            rvu_total=meta.get("rvu_total"),
            global_period=meta.get("global_period"),
            is_add_on=meta.get("add_on", False),
            requires_primary=meta.get("requires_primary", False),
            separate_procedure=meta.get("separate_procedure", False),
            modifier_info=meta.get("modifier_info"),
            common_modifiers=meta.get("common_modifiers", []),
            common_diagnosis_codes=meta.get("common_dx", []),
            relevance_score=score,
        )

    # -- Search operations -------------------------------------------------
    def search_icd10(
        self, query: str, max_results: int = 20, billable_only: bool = False, chapter: Optional[str] = None
    ) -> ICD10SearchResponse:
        norm = self._normalize_query(query)
        words = [w for w in re.split(r"\s+", norm) if w]
        results: List[Tuple[ICD10CodeResult, float]] = []

        # 1) Exact code match
        if norm.upper() in self.ICD10_DATABASE:
            meta = self.ICD10_DATABASE[norm.upper()]
            if not billable_only or meta.get("billable", True):
                if chapter is None or meta.get("chapter") == chapter:
                    results.append((self._icd10_to_result(norm.upper(), meta, 1.0), 1.0))

        # 2) Code prefix match
        for code, meta in self.ICD10_DATABASE.items():
            if code.upper().startswith(norm.upper()) and code.upper() != norm.upper():
                if billable_only and not meta.get("billable", True):
                    continue
                if chapter and meta.get("chapter") != chapter:
                    continue
                results.append((self._icd10_to_result(code, meta, 0.95), 0.95))

        # 3) Description search
        for code, meta in self.ICD10_DATABASE.items():
            if norm.upper() == code.upper():
                continue
            if billable_only and not meta.get("billable", True):
                continue
            if chapter and meta.get("chapter") != chapter:
                continue
            score = self._score_description(words, meta.get("desc", ""))
            if score > 0:
                results.append((self._icd10_to_result(code, meta, score), score))

        # 4) Sort and limit
        results.sort(key=lambda x: x[1], reverse=True)
        limited = [item[0] for item in results[:max_results]]

        suggestions: List[str] = []
        if len(limited) < 3:
            suggestions = ["Check spelling", "Try a broader term", "Use fewer words"]

        return ICD10SearchResponse(query=query, result_count=len(limited), results=limited, suggestions=suggestions)

    def search_cpt(self, query: str, max_results: int = 20, category: Optional[str] = None) -> CPTSearchResponse:
        norm = self._normalize_query(query)
        words = [w for w in re.split(r"\s+", norm) if w]
        results: List[Tuple[CPTCodeResult, float]] = []

        # 1) Exact code match
        if norm in self.CPT_DATABASE:
            meta = self.CPT_DATABASE[norm]
            if category is None or meta.get("category") == category:
                results.append((self._cpt_to_result(norm, meta, 1.0), 1.0))

        # 2) Code prefix match
        for code, meta in self.CPT_DATABASE.items():
            if code.startswith(norm) and code != norm:
                if category and meta.get("category") != category:
                    continue
                results.append((self._cpt_to_result(code, meta, 0.95), 0.95))

        # 3) Description search
        for code, meta in self.CPT_DATABASE.items():
            if norm == code:
                continue
            if category and meta.get("category") != category:
                continue
            score = self._score_description(words, meta.get("desc", ""))
            if score > 0:
                results.append((self._cpt_to_result(code, meta, score), score))

        results.sort(key=lambda x: x[1], reverse=True)
        limited = [item[0] for item in results[:max_results]]

        suggestions: List[str] = []
        if len(limited) < 3:
            suggestions = ["Consider code prefix", "Try different wording", "Use fewer filters"]

        return CPTSearchResponse(query=query, result_count=len(limited), results=limited, suggestions=suggestions)

    # -- Edit and version utilities ---------------------------------------
    def _build_recommendation(self, modifier: str, rationale: str) -> Tuple[str, bool, str]:
        if modifier == NCCIModifierIndicator.NOT_ALLOWED.value:
            return (
                f"These codes CANNOT be billed together. {rationale}",
                False,
                "No modifiers allowed to bypass edit",
            )
        if modifier == NCCIModifierIndicator.ALLOWED.value:
            return (
                f"These codes may be billed together with appropriate modifier (e.g., modifier 25 or 59). {rationale}",
                True,
                "Modifier allowed to bypass edit",
            )
        return (f"Edit not applicable to this pair. {rationale}", False, "Not applicable")

    def _check_mue(self, cpt_code: str, units: int) -> MUECheckResult:
        mue_info = self.MUE_LIMITS.get(cpt_code)
        if not mue_info:
            return MUECheckResult(
                cpt_code=cpt_code,
                mue_value=None,
                mue_adjudication_indicator=None,
                mue_rationale=None,
                requested_units=units,
                exceeds_mue=False,
                message="No MUE limit found",
            )
        exceeds = units > mue_info.get("mue", 0)
        message = "Within MUE" if not exceeds else "Requested units exceed MUE"
        return MUECheckResult(
            cpt_code=cpt_code,
            mue_value=mue_info.get("mue"),
            mue_adjudication_indicator=mue_info.get("indicator"),
            mue_rationale=mue_info.get("rationale"),
            requested_units=units,
            exceeds_mue=exceeds,
            message=message,
        )

    def check_ncci(self, cpt1: str, cpt2: str) -> NCCICheckResponse:
        edits: List[NCCIEditResult] = []
        pairs = [(cpt1, cpt2), (cpt2, cpt1)]
        for left, right in pairs:
            for edit in self.NCCI_EDITS:
                if edit.get("cpt1") == left and edit.get("cpt2") == right:
                    rec, allow_mod, indicator_desc = self._build_recommendation(edit.get("modifier"), edit.get("rationale", ""))
                    edits.append(
                        NCCIEditResult(
                            cpt1=left,
                            cpt2=right,
                            has_conflict=True,
                            edit_type=edit.get("type"),
                            modifier_indicator=edit.get("modifier"),
                            modifier_indicator_description=indicator_desc,
                            rationale=edit.get("rationale"),
                            recommendation=rec,
                            allowed_with_modifier=allow_mod,
                            suggested_resolution="Use distinct modifier and documentation" if allow_mod else "Do not bill together",
                        )
                    )
        has_conflict = any(edit.has_conflict for edit in edits)
        summary = "No conflicts found" if not has_conflict else "Conflicts detected; review recommendations"
        mue1 = self._check_mue(cpt1, 1)
        mue2 = self._check_mue(cpt2, 1)
        if mue1.exceeds_mue or mue2.exceeds_mue:
            summary += "; MUE exceeded"
        return NCCICheckResponse(cpt1=cpt1, cpt2=cpt2, edits_found=edits, has_any_conflict=has_conflict, summary=summary)

    def check_mue(self, cpt_code: str, units: int = 1) -> MUECheckResult:
        return self._check_mue(cpt_code, units)

    def get_version(self) -> KnowledgeVersionResponse:
        return self._build_version_info()

    def process_update(self, request: KnowledgeUpdateRequest) -> KnowledgeUpdateResponse:
        component = request.component.lower()
        valid_components = {"icd10", "cpt", "ncci", "lcd_ncd", "payer_policies", "all"}
        if component not in valid_components:
            raise ValueError("Invalid component")

        self.logger.info("Processing knowledge update for %s", component)
        response = KnowledgeUpdateResponse(
            component=component,
            status=UpdateStatus.IN_PROGRESS,
            previous_version=self._version_info.version,
        )

        if request.dry_run:
            simulated_add = 8
            simulated_mod = 3
            response.codes_added = simulated_add
            response.codes_modified = simulated_mod
            response.status = UpdateStatus.SUCCESS
            response.message = "Dry run completed; no changes applied"
            response.changes_summary = {"would_add": simulated_add, "would_modify": simulated_mod}
            response.dry_run = True
            self._update_history.append(response)
            return response

        additions = 4
        modifications = 2
        pass_rate = 0.998
        response.codes_added = additions
        response.codes_modified = modifications
        response.regression_test_result = {
            "cases": 1000,
            "passed": int(1000 * pass_rate),
            "failed": 1000 - int(1000 * pass_rate),
            "pass_rate": pass_rate * 100,
        }

        if pass_rate >= 99.5:
            response.status = UpdateStatus.SUCCESS
            response.new_version = f"{self._version_info.version}-r{len(self._update_history) + 1}"
            response.message = "Promoted after regression testing"
            self._version_info.version = response.new_version or self._version_info.version
        else:
            response.status = UpdateStatus.FAILED
            response.new_version = self._version_info.version
            response.message = "Regression tests below threshold; rollback"

        self._update_history.append(response)
        return response

    def get_code_hierarchy(self, code: str, code_type: str = "ICD-10-CM") -> Dict[str, Any]:
        if code_type != CodeType.ICD10.value:
            return {"code": code, "parent": None, "children": [], "siblings": [], "depth": 0}

        meta = self.ICD10_DATABASE.get(code)
        if not meta:
            return {"code": code, "parent": None, "children": [], "siblings": [], "depth": 0}

        parent = meta.get("parent")
        children = meta.get("child_codes", [])
        siblings: List[str] = []
        if parent:
            parent_meta = self.ICD10_DATABASE.get(parent, {})
            siblings = [c for c in parent_meta.get("child_codes", []) if c != code]

        depth = 1
        cur = parent
        while cur:
            depth += 1
            cur_meta = self.ICD10_DATABASE.get(cur)
            cur = cur_meta.get("parent") if cur_meta else None

        return {"code": code, "parent": parent, "children": children, "siblings": siblings, "depth": depth}


# Module-level store ------------------------------------------------------
_store = KnowledgeStore()


# Routes ------------------------------------------------------------------
@router.get(
    "/icd10/search",
    response_model=ICD10SearchResponse,
    summary="Search ICD-10-CM codes",
    description="Search ICD-10-CM diagnosis codes by code, description keyword, or clinical term.",
)
async def search_icd10(
    q: str = Query(..., min_length=1, description="Search query (code or description)"),
    max_results: int = Query(20, ge=1, le=100),
    billable_only: bool = Query(False, description="Only return billable codes"),
    chapter: Optional[str] = Query(None, description="Filter by ICD-10 chapter number"),
):
    start = time.time()
    result = _store.search_icd10(q, max_results, billable_only, chapter)
    result.search_time_ms = int((time.time() - start) * 1000)
    return result


@router.get(
    "/cpt/search",
    response_model=CPTSearchResponse,
    summary="Search CPT codes",
    description="Search CPT procedure codes by code, description keyword, or procedure term.",
)
async def search_cpt(
    q: str = Query(..., min_length=1, description="Search query"),
    max_results: int = Query(20, ge=1, le=100),
    category: Optional[str] = Query(None, description="Filter by category (E&M, Radiology, etc.)"),
):
    start = time.time()
    result = _store.search_cpt(q, max_results, category)
    result.search_time_ms = int((time.time() - start) * 1000)
    return result


@router.get(
    "/ncci/check",
    response_model=NCCICheckResponse,
    summary="Check NCCI edits between two CPT codes",
    description="Checks for NCCI bundling edits, mutually exclusive edits, and MUE limits between two CPT codes.",
)
async def check_ncci_edits(
    cpt1: str = Query(..., description="First CPT code"),
    cpt2: str = Query(..., description="Second CPT code"),
):
    return _store.check_ncci(cpt1, cpt2)


@router.get(
    "/version",
    response_model=KnowledgeVersionResponse,
    summary="Get knowledge base version",
    description="Returns current knowledge base version, code counts, update schedule, and data source information.",
)
async def get_kb_version():
    return _store.get_version()


@router.post(
    "/update",
    response_model=KnowledgeUpdateResponse,
    summary="Update knowledge base (admin only)",
    description="Triggers a knowledge base update for the specified component. Includes regression testing before promotion.",
)
async def update_knowledge_base(request: KnowledgeUpdateRequest):
    # In production, this would require admin auth: Depends(require_role("ADMIN"))
    try:
        result = _store.process_update(request)
        logger.info("Knowledge update: %s -> %s", request.component, result.status)
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # pragma: no cover - defensive catch
        logger.error("Update error: %s", exc)
        raise HTTPException(500, f"Update failed: {str(exc)}")
