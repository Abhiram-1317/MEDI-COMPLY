"""Utilities for extracting and repairing JSON from messy LLM output."""

from __future__ import annotations

import json
import re
from typing import Optional


class JSONExtractionError(Exception):
    """Raised when JSON cannot be recovered from a response."""


class JSONRepair:
    """Extracts and repairs JSON from imperfect LLM responses."""

    @staticmethod
    def extract_json(text: str) -> Optional[dict]:
        """Attempt to extract valid JSON from the supplied text."""
        if not text or not text.strip():
            return None

        raw = text.strip()

        # Strategy 1: direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Strategy 2: markdown blocks
        markdown = JSONRepair._extract_from_markdown(raw)
        if markdown:
            try:
                return json.loads(markdown)
            except json.JSONDecodeError:
                pass

        # Strategy 3: locate JSON-like substring
        candidate = JSONRepair._find_json_substring(raw)
        if candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                repaired_candidate = JSONRepair._repair_json_string(candidate)
                try:
                    return json.loads(repaired_candidate)
                except json.JSONDecodeError:
                    pass

        # Strategy 4: repair entire text
        repaired_full = JSONRepair._repair_json_string(raw)
        try:
            return json.loads(repaired_full)
        except json.JSONDecodeError:
            pass

        # Strategy 5: optional json5 parse (if available)
        try:
            import json5  # type: ignore

            return json5.loads(raw)
        except Exception:
            return None

    @staticmethod
    def extract_json_or_raise(text: str, context: str = "") -> dict:
        """Extract JSON or raise a JSONExtractionError with context."""
        result = JSONRepair.extract_json(text)
        if result is None:
            preview = (text or "").strip()[:200]
            raise JSONExtractionError(
                "Failed to extract JSON from LLM response. "
                f"Context: {context}. Preview: {preview}"
            )
        return result

    @staticmethod
    def _extract_from_markdown(text: str) -> Optional[str]:
        """Return the first block inside ```json ``` or generic fences."""
        patterns = [
            r"```json\s*\n?(.*?)\n?\s*```",
            r"```\s*\n?(.*?)\n?\s*```",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                snippet = match.group(1).strip()
                if snippet:
                    return snippet
        return None

    @staticmethod
    def _find_json_substring(text: str) -> Optional[str]:
        """Scan the text for a JSON object or array by brace matching."""
        start_pairs = {"{": "}", "[": "]"}
        for idx, char in enumerate(text):
            if char not in start_pairs:
                continue
            closing = start_pairs[char]
            depth = 0
            in_string = False
            escape = False
            for jdx in range(idx, len(text)):
                c = text[jdx]
                if escape:
                    escape = False
                    continue
                if c == "\\":
                    escape = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == char:
                    depth += 1
                elif c == closing:
                    depth -= 1
                    if depth == 0:
                        return text[idx : jdx + 1]
            # unmatched closing; append and return best effort
            return text[idx:] + closing
        return None

    @staticmethod
    def _repair_json_string(text: str) -> str:
        """Apply common cleanup operations to a JSON-like string."""
        snippet = text.strip()

        # Drop trailing commas before closing braces/brackets
        snippet = re.sub(r",\s*([}\]])", r"\1", snippet)

        # Replace single quotes when double quotes are absent
        if '"' not in snippet and "'" in snippet:
            snippet = snippet.replace("'", '"')

        # Remove // comments
        snippet = re.sub(r"//.*?(?=\n|$)", "", snippet)

        # Remove /* */ comments
        snippet = re.sub(r"/\*.*?\*/", "", snippet, flags=re.DOTALL)

        # Ensure the string starts at the first brace/bracket
        if snippet and snippet[0] not in "[{":
            brace_pos = min(
                [pos for pos in (snippet.find("{"), snippet.find("[")) if pos >= 0]
                or [len(snippet)]
            )
            snippet = snippet[brace_pos:]

        return snippet
