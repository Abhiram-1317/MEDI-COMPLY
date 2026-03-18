"""
MEDI-COMPLY — Immutable, append-only audit record storage.
"""

import sqlite3
import json
import contextlib
from datetime import datetime
from typing import Optional, Union

from medi_comply.audit.audit_models import (
    WorkflowTrace, AuditSearchResult, ChainVerificationResult, AuditStatistics
)
from medi_comply.audit.hash_chain import HashChain


class DuplicateAuditRecordError(Exception):
    pass


class AuditStore:
    """
    Immutable, append-only audit record storage.
    
    IMMUTABILITY GUARANTEES:
    1. Records can only be INSERTED, never UPDATED or DELETED
    2. The table has NO update/delete triggers (or they are blocked)
    3. Each record is hash-chain linked to the previous
    4. Any tampering breaks the hash chain
    """
    
    def __init__(self, db_path: str = "audit_trail.db"):
        self.db_path = db_path
        self.hash_chain = HashChain()
        self._initialize_db()
        self._sync_hash_chain()
    
    @contextlib.contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()
    
    def _initialize_db(self):
        """Create the audit tables if they don't exist."""
        script = '''
        CREATE TABLE IF NOT EXISTS audit_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT UNIQUE NOT NULL,
            workflow_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            encounter_type TEXT,
            record_data TEXT NOT NULL,
            record_hash TEXT NOT NULL,
            previous_hash TEXT,
            risk_score REAL,
            risk_level TEXT,
            compliance_decision TEXT,
            overall_confidence REAL,
            total_codes INTEGER,
            was_escalated BOOLEAN,
            processing_time_ms REAL,
            system_version TEXT,
            knowledge_base_version TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_created_at ON audit_records(created_at);
        CREATE INDEX IF NOT EXISTS idx_risk_level ON audit_records(risk_level);
        CREATE INDEX IF NOT EXISTS idx_compliance ON audit_records(compliance_decision);
        CREATE INDEX IF NOT EXISTS idx_workflow ON audit_records(workflow_type);
        CREATE INDEX IF NOT EXISTS idx_escalated ON audit_records(was_escalated);
        
        CREATE TABLE IF NOT EXISTS audit_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            code TEXT NOT NULL,
            code_type TEXT NOT NULL,
            description TEXT,
            sequence_position TEXT,
            confidence REAL,
            FOREIGN KEY (trace_id) REFERENCES audit_records(trace_id)
        );
        CREATE INDEX IF NOT EXISTS idx_code ON audit_codes(code);
        CREATE INDEX IF NOT EXISTS idx_code_trace ON audit_codes(trace_id);
        
        -- Trigger to prevent UPDATE
        CREATE TRIGGER IF NOT EXISTS prevent_audit_update
        BEFORE UPDATE ON audit_records
        BEGIN
            SELECT RAISE(ABORT, 'Audit records are immutable and cannot be updated.');
        END;
        
        -- Trigger to prevent DELETE
        CREATE TRIGGER IF NOT EXISTS prevent_audit_delete
        BEFORE DELETE ON audit_records
        BEGIN
            SELECT RAISE(ABORT, 'Audit records are immutable and cannot be deleted.');
        END;
        '''
        with self._get_connection() as conn:
            conn.executescript(script)
            
    def _sync_hash_chain(self):
        """Sync hash chain state on boot by fetching the last record."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT record_hash FROM audit_records ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                self.hash_chain._last_hash = row[0]
            
            cursor.execute("SELECT COUNT(*) FROM audit_records")
            cnt = cursor.fetchone()
            if cnt:
                self.hash_chain._chain_length = cnt[0]

    def store(self, workflow_trace: WorkflowTrace) -> str:
        """Store a complete workflow trace as an immutable audit record."""
        trace_data = workflow_trace.model_dump(mode='json')
        
        # Unlink the explicitly generated ones so hash_chain can enforce properties
        actual_record_hash, prev_hash = self.hash_chain.create_chain_link(trace_data)
        
        workflow_trace.record_hash = actual_record_hash
        workflow_trace.previous_record_hash = prev_hash
        trace_data = workflow_trace.model_dump(mode='json')  # Serialize again with filled fields
        
        with self._get_connection() as conn:
            # Enforce trace_id uniqueness manually before insert if exceptions catch is tricky across driver
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM audit_records WHERE trace_id = ?", (workflow_trace.trace_id,))
            if cur.fetchone():
                 raise DuplicateAuditRecordError(f"Trace ID {workflow_trace.trace_id} already exists.")
                 
            cur.execute(
                '''
                INSERT INTO audit_records (
                    trace_id, workflow_type, created_at, encounter_type, 
                    record_data, record_hash, previous_hash, risk_score, 
                    risk_level, compliance_decision, overall_confidence, 
                    total_codes, was_escalated, processing_time_ms, 
                    system_version, knowledge_base_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    workflow_trace.trace_id,
                    workflow_trace.workflow_type,
                    workflow_trace.completed_at.isoformat(),
                    workflow_trace.input_reference.encounter_type,
                    HashChain._canonical_serialize(trace_data),
                    actual_record_hash,
                    prev_hash,
                    workflow_trace.compliance_stage.risk_score,
                    workflow_trace.compliance_stage.risk_level,
                    workflow_trace.compliance_stage.overall_decision,
                    workflow_trace.final_output.overall_confidence,
                    workflow_trace.final_output.total_codes,
                    workflow_trace.final_output.was_escalated,
                    workflow_trace.total_processing_time_ms,
                    workflow_trace.system_metadata.system_version,
                    workflow_trace.system_metadata.knowledge_base_version
                )
            )
            
            for code in workflow_trace.final_output.final_diagnosis_codes + workflow_trace.final_output.final_procedure_codes:
                 cur.execute(
                     '''
                     INSERT INTO audit_codes (
                         trace_id, code, code_type, description, 
                         sequence_position, confidence
                     ) VALUES (?, ?, ?, ?, ?, ?)
                     ''',
                     (
                         workflow_trace.trace_id,
                         code.get("code", ""),
                         code.get("code_type", "ICD10"),
                         code.get("description", ""),
                         code.get("sequence_position", "UNKNOWN"),
                         code.get("confidence", 0.0)
                     )
                 )
        return workflow_trace.trace_id

    def retrieve(self, trace_id: str) -> Optional[WorkflowTrace]:
        """Retrieve a complete workflow trace by trace_id."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT record_data FROM audit_records WHERE trace_id = ?", (trace_id,))
            row = cur.fetchone()
            if row:
                return WorkflowTrace(**json.loads(row[0]))
        return None

    def _build_search_result(self, row) -> AuditSearchResult:
        return AuditSearchResult(
             trace_id=row[0],
             workflow_type=row[1],
             created_at=datetime.fromisoformat(row[2]),
             encounter_type=row[3],
             total_codes=row[4],
             overall_confidence=row[5],
             risk_score=row[6],
             risk_level=row[7],
             compliance_decision=row[8],
             was_escalated=bool(row[9]),
             processing_time_ms=row[10]
        )

    def retrieve_by_date_range(self, start_date: datetime, end_date: datetime, limit: int = 100) -> list[AuditSearchResult]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT trace_id, workflow_type, created_at, encounter_type, total_codes, 
                       overall_confidence, risk_score, risk_level, compliance_decision, 
                       was_escalated, processing_time_ms 
                FROM audit_records 
                WHERE created_at BETWEEN ? AND ? 
                ORDER BY created_at DESC LIMIT ?
            ''', (start_date.isoformat(), end_date.isoformat(), limit))
            return [self._build_search_result(row) for row in cur.fetchall()]

    def retrieve_by_risk_level(self, risk_level: str, limit: int = 100) -> list[AuditSearchResult]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT trace_id, workflow_type, created_at, encounter_type, total_codes, 
                       overall_confidence, risk_score, risk_level, compliance_decision, 
                       was_escalated, processing_time_ms 
                FROM audit_records 
                WHERE risk_level = ? 
                ORDER BY created_at DESC LIMIT ?
            ''', (risk_level, limit))
            return [self._build_search_result(row) for row in cur.fetchall()]

    def retrieve_by_code(self, code: str, limit: int = 100) -> list[AuditSearchResult]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT r.trace_id, r.workflow_type, r.created_at, r.encounter_type, r.total_codes, 
                       r.overall_confidence, r.risk_score, r.risk_level, r.compliance_decision, 
                       r.was_escalated, r.processing_time_ms 
                FROM audit_records r
                JOIN audit_codes c ON r.trace_id = c.trace_id
                WHERE c.code = ? 
                ORDER BY r.created_at DESC LIMIT ?
            ''', (code, limit))
            return [self._build_search_result(row) for row in cur.fetchall()]

    def retrieve_by_compliance_decision(self, decision: str, limit: int = 100) -> list[AuditSearchResult]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT trace_id, workflow_type, created_at, encounter_type, total_codes, 
                       overall_confidence, risk_score, risk_level, compliance_decision, 
                       was_escalated, processing_time_ms 
                FROM audit_records 
                WHERE compliance_decision = ? 
                ORDER BY created_at DESC LIMIT ?
            ''', (decision, limit))
            return [self._build_search_result(row) for row in cur.fetchall()]

    def retrieve_escalated(self, limit: int = 100) -> list[AuditSearchResult]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT trace_id, workflow_type, created_at, encounter_type, total_codes, 
                       overall_confidence, risk_score, risk_level, compliance_decision, 
                       was_escalated, processing_time_ms 
                FROM audit_records 
                WHERE was_escalated = 1 
                ORDER BY created_at DESC LIMIT ?
            ''', (limit,))
            return [self._build_search_result(row) for row in cur.fetchall()]

    def get_record_count(self) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM audit_records")
            return cur.fetchone()[0]

    def verify_chain_integrity(self, limit: int = None) -> ChainVerificationResult:
        with self._get_connection() as conn:
            cur = conn.cursor()
            q = "SELECT record_data FROM audit_records ORDER BY id ASC"
            if limit:
                # get last limit
                q = f"SELECT record_data FROM (SELECT id, record_data FROM audit_records ORDER BY id DESC LIMIT {limit}) ORDER BY id ASC"
            cur.execute(q)
            rows = cur.fetchall()
            records = [json.loads(row[0]) for row in rows]
            return self.hash_chain.verify_chain(records)

    def get_statistics(self) -> AuditStatistics:
        with self._get_connection() as conn:
            cur = conn.cursor()
            
            # Use safe empty values if DB is empty
            cur.execute("SELECT COUNT(*) FROM audit_records")
            total = cur.fetchone()[0]
            if total == 0:
                 return AuditStatistics(
                     total_records=0, records_by_workflow={}, records_by_risk_level={},
                     records_by_compliance={}, average_confidence=0.0, average_processing_time_ms=0.0,
                     escalation_rate=0.0, top_10_codes=[], chain_integrity="VALID", date_range={}
                 )
                 
            cur.execute("SELECT workflow_type, COUNT(*) FROM audit_records GROUP BY workflow_type")
            wflow = {row[0]: row[1] for row in cur.fetchall()}
            
            cur.execute("SELECT risk_level, COUNT(*) FROM audit_records GROUP BY risk_level")
            risk = {row[0]: row[1] for row in cur.fetchall()}
            
            cur.execute("SELECT compliance_decision, COUNT(*) FROM audit_records GROUP BY compliance_decision")
            comp = {row[0]: row[1] for row in cur.fetchall()}
            
            cur.execute("SELECT AVG(overall_confidence), AVG(processing_time_ms) FROM audit_records")
            avg_conf, avg_time = cur.fetchone()
            
            cur.execute("SELECT COUNT(*) FROM audit_records WHERE was_escalated = 1")
            esc = cur.fetchone()[0]
            esc_rate = esc / total if total else 0.0
            
            cur.execute("SELECT code, COUNT(*) as c FROM audit_codes GROUP BY code ORDER BY c DESC LIMIT 10")
            codes = [{"code": row[0], "count": row[1]} for row in cur.fetchall()]
            
            cur.execute("SELECT MIN(created_at), MAX(created_at) FROM audit_records")
            dates = {"earliest": row[0], "latest": row[1]} if (row := cur.fetchone()) else {}
            
            integrity = "VALID" if self.verify_chain_integrity().is_valid else "BROKEN"

            return AuditStatistics(
                total_records=total,
                records_by_workflow=wflow,
                records_by_risk_level=risk,
                records_by_compliance=comp,
                average_confidence=avg_conf or 0.0,
                average_processing_time_ms=avg_time or 0.0,
                escalation_rate=esc_rate,
                top_10_codes=codes,
                chain_integrity=integrity,
                date_range=dates
            )

    def export_records(self, trace_ids: list[str], format: str = "json") -> Union[str, bytes]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            ph = ','.join(['?']*len(trace_ids))
            cur.execute(f"SELECT record_data FROM audit_records WHERE trace_id IN ({ph})", trace_ids)
            records = [json.loads(row[0]) for row in cur.fetchall()]
            if format.lower() == "json":
                return json.dumps(records, indent=2)
            else:
                return str(records) # simplistic csv fallback
