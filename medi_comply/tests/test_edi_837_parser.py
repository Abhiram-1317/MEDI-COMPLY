import pytest

from medi_comply.integrations.edi_837_parser import (
    ClaimFrequencyCode,
    ClaimFilingIndicator,
    DiagnosisQualifier,
    EDI837Parser,
    EDI837Type,
    EDIClaim,
    EDIDiagnosis,
    EDIProvider,
    EDISegment,
    EDISubscriber,
    EDIServiceLine,
    PlaceOfService,
)


@pytest.fixture
def parser():
    return EDI837Parser()


@pytest.fixture
def sample_837p():
    return (
        "ISA*00*          *00*          *ZZ*MEDICOMPLY      *ZZ*BCBS           *240115*1030*^*00501*000000001*0*P*:~\n"
        "GS*HC*MEDICOMPLY*BCBS*20240115*1030*1*X*005010X222A1~\n"
        "ST*837*0001*005010X222A1~\n"
        "BHT*0019*00*BATCH001*20240115*1030*CH~\n"
        "NM1*85*2*SPRINGFIELD MEDICAL*****XX*1234567890~\n"
        "N3*123 MAIN ST~\n"
        "N4*SPRINGFIELD*IL*62701~\n"
        "SBR*P*18*GRP001****CI~\n"
        "NM1*IL*1*DOE*JANE****MI*SUB12345~\n"
        "DMG*D8*19600322*F~\n"
        "NM1*PR*2*BLUE CROSS BLUE SHIELD*****PI*BCBS001~\n"
        "CLM*CLAIM001*850***11:B:1*Y*A*Y*Y~\n"
        "HI*ABK:I214*ABF:E1122*ABF:N1831~\n"
        "NM1*82*1*SMITH*JOHN****XX*9876543210~\n"
        "SV1*HC:99223:25*500*UN*1***1~\n"
        "DTP*472*D8*20240115~\n"
        "SV1*HC:93000*150*UN*1***1:2~\n"
        "DTP*472*D8*20240115~\n"
        "SV1*HC:80053*100*UN*1***1:2:3~\n"
        "DTP*472*D8*20240115~\n"
        "SE*20*0001~\n"
        "GE*1*1~\n"
        "IEA*1*000000001~\n"
    )


@pytest.fixture
def sample_claim():
    """Pre-built EDIClaim for testing build methods."""
    return EDIClaim(
        claim_id="TEST-CLM-001",
        claim_type=EDI837Type.PROFESSIONAL,
        total_charge=650.00,
        place_of_service="11",
        billing_provider=EDIProvider(
            entity_type="2", last_name="TEST CLINIC", npi="1234567890", provider_type="billing"
        ),
        subscriber=EDISubscriber(
            payer_name="TEST PAYER",
            payer_id="PAYER001",
            subscriber_id="MEM001",
            subscriber_last_name="TEST",
            subscriber_first_name="PATIENT",
            subscriber_dob="19800101",
            subscriber_gender="M",
            claim_filing_indicator="CI",
        ),
        diagnoses=[
            EDIDiagnosis(code="E11.22", qualifier="ABK", sequence=1, is_primary=True),
            EDIDiagnosis(code="N18.31", qualifier="ABF", sequence=2, is_primary=False),
        ],
        service_lines=[
            EDIServiceLine(line_number=1, procedure_code="99214", charge_amount=250.00, units=1, diagnosis_pointers=[1]),
            EDIServiceLine(line_number=2, procedure_code="80053", charge_amount=100.00, units=1, diagnosis_pointers=[1, 2]),
        ],
    )


