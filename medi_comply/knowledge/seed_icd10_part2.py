"""Seed data: ICD-10 Nephrology, Pulmonology, Symptoms, and Injuries codes."""
from medi_comply.knowledge.icd10_db import ICD10CodeEntry

_E = ICD10CodeEntry


def get_nephrology_codes() -> list[ICD10CodeEntry]:
    """Return 20+ real ICD-10-CM nephrology codes."""
    return [
        _E(code="N18.1", description="Chronic kidney disease, stage 1", chapter="14", block="N17-N19", category="N18", is_billable=True, parent_code="N18", code_first=["Code first underlying condition (E08-E13 with .22)"]),
        _E(code="N18.2", description="Chronic kidney disease, stage 2 (mild)", chapter="14", block="N17-N19", category="N18", is_billable=True, parent_code="N18", code_first=["Code first underlying condition (E08-E13 with .22)"]),
        _E(code="N18.30", description="Chronic kidney disease, stage 3 unspecified", chapter="14", block="N17-N19", category="N18", is_billable=True, parent_code="N18", code_first=["Code first underlying condition (E08-E13 with .22)"]),
        _E(code="N18.31", description="Chronic kidney disease, stage 3a", chapter="14", block="N17-N19", category="N18", is_billable=True, parent_code="N18", code_first=["Code first underlying condition (E08-E13 with .22)"]),
        _E(code="N18.32", description="Chronic kidney disease, stage 3b", chapter="14", block="N17-N19", category="N18", is_billable=True, parent_code="N18", code_first=["Code first underlying condition (E08-E13 with .22)"]),
        _E(code="N18.4", description="Chronic kidney disease, stage 4 (severe)", chapter="14", block="N17-N19", category="N18", is_billable=True, parent_code="N18", code_first=["Code first underlying condition (E08-E13 with .22)"]),
        _E(code="N18.5", description="Chronic kidney disease, stage 5", chapter="14", block="N17-N19", category="N18", is_billable=True, parent_code="N18", code_first=["Code first underlying condition (E08-E13 with .22)"]),
        _E(code="N18.6", description="End stage renal disease", chapter="14", block="N17-N19", category="N18", is_billable=True, parent_code="N18", code_first=["Code first underlying condition (E08-E13 with .22)"]),
        _E(code="N18.9", description="Chronic kidney disease, unspecified", chapter="14", block="N17-N19", category="N18", is_billable=True, parent_code="N18", code_first=["Code first underlying condition (E08-E13 with .22)"]),
        _E(code="N18", description="Chronic kidney disease (CKD)", chapter="14", block="N17-N19", category="N18", is_billable=False, child_codes=["N18.1","N18.2","N18.30","N18.31","N18.32","N18.4","N18.5","N18.6","N18.9"], excludes1=["N17.xx"]),
        _E(code="N17.0", description="Acute kidney failure with tubular necrosis", chapter="14", block="N17-N19", category="N17", is_billable=True, parent_code="N17", excludes1=["N18.xx"]),
        _E(code="N17.1", description="Acute kidney failure with acute cortical necrosis", chapter="14", block="N17-N19", category="N17", is_billable=True, parent_code="N17", excludes1=["N18.xx"]),
        _E(code="N17.2", description="Acute kidney failure with medullary necrosis", chapter="14", block="N17-N19", category="N17", is_billable=True, parent_code="N17", excludes1=["N18.xx"]),
        _E(code="N17.9", description="Acute kidney failure, unspecified", chapter="14", block="N17-N19", category="N17", is_billable=True, parent_code="N17", excludes1=["N18.xx"]),
        _E(code="N04.0", description="Nephrotic syndrome with minor glomerular abnormality", chapter="14", block="N00-N08", category="N04", is_billable=True, parent_code="N04"),
        _E(code="N04.1", description="Nephrotic syndrome with focal and segmental glomerular lesions", chapter="14", block="N00-N08", category="N04", is_billable=True, parent_code="N04"),
        _E(code="N04.2", description="Nephrotic syndrome with diffuse membranous glomerulonephritis", chapter="14", block="N00-N08", category="N04", is_billable=True, parent_code="N04"),
        _E(code="N04.9", description="Nephrotic syndrome with unspecified morphologic changes", chapter="14", block="N00-N08", category="N04", is_billable=True, parent_code="N04"),
        _E(code="N39.0", description="Urinary tract infection, site not specified", chapter="14", block="N30-N39", category="N39", is_billable=True),
    ]


