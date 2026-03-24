# 🔥 MEDI-COMPLY Edge Case Handling Matrix

## Overview

MEDI-COMPLY handles 11 documented edge cases with explicit detection methods 
and handling protocols. Every edge case has been implemented in 
`medi_comply/core/edge_cases.py` and tested in `medi_comply/tests/test_edge_cases.py`.

## Matrix

| # | Edge Case | Detection Method | Severity | Action | Guideline |
|---|-----------|-----------------|----------|--------|-----------|
| 1 | **Ambiguous Diagnosis** — "suspected", "rule out" | NER assertion classifier detects uncertainty markers | MEDIUM | Outpatient: code symptoms only (OCG IV.D). Inpatient: code as confirmed (OCG II.H) | OCG Sec IV.D / II.H |
| 2 | **Combination Codes** — DM with complications | Knowledge graph detects co-occurring conditions with combo code available | MEDIUM | Use combination code. If combo exists → use it. If not → "Use Additional" pattern | OCG I.C.4.a |
| 3 | **Conflicting Information** — contradictions in doc | NER extracts both positive and negative assertions for same condition | HIGH | Flag conflict. Cite both locations. ESCALATE to human coder | — |
| 4 | **Missing Laterality** — no left/right specified | Laterality field is NULL for condition that requires it | MEDIUM | Do NOT assume. Use unspecified side code. Flag for query to physician | OCG I.A.16 |
| 5 | **Duplicate Claim** — same claim submitted twice | Claim hash matching + fuzzy matching (Jaccard >0.9) | HIGH | Exact dup → REJECT. Near-dup → flag for review | — |
| 6 | **Retro Auth** — auth after service rendered | Date of service vs submission date comparison | MEDIUM-HIGH | Check retro auth policy. Emergency within 72hrs = allowed. Non-emergency = generally denied | Payer-specific |
| 7 | **Unlisted Procedure** — no specific CPT code | CPT lookup returns no exact match | MEDIUM | Use unlisted procedure code for body area. Require op note. Flag for manual pricing | — |
| 8 | **Upcoding Attempt** — code more severe than docs | Semantic Layer 4 detects evidence gap between documentation and code severity | CRITICAL | HARD BLOCK. Log incident. Suggest correct lower-severity code | — |
| 9 | **Prompt Injection** — malicious input | Input sanitization + injection detection patterns | CRITICAL | BLOCK + ALERT. Log full incident. Do not process | — |
| 10 | **Knowledge Staleness** — outdated KB | Version check: KB effective date vs date of service | LOW-HIGH | Use version-stamped KB. If DOS > KB date → warn. If gap > 90 days → escalate | — |
| 11 | **Multi-Payer COB** — multiple insurance plans | Eligibility check returns multiple active plans | MEDIUM | Apply Coordination of Benefits rules. Determine primary/secondary payer | COB Rules |

## Severity Levels

| Level | Description | Action |
|-------|-------------|--------|
| LOW | Informational | Auto-handle, log only |
| MEDIUM | Warning | Continue but flag for review |
| HIGH | Requires attention | Escalate to human reviewer |
| CRITICAL | Immediate action | Block output, security alert |

## Implementation Reference

- **Module:** `medi_comply/core/edge_cases.py`
- **Tests:** `medi_comply/tests/test_edge_cases.py`
- **Class:** `EdgeCaseHandler`
- **Entry point:** `handler.run_all_checks(...)` for comprehensive checking
