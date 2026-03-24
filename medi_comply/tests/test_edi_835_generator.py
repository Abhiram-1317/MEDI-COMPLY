import pytest

from medi_comply.integrations.edi_835_generator import (
    EDI835Generator,
    RemittancePayment,
    RemittanceAdvice,
    RemittanceClaim,
    RemittanceServiceLine,
    RemittanceAdjustment,
)


@pytest.fixture
def generator():
    return EDI835Generator()


@pytest.fixture
def sample_payment():
    return RemittancePayment(
        payment_method="ACH",
        payment_amount=1050.00,
        payment_date="2024-01-20",
        trace_number="TRC001",
        payer_id="BCBS001",
        payer_name="Blue Cross Blue Shield",
        payee_name="Springfield Medical",
        payee_npi="1234567890",
    )


@pytest.fixture
def sample_paid_claim():
    return RemittanceClaim(
        claim_id="CLM001",
        claim_status="1",
        submitted_charge=500.00,
        paid_amount=400.00,
        patient_responsibility=50.00,
        patient_name="DOE, JANE",
        patient_id="MEM001",
        service_lines=[
            RemittanceServiceLine(
                procedure_code="99214",
                submitted_charge=250.00,
                paid_amount=200.00,
                allowed_amount=220.00,
                service_date="2024-01-15",
                adjustments=[
                    RemittanceAdjustment(group_code="CO", reason_code="45", amount=30.00),
                    RemittanceAdjustment(group_code="PR", reason_code="2", amount=20.00),
                ],
                coinsurance_amount=20.00,
            ),
            RemittanceServiceLine(
                procedure_code="80053",
                submitted_charge=100.00,
                paid_amount=80.00,
                allowed_amount=90.00,
                service_date="2024-01-15",
                adjustments=[
                    RemittanceAdjustment(group_code="CO", reason_code="45", amount=10.00),
                    RemittanceAdjustment(group_code="PR", reason_code="1", amount=10.00),
                ],
                deductible_amount=10.00,
            ),
        ],
    )


@pytest.fixture
def sample_denied_claim():
    return RemittanceClaim(
        claim_id="CLM002",
        claim_status="4",
        submitted_charge=300.00,
        paid_amount=0.00,
        patient_responsibility=0.00,
        patient_name="SMITH, JOHN",
        patient_id="MEM002",
        service_lines=[
            RemittanceServiceLine(
                procedure_code="73721",
                submitted_charge=300.00,
                paid_amount=0.00,
                service_date="2024-01-15",
                adjustments=[
                    RemittanceAdjustment(
                        group_code="CO",
                        reason_code="197",
                        amount=300.00,
                        reason_description="Prior authorization required",
                    ),
                ],
            ),
        ],
    )


@pytest.fixture
def sample_remittance(sample_payment, sample_paid_claim):
    return RemittanceAdvice(
        payment=sample_payment,
        claims=[sample_paid_claim],
        total_submitted=500.00,
        total_paid=400.00,
        total_patient_responsibility=50.00,
        claim_count=1,
    )


