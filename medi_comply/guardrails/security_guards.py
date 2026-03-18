"""
MEDI-COMPLY — Security Guards

Regex-based scanning utilities for catching PHI leaks and prompt injection
attempts in LLM outputs.
"""

import re
from typing import Any
from pydantic import BaseModel

class PHIMatch(BaseModel):
    phi_type: str
    matched_text: str
    position: tuple[int, int]
    pattern_used: str

class InjectionMatch(BaseModel):
    injection_type: str
    matched_text: str
    position: tuple[int, int]
    risk_level: str

class SecurityGuards:
    """Centralized security scanning utilities."""
    
    # Common PHI patterns
    PHI_PATTERNS = {
        "SSN": r'\b\d{3}-\d{2}-\d{4}\b',
        "MRN": r'\b(?:MRN|Medical Record)[:#\s]*\d{5,10}\b',
        "PHONE": r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
        "EMAIL": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        "DOB": r'\b(?:DOB|Date of Birth)[:\s]*\d{1,2}/\d{1,2}/\d{2,4}\b',
        "INSURANCE_ID": r'\b[A-Z]{3}\d{9}\b'
    }
    
    # Injection/jailbreak patterns
    INJECTION_PATTERNS = [
        (r'ignore\s+(previous|above|all)\s+(instructions?|prompts?|rules?)', "INSTRUCTION_OVERRIDE", "HIGH"),
        (r'disregard\s+(previous|above|all)', "INSTRUCTION_OVERRIDE", "HIGH"),
        (r'you\s+are\s+(now|actually)\s+a', "ROLE_SWITCH", "HIGH"),
        (r'forget\s+(everything|all|your)', "INSTRUCTION_OVERRIDE", "HIGH"),
        (r'new\s+instructions?:', "INSTRUCTION_OVERRIDE", "HIGH"),
        (r'system\s*:\s*', "SYSTEM_SPOOF", "HIGH"),
        (r'<\|.*?\|>', "SPECIAL_TOKEN", "MEDIUM"),
        (r'```(?:python|bash|sh|javascript)', "CODE_EXECUTION", "HIGH"),
        (r'import\s+(?:os|sys|subprocess)', "CODE_EXECUTION", "CRITICAL"),
        (r'eval\s*\(', "CODE_EXECUTION", "CRITICAL"),
        (r'exec\s*\(', "CODE_EXECUTION", "CRITICAL"),
        (r'__import__', "CODE_EXECUTION", "CRITICAL")
    ]
    
    @classmethod
    def scan_for_phi(cls, text: str) -> list[PHIMatch]:
        """Scan text for any PHI patterns. Return all matches."""
        if not text:
            return []
            
        matches = []
        for phi_type, pattern in cls.PHI_PATTERNS.items():
            for match in re.finditer(pattern, text, re.IGNORECASE):
                matches.append(PHIMatch(
                    phi_type=phi_type,
                    matched_text=match.group(),
                    position=(match.start(), match.end()),
                    pattern_used=pattern
                ))
        return matches
    
    @classmethod
    def scan_for_injection(cls, text: str) -> list[InjectionMatch]:
        """Scan text for prompt injection patterns."""
        if not text:
            return []
            
        matches = []
        for pattern, inj_type, context_risk in cls.INJECTION_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                matches.append(InjectionMatch(
                    injection_type=inj_type,
                    matched_text=match.group(),
                    position=(match.start(), match.end()),
                    risk_level=context_risk
                ))
        return matches
    
    @classmethod
    def redact_phi(cls, text: str) -> str:
        """Replace detected PHI with redaction markers [REDACTED-SSN], etc."""
        if not text:
            return text
            
        redacted_text = text
        for phi_type, pattern in cls.PHI_PATTERNS.items():
            redacted_text = re.sub(pattern, f"[REDACTED-{phi_type}]", redacted_text, flags=re.IGNORECASE)
            
        return redacted_text
    
    @classmethod
    def sanitize_output(cls, output: Any) -> Any:
        """Deep-scan a dict/list and redact any PHI found in string values."""
        if isinstance(output, str):
            return cls.redact_phi(output)
        elif isinstance(output, dict):
            return {k: cls.sanitize_output(v) for k, v in output.items()}
        elif isinstance(output, list):
            return [cls.sanitize_output(v) for v in output]
        return output
    
    @classmethod
    def validate_input_safe(cls, input_text: str) -> tuple[bool, list[str]]:
        """Check if input text is safe to process (no injection attempts)."""
        matches = cls.scan_for_injection(input_text)
        if matches:
            reasons = [f"Found {m.injection_type}: '{m.matched_text}'" for m in matches]
            return False, reasons
        return True, []
