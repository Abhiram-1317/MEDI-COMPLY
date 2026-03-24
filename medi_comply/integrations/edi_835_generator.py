"""
EDI 835 (Healthcare Payment/Remittance Advice) generator and lightweight parser
for MEDI-COMPLY. Builds outbound 835 remittance transactions from adjudication
results and can parse simple inbound 835 payloads. Follows patterns used in the
EDI 837 parser (segment handling, formatting helpers, docstrings, defensive
checks) while remaining dependency-free.
"""
from __future__ import annotations
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class PaymentMethodCode(str, Enum):
    CHECK = "CHK"
    ACH = "ACH"
    FWT = "FWT"  # Federal Reserve Wire Transfer
    NON = "NON"  # Non-payment (zero pay)
class ClaimStatusCode(str, Enum):
    PRIMARY = "1"
    SECONDARY = "2"
    TERTIARY = "3"
    DENIED = "4"
    PRIMARY_FORWARDED = "19"
    SECONDARY_FORWARDED = "20"
    REVERSAL = "22"
class CASGroupCode(str, Enum):
    CONTRACTUAL = "CO"
    PATIENT_RESPONSIBILITY = "PR"
    OTHER_ADJUSTMENT = "OA"
    PAYOR_INITIATED = "PI"
    CORRECTION = "CR"
class AdjustmentReasonCategory(str, Enum):
    DEDUCTIBLE = "deductible"
    COINSURANCE = "coinsurance"
    COPAYMENT = "copayment"
    NOT_COVERED = "not_covered"
    FEE_SCHEDULE = "fee_schedule"
    DUPLICATE = "duplicate"
    TIMELY_FILING = "timely_filing"
    AUTH_REQUIRED = "auth_required"
    NOT_MEDICALLY_NECESSARY = "not_necessary"
    BUNDLED = "bundled"
    BENEFIT_MAXIMUM = "benefit_max"
    COB = "coordination_of_benefits"
    SEQUESTRATION = "sequestration"
    OTHER = "other"
# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
CARC_DESCRIPTIONS: Dict[str, str] = {
    "1": "Deductible amount",
    "2": "Coinsurance amount",
    "3": "Copayment amount",
    "4": "Procedure code inconsistent with modifier",
    "5": "Procedure code/bill type inconsistent with place of service",
    "16": "Claim/service lacks information for adjudication",
    "18": "Exact duplicate claim/service",
    "22": "Care may be covered by another payer (COB)",
    "23": "Impact of prior payer adjudication",
    "27": "Expenses incurred after coverage terminated",
    "29": "Time limit for filing expired",
    "45": "Charge exceeds fee schedule/maximum allowable",
    "50": "Non-covered services (not medically necessary)",
    "96": "Non-covered charge(s)",
    "97": "Benefit included in payment for another service",
    "109": "Claim/service not covered by this payer",
    "119": "Benefit maximum reached",
    "136": "Failure to follow prior authorization guidelines",
    "150": "Information submitted does not support level of service",
    "151": "Information does not support number of services billed",
    "167": "Diagnosis not covered",
    "197": "Precertification/authorization/notification absent",
    "204": "Service not covered under current benefit plan",
    "242": "Service not provided by network provider",
    "253": "Sequestration adjustment",
    "B7": "Provider not certified/eligible on service date",
    "B15": "Qualifying service required",
    "N432": "Adjustment due to correction to prior claim",
    "P1": "State-mandated requirement for property/casualty",
    "18A": "Duplicate line item",
    "22A": "COB indicates other payer primary",
    "59": "Processed as primary, forwarding to additional payer",
    "100": "Payment made to patient/insured",
    "109A": "Contract not in force",
    "11": "Diagnosis inconsistent with procedure",
}
RARC_DESCRIPTIONS: Dict[str, str] = {
    "N30": "Patient not eligible for this service",
    "N362": "Missing/incomplete/invalid prior authorization",
    "N386": "Decision based on National Coverage Determination",
    "N479": "Charges adjusted based on fee schedule",
    "N657": "Not covered with another procedure same day",
    "N699": "Payment based on review of previous denials",
    "MA18": "Denied/rejected due to insufficient information",
    "MA130": "Claim contains incomplete/invalid information",
    "M15": "Separately billed services/tests have been bundled",
    "M20": "Missing/incomplete/invalid HCPCS",
    "M51": "Missing/incomplete/invalid procedure code",
    "M76": "Missing/incomplete/invalid diagnosis code(s)",
    "M77": "Missing/incomplete/invalid place of service",
    "N56": "Missing/incomplete/invalid procedure code",
    "N657": "Not covered with/within time of another service",
    "N704": "Review indicates partial allowance",
    "N781": "Authorization limit reached",
}
PROCEDURE_DESCRIPTIONS: Dict[str, str] = {
    "99213": "Office/outpatient visit est",
    "99214": "Office/outpatient visit est, mod complexity",
    "99223": "Initial hospital care, high complexity",
    "93000": "Electrocardiogram complete",
    "71046": "Chest X-ray",
    "80053": "Comprehensive metabolic panel",
    "84484": "Troponin quantitative",
    "87635": "COVID-19 PCR",
    "12001": "Simple repair superficial wounds",
    "29881": "Knee arthroscopy meniscectomy",
    "66984": "Cataract removal with IOL",
    "36415": "Collection of venous blood",
    "73630": "X-ray ankle",
    "72148": "MRI lumbar spine",
    "87070": "Bacterial culture",
    "90658": "Influenza vaccine",
    "J1100": "Injection dexamethasone",
    "J1885": "Injection ketorolac",
    "J0696": "Injection ceftriaxone",
    "J2250": "Injection midazolam",
    "J7030": "Normal saline infusion",
    "20550": "Injection tendon sheath",
    "20610": "Arthrocentesis major joint",
    "27447": "Total knee arthroplasty",
    "29827": "Arthroscopy shoulder repair",
    "97110": "Therapeutic exercises",
}
# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
class RemittanceAdjustment(BaseModel):
    group_code: str = CASGroupCode.CONTRACTUAL.value
    reason_code: str = ""
    amount: float = 0.0
    quantity: Optional[float] = None
    reason_description: Optional[str] = None
    category: Optional[str] = None
