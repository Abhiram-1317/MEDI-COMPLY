"""Seed data: ICD-10 Cardiology + Diabetes codes."""
from medi_comply.knowledge.icd10_db import ICD10CodeEntry

_E = ICD10CodeEntry  # short alias


def get_cardiology_codes() -> list[ICD10CodeEntry]:
    """Return 30+ real ICD-10-CM cardiology codes."""
    return [
        _E(code="I21.0", description="ST elevation myocardial infarction involving left main coronary artery", chapter="9", chapter_title="Diseases of the circulatory system (I00-I99)", block="I20-I25", block_title="Ischemic heart diseases", category="I21", category_title="ST elevation and non-ST elevation myocardial infarction", is_billable=True, parent_code="I21", child_codes=["I21.01","I21.02","I21.09"], excludes1=[], use_additional=[]),
        _E(code="I21.01", description="ST elevation myocardial infarction involving left main coronary artery", chapter="9", block="I20-I25", category="I21", is_billable=True, parent_code="I21.0"),
        _E(code="I21.02", description="ST elevation myocardial infarction involving left anterior descending coronary artery", chapter="9", block="I20-I25", category="I21", is_billable=True, parent_code="I21.0"),
        _E(code="I21.09", description="ST elevation myocardial infarction involving other coronary artery of anterior wall", chapter="9", block="I20-I25", category="I21", is_billable=True, parent_code="I21.0"),
        _E(code="I21.11", description="ST elevation myocardial infarction involving right coronary artery", chapter="9", block="I20-I25", category="I21", is_billable=True, parent_code="I21"),
        _E(code="I21.19", description="ST elevation myocardial infarction involving other coronary artery of inferior wall", chapter="9", block="I20-I25", category="I21", is_billable=True, parent_code="I21"),
        _E(code="I21.21", description="ST elevation myocardial infarction involving left circumflex coronary artery", chapter="9", block="I20-I25", category="I21", is_billable=True, parent_code="I21"),
        _E(code="I21.29", description="ST elevation myocardial infarction involving other sites", chapter="9", block="I20-I25", category="I21", is_billable=True, parent_code="I21"),
        _E(code="I21.3", description="ST elevation myocardial infarction of unspecified site", chapter="9", block="I20-I25", category="I21", is_billable=True, parent_code="I21"),
        _E(code="I21.4", description="Non-ST elevation (NSTEMI) myocardial infarction", chapter="9", block="I20-I25", category="I21", is_billable=True, parent_code="I21"),
        _E(code="I21", description="ST elevation and non-ST elevation myocardial infarction", chapter="9", block="I20-I25", category="I21", is_billable=False, child_codes=["I21.0","I21.01","I21.02","I21.09","I21.11","I21.19","I21.21","I21.29","I21.3","I21.4"]),
        _E(code="I25.10", description="Atherosclerotic heart disease of native coronary artery without angina pectoris", chapter="9", block="I20-I25", category="I25", is_billable=True, parent_code="I25"),
        _E(code="I25.110", description="Atherosclerotic heart disease of native coronary artery with unstable angina pectoris", chapter="9", block="I20-I25", category="I25", is_billable=True, parent_code="I25.10"),
        _E(code="I25.111", description="Atherosclerotic heart disease of native coronary artery with angina pectoris with documented spasm", chapter="9", block="I20-I25", category="I25", is_billable=True, parent_code="I25.10"),
        _E(code="I25.118", description="Atherosclerotic heart disease of native coronary artery with other forms of angina pectoris", chapter="9", block="I20-I25", category="I25", is_billable=True, parent_code="I25.10"),
        _E(code="I25.119", description="Atherosclerotic heart disease of native coronary artery with unspecified angina pectoris", chapter="9", block="I20-I25", category="I25", is_billable=True, parent_code="I25.10"),
        _E(code="I10", description="Essential (primary) hypertension", chapter="9", chapter_title="Diseases of the circulatory system (I00-I99)", block="I10-I16", block_title="Hypertensive diseases", category="I10", is_billable=True, excludes1=["I11.xx","I13.xx"]),
        _E(code="I11.0", description="Hypertensive heart disease with heart failure", chapter="9", block="I10-I16", category="I11", is_billable=True, parent_code="I11", use_additional=["Use additional code to identify type of heart failure (I50.-)"], excludes1=["I10"]),
        _E(code="I11.9", description="Hypertensive heart disease without heart failure", chapter="9", block="I10-I16", category="I11", is_billable=True, parent_code="I11", excludes1=["I10"]),
        _E(code="I11", description="Hypertensive heart disease", chapter="9", block="I10-I16", category="I11", is_billable=False, child_codes=["I11.0","I11.9"], excludes1=["I10"]),
        _E(code="I13.0", description="Hypertensive heart and chronic kidney disease with heart failure and stage 1-4 or unspecified CKD", chapter="9", block="I10-I16", category="I13", is_billable=True, parent_code="I13", use_additional=["Use additional code to identify type of heart failure (I50.-)", "Use additional code to identify stage of CKD (N18.1-N18.4, N18.9)"], excludes1=["I10"]),
        _E(code="I13.10", description="Hypertensive heart and chronic kidney disease without heart failure, with stage 1-4 or unspecified CKD", chapter="9", block="I10-I16", category="I13", is_billable=True, parent_code="I13", use_additional=["Use additional code to identify stage of CKD (N18.-)"], excludes1=["I10"]),
        _E(code="I13.11", description="Hypertensive heart and chronic kidney disease without heart failure, with stage 5 CKD or ESRD", chapter="9", block="I10-I16", category="I13", is_billable=True, parent_code="I13", excludes1=["I10"]),
        _E(code="I13.2", description="Hypertensive heart and chronic kidney disease with heart failure and stage 5 CKD or ESRD", chapter="9", block="I10-I16", category="I13", is_billable=True, parent_code="I13", excludes1=["I10"]),
        _E(code="I48.0", description="Paroxysmal atrial fibrillation", chapter="9", block="I30-I52", category="I48", is_billable=True, parent_code="I48"),
        _E(code="I48.1", description="Persistent atrial fibrillation", chapter="9", block="I30-I52", category="I48", is_billable=True, parent_code="I48"),
        _E(code="I48.2", description="Chronic atrial fibrillation", chapter="9", block="I30-I52", category="I48", is_billable=True, parent_code="I48"),
        _E(code="I48.91", description="Unspecified atrial fibrillation", chapter="9", block="I30-I52", category="I48", is_billable=True, parent_code="I48"),
        _E(code="I50.1", description="Left ventricular failure, unspecified", chapter="9", block="I30-I52", category="I50", is_billable=True, parent_code="I50"),
        _E(code="I50.20", description="Unspecified systolic (congestive) heart failure", chapter="9", block="I30-I52", category="I50", is_billable=True, parent_code="I50"),
        _E(code="I50.21", description="Acute systolic (congestive) heart failure", chapter="9", block="I30-I52", category="I50", is_billable=True, parent_code="I50"),
        _E(code="I50.22", description="Chronic systolic (congestive) heart failure", chapter="9", block="I30-I52", category="I50", is_billable=True, parent_code="I50"),
        _E(code="I50.23", description="Acute on chronic systolic (congestive) heart failure", chapter="9", block="I30-I52", category="I50", is_billable=True, parent_code="I50"),
        _E(code="I63.30", description="Cerebral infarction due to thrombosis of unspecified cerebral artery", chapter="9", block="I60-I69", category="I63", is_billable=True, parent_code="I63"),
        _E(code="I63.311", description="Cerebral infarction due to thrombosis of right middle cerebral artery", chapter="9", block="I60-I69", category="I63", is_billable=True, parent_code="I63"),
        _E(code="I63.312", description="Cerebral infarction due to thrombosis of left middle cerebral artery", chapter="9", block="I60-I69", category="I63", is_billable=True, parent_code="I63"),
    ]


