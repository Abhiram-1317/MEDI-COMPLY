"""
MEDI-COMPLY — Tamper-evident hash chain.
"""

import json
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Any
from pydantic import BaseModel

from medi_comply.audit.audit_models import ChainVerificationResult

class HashChain:
    """Tamper-evident hash chain for audit records."""
    
    def __init__(self):
        self._last_hash: Optional[str] = None
        self._chain_length: int = 0
    
    @staticmethod
    def _canonical_serialize(data: dict) -> str:
        """Serialize dict to canonical JSON string."""
        def serialize_item(item: Any) -> Any:
             if isinstance(item, BaseModel):
                  return item.model_dump(mode='json')
             elif isinstance(item, datetime):
                  return item.isoformat()
             elif isinstance(item, dict):
                  return {k: serialize_item(v) for k, v in item.items()}
             elif isinstance(item, list):
                  return [serialize_item(i) for i in item]
             return item
        clean_data = serialize_item(data)
        return json.dumps(clean_data, separators=(',', ':'), sort_keys=True, default=str)
    
    def compute_record_hash(self, record_data: dict) -> str:
        """Compute SHA-256 hash of record data."""
        serialized = self._canonical_serialize(record_data)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    
    def create_chain_link(self, record_data: dict) -> tuple[str, Optional[str]]:
        """Create a new chain link."""
        record_data_copy = dict(record_data)
        record_data_copy["previous_record_hash"] = self._last_hash
        
        # Remove record_hash itself if present to avoid recursion
        if "record_hash" in record_data_copy:
            del record_data_copy["record_hash"]
            
        record_hash = self.compute_record_hash(record_data_copy)
        
        self._last_hash = record_hash
        self._chain_length += 1
        
        return record_hash, record_data_copy["previous_record_hash"]
    
    def verify_chain(self, records: list[dict]) -> ChainVerificationResult:
        """Verify the integrity of a sequence of audit records."""
        start_time = time.time()
        
        if not records:
             return ChainVerificationResult(
                 is_valid=True, records_checked=0, verification_time_ms=(time.time() - start_time) * 1000
             )
             
        for i, record in enumerate(records):
             rec_copy = dict(record)
             stored_hash = rec_copy.pop("record_hash", None)
             stored_prev_hash = rec_copy.get("previous_record_hash")
             
             if not stored_hash:
                 return ChainVerificationResult(
                     is_valid=False, records_checked=i, first_broken_link=i,
                     broken_details=f"Record at index {i} is missing its hash.",
                     verification_time_ms=(time.time() - start_time) * 1000
                 )
             
             expected_hash = self.compute_record_hash(rec_copy)
             if stored_hash != expected_hash:
                 return ChainVerificationResult(
                     is_valid=False, records_checked=i, first_broken_link=i,
                     broken_details=f"Record at index {i} has been tampered with (hash mismatch).",
                     verification_time_ms=(time.time() - start_time) * 1000
                 )
             
             if i > 0:
                 actual_prev_hash = records[i-1].get("record_hash")
                 if stored_prev_hash != actual_prev_hash:
                     return ChainVerificationResult(
                         is_valid=False, records_checked=i, first_broken_link=i,
                         broken_details=f"Record at index {i} has broken link to previous record.",
                         verification_time_ms=(time.time() - start_time) * 1000
                     )
                     
        return ChainVerificationResult(
            is_valid=True, records_checked=len(records),
            verification_time_ms=(time.time() - start_time) * 1000
        )
    
    def verify_single_record(self, record: dict, expected_hash: str) -> bool:
        rec_copy = dict(record)
        rec_copy.pop("record_hash", None)
        return self.compute_record_hash(rec_copy) == expected_hash
    
    def get_chain_length(self) -> int:
        return self._chain_length
        
    def get_last_hash(self) -> Optional[str]:
        return self._last_hash