class TestSeparatorDetection:
    def test_detect_standard_separators(self, parser, sample_837p):
        elem, comp, term = parser._detect_separators(sample_837p)
        assert (elem, comp, term) == ("*", ":", "~")

    def test_detect_custom_separators(self, parser):
        custom = (
            "ISA|00|          |00|          |ZZ|SENDER         |ZZ|RECEIVER       |240101|1234|^|00501|000000001|0|P|!~"
        )
        elem, comp, term = parser._detect_separators(custom)
        assert elem == "|"
        assert comp == "!"
        assert term == "~"

    def test_empty_input(self, parser):
        result = parser.parse("")
        assert result.success is False
        assert "Empty EDI input" in result.errors

    def test_short_isa(self, parser):
        elem, comp, term = parser._detect_separators("ISA*")
        assert elem == "*"
        assert comp == ":"
        assert term == "~"


class TestParseISA:
    def _get_isa_segment(self, parser, sample_837p):
        parser._detect_separators(sample_837p)
        segments = parser._parse_segments(sample_837p)
        return segments[0]

    def test_parse_sender_receiver(self, parser, sample_837p):
        isa_segment = self._get_isa_segment(parser, sample_837p)
        vals = parser._parse_isa(isa_segment)
        assert vals["sender_id"].strip() == "MEDICOMPLY"
        assert vals["receiver_id"].strip() == "BCBS"

    def test_parse_control_number(self, parser, sample_837p):
        isa_segment = self._get_isa_segment(parser, sample_837p)
        vals = parser._parse_isa(isa_segment)
        assert vals["control_number"] == "000000001"

    def test_parse_date_time(self, parser, sample_837p):
        isa_segment = self._get_isa_segment(parser, sample_837p)
        vals = parser._parse_isa(isa_segment)
        assert vals["date"] == "240115"
        assert vals["time"] == "1030"

    def test_parse_version(self, parser, sample_837p):
        isa_segment = self._get_isa_segment(parser, sample_837p)
        version = parser._get_element(isa_segment, 11)
        assert version == "00501"