def get_pulmonology_codes() -> list[ICD10CodeEntry]:
    """Return 20+ real ICD-10-CM pulmonology codes."""
    return [
        _E(code="J18.0", description="Bronchopneumonia, unspecified organism", chapter="10", block="J12-J18", category="J18", is_billable=True, parent_code="J18"),
        _E(code="J18.1", description="Lobar pneumonia, unspecified organism", chapter="10", block="J12-J18", category="J18", is_billable=True, parent_code="J18"),
        _E(code="J18.9", description="Pneumonia, unspecified organism", chapter="10", block="J12-J18", category="J18", is_billable=True, parent_code="J18"),
        _E(code="J44.0", description="Chronic obstructive pulmonary disease with acute lower respiratory infection", chapter="10", block="J40-J47", category="J44", is_billable=True, parent_code="J44", excludes1=["J44.1"], use_additional=["Use additional code to identify the infection"]),
        _E(code="J44.1", description="Chronic obstructive pulmonary disease with (acute) exacerbation", chapter="10", block="J40-J47", category="J44", is_billable=True, parent_code="J44", excludes1=["J44.0"]),
        _E(code="J44.9", description="Chronic obstructive pulmonary disease, unspecified", chapter="10", block="J40-J47", category="J44", is_billable=True, parent_code="J44"),
        _E(code="J45.20", description="Mild intermittent asthma, uncomplicated", chapter="10", block="J40-J47", category="J45", is_billable=True, parent_code="J45"),
        _E(code="J45.21", description="Mild intermittent asthma with (acute) exacerbation", chapter="10", block="J40-J47", category="J45", is_billable=True, parent_code="J45"),
        _E(code="J45.22", description="Mild intermittent asthma with status asthmaticus", chapter="10", block="J40-J47", category="J45", is_billable=True, parent_code="J45"),
        _E(code="J45.30", description="Mild persistent asthma, uncomplicated", chapter="10", block="J40-J47", category="J45", is_billable=True, parent_code="J45"),
        _E(code="J45.31", description="Mild persistent asthma with (acute) exacerbation", chapter="10", block="J40-J47", category="J45", is_billable=True, parent_code="J45"),
        _E(code="J96.00", description="Acute respiratory failure, unspecified whether with hypoxia or hypercapnia", chapter="10", block="J96-J99", category="J96", is_billable=True, parent_code="J96"),
        _E(code="J96.01", description="Acute respiratory failure with hypoxia", chapter="10", block="J96-J99", category="J96", is_billable=True, parent_code="J96"),
        _E(code="J96.02", description="Acute respiratory failure with hypercapnia", chapter="10", block="J96-J99", category="J96", is_billable=True, parent_code="J96"),
        _E(code="J80", description="Acute respiratory distress syndrome", chapter="10", block="J80-J84", category="J80", is_billable=True),
        _E(code="U07.1", description="COVID-19", chapter="22", block="U00-U49", category="U07", is_billable=True, use_additional=["Use additional code to identify pneumonia or other manifestations"]),
    ]


def get_symptom_codes() -> list[ICD10CodeEntry]:
    """Return 20+ real ICD-10-CM symptom and sign codes."""
    return [
        _E(code="R07.1", description="Chest pain on breathing", chapter="18", block="R00-R09", category="R07", is_billable=True, parent_code="R07"),
        _E(code="R07.2", description="Precordial pain", chapter="18", block="R00-R09", category="R07", is_billable=True, parent_code="R07"),
        _E(code="R07.9", description="Chest pain, unspecified", chapter="18", block="R00-R09", category="R07", is_billable=True, parent_code="R07"),
        _E(code="R06.00", description="Dyspnea, unspecified", chapter="18", block="R00-R09", category="R06", is_billable=True, parent_code="R06"),
        _E(code="R06.02", description="Shortness of breath", chapter="18", block="R00-R09", category="R06", is_billable=True, parent_code="R06"),
        _E(code="R06.09", description="Other forms of dyspnea", chapter="18", block="R00-R09", category="R06", is_billable=True, parent_code="R06"),
        _E(code="R50.9", description="Fever, unspecified", chapter="18", block="R50-R69", category="R50", is_billable=True),
        _E(code="R51.0", description="Headache with orthostatic component, not elsewhere classified", chapter="18", block="R50-R69", category="R51", is_billable=True, parent_code="R51"),
        _E(code="R51.9", description="Headache, unspecified", chapter="18", block="R50-R69", category="R51", is_billable=True, parent_code="R51"),
        _E(code="R00.0", description="Tachycardia, unspecified", chapter="18", block="R00-R09", category="R00", is_billable=True, parent_code="R00"),
        _E(code="R00.1", description="Bradycardia, unspecified", chapter="18", block="R00-R09", category="R00", is_billable=True, parent_code="R00"),
        _E(code="R55", description="Syncope and collapse", chapter="18", block="R50-R69", category="R55", is_billable=True),
        _E(code="R40.20", description="Unspecified coma", chapter="18", block="R40-R46", category="R40", is_billable=True, parent_code="R40"),
        _E(code="R09.02", description="Hypoxemia", chapter="18", block="R00-R09", category="R09", is_billable=True),
        _E(code="R03.0", description="Elevated blood-pressure reading without diagnosis of hypertension", chapter="18", block="R00-R09", category="R03", is_billable=True),
        _E(code="R73.01", description="Impaired fasting glucose", chapter="18", block="R70-R79", category="R73", is_billable=True),
        _E(code="R73.02", description="Impaired glucose tolerance (oral)", chapter="18", block="R70-R79", category="R73", is_billable=True),
        _E(code="R11.0", description="Nausea", chapter="18", block="R10-R19", category="R11", is_billable=True),
        _E(code="R11.10", description="Vomiting, unspecified", chapter="18", block="R10-R19", category="R11", is_billable=True),
        _E(code="R42", description="Dizziness and giddiness", chapter="18", block="R40-R46", category="R42", is_billable=True),
    ]