class RemittanceServiceLine(BaseModel):
    procedure_code: str = ""
    modifiers: List[str] = Field(default_factory=list)
    submitted_charge: float = 0.0
    paid_amount: float = 0.0
    revenue_code: Optional[str] = None
    units_paid: float = 1.0
    units_submitted: float = 1.0
    adjustments: List[RemittanceAdjustment] = Field(default_factory=list)
    service_date: Optional[str] = None
    service_end_date: Optional[str] = None
    remark_codes: List[str] = Field(default_factory=list)
    allowed_amount: Optional[float] = None
    deductible_amount: float = 0.0
    coinsurance_amount: float = 0.0
    copay_amount: float = 0.0
    line_item_control_number: Optional[str] = None
class RemittanceClaim(BaseModel):
    claim_id: str = ""
    claim_status: str = ClaimStatusCode.PRIMARY.value
    submitted_charge: float = 0.0
    paid_amount: float = 0.0
    patient_responsibility: float = 0.0
    claim_filing_indicator: str = "12"
    payer_claim_control_number: Optional[str] = None
    facility_code: Optional[str] = None
    claim_frequency_code: str = "1"
    patient_id: Optional[str] = None
    patient_name: Optional[str] = None
    insured_id: Optional[str] = None
    corrected_insured_id: Optional[str] = None
    rendering_provider_npi: Optional[str] = None
    service_lines: List[RemittanceServiceLine] = Field(default_factory=list)
    claim_adjustments: List[RemittanceAdjustment] = Field(default_factory=list)
    claim_received_date: Optional[str] = None
    statement_start_date: Optional[str] = None
    statement_end_date: Optional[str] = None
    claim_remark_codes: List[str] = Field(default_factory=list)
    forwarded_payer_id: Optional[str] = None
    forwarded_payer_name: Optional[str] = None
class RemittancePayment(BaseModel):
    payment_id: str = Field(default_factory=lambda: f"PMT-{uuid.uuid4().hex[:8].upper()}")
    payment_method: str = PaymentMethodCode.ACH.value
    payment_amount: float = 0.0
    payment_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    credit_debit: str = "C"  # C=Credit, D=Debit
    check_number: Optional[str] = None
    routing_number: Optional[str] = None
    account_number: Optional[str] = None
    trace_number: str = Field(default_factory=lambda: f"TRC{uuid.uuid4().hex[:10].upper()}")
    payer_id: str = ""
    payer_name: str = ""
    payer_address: Optional[Dict[str, str]] = None
    payer_tax_id: Optional[str] = None
    payee_name: str = ""
    payee_npi: str = ""
    payee_tax_id: Optional[str] = None
    payee_address: Optional[Dict[str, str]] = None
class RemittanceAdvice(BaseModel):
    remittance_id: str = Field(default_factory=lambda: f"RA-{uuid.uuid4().hex[:8].upper()}")
    payment: RemittancePayment = Field(default_factory=RemittancePayment)
    claims: List[RemittanceClaim] = Field(default_factory=list)
    production_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    provider_adjustments: List[Dict[str, Any]] = Field(default_factory=list)
    total_submitted: float = 0.0
    total_paid: float = 0.0
    total_patient_responsibility: float = 0.0
    total_contractual_adjustment: float = 0.0
    claim_count: int = 0
class EDI835BuildResult(BaseModel):
    success: bool
    edi_text: str = ""
    segment_count: int = 0
    claim_count: int = 0
    total_payment: float = 0.0
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
class EDI835ParseResult(BaseModel):
    success: bool
    remittance: Optional[RemittanceAdvice] = None
    segment_count: int = 0
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
class EOBLine(BaseModel):
    """Human-readable Explanation of Benefits line item."""
    service_date: str = ""
    procedure_code: str = ""
    procedure_description: str = ""
    submitted_charge: float = 0.0
    allowed_amount: float = 0.0
    paid_amount: float = 0.0
    deductible: float = 0.0
    coinsurance: float = 0.0
    copay: float = 0.0
    adjustment_reason: str = ""
    status: str = ""  # Paid, Denied, Partially Paid
class EOBReport(BaseModel):
    """Human-readable Explanation of Benefits."""
    report_id: str = Field(default_factory=lambda: f"EOB-{uuid.uuid4().hex[:8].upper()}")
    patient_name: str = ""
    patient_id: str = ""
    payer_name: str = ""
    provider_name: str = ""
    claim_id: str = ""
    payment_date: str = ""
    payment_method: str = ""
    check_number: Optional[str] = None
    line_items: List[EOBLine] = Field(default_factory=list)
    total_submitted: float = 0.0
    total_allowed: float = 0.0
    total_paid: float = 0.0
    total_patient_responsibility: float = 0.0
    total_deductible: float = 0.0
    total_coinsurance: float = 0.0
    total_copay: float = 0.0
    total_adjustment: float = 0.0
    message: str = ""
    appeal_info: Optional[str] = None