class TestParseGS:
    def test_parse_functional_code(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        assert result.envelope.transaction_type == "HC"

    def test_parse_version_837p(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        assert result.envelope.version == "005010X222A1"

    def test_parse_group_control(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        assert result.envelope.group_control_number == "1"


class TestParseNM1:
    def test_parse_billing_provider(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        claim = result.claims[0]
        assert claim.billing_provider is not None
        assert claim.billing_provider.npi == "1234567890"

    def test_parse_subscriber(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        claim = result.claims[0]
        assert claim.subscriber is not None
        assert claim.subscriber.subscriber_id == "SUB12345"

    def test_parse_payer(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        claim = result.claims[0]
        assert claim.subscriber is not None
        assert claim.subscriber.payer_id == "BCBS001"
        assert claim.subscriber.payer_name == "BLUE CROSS BLUE SHIELD"

    def test_parse_rendering_provider(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        claim = result.claims[0]
        assert claim.rendering_provider is not None
        assert claim.rendering_provider.npi == "9876543210"

    def test_parse_person_vs_org(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        claim = result.claims[0]
        assert claim.billing_provider.entity_type == "2"
        assert claim.rendering_provider.entity_type == "1"


class TestParseCLM:
    def _get_clm_segment(self, parser, sample_837p):
        parser._detect_separators(sample_837p)
        segments = parser._parse_segments(sample_837p)
        return next(seg for seg in segments if seg.segment_id == "CLM")

    def test_parse_claim_id(self, parser, sample_837p):
        clm_segment = self._get_clm_segment(parser, sample_837p)
        values = parser._parse_clm(clm_segment)
        assert values["claim_id"] == "CLAIM001"

    def test_parse_total_charge(self, parser, sample_837p):
        clm_segment = self._get_clm_segment(parser, sample_837p)
        values = parser._parse_clm(clm_segment)
        assert values["total_charge"] == 850.0

    def test_parse_place_of_service(self, parser, sample_837p):
        clm_segment = self._get_clm_segment(parser, sample_837p)
        values = parser._parse_clm(clm_segment)
        assert values["place_of_service"] == "11"

    def test_parse_frequency_code(self, parser, sample_837p):
        clm_segment = self._get_clm_segment(parser, sample_837p)
        values = parser._parse_clm(clm_segment)
        assert values["frequency_code"] == ClaimFrequencyCode.ORIGINAL.value

    def test_parse_claim_indicators(self, parser, sample_837p):
        clm_segment = self._get_clm_segment(parser, sample_837p)
        values = parser._parse_clm(clm_segment)
        assert values["provider_signature"] is True
        assert values["assignment_of_benefits"] is True
        assert values["release_of_info"] is True


class TestParseHI:
    def test_parse_primary_diagnosis(self, parser):
        seg = EDISegment(segment_id="HI", elements=["ABK:I214"], raw_text="")
        diagnoses = parser._parse_hi(seg)
        assert diagnoses[0].qualifier == DiagnosisQualifier.ABK.value
        assert diagnoses[0].is_primary is True

    def test_parse_secondary_diagnoses(self, parser):
        seg = EDISegment(segment_id="HI", elements=["ABK:I214", "ABF:E1122"], raw_text="")
        diagnoses = parser._parse_hi(seg)
        assert diagnoses[1].qualifier == DiagnosisQualifier.ABF.value
        assert diagnoses[1].is_primary is False

    def test_parse_icd10_dot_insertion(self, parser):
        seg = EDISegment(segment_id="HI", elements=["ABK:E1122"], raw_text="")
        diagnoses = parser._parse_hi(seg)
        assert diagnoses[0].code == "E11.22"

    def test_parse_short_icd10(self, parser):
        seg = EDISegment(segment_id="HI", elements=["ABK:I214"], raw_text="")
        diagnoses = parser._parse_hi(seg)
        assert diagnoses[0].code == "I21.4"

    def test_parse_three_char_code(self, parser):
        seg = EDISegment(segment_id="HI", elements=["ABK:I10"], raw_text="")
        diagnoses = parser._parse_hi(seg)
        assert diagnoses[0].code == "I10"

    def test_parse_multiple_diagnoses(self, parser):
        seg = EDISegment(segment_id="HI", elements=["ABK:I214", "ABF:E1122", "ABF:N1831"], raw_text="")
        diagnoses = parser._parse_hi(seg)
        assert [dx.code for dx in diagnoses] == ["I21.4", "E11.22", "N18.31"]


class TestParseSV1:
    def test_parse_cpt_code(self, parser):
        seg = EDISegment(segment_id="SV1", elements=["HC:99223:25", "500", "UN", "1", "11", "", "1"], raw_text="")
        line = parser._parse_sv1(seg)
        assert line.procedure_code == "99223"

    def test_parse_modifiers(self, parser):
        seg = EDISegment(segment_id="SV1", elements=["HC:99223:25:59", "500", "UN", "1", "11", "", "1"], raw_text="")
        line = parser._parse_sv1(seg)
        assert line.modifiers == ["25", "59"]

    def test_parse_charge_amount(self, parser):
        seg = EDISegment(segment_id="SV1", elements=["HC:99223", "500", "UN", "1", "11", "", "1"], raw_text="")
        line = parser._parse_sv1(seg)
        assert line.charge_amount == 500.0

    def test_parse_units(self, parser):
        seg = EDISegment(segment_id="SV1", elements=["HC:99223", "500", "UN", "2", "11", "", "1"], raw_text="")
        line = parser._parse_sv1(seg)
        assert line.units == 2.0

    def test_parse_diagnosis_pointers(self, parser):
        seg = EDISegment(segment_id="SV1", elements=["HC:99223", "500", "UN", "1", "11", "", "1:2:3"], raw_text="")
        line = parser._parse_sv1(seg)
        assert line.diagnosis_pointers == [1, 2, 3]


class TestParseDTP:
    def test_parse_service_date(self, parser):
        seg = EDISegment(segment_id="DTP", elements=["472", "D8", "20240115"], raw_text="")
        dtp = parser._parse_dtp(seg)
        assert dtp == {"qualifier": "472", "format": "D8", "value": "20240115"}

    def test_parse_admission_date(self, parser):
        seg = EDISegment(segment_id="DTP", elements=["435", "D8", "20240101"], raw_text="")
        dtp = parser._parse_dtp(seg)
        assert dtp["qualifier"] == "435"
        assert dtp["value"] == "20240101"

    def test_parse_date_range(self, parser):
        seg = EDISegment(segment_id="DTP", elements=["472", "RD8", "20240101-20240105"], raw_text="")
        dtp = parser._parse_dtp(seg)
        assert dtp["format"] == "RD8"
        assert dtp["value"] == "20240101-20240105"

    def test_parse_onset_date(self, parser):
        seg = EDISegment(segment_id="DTP", elements=["431", "D8", "20240108"], raw_text="")
        dtp = parser._parse_dtp(seg)
        assert dtp["qualifier"] == "431"
        assert dtp["value"] == "20240108"


class TestParseFullMessage:
    def test_parse_837p_complete(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        assert result.success is True

    def test_parse_finds_claims(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        assert len(result.claims) >= 1

    def test_parse_claim_has_diagnoses(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        claim = result.claims[0]
        assert len(claim.diagnoses) == 3

    def test_parse_claim_has_lines(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        claim = result.claims[0]
        assert len(claim.service_lines) == 3

    def test_parse_claim_has_providers(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        claim = result.claims[0]
        assert claim.billing_provider is not None
        assert claim.rendering_provider is not None

    def test_parse_claim_has_subscriber(self, parser, sample_837p):
        result = parser.parse(sample_837p)
        claim = result.claims[0]
        assert claim.subscriber is not None
        assert claim.subscriber.subscriber_id == "SUB12345"


class TestBuild837P:
    def test_build_success(self, parser, sample_claim):
        build = parser.build_837p(sample_claim)
        assert build.success is True

    def test_build_has_isa(self, parser, sample_claim):
        build = parser.build_837p(sample_claim)
        assert "ISA" in build.edi_text.split("\n")[0]

    def test_build_has_clm(self, parser, sample_claim):
        build = parser.build_837p(sample_claim)
        assert "CLM*TEST-CLM-001" in build.edi_text

    def test_build_has_hi(self, parser, sample_claim):
        build = parser.build_837p(sample_claim)
        assert "HI*ABK:" in build.edi_text

    def test_build_has_sv1(self, parser, sample_claim):
        build = parser.build_837p(sample_claim)
        assert "SV1*HC:99214" in build.edi_text


class TestICD10Formatting:
    def test_format_E1122(self, parser):
        assert parser._format_icd10_code("E1122") == "E11.22"

    def test_format_I214(self, parser):
        assert parser._format_icd10_code("I214") == "I21.4"

    def test_format_N1831(self, parser):
        assert parser._format_icd10_code("N1831") == "N18.31"

    def test_format_already_dotted(self, parser):
        assert parser._format_icd10_code("E11.22") == "E11.22"

    def test_strip_dot(self, parser):
        assert parser._strip_icd10_dot("E11.22") == "E1122"

    def test_format_three_char(self, parser):
        assert parser._format_icd10_code("I10") == "I10"


class TestDateFormatting:
    def test_format_edi_date(self, parser):
        assert parser._format_date("20240115") == "2024-01-15"

    def test_format_empty_date(self, parser):
        assert parser._format_date("") is None

    def test_format_invalid_date(self, parser):
        assert parser._format_date("2024-01-15") is None


class TestClaimValidation:
    def test_valid_claim(self, parser, sample_claim):
        claim = sample_claim.copy(deep=True)
        claim.total_charge = sum(line.charge_amount for line in claim.service_lines)
        validation = parser.validate_claim(claim)
        assert validation["valid"] is True
        assert validation["errors"] == []

    def test_missing_claim_id(self, parser, sample_claim):
        claim = sample_claim.copy(deep=True)
        claim.claim_id = ""
        validation = parser.validate_claim(claim)
        assert "Missing claim ID" in validation["errors"]

    def test_missing_diagnoses(self, parser, sample_claim):
        claim = sample_claim.copy(deep=True)
        claim.diagnoses = []
        validation = parser.validate_claim(claim)
        assert "At least one diagnosis required" in validation["errors"]

    def test_missing_lines(self, parser, sample_claim):
        claim = sample_claim.copy(deep=True)
        claim.service_lines = []
        validation = parser.validate_claim(claim)
        assert "At least one service line required" in validation["errors"]

    def test_charge_mismatch(self, parser, sample_claim):
        validation = parser.validate_claim(sample_claim)
        assert "Total charge does not match sum of service lines" in validation["warnings"]

    def test_invalid_cpt_format(self, parser, sample_claim):
        claim = sample_claim.copy(deep=True)
        claim.service_lines = [EDIServiceLine(line_number=1, procedure_code="99", charge_amount=10.0, units=1, diagnosis_pointers=[1])]
        validation = parser.validate_claim(claim)
        assert any("Invalid procedure code" in err for err in validation["errors"])


class TestConversion:
    def test_to_internal_claim(self, parser, sample_claim):
        claim = sample_claim.copy(deep=True)
        internal = parser.to_internal_claim(claim)
        assert internal["claim_id"] == claim.claim_id
        assert internal["provider_id"] == claim.billing_provider.npi
        assert internal["diagnosis_codes"][0]["code"] == "E11.22"
        assert len(internal["line_items"]) == 2

    def test_to_internal_diagnoses(self, parser, sample_claim):
        claim = sample_claim.copy(deep=True)
        internal = parser.to_internal_claim(claim)
        codes = [d["code"] for d in internal["diagnosis_codes"]]
        assert codes == ["E11.22", "N18.31"]

    def test_to_internal_line_items(self, parser, sample_claim):
        claim = sample_claim.copy(deep=True)
        internal = parser.to_internal_claim(claim)
        assert internal["line_items"][0]["cpt_code"] == "99214"
        assert internal["line_items"][1]["diagnosis_pointers"] == [1, 2]

    def test_from_internal_claim(self, parser):
        internal = {
            "claim_id": "INTERNAL-1",
            "claim_type": EDI837Type.PROFESSIONAL.value,
            "total_charge": 200.0,
            "diagnosis_codes": [
                {"code": "E11.22", "sequence": 1, "is_primary": True},
                {"code": "N18.31", "sequence": 2, "is_primary": False},
            ],
            "line_items": [
                {
                    "cpt_code": "99213",
                    "modifiers": ["25"],
                    "charge": 120.0,
                    "units": 1,
                    "place_of_service": PlaceOfService.OFFICE.value,
                    "diagnosis_pointers": [1],
                    "service_date": "2024-01-15",
                }
            ],
        }
        claim = parser.from_internal_claim(internal)
        assert claim.claim_id == "INTERNAL-1"
        assert len(claim.diagnoses) == 2
        assert claim.service_lines[0].procedure_code == "99213"


class TestRoundTrip:
    def test_parse_build_parse(self, parser, sample_837p):
        first = parser.parse(sample_837p)
        claim = first.claims[0]
        built = parser.build_837p(claim)
        second = parser.parse(built.edi_text)
        assert second.claims[0].claim_id == claim.claim_id

    def test_diagnoses_roundtrip(self, parser, sample_837p):
        first = parser.parse(sample_837p)
        claim = first.claims[0]
        built = parser.build_837p(claim)
        second = parser.parse(built.edi_text)
        codes = [dx.code for dx in second.claims[0].diagnoses]
        assert codes[0] == claim.diagnoses[0].code

    def test_lines_roundtrip(self, parser, sample_837p):
        first = parser.parse(sample_837p)
        claim = first.claims[0]
        built = parser.build_837p(claim)
        second = parser.parse(built.edi_text)
        assert len(second.claims[0].service_lines) == len(claim.service_lines)
