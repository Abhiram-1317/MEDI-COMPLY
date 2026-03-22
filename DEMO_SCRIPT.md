# MEDI-COMPLY — 3-Minute Judge Demo

> Goal: show MEDI-COMPLY is the only domain-specialized AI coder that *guarantees* compliance, proves it with tests, and delights with an audit-ready UI.

---

## ⏱️ Timeline (180 seconds)

### 0:00 – 0:30 · Hook & Stakes
- "Healthcare loses $262B/year to claim denials; 20% are simple coding errors. Existing AI coders hallucinate and have zero audit trail."
- Flash the hero slide (or README header) showing **103 tests passing** and the Compliance Cage callout.

### 0:30 – 1:00 · Proof via Tests
- Run `python run_demo.py --tests-only` (or reference pre-run) to show the **100-case golden suite + e2e** finishing in <5 s.
- Narrate: "Every edge case you care about—empty notes, messy abbreviations, Unicode, vitals-only—is locked in this suite."

### 1:00 – 1:30 · Launch the Experience
- Run `python run_demo.py --demo-only` (or highlight Streamlit is already live).
- On the sidebar, show the **7 pre-loaded scenarios** plus the custom slot. Mention patient context overrides.

### 1:30 – 2:15 · Walk Through Two Killer Scenarios
1. **🫀 NSTEMI Combo**
   - Click "Process" and highlight:
     - Code cards with reasoning chains + evidence quotes.
     - Compliance metrics: all 23 checks pass → "pass badge".
     - Audit trail snippet proving chain-of-custody.
2. **📝 Messy Abbreviated Note**
   - Show negation/abbreviation handling and how Compliance flags low confidence or escalations.

### 2:15 – 2:45 · Custom Judge Input
- Switch to "✍️ Custom Note".
- Paste the judge's quick example (e.g., "45yo female with pneumonia, denies chest pain...").
- Run once; show trace_id + metrics + JSON export expander.

### 2:45 – 3:00 · Close the Loop
- Recap pillars: **Zero hallucination**, **Compliance Cage**, **Audit Ledger**.
- Invite questions: "Want to drill into the audit report or the guardrail logic? It's all here." 

---

## Demo Checklist
- ✅ Tests tab open or screenshot ready.
- ✅ Streamlit already warmed up (first run takes 5–10 s to initialize the system).
- ✅ Copy-ready clinical note snippet for the custom slot.
- ✅ If time permits, mention hash-chained audit store + retry feedback loop.

Good luck — finish with "MEDI-COMPLY makes AI outputs admissible in court, not just in a slide deck."