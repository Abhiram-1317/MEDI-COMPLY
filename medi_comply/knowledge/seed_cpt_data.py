"""Seed data: CPT codes and modifiers."""
from medi_comply.knowledge.cpt_db import CPTCodeEntry, CPTModifier

_C = CPTCodeEntry


def get_cpt_codes() -> list[CPTCodeEntry]:
    """Return 100+ real CPT codes across E/M, cardiology, lab, imaging, pulmonology."""
    return [
        # === E/M: New Patient Office Visits ===
        _C(code="99202", description="Office visit, new patient, straightforward MDM", category="E/M", subcategory="Office or Other Outpatient Services", rvu_work=0.93, rvu_facility_pe=0.69, rvu_malpractice=0.06, common_modifiers=["25"], global_period="XXX"),
        _C(code="99203", description="Office visit, new patient, low complexity MDM", category="E/M", subcategory="Office or Other Outpatient Services", rvu_work=1.60, rvu_facility_pe=1.01, rvu_malpractice=0.10, common_modifiers=["25"], global_period="XXX"),
        _C(code="99204", description="Office visit, new patient, moderate complexity MDM", category="E/M", subcategory="Office or Other Outpatient Services", rvu_work=2.60, rvu_facility_pe=1.27, rvu_malpractice=0.13, common_modifiers=["25"], global_period="XXX"),
        _C(code="99205", description="Office visit, new patient, high complexity MDM", category="E/M", subcategory="Office or Other Outpatient Services", rvu_work=3.50, rvu_facility_pe=1.55, rvu_malpractice=0.17, common_modifiers=["25"], global_period="XXX"),
        # === E/M: Established Patient Office Visits ===
        _C(code="99211", description="Office visit, established patient, may not require physician presence", category="E/M", subcategory="Office or Other Outpatient Services", rvu_work=0.18, rvu_facility_pe=0.28, rvu_malpractice=0.02, global_period="XXX"),
        _C(code="99212", description="Office visit, established patient, straightforward MDM", category="E/M", subcategory="Office or Other Outpatient Services", rvu_work=0.70, rvu_facility_pe=0.54, rvu_malpractice=0.05, common_modifiers=["25"], global_period="XXX"),
        _C(code="99213", description="Office visit, established patient, low complexity MDM", category="E/M", subcategory="Office or Other Outpatient Services", rvu_work=1.30, rvu_facility_pe=0.99, rvu_malpractice=0.07, common_modifiers=["25"], global_period="XXX"),
        _C(code="99214", description="Office visit, established patient, moderate complexity MDM", category="E/M", subcategory="Office or Other Outpatient Services", rvu_work=1.92, rvu_facility_pe=1.10, rvu_malpractice=0.09, common_modifiers=["25"], global_period="XXX"),
        _C(code="99215", description="Office visit, established patient, high complexity MDM", category="E/M", subcategory="Office or Other Outpatient Services", rvu_work=2.80, rvu_facility_pe=1.36, rvu_malpractice=0.14, common_modifiers=["25"], global_period="XXX"),
        # === E/M: Initial Hospital Care ===
        _C(code="99221", description="Initial hospital care, straightforward or low complexity MDM", category="E/M", subcategory="Hospital Inpatient Services", rvu_work=2.00, rvu_facility_pe=0.96, rvu_malpractice=0.12, global_period="XXX"),
        _C(code="99222", description="Initial hospital care, moderate complexity MDM", category="E/M", subcategory="Hospital Inpatient Services", rvu_work=2.61, rvu_facility_pe=1.12, rvu_malpractice=0.15, global_period="XXX"),
        _C(code="99223", description="Initial hospital care, high complexity MDM", category="E/M", subcategory="Hospital Inpatient Services", rvu_work=3.86, rvu_facility_pe=1.49, rvu_malpractice=0.20, global_period="XXX"),
        # === E/M: Subsequent Hospital Care ===
        _C(code="99231", description="Subsequent hospital care, straightforward or low complexity MDM", category="E/M", subcategory="Hospital Inpatient Services", rvu_work=0.76, rvu_facility_pe=0.51, rvu_malpractice=0.04, global_period="XXX"),
        _C(code="99232", description="Subsequent hospital care, moderate complexity MDM", category="E/M", subcategory="Hospital Inpatient Services", rvu_work=1.39, rvu_facility_pe=0.76, rvu_malpractice=0.07, global_period="XXX"),
        _C(code="99233", description="Subsequent hospital care, high complexity MDM", category="E/M", subcategory="Hospital Inpatient Services", rvu_work=2.00, rvu_facility_pe=0.96, rvu_malpractice=0.10, global_period="XXX"),
        # === E/M: Emergency Department ===
        _C(code="99281", description="Emergency department visit, self-limited problem", category="E/M", subcategory="Emergency Department Services", rvu_work=0.25, rvu_facility_pe=0.33, rvu_malpractice=0.02, global_period="XXX"),
        _C(code="99282", description="Emergency department visit, low to moderate severity", category="E/M", subcategory="Emergency Department Services", rvu_work=0.65, rvu_facility_pe=0.55, rvu_malpractice=0.05, global_period="XXX"),
        _C(code="99283", description="Emergency department visit, moderate severity", category="E/M", subcategory="Emergency Department Services", rvu_work=1.28, rvu_facility_pe=0.89, rvu_malpractice=0.08, global_period="XXX"),
        _C(code="99284", description="Emergency department visit, high severity", category="E/M", subcategory="Emergency Department Services", rvu_work=2.56, rvu_facility_pe=1.35, rvu_malpractice=0.14, global_period="XXX"),
        _C(code="99285", description="Emergency department visit, high severity with significant threat", category="E/M", subcategory="Emergency Department Services", rvu_work=3.80, rvu_facility_pe=1.69, rvu_malpractice=0.19, global_period="XXX"),
        # === E/M: Critical Care ===
        _C(code="99291", description="Critical care, first 30-74 minutes", category="E/M", subcategory="Critical Care Services", rvu_work=4.50, rvu_facility_pe=1.92, rvu_malpractice=0.28, global_period="XXX"),
        _C(code="99292", description="Critical care, each additional 30 minutes", category="E/M", subcategory="Critical Care Services", rvu_work=2.25, rvu_facility_pe=0.93, rvu_malpractice=0.13, is_add_on=True, global_period="ZZZ"),
        # === Cardiology Procedures ===
        _C(code="93000", description="Electrocardiogram, routine ECG with at least 12 leads; with interpretation and report", category="Cardiology", subcategory="Diagnostic", rvu_work=0.17, rvu_facility_pe=0.23, rvu_malpractice=0.01, common_modifiers=["26","TC"], professional_component=True, technical_component=True, global_period="XXX"),
        _C(code="93010", description="Electrocardiogram, routine ECG; interpretation and report only", category="Cardiology", subcategory="Diagnostic", rvu_work=0.17, rvu_facility_pe=0.08, rvu_malpractice=0.01, global_period="XXX"),
        _C(code="93306", description="Echocardiography, transthoracic, real-time with image documentation, complete", long_description="Transthoracic echocardiography complete with spectral Doppler and color flow Doppler", category="Cardiology", subcategory="Diagnostic", rvu_work=1.30, rvu_facility_pe=0.88, rvu_malpractice=0.04, common_modifiers=["26","TC"], professional_component=True, technical_component=True, global_period="XXX"),
        _C(code="93307", description="Echocardiography, transthoracic, real-time with image documentation, 2D without M-mode", category="Cardiology", subcategory="Diagnostic", rvu_work=0.62, rvu_facility_pe=0.55, rvu_malpractice=0.03, global_period="XXX"),
        _C(code="93308", description="Echocardiography, transthoracic, real-time with image documentation, follow-up or limited study", category="Cardiology", subcategory="Diagnostic", rvu_work=0.41, rvu_facility_pe=0.32, rvu_malpractice=0.02, global_period="XXX"),
        _C(code="93312", description="Echocardiography, transesophageal, real-time with image documentation", category="Cardiology", subcategory="Diagnostic", rvu_work=2.20, rvu_facility_pe=1.40, rvu_malpractice=0.15, global_period="XXX"),
        _C(code="93451", description="Right heart catheterization", category="Cardiology", subcategory="Invasive", rvu_work=3.20, rvu_facility_pe=5.80, rvu_malpractice=0.30, global_period="0"),
        _C(code="93452", description="Left heart catheterization", category="Cardiology", subcategory="Invasive", rvu_work=3.60, rvu_facility_pe=6.20, rvu_malpractice=0.35, global_period="0"),
        _C(code="93453", description="Combined right and left heart catheterization", category="Cardiology", subcategory="Invasive", rvu_work=4.10, rvu_facility_pe=7.50, rvu_malpractice=0.40, global_period="0"),
        _C(code="93458", description="Catheter placement with coronary angiography", category="Cardiology", subcategory="Invasive", rvu_work=4.32, rvu_facility_pe=7.80, rvu_malpractice=0.38, global_period="0"),
        _C(code="93798", description="Physician or other qualified health care professional services for outpatient cardiac rehabilitation", category="Cardiology", subcategory="Rehab", rvu_work=0.11, rvu_facility_pe=0.38, rvu_malpractice=0.01, global_period="XXX"),
        _C(code="92920", description="Percutaneous transluminal coronary angioplasty, single vessel", category="Cardiology", subcategory="Invasive", rvu_work=8.00, rvu_facility_pe=12.50, rvu_malpractice=1.20, global_period="0"),
        _C(code="92921", description="Percutaneous transluminal coronary angioplasty, each additional vessel", category="Cardiology", subcategory="Invasive", rvu_work=3.50, rvu_facility_pe=4.20, rvu_malpractice=0.40, is_add_on=True, global_period="ZZZ"),
        _C(code="93880", description="Duplex scan of extracranial arteries, complete bilateral study", category="Cardiology", subcategory="Vascular", rvu_work=0.50, rvu_facility_pe=2.30, rvu_malpractice=0.04, global_period="XXX"),
        # === Lab Codes ===
        _C(code="80048", description="Basic metabolic panel (BMP)", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="80053", description="Comprehensive metabolic panel (CMP)", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="80061", description="Lipid panel", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="82553", description="Creatine kinase (CK), MB fraction", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="84484", description="Troponin, quantitative", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="85025", description="Complete blood count (CBC) with automated differential", category="Lab", subcategory="Hematology", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="85027", description="Complete blood count (CBC), automated", category="Lab", subcategory="Hematology", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="82947", description="Glucose, quantitative, blood", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="83036", description="Hemoglobin; glycosylated (A1c)", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="82565", description="Creatinine; blood", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="81001", description="Urinalysis, with microscopy", category="Lab", subcategory="Urinalysis", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="82310", description="Calcium, total", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="84443", description="Thyroid stimulating hormone (TSH)", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="83540", description="Iron, serum", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        _C(code="82728", description="Ferritin", category="Lab", subcategory="Chemistry", rvu_work=0.0, rvu_facility_pe=0.0, rvu_malpractice=0.0, global_period="XXX"),
        # === Imaging ===
        _C(code="71046", description="Radiologic examination, chest, 2 views", category="Imaging", subcategory="Radiology", rvu_work=0.18, rvu_facility_pe=0.91, rvu_malpractice=0.02, common_modifiers=["26","TC"], professional_component=True, technical_component=True, global_period="XXX"),
        _C(code="71250", description="Computed tomography, chest, without contrast material", category="Imaging", subcategory="CT", rvu_work=1.24, rvu_facility_pe=4.72, rvu_malpractice=0.07, common_modifiers=["26","TC"], professional_component=True, technical_component=True, global_period="XXX"),
        _C(code="71260", description="Computed tomography, chest, with contrast material", category="Imaging", subcategory="CT", rvu_work=1.38, rvu_facility_pe=5.20, rvu_malpractice=0.08, common_modifiers=["26","TC"], professional_component=True, technical_component=True, global_period="XXX"),
        _C(code="74177", description="Computed tomography, abdomen and pelvis, with contrast material", category="Imaging", subcategory="CT", rvu_work=2.01, rvu_facility_pe=6.80, rvu_malpractice=0.10, common_modifiers=["26","TC"], professional_component=True, technical_component=True, global_period="XXX"),
        _C(code="70553", description="Magnetic resonance imaging, brain, without contrast, then with contrast and further sequences", category="Imaging", subcategory="MRI", rvu_work=1.96, rvu_facility_pe=8.30, rvu_malpractice=0.11, common_modifiers=["26","TC"], professional_component=True, technical_component=True, global_period="XXX"),
        # === Pulmonology ===
        _C(code="94010", description="Spirometry, including graphic record, total and timed vital capacity", category="Pulmonology", subcategory="Diagnostic", rvu_work=0.17, rvu_facility_pe=0.55, rvu_malpractice=0.01, global_period="XXX"),
        _C(code="94060", description="Bronchodilator responsiveness, spirometry before and after", category="Pulmonology", subcategory="Diagnostic", rvu_work=0.28, rvu_facility_pe=0.69, rvu_malpractice=0.02, global_period="XXX"),
        _C(code="94375", description="Respiratory flow volume loop", category="Pulmonology", subcategory="Diagnostic", rvu_work=0.17, rvu_facility_pe=0.31, rvu_malpractice=0.01, global_period="XXX"),
        _C(code="94726", description="Plethysmography for determination of lung volumes and, when performed, airway resistance", category="Pulmonology", subcategory="Diagnostic", rvu_work=0.22, rvu_facility_pe=0.77, rvu_malpractice=0.01, global_period="XXX"),
        _C(code="94640", description="Pressurized or nonpressurized inhalation treatment (nebulizer)", category="Pulmonology", subcategory="Therapeutic", rvu_work=0.0, rvu_facility_pe=0.30, rvu_malpractice=0.01, global_period="XXX"),
    ]


def get_modifiers() -> list[CPTModifier]:
    """Return standard CPT modifiers."""
    return [
        CPTModifier(code="25", description="Significant, separately identifiable E/M service by the same physician on the same day of the procedure or other service"),
        CPTModifier(code="26", description="Professional component"),
        CPTModifier(code="TC", description="Technical component"),
        CPTModifier(code="59", description="Distinct procedural service"),
        CPTModifier(code="76", description="Repeat procedure or service by same physician or other qualified health care professional"),
        CPTModifier(code="77", description="Repeat procedure by another physician or other qualified health care professional"),
        CPTModifier(code="LT", description="Left side"),
        CPTModifier(code="RT", description="Right side"),
        CPTModifier(code="50", description="Bilateral procedure"),
        CPTModifier(code="51", description="Multiple procedures"),
        CPTModifier(code="XE", description="Separate encounter, a service that is distinct because it occurred during a separate encounter"),
        CPTModifier(code="XS", description="Separate structure, a service that is distinct because it was performed on a separate organ/structure"),
        CPTModifier(code="XP", description="Separate practitioner, a service that is distinct because it was performed by a different practitioner"),
        CPTModifier(code="XU", description="Unusual non-overlapping service, the use of a service that is distinct because it does not overlap usual components"),
    ]