def get_diabetes_codes() -> list[ICD10CodeEntry]:
    """Return 40+ real ICD-10-CM diabetes codes with Excludes1 relationships."""
    excl_t1 = ["E10.xx"]
    excl_t2 = ["E11.xx"]
    ch = "4"
    ct = "Endocrine, nutritional and metabolic diseases (E00-E89)"
    bl = "E08-E13"
    bt = "Diabetes mellitus"
    return [
        # Type 2 category
        _E(code="E11", description="Type 2 diabetes mellitus", chapter=ch, chapter_title=ct, block=bl, block_title=bt, category="E11", category_title="Type 2 diabetes mellitus", is_billable=False, child_codes=["E11.00","E11.01","E11.10","E11.11","E11.21","E11.22","E11.29","E11.311","E11.319","E11.321","E11.329","E11.40","E11.41","E11.42","E11.43","E11.44","E11.49","E11.51","E11.52","E11.610","E11.618","E11.620","E11.621","E11.622","E11.65","E11.69","E11.8","E11.9"], excludes1=excl_t1),
        _E(code="E11.00", description="Type 2 diabetes mellitus with hyperosmolarity without nonketotic hyperglycemic-hyperosmolar coma", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.01", description="Type 2 diabetes mellitus with hyperosmolarity with coma", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.10", description="Type 2 diabetes mellitus with ketoacidosis without coma", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.11", description="Type 2 diabetes mellitus with ketoacidosis with coma", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.21", description="Type 2 diabetes mellitus with diabetic nephropathy", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11.2", excludes1=excl_t1, use_additional=["Use additional code to identify stage of CKD (N18.-)"]),
        _E(code="E11.22", description="Type 2 diabetes mellitus with diabetic chronic kidney disease", long_description="Type 2 diabetes mellitus with diabetic chronic kidney disease. Use additional code to identify stage of chronic kidney disease.", chapter=ch, chapter_title=ct, block=bl, block_title=bt, category="E11", category_title="Type 2 diabetes mellitus", is_billable=True, parent_code="E11.2", excludes1=excl_t1, use_additional=["Use additional code N18.- to identify stage of CKD"]),
        _E(code="E11.29", description="Type 2 diabetes mellitus with other diabetic kidney complication", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11.2", excludes1=excl_t1),
        _E(code="E11.2", description="Type 2 diabetes mellitus with kidney complications", chapter=ch, block=bl, category="E11", is_billable=False, parent_code="E11", child_codes=["E11.21","E11.22","E11.29"], excludes1=excl_t1),
        _E(code="E11.311", description="Type 2 diabetes mellitus with unspecified diabetic retinopathy with macular edema", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.319", description="Type 2 diabetes mellitus with unspecified diabetic retinopathy without macular edema", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.321", description="Type 2 diabetes mellitus with mild nonproliferative diabetic retinopathy with macular edema", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.329", description="Type 2 diabetes mellitus with mild nonproliferative diabetic retinopathy without macular edema", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.40", description="Type 2 diabetes mellitus with diabetic neuropathy, unspecified", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.41", description="Type 2 diabetes mellitus with diabetic mononeuropathy", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.42", description="Type 2 diabetes mellitus with diabetic polyneuropathy", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.43", description="Type 2 diabetes mellitus with diabetic autonomic (poly)neuropathy", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.44", description="Type 2 diabetes mellitus with diabetic amyotrophy", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.49", description="Type 2 diabetes mellitus with other diabetic neurological complication", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.51", description="Type 2 diabetes mellitus with diabetic peripheral angiopathy without gangrene", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.52", description="Type 2 diabetes mellitus with diabetic peripheral angiopathy with gangrene", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.610", description="Type 2 diabetes mellitus with diabetic neuropathic arthropathy", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.618", description="Type 2 diabetes mellitus with other diabetic arthropathy", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.620", description="Type 2 diabetes mellitus with diabetic dermatitis", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.621", description="Type 2 diabetes mellitus with foot ulcer", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.622", description="Type 2 diabetes mellitus with other skin ulcer", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.65", description="Type 2 diabetes mellitus with hyperglycemia", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.69", description="Type 2 diabetes mellitus with other specified complication", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.8", description="Type 2 diabetes mellitus with unspecified complications", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        _E(code="E11.9", description="Type 2 diabetes mellitus without complications", chapter=ch, block=bl, category="E11", is_billable=True, parent_code="E11", excludes1=excl_t1),
        # Type 1 (for excludes1 testing)
        _E(code="E10.10", description="Type 1 diabetes mellitus with ketoacidosis without coma", chapter=ch, block=bl, category="E10", is_billable=True, parent_code="E10", excludes1=excl_t2),
        _E(code="E10.11", description="Type 1 diabetes mellitus with ketoacidosis with coma", chapter=ch, block=bl, category="E10", is_billable=True, parent_code="E10", excludes1=excl_t2),
        _E(code="E10.21", description="Type 1 diabetes mellitus with diabetic nephropathy", chapter=ch, block=bl, category="E10", is_billable=True, parent_code="E10", excludes1=excl_t2),
        _E(code="E10.22", description="Type 1 diabetes mellitus with diabetic chronic kidney disease", chapter=ch, block=bl, category="E10", is_billable=True, parent_code="E10", excludes1=excl_t2),
        _E(code="E10.9", description="Type 1 diabetes mellitus without complications", chapter=ch, block=bl, category="E10", is_billable=True, parent_code="E10", excludes1=excl_t2),
        _E(code="E13.9", description="Other specified diabetes mellitus without complications", chapter=ch, block=bl, category="E13", is_billable=True, parent_code="E13"),
    ]
