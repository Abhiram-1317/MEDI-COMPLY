"""
MEDI-COMPLY — Negation, assertion, and hedging detector.

Determines whether a clinical entity is PRESENT, ABSENT, POSSIBLE,
HISTORICAL, FAMILY, or HYPOTHETICAL based on surrounding context.
Uses scope-aware trigger matching with pre-/post-negation patterns
and pseudo-negation filtering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class AssertionResult:
    """Result of assertion detection for a clinical entity."""
    assertion: str = "PRESENT"
    trigger_text: str = ""
    trigger_type: str = ""
    confidence: float = 0.95


# ---------------------------------------------------------------------------
# Trigger lists
# ---------------------------------------------------------------------------

PRE_NEGATION: list[str] = [
    "no evidence of", "no signs of", "no symptoms of", "no complaint of",
    "no complaints of", "no history of", "no h/o",
    "does not have", "doesn't have", "did not have",
    "has no", "had no", "fails to reveal",
    "not demonstrate", "not associated with",
    "negative for", "absence of", "free of",
    "unremarkable for", "ruled out", "rules out",
    "denies", "denied", "denying",
    "no", "not", "none", "neither", "never", "without",
    "negative", "declined", "r/o",
]

POST_NEGATION: list[str] = [
    "is absent", "was absent", "are absent",
    "is negative", "was negative", "are negative",
    "is ruled out", "was ruled out",
    "is unlikely", "was unlikely",
    "has been ruled out", "have been excluded",
    "not found", "not seen", "not present",
    "not identified", "not detected", "not appreciated",
]

PSEUDO_NEGATION: list[str] = [
    "no change", "no increase", "no decrease",
    "not only", "not just", "no longer",
    "no new", "not limited to",
    "gram negative", "gram-negative",
]

UNCERTAINTY_TRIGGERS: list[str] = [
    "cannot exclude", "cannot rule out",
    "differential includes", "may represent",
    "suspicious for", "suspicion of",
    "suggestive of", "compatible with",
    "concerning for", "consistent with",
    "possible", "possibly", "probable", "probably",
    "suspected", "consider", "could be", "might be",
    "questionable", "equivocal",
]

HISTORICAL_TRIGGERS: list[str] = [
    "remote history", "childhood history of",
    "history of", "h/o", "hx of",
    "status post", "s/p",
    "prior", "previous", "previously", "formerly",
    "past", "in the past", "years ago",
]

FAMILY_TRIGGERS: list[str] = [
    "family history of", "fhx", "fh",
    "mother had", "father had", "sibling with",
    "maternal history", "paternal history",
    "runs in family", "familial",
]

# Scope terminators
_SCOPE_TERMINATORS = re.compile(r"[.;:]|\bbut\b|\bhowever\b|\balthough\b|\bexcept\b", re.IGNORECASE)
_MAX_SCOPE_TOKENS = 6


# ---------------------------------------------------------------------------
# Negation Detector
# ---------------------------------------------------------------------------

class NegationDetector:
    """Detects negation, uncertainty, historical, and family assertions.

    Uses scope-aware trigger matching:
    - Pre-negation triggers appear *before* the entity.
    - Post-negation triggers appear *after* the entity.
    - Pseudo-negation patterns are filtered out to avoid false positives.
    - Scope is bounded by punctuation, conjunctions like "but", or a
      maximum token window.
    """

    def __init__(self) -> None:
        # Sort triggers longest-first for greedy matching
        self._pre_neg = sorted(PRE_NEGATION, key=len, reverse=True)
        self._post_neg = sorted(POST_NEGATION, key=len, reverse=True)
        self._pseudo = sorted(PSEUDO_NEGATION, key=len, reverse=True)
        self._uncertainty = sorted(UNCERTAINTY_TRIGGERS, key=len, reverse=True)
        self._historical = sorted(HISTORICAL_TRIGGERS, key=len, reverse=True)
        self._family = sorted(FAMILY_TRIGGERS, key=len, reverse=True)

    def detect(self, entity_text: str, context: str) -> AssertionResult:
        """Determine the assertion status of an entity in its context.

        Checks are applied in priority order: family → historical →
        pseudo-negation filter → pre-negation → post-negation →
        uncertainty → default PRESENT.

        Parameters
        ----------
        entity_text:
            The entity surface text.
        context:
            Surrounding sentence/clause text.

        Returns
        -------
        AssertionResult
        """
        if not entity_text or not context:
            return AssertionResult()

        # Family first (takes precedence)
        r = self._check_family(entity_text, context)
        if r:
            return r

        # Historical
        r = self._check_historical(entity_text, context)
        if r:
            return r

        # Pre-negation
        r = self._check_pre_negation(entity_text, context)
        if r:
            return r

        # Post-negation
        r = self._check_post_negation(entity_text, context)
        if r:
            return r

        # Uncertainty
        r = self._check_uncertainty(entity_text, context)
        if r:
            return r

        return AssertionResult(assertion="PRESENT", confidence=0.90)

    # -- Trigger checkers --------------------------------------------------

    def _check_pre_negation(self, entity_text: str, context: str) -> Optional[AssertionResult]:
        """Check for pre-negation triggers before the entity.

        Parameters
        ----------
        entity_text:
            Entity surface form.
        context:
            Surrounding text.

        Returns
        -------
        Optional[AssertionResult]
        """
        ctx_lower = context.lower()
        ent_lower = entity_text.lower()
        ent_pos = ctx_lower.find(ent_lower)
        if ent_pos < 0:
            return None

        prefix = ctx_lower[:ent_pos]

        # Check pseudo-negation first
        if self._check_pseudo_negation(prefix):
            return None

        for trigger in self._pre_neg:
            trig_pos = prefix.rfind(trigger)
            if trig_pos < 0:
                continue

            # Determine scope: trigger text to the entity
            scope_start, scope_end = self._determine_scope(trig_pos + len(trigger), ctx_lower)

            if self._entity_in_scope(ent_pos, (scope_start, scope_end)):
                return AssertionResult(
                    assertion="ABSENT",
                    trigger_text=trigger,
                    trigger_type="PRE_NEGATION",
                    confidence=0.93,
                )
        return None

    def _check_post_negation(self, entity_text: str, context: str) -> Optional[AssertionResult]:
        """Check for post-negation triggers after the entity.

        Parameters
        ----------
        entity_text:
            Entity surface form.
        context:
            Surrounding text.

        Returns
        -------
        Optional[AssertionResult]
        """
        ctx_lower = context.lower()
        ent_lower = entity_text.lower()
        ent_pos = ctx_lower.find(ent_lower)
        if ent_pos < 0:
            return None

        suffix = ctx_lower[ent_pos + len(ent_lower):]
        for trigger in self._post_neg:
            trig_pos = suffix.find(trigger)
            if trig_pos >= 0 and trig_pos < 30:
                return AssertionResult(
                    assertion="ABSENT",
                    trigger_text=trigger,
                    trigger_type="POST_NEGATION",
                    confidence=0.90,
                )
        return None

    def _check_pseudo_negation(self, context: str) -> bool:
        """Return True if context contains a pseudo-negation pattern.

        Parameters
        ----------
        context:
            The text *before* the entity (lowered).

        Returns
        -------
        bool
        """
        for pseudo in self._pseudo:
            if pseudo in context:
                return True
        return False

    def _check_uncertainty(self, entity_text: str, context: str) -> Optional[AssertionResult]:
        """Check for uncertainty triggers.

        Parameters
        ----------
        entity_text:
            Entity surface form.
        context:
            Surrounding text.

        Returns
        -------
        Optional[AssertionResult]
        """
        ctx_lower = context.lower()
        ent_lower = entity_text.lower()
        ent_pos = ctx_lower.find(ent_lower)
        if ent_pos < 0:
            return None

        # Check prefix for uncertainty triggers
        prefix = ctx_lower[:ent_pos]
        for trigger in self._uncertainty:
            if trigger in prefix:
                return AssertionResult(
                    assertion="POSSIBLE",
                    trigger_text=trigger,
                    trigger_type="UNCERTAINTY",
                    confidence=0.85,
                )

        # Also check close suffix
        suffix = ctx_lower[ent_pos + len(ent_lower):ent_pos + len(ent_lower) + 40]
        for trigger in self._uncertainty:
            if trigger in suffix:
                return AssertionResult(
                    assertion="POSSIBLE",
                    trigger_text=trigger,
                    trigger_type="UNCERTAINTY",
                    confidence=0.82,
                )
        return None

    def _check_historical(self, entity_text: str, context: str) -> Optional[AssertionResult]:
        """Check for historical triggers.

        Parameters
        ----------
        entity_text:
            Entity surface form.
        context:
            Surrounding text.

        Returns
        -------
        Optional[AssertionResult]
        """
        ctx_lower = context.lower()
        ent_lower = entity_text.lower()
        ent_pos = ctx_lower.find(ent_lower)
        if ent_pos < 0:
            return None

        prefix = ctx_lower[:ent_pos]
        for trigger in self._historical:
            if trigger in prefix:
                return AssertionResult(
                    assertion="HISTORICAL",
                    trigger_text=trigger,
                    trigger_type="HISTORICAL",
                    confidence=0.90,
                )
        return None

    def _check_family(self, entity_text: str, context: str) -> Optional[AssertionResult]:
        """Check for family history triggers.

        Parameters
        ----------
        entity_text:
            Entity surface form.
        context:
            Surrounding text.

        Returns
        -------
        Optional[AssertionResult]
        """
        ctx_lower = context.lower()
        ent_lower = entity_text.lower()
        ent_pos = ctx_lower.find(ent_lower)
        if ent_pos < 0:
            return None

        prefix = ctx_lower[:ent_pos]
        for trigger in self._family:
            if trigger in prefix:
                return AssertionResult(
                    assertion="FAMILY",
                    trigger_text=trigger,
                    trigger_type="FAMILY",
                    confidence=0.92,
                )
        return None

    # -- Scope helpers -----------------------------------------------------

    def _determine_scope(self, trigger_end: int, context: str) -> tuple[int, int]:
        """Determine the scope (start, end) from a trigger's end position.

        Scope extends from trigger_end until a terminator is hit or
        the max token window is reached.

        Parameters
        ----------
        trigger_end:
            Character position right after the trigger.
        context:
            Lowered full context.

        Returns
        -------
        tuple[int, int]
        """
        scope_start = trigger_end
        remaining = context[trigger_end:]

        # Find nearest scope terminator
        m = _SCOPE_TERMINATORS.search(remaining)
        if m:
            scope_end = trigger_end + m.start()
        else:
            scope_end = len(context)

        # Also cap by token window
        tokens = remaining.split()
        if len(tokens) > _MAX_SCOPE_TOKENS:
            # Find the char position after MAX tokens
            token_count = 0
            pos = trigger_end
            for ch_idx, ch in enumerate(remaining):
                if ch == " ":
                    token_count += 1
                    if token_count >= _MAX_SCOPE_TOKENS:
                        scope_end = min(scope_end, trigger_end + ch_idx)
                        break
        return scope_start, scope_end

    @staticmethod
    def _entity_in_scope(entity_pos: int, scope: tuple[int, int]) -> bool:
        """Check whether the entity falls within the negation scope.

        Parameters
        ----------
        entity_pos:
            Start char of the entity in context.
        scope:
            (start, end) char range.

        Returns
        -------
        bool
        """
        return scope[0] <= entity_pos <= scope[1]