class TestGenerate835:
    def test_generate_success(self, generator, sample_remittance):
        result = generator.generate(sample_remittance)
        assert result.success
        assert result.claim_count == 1

    def test_generate_has_isa(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        assert "ISA" in edi.split("\n")[0]

    def test_generate_has_bpr(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        assert "BPR*I*1050.00" in edi

    def test_generate_has_trn(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        assert "TRN*1*TRC001" in edi

    def test_generate_has_clp(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        assert "CLP*CLM001" in edi

    def test_generate_has_svc(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        assert "SVC*HC:99214" in edi
        assert "SVC*HC:80053" in edi

    def test_generate_has_cas(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        assert "CAS*CO*45*30.00" in edi
        assert "CAS*PR*2*20.00" in edi

    def test_generate_segment_count(self, generator, sample_remittance):
        result = generator.generate(sample_remittance)
        actual_segments = len([s for s in result.edi_text.split("\n") if s])
        assert result.segment_count == actual_segments


class TestBuildSegments:
    def test_build_bpr_ach(self, generator, sample_payment):
        bpr = generator._build_bpr(sample_payment)
        assert bpr.startswith("BPR*I*1050.00*C*ACH*CCP")
        assert "20240120" in bpr

    def test_build_bpr_check(self, generator):
        pay = RemittancePayment(
            payment_method="CHK",
            payment_amount=100.00,
            payment_date="2024-01-01",
            payer_id="PAYER",
            payee_npi="PROV",
        )
        bpr = generator._build_bpr(pay)
        assert bpr.startswith("BPR*I*100.00*C*CHK*CHK")

    def test_build_bpr_zero_pay(self, generator):
        pay = RemittancePayment(
            payment_method="ACH",
            payment_amount=0.00,
            payment_date="2024-01-01",
            payer_id="PAYER",
            payee_npi="PROV",
        )
        bpr = generator._build_bpr(pay)
        assert bpr.startswith("BPR*H*0.00*C*ACH*CCP")

    def test_build_clp_paid(self, generator, sample_paid_claim):
        clp = generator._build_clp(sample_paid_claim)
        assert "CLP*CLM001*1*500.00*400.00*50.00" in clp

    def test_build_clp_denied(self, generator, sample_denied_claim):
        clp = generator._build_clp(sample_denied_claim)
        assert "CLP*CLM002*4" in clp

    def test_build_svc(self, generator):
        line = RemittanceServiceLine(procedure_code="93000", submitted_charge=150.0, paid_amount=120.0, units_paid=1)
        svc = generator._build_svc(line)
        assert "SVC*HC:93000*150.00*120.00" in svc

    def test_build_svc_with_modifiers(self, generator):
        line = RemittanceServiceLine(procedure_code="99214", modifiers=["25"], submitted_charge=100.0, paid_amount=90.0, units_paid=1)
        svc = generator._build_svc(line)
        assert "SVC*HC:99214:25*100.00*90.00" in svc

    def test_build_cas_groups(self, generator):
        adjs = [
            RemittanceAdjustment(group_code="CO", reason_code="45", amount=10),
            RemittanceAdjustment(group_code="PR", reason_code="1", amount=5),
        ]
        cas_segments = generator._build_cas(adjs)
        assert any(seg.startswith("CAS*CO") for seg in cas_segments)
        assert any(seg.startswith("CAS*PR") for seg in cas_segments)


class TestCASGeneration:
    def test_cas_contractual(self, generator):
        adjs = [RemittanceAdjustment(group_code="CO", reason_code="45", amount=25.0)]
        cas = generator._build_cas(adjs)
        assert cas[0].startswith("CAS*CO*45*25.00")

    def test_cas_patient_responsibility(self, generator):
        adjs = [RemittanceAdjustment(group_code="PR", reason_code="1", amount=10.0)]
        cas = generator._build_cas(adjs)
        assert cas[0].startswith("CAS*PR*1*10.00")

    def test_cas_multiple_per_group(self, generator):
        adjs = [
            RemittanceAdjustment(group_code="CO", reason_code="45", amount=10.0),
            RemittanceAdjustment(group_code="CO", reason_code="97", amount=5.0),
        ]
        cas = generator._build_cas(adjs)
        assert "CAS*CO*45*10.00*97*5.00" in cas[0]

    def test_cas_max_triplets(self, generator):
        adjs = [RemittanceAdjustment(group_code="CO", reason_code=str(i), amount=1.0) for i in range(1, 9)]
        cas = generator._build_cas(adjs)
        assert len(cas) == 2
        assert cas[0].startswith("CAS*CO")
        assert cas[1].startswith("CAS*CO")

    def test_cas_empty(self, generator):
        assert generator._build_cas([]) == []


class TestCARCLookup:
    def test_carc_deductible(self, generator):
        assert "Deductible" in generator.get_carc_description("1")

    def test_carc_coinsurance(self, generator):
        assert "Coinsurance" in generator.get_carc_description("2")

    def test_carc_fee_schedule(self, generator):
        desc = generator.get_carc_description("45")
        assert "fee schedule" in desc.lower() or "maximum" in desc.lower()

    def test_carc_unknown(self, generator):
        assert generator.get_carc_description("XYZ") == "Unknown adjustment reason"

    def test_rarc_lookup(self, generator):
        assert generator.get_rarc_description("N362").startswith("Missing")


class TestDenialMapping:
    def test_map_deductible(self, generator):
        assert generator.map_denial_to_carc("deductible") == ("PR", "1")

    def test_map_not_covered(self, generator):
        assert generator.map_denial_to_carc("not covered service") == ("CO", "96")

    def test_map_no_auth(self, generator):
        assert generator.map_denial_to_carc("prior authorization") == ("CO", "197")

    def test_map_duplicate(self, generator):
        assert generator.map_denial_to_carc("duplicate claim") == ("CO", "18")

    def test_map_medical_necessity(self, generator):
        assert generator.map_denial_to_carc("not medically necessary") == ("CO", "50")

    def test_map_unknown(self, generator):
        assert generator.map_denial_to_carc("random reason") == ("OA", "23")


class TestConversion:
    def test_from_adjudication_approved(self, generator):
        adjudication = {
            "status": "APPROVED",
            "claim_id": "CLM100",
            "total_submitted": 200.0,
            "total_paid": 180.0,
            "patient_responsibility": 20.0,
            "line_decisions": [
                {
                    "procedure_code": "99213",
                    "submitted": 200.0,
                    "allowed": 180.0,
                    "paid": 180.0,
                    "service_date": "2024-01-10",
                }
            ],
        }
        ra = generator.from_adjudication_result(adjudication)
        assert ra.claims[0].claim_status == "1"
        assert ra.total_paid == 180.0

    def test_from_adjudication_denied(self, generator):
        adjudication = {
            "status": "DENIED",
            "claim_id": "CLM101",
            "total_submitted": 300.0,
            "total_paid": 0.0,
            "line_decisions": [
                {
                    "procedure_code": "71046",
                    "submitted": 300.0,
                    "allowed": 0.0,
                    "paid": 0.0,
                    "denial_reasons": ["duplicate"],
                }
            ],
        }
        ra = generator.from_adjudication_result(adjudication)
        assert ra.claims[0].claim_status == "4"
        assert any(adj.reason_code == "18" for adj in ra.claims[0].service_lines[0].adjustments)

    def test_from_adjudication_partial(self, generator):
        adjudication = {
            "status": "PARTIAL",
            "claim_id": "CLM102",
            "total_submitted": 400.0,
            "total_paid": 200.0,
            "patient_responsibility": 50.0,
            "line_decisions": [
                {
                    "procedure_code": "93000",
                    "submitted": 400.0,
                    "allowed": 250.0,
                    "paid": 200.0,
                    "deductible": 50.0,
                }
            ],
        }
        ra = generator.from_adjudication_result(adjudication)
        assert ra.claims[0].paid_amount == 200.0
        assert any(adj.reason_code == "1" for adj in ra.claims[0].service_lines[0].adjustments)

    def test_from_adjudication_line_adjustments(self, generator):
        adjudication = {
            "status": "APPROVED",
            "claim_id": "CLM103",
            "total_submitted": 300.0,
            "total_paid": 250.0,
            "line_decisions": [
                {
                    "procedure_code": "99214",
                    "submitted": 300.0,
                    "allowed": 260.0,
                    "paid": 250.0,
                    "coinsurance": 10.0,
                    "denial_reasons": ["not medically necessary"],
                }
            ],
        }
        ra = generator.from_adjudication_result(adjudication)
        line = ra.claims[0].service_lines[0]
        assert any(adj.reason_code == "2" for adj in line.adjustments)
        assert any(adj.reason_code == "50" for adj in line.adjustments)


class TestEOBGeneration:
    def test_eob_report_created(self, generator, sample_remittance):
        eob = generator.generate_eob(sample_remittance)
        assert eob.claim_id == "CLM001"
        assert eob.patient_name == "DOE, JANE"

    def test_eob_line_items(self, generator, sample_remittance):
        eob = generator.generate_eob(sample_remittance)
        assert len(eob.line_items) == len(sample_remittance.claims[0].service_lines)

    def test_eob_totals(self, generator, sample_remittance):
        eob = generator.generate_eob(sample_remittance)
        assert eob.total_paid == sample_remittance.total_paid
        assert eob.total_submitted == sample_remittance.total_submitted

    def test_eob_denied_claim(self, generator, sample_denied_claim, sample_payment):
        ra = RemittanceAdvice(payment=sample_payment, claims=[sample_denied_claim], total_submitted=300.0, total_paid=0.0, claim_count=1)
        eob = generator.generate_eob(ra)
        assert any(item.status == "Denied" for item in eob.line_items)
        assert eob.message.startswith("Claim denied")

    def test_eob_text_format(self, generator, sample_remittance):
        eob = generator.generate_eob(sample_remittance)
        text = generator.generate_eob_text(eob)
        assert "Date       | Service | Charge | Allowed | Paid | You Owe | Reason" in text

    def test_eob_appeal_info(self, generator, sample_payment, sample_denied_claim):
        ra = RemittanceAdvice(payment=sample_payment, claims=[sample_denied_claim], total_submitted=300.0, total_paid=0.0, claim_count=1)
        eob = generator.generate_eob(ra)
        assert eob.appeal_info is not None


class TestParse835:
    def test_parse_generated(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        parsed = generator.parse(edi)
        assert parsed.success
        assert parsed.remittance

    def test_parse_payment_amount(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        parsed = generator.parse(edi)
        assert parsed.remittance.payment.payment_amount == pytest.approx(1050.00)

    def test_parse_claim_count(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        parsed = generator.parse(edi)
        assert len(parsed.remittance.claims) == 1

    def test_parse_service_lines(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        parsed = generator.parse(edi)
        claim = parsed.remittance.claims[0]
        assert len(claim.service_lines) == 2

    def test_parse_adjustments(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        parsed = generator.parse(edi)
        line = parsed.remittance.claims[0].service_lines[0]
        assert any(adj.reason_code == "45" for adj in line.adjustments)


class TestAmountFormatting:
    def test_format_whole_dollars(self, generator):
        assert generator._format_amount(1500) == "1500.00"

    def test_format_cents(self, generator):
        assert generator._format_amount(1500.5) == "1500.50"

    def test_format_zero(self, generator):
        assert generator._format_amount(0) == "0.00"

    def test_format_negative(self, generator):
        assert generator._format_amount(-100.0) == "-100.00"


class TestDateFormatting:
    def test_format_date_to_edi(self, generator):
        assert generator._format_date_edi("2024-01-15") == "20240115"

    def test_parse_date_from_edi(self, generator):
        assert generator._parse_date_edi("20240115") == "2024-01-15"

    def test_empty_date(self, generator):
        assert generator._format_date_edi("") == ""


class TestRoundTrip:
    def test_generate_parse_roundtrip(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        parsed = generator.parse(edi)
        assert parsed.remittance.total_paid == pytest.approx(sample_remittance.total_paid)

    def test_claim_id_roundtrip(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        parsed = generator.parse(edi)
        assert parsed.remittance.claims[0].claim_id == "CLM001"

    def test_line_count_roundtrip(self, generator, sample_remittance):
        edi = generator.generate(sample_remittance).edi_text
        parsed = generator.parse(edi)
        assert len(parsed.remittance.claims[0].service_lines) == len(sample_remittance.claims[0].service_lines)