def get_injury_codes() -> list[ICD10CodeEntry]:
    """Return 15+ real ICD-10-CM injury codes with 7th character handling."""
    seventh_chars = {"A": "initial encounter", "D": "subsequent encounter", "S": "sequela"}
    return [
        _E(code="S72.001A", description="Fracture of unspecified part of neck of right femur, initial encounter", chapter="19", block="S70-S79", category="S72", is_billable=True, parent_code="S72", requires_7th_character=True, seventh_characters=seventh_chars),
        _E(code="S72.001D", description="Fracture of unspecified part of neck of right femur, subsequent encounter", chapter="19", block="S70-S79", category="S72", is_billable=True, parent_code="S72", requires_7th_character=True, seventh_characters=seventh_chars),
        _E(code="S72.001S", description="Fracture of unspecified part of neck of right femur, sequela", chapter="19", block="S70-S79", category="S72", is_billable=True, parent_code="S72", requires_7th_character=True, seventh_characters=seventh_chars),
        _E(code="S52.501A", description="Unspecified fracture of the lower end of right radius, initial encounter", chapter="19", block="S50-S59", category="S52", is_billable=True, parent_code="S52", requires_7th_character=True, seventh_characters=seventh_chars),
        _E(code="S52.501D", description="Unspecified fracture of the lower end of right radius, subsequent encounter", chapter="19", block="S50-S59", category="S52", is_billable=True, parent_code="S52", requires_7th_character=True, seventh_characters=seventh_chars),
        _E(code="S06.0X0A", description="Concussion without loss of consciousness, initial encounter", chapter="19", block="S00-S09", category="S06", is_billable=True, parent_code="S06", requires_7th_character=True, seventh_characters=seventh_chars),
        _E(code="S06.0X1A", description="Concussion with loss of consciousness of 30 minutes or less, initial encounter", chapter="19", block="S00-S09", category="S06", is_billable=True, parent_code="S06", requires_7th_character=True, seventh_characters=seventh_chars),
        _E(code="S42.001A", description="Fracture of unspecified part of right clavicle, initial encounter", chapter="19", block="S40-S49", category="S42", is_billable=True, requires_7th_character=True, seventh_characters=seventh_chars),
        _E(code="S82.001A", description="Unspecified fracture of right patella, initial encounter", chapter="19", block="S80-S89", category="S82", is_billable=True, requires_7th_character=True, seventh_characters=seventh_chars),
        _E(code="S62.001A", description="Unspecified fracture of right navicular bone of wrist, initial encounter", chapter="19", block="S60-S69", category="S62", is_billable=True, requires_7th_character=True, seventh_characters=seventh_chars),
        # Gender/age-specific codes for validation testing
        _E(code="O80", description="Encounter for full-term uncomplicated delivery", chapter="15", block="O80-O82", category="O80", is_billable=True, valid_for_gender="FEMALE", valid_age_range=(12, 55)),
        _E(code="P07.14", description="Other low birth weight newborn, 1500-1749 grams", chapter="16", block="P05-P08", category="P07", is_billable=True, valid_age_range=(0, 0)),
        _E(code="N40.0", description="Benign prostatic hyperplasia without lower urinary tract symptoms", chapter="14", block="N40-N53", category="N40", is_billable=True, valid_for_gender="MALE"),
        _E(code="Z87.39", description="Personal history of other diseases of the musculoskeletal system and connective tissue", chapter="21", block="Z77-Z99", category="Z87", is_billable=True),
        _E(code="Z23", description="Encounter for immunization", chapter="21", block="Z20-Z29", category="Z23", is_billable=True),
    ]
