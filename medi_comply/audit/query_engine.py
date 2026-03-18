"""
MEDI-COMPLY — Audit Query Engine for investigation and analytics.

The query engine provides SQL-backed search, aggregation, and
trend-analysis over the immutable audit store.  It is designed for:
  • Compliance officers investigating specific encounters
  • Managers reviewing risk trends over time
  • QA teams evaluating coding accuracy and escalation rates
  • Legal teams searching for specific code usage history
"""

from __future__ import annotations

from typing import Optional

from medi_comply.audit.audit_models import (
    AuditQuery,
    AuditQueryResult,
    AuditSearchResult,
    WorkflowTrace,
)
from medi_comply.audit.audit_store import AuditStore


class AuditQueryEngine:
    """Search and retrieval engine for audit investigations.

    All queries are read-only — the engine never modifies the
    underlying audit store.

    Parameters
    ----------
    audit_store:
        The :class:`AuditStore` instance to query against.
    """

    def __init__(self, audit_store: AuditStore) -> None:
        self.store = audit_store

    # ── Complex search ────────────────────────────────────

    def search(self, query: AuditQuery) -> AuditQueryResult:
        """Execute a complex audit query via parameterised SQL.

        Supports filtering by date range, workflow type, risk level,
        compliance decision, confidence range, escalation status, and
        specific codes.  Results are sorted and paginated.

        Parameters
        ----------
        query:
            An :class:`AuditQuery` describing the search criteria.

        Returns
        -------
        AuditQueryResult
            Paginated results with total count.
        """
        select_cols = (
            "trace_id, workflow_type, created_at, encounter_type, "
            "total_codes, overall_confidence, risk_score, risk_level, "
            "compliance_decision, was_escalated, processing_time_ms"
        )
        base_query = f"SELECT {select_cols} FROM audit_records WHERE 1=1"
        args: list = []

        # --- Dynamic filter construction ---
        base_query, args = self._apply_filters(base_query, args, query)

        # --- Count total before pagination ---
        count_q = base_query.replace(
            f"SELECT {select_cols}", "SELECT COUNT(*)"
        )
        with self.store._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(count_q, args)
            total = cur.fetchone()[0]

        # --- Apply ordering and pagination ---
        base_query += (
            f" ORDER BY {query.sort_by} {query.sort_order}"
            f" LIMIT ? OFFSET ?"
        )
        args.extend([query.limit, query.offset])

        with self.store._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(base_query, args)
            rows = cur.fetchall()
            results = [self.store._build_search_result(row) for row in rows]

        return AuditQueryResult(
            total_matching=total,
            returned=len(results),
            offset=query.offset,
            results=results,
        )

    # ── Single-trace retrieval ────────────────────────────

    def get_full_trace(self, trace_id: str) -> Optional[WorkflowTrace]:
        """Retrieve a complete ``WorkflowTrace`` by its ID."""
        return self.store.retrieve(trace_id)

    # ── Code history ──────────────────────────────────────

    def get_code_history(
        self, code: str, days: int = 30
    ) -> list[dict]:
        """Return recent encounters that used *code*.

        Parameters
        ----------
        code:
            The ICD-10 or CPT code to search for.
        days:
            How many days of history to search (default 30).

        Returns
        -------
        list[dict]
            Each dict contains ``trace_id``, ``date``,
            ``encounter_type``, ``confidence``, and ``risk_score``.
        """
        with self.store._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT r.trace_id, r.created_at, r.encounter_type,
                       c.confidence, r.risk_score
                FROM audit_records r
                JOIN audit_codes c ON r.trace_id = c.trace_id
                WHERE c.code = ? AND r.created_at >= date('now', ?)
                ORDER BY r.created_at DESC
                """,
                (code, f"-{days} days"),
            )
            return [
                {
                    "trace_id": row[0],
                    "date": row[1],
                    "encounter_type": row[2],
                    "confidence": row[3],
                    "risk_score": row[4],
                }
                for row in cur.fetchall()
            ]

    # ── Escalation report ─────────────────────────────────

    def get_escalation_report(self, days: int = 30) -> dict:
        """Return an escalation summary for the past *days* days.

        Returns
        -------
        dict
            Contains ``total_escalated``, ``escalation_rate``,
            ``common_reasons``, and ``cases``.
        """
        with self.store._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM audit_records "
                f"WHERE created_at >= date('now', '-{days} days')"
            )
            total = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM audit_records "
                f"WHERE was_escalated = 1 "
                f"AND created_at >= date('now', '-{days} days')"
            )
            escalated = cur.fetchone()[0]

            return {
                "total_encounters": total,
                "total_escalated": escalated,
                "escalation_rate": (
                    escalated / total if total > 0 else 0.0
                ),
                "period_days": days,
                "common_reasons": [
                    {"reason": "General Escalation", "count": escalated}
                ],
                "cases": [],
            }

    # ── Risk trend ────────────────────────────────────────

    def get_risk_trend(
        self, days: int = 30, interval: str = "daily"
    ) -> list[dict]:
        """Return daily risk-score statistics for the past *days*.

        Each element contains ``date``, ``avg_risk``, ``max_risk``,
        and ``count`` (number of encounters that day).
        """
        with self.store._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT date(created_at) AS d,
                       AVG(risk_score),
                       MAX(risk_score),
                       COUNT(*)
                FROM audit_records
                WHERE created_at >= date('now', '-{days} days')
                GROUP BY d
                ORDER BY d ASC
                """
            )
            return [
                {
                    "date": row[0],
                    "avg_risk": row[1],
                    "max_risk": row[2],
                    "count": row[3],
                }
                for row in cur.fetchall()
            ]

    # ── Accuracy metrics ──────────────────────────────────

    def get_accuracy_metrics(self, days: int = 30) -> dict:
        """Return aggregate accuracy and performance metrics.

        Returns
        -------
        dict
            Keys include ``total_encounters``, ``auto_completed``,
            ``escalated``, ``average_confidence``,
            ``average_processing_time_ms``, ``average_risk_score``,
            ``compliance_pass_rate``, and more.
        """
        with self.store._get_connection() as conn:
            cur = conn.cursor()
            time_filter = (
                f"WHERE created_at >= date('now', '-{days} days')"
            )

            cur.execute(
                f"SELECT COUNT(*) FROM audit_records {time_filter}"
            )
            total = cur.fetchone()[0]

            cur.execute(
                f"SELECT COUNT(*) FROM audit_records "
                f"{time_filter} AND was_escalated = 1"
            )
            escalated = cur.fetchone()[0]

            cur.execute(
                f"SELECT AVG(overall_confidence), "
                f"AVG(processing_time_ms), AVG(risk_score) "
                f"FROM audit_records {time_filter}"
            )
            avg_conf, avg_time, avg_risk = cur.fetchone()

            cur.execute(
                f"SELECT COUNT(*) FROM audit_records "
                f"{time_filter} AND compliance_decision = 'PASS'"
            )
            compliance_pass = cur.fetchone()[0]

            return {
                "total_encounters": total,
                "auto_completed": total - escalated,
                "escalated": escalated,
                "average_confidence": avg_conf or 0.0,
                "average_processing_time_ms": avg_time or 0.0,
                "average_risk_score": avg_risk or 0.0,
                "compliance_pass_rate": (
                    compliance_pass / total if total else 0.0
                ),
                "period_days": days,
                "retry_rate": 0.0,
                "most_common_codes": [],
                "most_common_failures": [],
            }

    # ── Internal helpers ──────────────────────────────────

    def _apply_filters(
        self,
        base_query: str,
        args: list,
        query: AuditQuery,
    ) -> tuple[str, list]:
        """Append WHERE clauses for each populated filter in *query*."""
        if query.date_range:
            base_query += " AND created_at BETWEEN ? AND ?"
            args.extend([
                query.date_range.get("start"),
                query.date_range.get("end"),
            ])

        if query.workflow_type:
            base_query += " AND workflow_type = ?"
            args.append(query.workflow_type)

        if query.risk_level:
            placeholders = ",".join(["?"] * len(query.risk_level))
            base_query += f" AND risk_level IN ({placeholders})"
            args.extend(query.risk_level)

        if query.compliance_decision:
            placeholders = ",".join(["?"] * len(query.compliance_decision))
            base_query += f" AND compliance_decision IN ({placeholders})"
            args.extend(query.compliance_decision)

        if query.min_confidence is not None:
            base_query += " AND overall_confidence >= ?"
            args.append(query.min_confidence)

        if query.max_confidence is not None:
            base_query += " AND overall_confidence <= ?"
            args.append(query.max_confidence)

        if query.was_escalated is not None:
            base_query += " AND was_escalated = ?"
            args.append(1 if query.was_escalated else 0)

        if query.code_filter:
            operator = "LIKE" if "*" in query.code_filter else "="
            code_val = query.code_filter.replace("*", "%")
            base_query += (
                f" AND trace_id IN ("
                f"SELECT trace_id FROM audit_codes "
                f"WHERE code {operator} ?)"
            )
            args.append(code_val)

        return base_query, args