# ---------------------------------------------------------------------------
# EDI 835 Generator
# ---------------------------------------------------------------------------
class EDI835Generator:
    """Generator and lightweight parser for EDI 835 transactions."""
    def __init__(self) -> None:
        self.logger = logger
        self.element_separator = "*"
        self.segment_terminator = "~"
        self.component_separator = ":"
        self._build_stats: Dict[str, int] = {"generated": 0, "claims_processed": 0, "errors": 0}
        self._carc_lookup = CARC_DESCRIPTIONS
        self._rarc_lookup = RARC_DESCRIPTIONS
        self._procedure_lookup = PROCEDURE_DESCRIPTIONS
    # ------------------------------------------------------------------
    # Generation entrypoint
    # ------------------------------------------------------------------
    def generate(self, remittance: RemittanceAdvice) -> EDI835BuildResult:
        """Generate a full EDI 835 transaction from a RemittanceAdvice."""
        errors: List[str] = []
        warnings: List[str] = []
        if not remittance.claims:
            errors.append("No claims to remit")
            self._build_stats["errors"] += 1
            return EDI835BuildResult(success=False, errors=errors)
        payment = remittance.payment
        segments: List[str] = []
        try:
            segments.append(self._build_isa(payment.payer_id or "PAYER", payment.payee_npi or "PROVIDER"))
            segments.append(self._build_gs(payment.payer_id or "PAYER", payment.payee_npi or "PROVIDER"))
            segments.append(self.element_separator.join(["ST", "835", "0001"]) + self.segment_terminator)
            segments.append(self._build_bpr(payment))
            segments.append(self._build_trn(payment))
            segments.append(self._build_dtm("405", remittance.production_date))
            segments.extend(self._build_payer_id(payment))
            segments.extend(self._build_payee_id(payment))
            for claim in remittance.claims:
                segments.extend(self._build_claim_block(claim))
            plb_seg = self._build_plb(remittance.provider_adjustments, payment.payee_npi or payment.payee_name)
            if plb_seg:
                segments.append(plb_seg)
            se_count = len(segments) + 2  # include SE and GE/IEA soon
            segments.append(self.element_separator.join(["SE", str(se_count), "0001"]) + self.segment_terminator)
            segments.append(self.element_separator.join(["GE", "1", "1"]) + self.segment_terminator)
            segments.append(self.element_separator.join(["IEA", "1", "000000001"]) + self.segment_terminator)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.exception("Failed to generate 835")
            errors.append(str(exc))
            self._build_stats["errors"] += 1
            return EDI835BuildResult(success=False, errors=errors)
        edi_text = "\n".join(segments)
        self._build_stats["generated"] += 1
        self._build_stats["claims_processed"] += len(remittance.claims)
        return EDI835BuildResult(
            success=True,
            edi_text=edi_text,
            segment_count=len(segments),
            claim_count=len(remittance.claims),
            total_payment=payment.payment_amount,
            errors=errors,
            warnings=warnings,
        )
    # ------------------------------------------------------------------
    # Core builders
    # ------------------------------------------------------------------
    def _build_isa(self, sender_id: str, receiver_id: str) -> str:
        """Build ISA segment with payer as sender and provider as receiver."""
        now = datetime.utcnow()
        control = f"{now.strftime('%H%M%S')}{now.microsecond:06d}"[-9:]
        sender_padded = sender_id.ljust(15)[:15]
        receiver_padded = receiver_id.ljust(15)[:15]
        return self.element_separator.join(
            [
                "ISA",
                "00",
                "".ljust(10),
                "00",
                "".ljust(10),
                "ZZ",
                sender_padded,
                "ZZ",
                receiver_padded,
                now.strftime("%y%m%d"),
                now.strftime("%H%M"),
                self.component_separator,
                "00501",
                control,
                "0",
                "P",
                self.component_separator,
            ]
        ) + self.segment_terminator
    def _build_gs(self, sender_id: str, receiver_id: str) -> str:
        """Build GS functional group header for 835 (HP)."""
        now = datetime.utcnow()
        control = now.strftime("%H%M%S")
        return self.element_separator.join(
            [
                "GS",
                "HP",
                sender_id,
                receiver_id,
                now.strftime("%Y%m%d"),
                now.strftime("%H%M"),
                control,
                "X",
                "005010X221A1",
            ]
        ) + self.segment_terminator
    def _build_bpr(self, payment: RemittancePayment) -> str:
        """Build BPR (Financial Information) segment."""
        handling_code = "I" if payment.payment_amount > 0 else "H"
        credit_debit = payment.credit_debit or ("C" if payment.payment_amount >= 0 else "D")
        method_code = "CCP" if payment.payment_method == PaymentMethodCode.ACH.value else "CHK"
        dfi_qualifier = "01" if payment.routing_number else ""
        routing = payment.routing_number or ""
        account_type = "DA" if payment.account_number else ""
        account = payment.account_number or ""
        return self.element_separator.join(
            [
                "BPR",
                handling_code,
                self._format_amount(payment.payment_amount),
                credit_debit,
                payment.payment_method,
                method_code,
                dfi_qualifier,
                routing,
                account_type,
                account,
                "",
                "",
                "",
                "",
                "",
                self._format_date_edi(payment.payment_date),
            ]
        ) + self.segment_terminator
    def _build_trn(self, payment: RemittancePayment) -> str:
        """Build TRN trace segment."""
        return self.element_separator.join(
            [
                "TRN",
                "1",
                payment.trace_number,
                payment.payer_id or payment.payer_tax_id or "",
            ]
        ) + self.segment_terminator
    def _build_dtm(self, qualifier: str, iso_date: str) -> str:
        """Build DTM date segment."""
        return self.element_separator.join(["DTM", qualifier, self._format_date_edi(iso_date)]) + self.segment_terminator
    def _build_payer_id(self, payment: RemittancePayment) -> List[str]:
        """Build payer identification loop (N1*PR)."""
        segments: List[str] = []
        qualifier = "PI" if payment.payer_id else "XV"
        id_value = payment.payer_id or payment.payer_tax_id or ""
        segments.append(
            self.element_separator.join([
                "N1",
                "PR",
                payment.payer_name or "PAYER",
                qualifier,
                id_value,
            ]) + self.segment_terminator
        )
        if payment.payer_address:
            segments.append(self.element_separator.join(["N3", payment.payer_address.get("line1", "")]) + self.segment_terminator)
            segments.append(
                self.element_separator.join([
                    "N4",
                    payment.payer_address.get("city", ""),
                    payment.payer_address.get("state", ""),
                    payment.payer_address.get("zip", ""),
                ]) + self.segment_terminator
            )
        segments.append(self.element_separator.join(["PER", "CX", "CLAIMS", "TE", "8005551234"]) + self.segment_terminator)
        return segments
    def _build_payee_id(self, payment: RemittancePayment) -> List[str]:
        """Build payee (provider) identification loop (N1*PE)."""
        segments: List[str] = []
        segments.append(
            self.element_separator.join([
                "N1",
                "PE",
                payment.payee_name or "PROVIDER",
                "XX",
                payment.payee_npi or "",
            ]) + self.segment_terminator
        )
        if payment.payee_address:
            segments.append(self.element_separator.join(["N3", payment.payee_address.get("line1", "")]) + self.segment_terminator)
            segments.append(
                self.element_separator.join([
                    "N4",
                    payment.payee_address.get("city", ""),
                    payment.payee_address.get("state", ""),
                    payment.payee_address.get("zip", ""),
                ]) + self.segment_terminator
            )
        if payment.payee_tax_id:
            segments.append(self.element_separator.join(["REF", "TJ", payment.payee_tax_id]) + self.segment_terminator)
        return segments
    def _build_clp(self, claim: RemittanceClaim) -> str:
        """Build CLP claim payment segment."""
        return self.element_separator.join(
            [
                "CLP",
                claim.claim_id,
                claim.claim_status,
                self._format_amount(claim.submitted_charge),
                self._format_amount(claim.paid_amount),
                self._format_amount(claim.patient_responsibility),
                claim.claim_filing_indicator,
                claim.payer_claim_control_number or "",
                claim.facility_code or "",
                claim.claim_frequency_code or "",
            ]
        ) + self.segment_terminator
    def _build_cas(self, adjustments: List[RemittanceAdjustment]) -> List[str]:
        """Build CAS segments grouped by group code with up to 6 triplets each."""
        if not adjustments:
            return []
        segments: List[str] = []
        grouped: Dict[str, List[RemittanceAdjustment]] = {}
        for adj in adjustments:
            grouped.setdefault(adj.group_code, []).append(adj)
        for group_code, items in grouped.items():
            chunk: List[str] = ["CAS", group_code]
            triplet_count = 0
            for adj in items:
                if triplet_count == 6:
                    segments.append(self.element_separator.join(chunk) + self.segment_terminator)
                    chunk = ["CAS", group_code]
                    triplet_count = 0
                chunk.append(adj.reason_code)
                chunk.append(self._format_amount(adj.amount))
                if adj.quantity is not None:
                    chunk.append(str(adj.quantity))
                triplet_count += 1
            segments.append(self.element_separator.join(chunk) + self.segment_terminator)
        return segments
    def _build_svc(self, line: RemittanceServiceLine) -> str:
        """Build SVC service payment segment."""
        code_parts = ["HC", line.procedure_code]
        code_parts.extend(m for m in line.modifiers if m)
        proc_comp = self.component_separator.join(code_parts)
        return self.element_separator.join(
            [
                "SVC",
                proc_comp,
                self._format_amount(line.submitted_charge),
                self._format_amount(line.paid_amount),
                "",
                str(line.units_paid),
            ]
        ) + self.segment_terminator
    def _build_claim_block(self, claim: RemittanceClaim) -> List[str]:
        """Build all segments for a single claim (CLP loop)."""
        segs: List[str] = []
        segs.append(self._build_clp(claim))
        segs.extend(self._build_cas(claim.claim_adjustments))
        if claim.patient_name:
            name_parts = claim.patient_name.split(",")
            last = name_parts[0].strip()
            first = name_parts[1].strip() if len(name_parts) > 1 else ""
            segs.append(self.element_separator.join(["NM1", "QC", "1", last, first, "", "", "", "MI", claim.patient_id or ""]) + self.segment_terminator)
        if claim.insured_id:
            segs.append(self.element_separator.join(["NM1", "IL", "1", "", "", "", "", "", "MI", claim.insured_id]) + self.segment_terminator)
        if claim.rendering_provider_npi:
            segs.append(self.element_separator.join(["NM1", "82", "1", "", "", "", "", "", "XX", claim.rendering_provider_npi]) + self.segment_terminator)
        if claim.claim_received_date:
            segs.append(self._build_dtm("050", claim.claim_received_date))
        if claim.statement_start_date and claim.statement_end_date:
            segs.append(self.element_separator.join(["DTP", "232", "RD8", f"{self._format_date_edi(claim.statement_start_date)}-{self._format_date_edi(claim.statement_end_date)}"]) + self.segment_terminator)
        for line in claim.service_lines:
            segs.append(self._build_svc(line))
            segs.extend(self._build_cas(line.adjustments))
            if line.service_date:
                segs.append(self._build_dtm("472", line.service_date))
            if line.allowed_amount is not None:
                segs.append(self.element_separator.join(["AMT", "B6", self._format_amount(line.allowed_amount)]) + self.segment_terminator)
            if line.remark_codes:
                for code in line.remark_codes:
                    segs.append(self.element_separator.join(["LQ", "HE", code]) + self.segment_terminator)
        if claim.claim_remark_codes:
            for code in claim.claim_remark_codes:
                segs.append(self.element_separator.join(["LQ", "HE", code]) + self.segment_terminator)
        return segs
    def _build_plb(self, adjustments: List[Dict[str, Any]], provider_id: str) -> Optional[str]:
        """Build provider level adjustment (PLB) segment if any."""
        if not adjustments:
            return None
        fiscal_period = datetime.utcnow().strftime("%Y1231")
        parts = ["PLB", provider_id or "", fiscal_period]
        for adj in adjustments:
            reason = adj.get("reason", "WO")
            ref = adj.get("reference", "ADJ")
            amount = adj.get("amount", 0.0)
            parts.append(f"{reason}:{ref}")
            parts.append(self._format_amount(amount))
        return self.element_separator.join(parts) + self.segment_terminator
    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------
    def from_adjudication_result(
        self,
        adjudication: Dict[str, Any],
        payer_info: Dict[str, str] | None = None,
        provider_info: Dict[str, str] | None = None,
    ) -> RemittanceAdvice:
        """Convert MEDI-COMPLY adjudication result dict to RemittanceAdvice."""
        payer_info = payer_info or {}
        provider_info = provider_info or {}
        payment = RemittancePayment(
            payment_method=payer_info.get("payment_method", PaymentMethodCode.ACH.value),
            payment_amount=float(adjudication.get("total_paid", 0.0)),
            payment_date=adjudication.get("payment_date", datetime.utcnow().strftime("%Y-%m-%d")),
            payer_id=payer_info.get("payer_id", "PAYER"),
            payer_name=payer_info.get("payer_name", "PAYER"),
            payer_address=payer_info.get("payer_address"),
            payer_tax_id=payer_info.get("payer_tax_id"),
            payee_name=provider_info.get("provider_name", "PROVIDER"),
            payee_npi=provider_info.get("provider_npi", ""),
            payee_tax_id=provider_info.get("provider_tax_id"),
            payee_address=provider_info.get("provider_address"),
        )
        claim_status_map = {
            "APPROVED": ClaimStatusCode.PRIMARY.value,
            "DENIED": ClaimStatusCode.DENIED.value,
            "PARTIAL": ClaimStatusCode.PRIMARY.value,
        }
        status = claim_status_map.get(adjudication.get("status", "APPROVED"), ClaimStatusCode.PRIMARY.value)
        lines: List[RemittanceServiceLine] = []
        for line_decision in adjudication.get("line_decisions", []):
            adjustments: List[RemittanceAdjustment] = []
            submitted = float(line_decision.get("submitted", 0.0))
            allowed = float(line_decision.get("allowed", submitted))
            paid = float(line_decision.get("paid", allowed))
            # Deductible/coinsurance/copay adjustments
            ded = float(line_decision.get("deductible", 0.0))
            coi = float(line_decision.get("coinsurance", 0.0))
            cop = float(line_decision.get("copay", 0.0))
            if ded:
                adjustments.append(RemittanceAdjustment(group_code=CASGroupCode.PATIENT_RESPONSIBILITY.value, reason_code="1", amount=ded, category=AdjustmentReasonCategory.DEDUCTIBLE.value))
            if coi:
                adjustments.append(RemittanceAdjustment(group_code=CASGroupCode.PATIENT_RESPONSIBILITY.value, reason_code="2", amount=coi, category=AdjustmentReasonCategory.COINSURANCE.value))
            if cop:
                adjustments.append(RemittanceAdjustment(group_code=CASGroupCode.PATIENT_RESPONSIBILITY.value, reason_code="3", amount=cop, category=AdjustmentReasonCategory.COPAYMENT.value))
            # Fee schedule reduction
            fee_reduction = submitted - allowed if submitted > allowed else 0.0
            if fee_reduction > 0:
                adjustments.append(RemittanceAdjustment(group_code=CASGroupCode.CONTRACTUAL.value, reason_code="45", amount=fee_reduction, category=AdjustmentReasonCategory.FEE_SCHEDULE.value))
            # Denial reasons mapping
            for denial in line_decision.get("denial_reasons", []):
                grp, carc = self.map_denial_to_carc(str(denial))
                adjustments.append(RemittanceAdjustment(group_code=grp, reason_code=carc, amount=0.0, reason_description=self.get_carc_description(carc)))
            line = RemittanceServiceLine(
                procedure_code=line_decision.get("procedure_code", ""),
                modifiers=line_decision.get("modifiers", []) or [],
                submitted_charge=submitted,
                paid_amount=paid,
                allowed_amount=allowed,
                units_paid=float(line_decision.get("units", 1)),
                service_date=line_decision.get("service_date"),
                adjustments=adjustments,
                deductible_amount=ded,
                coinsurance_amount=coi,
                copay_amount=cop,
                remark_codes=[r for r in line_decision.get("remark_codes", [])],
            )
            lines.append(line)
        claim = RemittanceClaim(
            claim_id=adjudication.get("claim_id", ""),
            claim_status=status,
            submitted_charge=float(adjudication.get("total_submitted", 0.0)),
            paid_amount=float(adjudication.get("total_paid", 0.0)),
            patient_responsibility=float(adjudication.get("patient_responsibility", 0.0)),
            payer_claim_control_number=adjudication.get("payer_claim_control_number"),
            patient_id=adjudication.get("patient_id"),
            patient_name=adjudication.get("patient_name"),
            insured_id=adjudication.get("insured_id"),
            rendering_provider_npi=adjudication.get("rendering_provider_npi"),
            claim_received_date=adjudication.get("claim_received_date"),
            statement_start_date=adjudication.get("statement_start_date"),
            statement_end_date=adjudication.get("statement_end_date"),
            service_lines=lines,
        )
        return RemittanceAdvice(
            payment=payment,
            claims=[claim],
            total_submitted=claim.submitted_charge,
            total_paid=claim.paid_amount,
            total_patient_responsibility=claim.patient_responsibility,
            total_contractual_adjustment=sum(adj.amount for adj in claim.claim_adjustments if adj.group_code == CASGroupCode.CONTRACTUAL.value),
            claim_count=1,
        )
    def generate_eob(self, remittance: RemittanceAdvice) -> EOBReport:
        """Generate a human-readable EOB report from RemittanceAdvice."""
        report = EOBReport(
            patient_name=remittance.claims[0].patient_name or "",
            patient_id=remittance.claims[0].patient_id or "",
            payer_name=remittance.payment.payer_name,
            provider_name=remittance.payment.payee_name,
            claim_id=remittance.claims[0].claim_id,
            payment_date=remittance.payment.payment_date,
            payment_method=remittance.payment.payment_method,
            check_number=remittance.payment.check_number,
        )
        total_allowed = 0.0
        total_adjustment = 0.0
        total_deductible = 0.0
        total_coinsurance = 0.0
        total_copay = 0.0
        for claim in remittance.claims:
            for line in claim.service_lines:
                allowed = line.allowed_amount or line.submitted_charge
                adj_reason = ", ".join(f"{adj.group_code}*{adj.reason_code}: {self.get_carc_description(adj.reason_code)}" for adj in line.adjustments if adj.reason_code)
                status = "Denied" if line.paid_amount == 0 else ("Partially Paid" if line.paid_amount < line.submitted_charge else "Paid")
                eob_line = EOBLine(
                    service_date=line.service_date or "",
                    procedure_code=line.procedure_code,
                    procedure_description=self._procedure_lookup.get(line.procedure_code, ""),
                    submitted_charge=line.submitted_charge,
                    allowed_amount=allowed,
                    paid_amount=line.paid_amount,
                    deductible=line.deductible_amount,
                    coinsurance=line.coinsurance_amount,
                    copay=line.copay_amount,
                    adjustment_reason=adj_reason,
                    status=status,
                )
                report.line_items.append(eob_line)
                total_allowed += allowed
                total_adjustment += sum(adj.amount for adj in line.adjustments)
                total_deductible += line.deductible_amount
                total_coinsurance += line.coinsurance_amount
                total_copay += line.copay_amount
        report.total_submitted = remittance.total_submitted
        report.total_allowed = total_allowed
        report.total_paid = remittance.total_paid
        report.total_patient_responsibility = remittance.total_patient_responsibility
        report.total_adjustment = total_adjustment
        report.total_deductible = total_deductible
        report.total_coinsurance = total_coinsurance
        report.total_copay = total_copay
        if remittance.total_paid == 0:
            report.message = "Claim denied. See reasons above."
        elif remittance.total_paid < remittance.total_submitted:
            report.message = "Claim partially paid. Patient responsibility and adjustments apply."
        else:
            report.message = "Claim paid in full."
        if any(claim.claim_status == ClaimStatusCode.DENIED.value for claim in remittance.claims):
            report.appeal_info = "If you disagree with this decision, submit an appeal within 60 days with supporting documentation."
        return report
    def generate_eob_text(self, eob: EOBReport) -> str:
        """Render EOBReport into a human-readable text block."""
        lines: List[str] = []
        lines.append(f"Payer: {eob.payer_name}    Provider: {eob.provider_name}")
        lines.append(f"Patient: {eob.patient_name} (ID: {eob.patient_id})")
        lines.append(f"Claim: {eob.claim_id}    Payment Date: {eob.payment_date}    Method: {eob.payment_method}")
        if eob.check_number:
            lines.append(f"Check #: {eob.check_number}")
        lines.append("")
        lines.append("Date       | Service | Charge | Allowed | Paid | You Owe | Reason")
        lines.append("-" * 80)
        for item in eob.line_items:
            you_owe = item.submitted_charge - item.paid_amount
            lines.append(
                f"{item.service_date or '':<10} | {item.procedure_code:<7} | "
                f"{item.submitted_charge:>7.2f} | {item.allowed_amount:>7.2f} | {item.paid_amount:>5.2f} | {you_owe:>7.2f} | {item.adjustment_reason}"
            )
        lines.append("-" * 80)
        lines.append(
            f"Totals -> Charge: {eob.total_submitted:.2f}  Allowed: {eob.total_allowed:.2f}  Paid: {eob.total_paid:.2f}  "
            f"Patient Resp: {eob.total_patient_responsibility:.2f}  Adj: {eob.total_adjustment:.2f}"
        )
        lines.append(f"Deductible: {eob.total_deductible:.2f}  Coinsurance: {eob.total_coinsurance:.2f}  Copay: {eob.total_copay:.2f}")
        lines.append("")
        lines.append(f"Message: {eob.message}")
        if eob.appeal_info:
            lines.append(f"Appeal: {eob.appeal_info}")
        return "\n".join(lines)
    # ------------------------------------------------------------------
    # Parsing helpers (lightweight, not full X12 validation)
    # ------------------------------------------------------------------
    def parse(self, raw_edi: str) -> EDI835ParseResult:
        """Parse a minimal 835 back into RemittanceAdvice."""
        if not raw_edi or not raw_edi.strip():
            return EDI835ParseResult(success=False, errors=["Empty EDI input"], segment_count=0)
        segments = [seg.strip() for seg in re.split(r"~\s*", raw_edi) if seg.strip()]
        parse_result = EDI835ParseResult(success=True, segment_count=len(segments))
        payment = RemittancePayment()
        claims: List[RemittanceClaim] = []
        current_claim: Optional[RemittanceClaim] = None
        current_line: Optional[RemittanceServiceLine] = None
        try:
            for seg in segments:
                parts = seg.split(self.element_separator)
                sid = parts[0]
                if sid == "BPR":
                    bpr = self._parse_bpr(parts[1:])
                    payment.payment_amount = bpr.get("amount", 0.0)
                    payment.payment_method = bpr.get("payment_method", PaymentMethodCode.ACH.value)
                    payment.payment_date = bpr.get("payment_date", payment.payment_date)
                elif sid == "TRN":
                    payment.trace_number = parts[2] if len(parts) > 2 else payment.trace_number
                elif sid == "N1" and len(parts) > 2 and parts[1] == "PR":
                    payment.payer_name = parts[2]
                    if len(parts) > 4:
                        payment.payer_id = parts[4]
                elif sid == "N1" and len(parts) > 2 and parts[1] == "PE":
                    payment.payee_name = parts[2]
                    if len(parts) > 4:
                        payment.payee_npi = parts[4]
                elif sid == "CLP":
                    if current_claim:
                        claims.append(current_claim)
                    clp = self._parse_clp(parts[1:])
                    current_claim = RemittanceClaim(
                        claim_id=clp.get("claim_id", ""),
                        claim_status=clp.get("status", ClaimStatusCode.PRIMARY.value),
                        submitted_charge=clp.get("submitted", 0.0),
                        paid_amount=clp.get("paid", 0.0),
                        patient_responsibility=clp.get("patient_resp", 0.0),
                        claim_filing_indicator=clp.get("filing_indicator", ""),
                        payer_claim_control_number=clp.get("payer_ref"),
                        facility_code=clp.get("facility_code"),
                        claim_frequency_code=clp.get("frequency"),
                    )
                    current_line = None
                elif sid == "CAS" and current_claim:
                    adjustments = self._parse_cas(parts[1:])
                    if current_line:
                        current_line.adjustments.extend(adjustments)
                    else:
                        current_claim.claim_adjustments.extend(adjustments)
                elif sid == "NM1" and current_claim:
                    if parts[1] == "QC":
                        current_claim.patient_name = parts[3]
                        current_claim.patient_id = parts[9] if len(parts) > 9 else current_claim.patient_id
                    elif parts[1] == "IL":
                        current_claim.insured_id = parts[9] if len(parts) > 9 else current_claim.insured_id
                    elif parts[1] == "82":
                        current_claim.rendering_provider_npi = parts[9] if len(parts) > 9 else current_claim.rendering_provider_npi
                elif sid == "DTP" and current_claim:
                    if parts[1] == "050":
                        current_claim.claim_received_date = self._parse_date_edi(parts[2]) if len(parts) > 2 else None
                    elif parts[1] == "232" and len(parts) > 2:
                        rng = parts[2].split("-")
                        if len(rng) == 2:
                            current_claim.statement_start_date = self._parse_date_edi(rng[0])
                            current_claim.statement_end_date = self._parse_date_edi(rng[1])
                    elif parts[1] == "472" and current_line:
                        current_line.service_date = self._parse_date_edi(parts[2]) if len(parts) > 2 else None
                elif sid == "SVC" and current_claim:
                    svc = self._parse_svc(parts[1:])
                    current_line = RemittanceServiceLine(
                        procedure_code=svc.get("procedure_code", ""),
                        modifiers=svc.get("modifiers", []),
                        submitted_charge=svc.get("submitted", 0.0),
                        paid_amount=svc.get("paid", 0.0),
                        units_paid=svc.get("units", 1.0),
                    )
                    current_claim.service_lines.append(current_line)
                elif sid == "AMT" and current_line:
                    if len(parts) > 2 and parts[1] == "B6":
                        current_line.allowed_amount = float(parts[2]) if parts[2] else None
                elif sid == "LQ" and current_line:
                    if len(parts) > 2:
                        current_line.remark_codes.append(parts[2])
            if current_claim:
                claims.append(current_claim)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.exception("Failed to parse 835")
            parse_result.success = False
            parse_result.errors.append(str(exc))
            return parse_result
        ra = RemittanceAdvice(payment=payment, claims=claims)
        ra.total_paid = sum(cl.paid_amount for cl in claims)
        ra.total_submitted = sum(cl.submitted_charge for cl in claims)
        ra.total_patient_responsibility = sum(cl.patient_responsibility for cl in claims)
        ra.claim_count = len(claims)
        parse_result.remittance = ra
        return parse_result
    def _parse_bpr(self, elements: List[str]) -> Dict[str, Any]:
        """Parse BPR elements into payment info."""
        result: Dict[str, Any] = {}
        try:
            result["amount"] = float(elements[1]) if len(elements) > 1 else 0.0
            result["payment_method"] = elements[3] if len(elements) > 3 else PaymentMethodCode.ACH.value
            result["payment_date"] = self._parse_date_edi(elements[13]) if len(elements) > 13 else None
        except Exception:  # pragma: no cover - defensive
            pass
        return result
    def _parse_clp(self, elements: List[str]) -> Dict[str, Any]:
        """Parse CLP elements into claim info."""
        res: Dict[str, Any] = {}
        try:
            res["claim_id"] = elements[0]
            res["status"] = elements[1]
            res["submitted"] = float(elements[2]) if len(elements) > 2 else 0.0
            res["paid"] = float(elements[3]) if len(elements) > 3 else 0.0
            res["patient_resp"] = float(elements[4]) if len(elements) > 4 else 0.0
            res["filing_indicator"] = elements[5] if len(elements) > 5 else ""
            res["payer_ref"] = elements[6] if len(elements) > 6 else ""
            res["facility_code"] = elements[7] if len(elements) > 7 else ""
            res["frequency"] = elements[8] if len(elements) > 8 else ""
        except Exception:  # pragma: no cover - defensive
            pass
        return res
    def _parse_svc(self, elements: List[str]) -> Dict[str, Any]:
        """Parse SVC elements into service line info."""
        res: Dict[str, Any] = {}
        try:
            proc_comp = elements[0] if elements else ""
            comps = proc_comp.split(self.component_separator)
            res["procedure_code"] = comps[1] if len(comps) > 1 else ""
            res["modifiers"] = comps[2:]
            res["submitted"] = float(elements[1]) if len(elements) > 1 else 0.0
            res["paid"] = float(elements[2]) if len(elements) > 2 else 0.0
            res["units"] = float(elements[5]) if len(elements) > 5 else 1.0
        except Exception:  # pragma: no cover - defensive
            pass
        return res
    def _parse_cas(self, elements: List[str]) -> List[RemittanceAdjustment]:
        """Parse CAS elements into list of adjustments."""
        adjs: List[RemittanceAdjustment] = []
        if not elements:
            return adjs
        group = elements[0]
        triplets = elements[1:]
        for i in range(0, len(triplets), 3):
            if i + 1 >= len(triplets):
                break
            reason = triplets[i]
            amt_str = triplets[i + 1] if i + 1 < len(triplets) else "0"
            qty = triplets[i + 2] if i + 2 < len(triplets) else None
            try:
                amt = float(amt_str)
            except Exception:
                amt = 0.0
            adj = RemittanceAdjustment(group_code=group, reason_code=reason, amount=amt)
            if qty:
                try:
                    adj.quantity = float(qty)
                except Exception:
                    adj.quantity = None
            adjs.append(adj)
        return adjs
    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def get_carc_description(self, carc_code: str) -> str:
        """Return human-readable description for CARC code."""
        return self._carc_lookup.get(carc_code, "Unknown adjustment reason")
    def get_rarc_description(self, rarc_code: str) -> str:
        """Return human-readable description for RARC code."""
        return self._rarc_lookup.get(rarc_code, "Unknown remark code")
    def map_denial_to_carc(self, denial_reason: str) -> Tuple[str, str]:
        """Map denial reason string to (group_code, reason_code)."""
        reason = denial_reason.lower()
        if "deductible" in reason:
            return (CASGroupCode.PATIENT_RESPONSIBILITY.value, "1")
        if "coinsurance" in reason:
            return (CASGroupCode.PATIENT_RESPONSIBILITY.value, "2")
        if "copay" in reason:
            return (CASGroupCode.PATIENT_RESPONSIBILITY.value, "3")
        if "not covered" in reason or "non-covered" in reason:
            return (CASGroupCode.CONTRACTUAL.value, "96")
        if "not medically necessary" in reason or "medical necessity" in reason:
            return (CASGroupCode.CONTRACTUAL.value, "50")
        if "fee schedule" in reason or "exceeds" in reason:
            return (CASGroupCode.CONTRACTUAL.value, "45")
        if "duplicate" in reason:
            return (CASGroupCode.CONTRACTUAL.value, "18")
        if "timely filing" in reason or "time limit" in reason:
            return (CASGroupCode.CONTRACTUAL.value, "29")
        if "authorization" in reason or "prior auth" in reason:
            return (CASGroupCode.CONTRACTUAL.value, "197")
        if "bundled" in reason or "included in" in reason:
            return (CASGroupCode.CONTRACTUAL.value, "97")
        if "terminated" in reason or "not eligible" in reason:
            return (CASGroupCode.CONTRACTUAL.value, "27")
        if "benefit maximum" in reason:
            return (CASGroupCode.CONTRACTUAL.value, "119")
        return (CASGroupCode.OTHER_ADJUSTMENT.value, "23")
    def _format_amount(self, amount: float) -> str:
        """Format a float to two-decimal string without commas."""
        try:
            return str(Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        except Exception:
            return "0.00"
    def _format_date_edi(self, iso_date: str) -> str:
        """Convert ISO date (YYYY-MM-DD) to EDI CCYYMMDD."""
        if not iso_date:
            return ""
        try:
            dt = datetime.strptime(iso_date, "%Y-%m-%d").date()
            return dt.strftime("%Y%m%d")
        except Exception:
            return ""
    def _parse_date_edi(self, edi_date: str) -> str:
        """Convert CCYYMMDD to ISO date string."""
        try:
            dt = datetime.strptime(edi_date, "%Y%m%d").date()
            return dt.isoformat()
        except Exception:
            return ""
    def get_build_stats(self) -> Dict[str, int]:
        """Return generation statistics."""
        return dict(self._build_stats)
# ---------------------------------------------------------------------------
# __main__ demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    generator = EDI835Generator()
    remittance = RemittanceAdvice(
        payment=RemittancePayment(
            payment_method="ACH",
            payment_amount=1050.00,
            payment_date="2024-01-20",
            trace_number="TRC20240120001",
            payer_id="BCBS001",
            payer_name="Blue Cross Blue Shield",
            payer_address={"line1": "PO Box 1234", "city": "Chicago", "state": "IL", "zip": "60601"},
            payee_name="Springfield Medical Group",
            payee_npi="1234567890",
            payee_tax_id="36-1234567",
            payee_address={"line1": "123 Main St", "city": "Springfield", "state": "IL", "zip": "62701"},
        ),
        claims=[
            RemittanceClaim(
                claim_id="CLAIM001",
                claim_status="1",
                submitted_charge=1500.00,
                paid_amount=1050.00,
                patient_responsibility=300.00,
                payer_claim_control_number="BCBS-2024-001",
                patient_name="DOE, JANE",
                patient_id="MEM12345",
                insured_id="SUB12345",
                rendering_provider_npi="9876543210",
                claim_received_date="2024-01-16",
                statement_start_date="2024-01-15",
                statement_end_date="2024-01-15",
                service_lines=[
                    RemittanceServiceLine(
                        procedure_code="99223",
                        modifiers=["25"],
                        submitted_charge=500.00,
                        paid_amount=350.00,
                        allowed_amount=400.00,
                        units_paid=1,
                        service_date="2024-01-15",
                        adjustments=[
                            RemittanceAdjustment(
                                group_code="CO",
                                reason_code="45",
                                amount=100.00,
                                reason_description="Charge exceeds fee schedule",
                            ),
                            RemittanceAdjustment(
                                group_code="PR",
                                reason_code="1",
                                amount=50.00,
                                reason_description="Deductible amount",
                            ),
                        ],
                        deductible_amount=50.00,
                    ),
                    RemittanceServiceLine(
                        procedure_code="93000",
                        submitted_charge=150.00,
                        paid_amount=120.00,
                        allowed_amount=130.00,
                        units_paid=1,
                        service_date="2024-01-15",
                        adjustments=[
                            RemittanceAdjustment(group_code="CO", reason_code="45", amount=20.00),
                            RemittanceAdjustment(
                                group_code="PR",
                                reason_code="2",
                                amount=10.00,
                                reason_description="Coinsurance",
                            ),
                        ],
                        coinsurance_amount=10.00,
                    ),
                    RemittanceServiceLine(
                        procedure_code="71046",
                        submitted_charge=200.00,
                        paid_amount=160.00,
                        allowed_amount=180.00,
                        units_paid=1,
                        service_date="2024-01-15",
                        adjustments=[
                            RemittanceAdjustment(group_code="CO", reason_code="45", amount=20.00),
                            RemittanceAdjustment(group_code="PR", reason_code="2", amount=20.00),
                        ],
                        coinsurance_amount=20.00,
                    ),
                    RemittanceServiceLine(
                        procedure_code="80053",
                        submitted_charge=100.00,
                        paid_amount=80.00,
                        allowed_amount=90.00,
                        units_paid=1,
                        service_date="2024-01-15",
                        adjustments=[
                            RemittanceAdjustment(group_code="CO", reason_code="45", amount=10.00),
                            RemittanceAdjustment(group_code="PR", reason_code="2", amount=10.00),
                        ],
                        coinsurance_amount=10.00,
                    ),
                    RemittanceServiceLine(
                        procedure_code="84484",
                        submitted_charge=75.00,
                        paid_amount=60.00,
                        allowed_amount=70.00,
                        units_paid=1,
                        service_date="2024-01-15",
                        adjustments=[
                            RemittanceAdjustment(group_code="CO", reason_code="45", amount=5.00),
                            RemittanceAdjustment(group_code="PR", reason_code="2", amount=10.00),
                        ],
                        coinsurance_amount=10.00,
                    ),
                ],
            ),
        ],
        total_submitted=1500.00,
        total_paid=1050.00,
        total_patient_responsibility=300.00,
        total_contractual_adjustment=155.00,
        claim_count=1,
    )
    result = generator.generate(remittance)
    print("=== EDI 835 Generation ===")
    print(f"Success: {result.success}")
    print(f"Segments: {result.segment_count}")
    print(f"Claims: {result.claim_count}")
    print(f"Total payment: ${result.total_payment:.2f}")
    print(f"\n{result.edi_text}")
    eob = generator.generate_eob(remittance)
    eob_text = generator.generate_eob_text(eob)
    print("\n=== Explanation of Benefits ===")
    print(eob_text)
    print("\n=== CARC Lookups ===")
    for code in ["1", "2", "3", "45", "50", "96", "197"]:
        print(f"  CARC {code}: {generator.get_carc_description(code)}")
    print("\n=== Denial Reason Mapping ===")
    test_reasons = ["not medically necessary", "deductible applies", "no prior authorization", "duplicate claim"]
    for reason in test_reasons:
        group, carc = generator.map_denial_to_carc(reason)
        print(f"  '{reason}' -> {group}*{carc} ({generator.get_carc_description(carc)})")
    print("\n=== Parse Generated 835 ===")
    parse_result = generator.parse(result.edi_text)
    print(f"Parse success: {parse_result.success}")
    if parse_result.remittance:
        ra = parse_result.remittance
        print(f"Payment: ${ra.total_paid:.2f}")
        print(f"Claims: {len(ra.claims)}")
    print(f"\nBuild stats: {generator.get_build_stats()}")
