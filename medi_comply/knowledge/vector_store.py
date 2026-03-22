"""
MEDI-COMPLY — ChromaDB-based semantic search for medical codes and guidelines.

Uses ChromaDB in ephemeral (in-memory) mode so no external server is needed
for the hackathon.  Provides vector-similarity search across ICD-10 codes,
CPT codes, and coding guidelines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


@dataclass
class CodeSearchResult:
    """A single result from a semantic code search."""

    code: str
    description: str
    similarity_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GuidelineSearchResult:
    """A single result from a semantic guideline search."""

    guideline_id: str
    title: str
    rule_text: str = ""
    similarity_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Medical Vector Store
# ---------------------------------------------------------------------------


class MedicalVectorStore:
    """ChromaDB-backed semantic search over medical knowledge.

    Creates three collections:

    * ``icd10_codes`` — ICD-10-CM codes + descriptions
    * ``cpt_codes`` — CPT / HCPCS codes + descriptions
    * ``coding_guidelines`` — Official coding guidelines

    Uses ChromaDB's built-in all-MiniLM-L6-v2 embedding model by default
    (runs locally, no API key needed).

    Parameters
    ----------
    persist_directory:
        Optional filesystem path for persistent storage.
        If ``None``, uses ephemeral (in-memory) mode.
    """

    def __init__(self, persist_directory: Optional[str] = None) -> None:
        self._persist_directory = persist_directory
        self._client: Any = None
        self._collections: dict[str, Any] = {}
        self._initialized: bool = False

    # -- Initialization ----------------------------------------------------

    def initialize(self) -> None:
        """Create the ChromaDB client and collections.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._initialized:
            return
        try:
            import chromadb  # type: ignore

            if self._persist_directory:
                self._client = chromadb.PersistentClient(path=self._persist_directory)
            else:
                self._client = chromadb.EphemeralClient()

            for name in ("icd10_codes", "cpt_codes", "coding_guidelines"):
                try:
                    self._collections[name] = self._client.get_or_create_collection(
                        name=name,
                        metadata={"hnsw:space": "cosine"},
                    )
                except Exception as coll_exc:
                    logger.error("Unable to prepare ChromaDB collection %s: %s", name, coll_exc)
                    self._collections.clear()
                    self._client = None
                    self._initialized = False
                    return

            if not self._run_health_check():
                logger.warning("ChromaDB health check failed — disabling vector search")
                self._collections.clear()
                self._client = None
                self._initialized = False
                return

            self._initialized = True
            logger.info(
                "MedicalVectorStore initialized with %d collections", len(self._collections)
            )
        except ImportError:
            logger.warning(
                "chromadb not installed — vector search will use fallback keyword matching"
            )
            self._initialized = False
        except Exception as exc:
            logger.error("Failed to initialize ChromaDB: %s", exc)
            self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Whether the vector store has been successfully initialized."""
        return self._initialized

    # -- Adding documents --------------------------------------------------

    def add_codes(self, codes: list[dict[str, Any]], collection: str) -> None:
        """Add code entries to a collection.

        Parameters
        ----------
        codes:
            List of dicts with at least ``code`` and ``description`` keys.
        collection:
            Collection name (``"icd10_codes"`` or ``"cpt_codes"``).
        """
        if not self._initialized:
            logger.warning("Vector store not initialized — skipping add_codes")
            return

        coll = self._collections.get(collection)
        if coll is None:
            logger.error("Unknown collection: %s", collection)
            return

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for entry in codes:
            code = entry.get("code", "")
            desc = entry.get("description", "")
            long_desc = entry.get("long_description", "")
            doc_text = f"{code} {desc} {long_desc}"

            # ChromaDB metadata must be str/int/float/bool
            safe_meta: dict[str, Any] = {}
            for k, v in entry.items():
                if isinstance(v, (str, int, float, bool)):
                    safe_meta[k] = v

            ids.append(code)
            documents.append(doc_text)
            metadatas.append(safe_meta)

        if ids:
            try:
                coll.upsert(ids=ids, documents=documents, metadatas=metadatas)
                logger.info("Upserted %d items into %s", len(ids), collection)
            except Exception as exc:
                logger.error("Failed to upsert %d items into %s: %s", len(ids), collection, exc)

    def add_guidelines(self, guidelines: list[dict[str, Any]]) -> None:
        """Add coding guidelines to the guidelines collection.

        Parameters
        ----------
        guidelines:
            List of guideline dicts with ``guideline_id``, ``title``,
            ``rule_text``, etc.
        """
        if not self._initialized:
            return

        coll = self._collections.get("coding_guidelines")
        if coll is None:
            return

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for g in guidelines:
            gid = g.get("guideline_id", "")
            title = g.get("title", "")
            rule = g.get("rule_text", "")
            doc_text = f"{gid} {title} {rule}"

            safe_meta: dict[str, Any] = {}
            for k, v in g.items():
                if isinstance(v, (str, int, float, bool)):
                    safe_meta[k] = v

            ids.append(gid)
            documents.append(doc_text)
            metadatas.append(safe_meta)

        if ids:
            try:
                coll.upsert(ids=ids, documents=documents, metadatas=metadatas)
            except Exception as exc:
                logger.error("Failed to upsert guidelines: %s", exc)

    # -- Search ------------------------------------------------------------

    def search_icd10(self, query: str, top_k: int = 10) -> list[CodeSearchResult]:
        """Semantic search over ICD-10 codes.

        Parameters
        ----------
        query:
            Natural-language clinical description.
        top_k:
            Maximum number of results to return.

        Returns
        -------
        list[CodeSearchResult]
        """
        return self._search_codes(query, "icd10_codes", top_k)

    def search_cpt(self, query: str, top_k: int = 10) -> list[CodeSearchResult]:
        """Semantic search over CPT codes.

        Parameters
        ----------
        query:
            Natural-language procedure description.
        top_k:
            Maximum number of results.

        Returns
        -------
        list[CodeSearchResult]
        """
        return self._search_codes(query, "cpt_codes", top_k)

    def search_guidelines(self, query: str, top_k: int = 5) -> list[GuidelineSearchResult]:
        """Semantic search over coding guidelines.

        Parameters
        ----------
        query:
            Natural-language query.
        top_k:
            Maximum number of results.

        Returns
        -------
        list[GuidelineSearchResult]
        """
        if not self._initialized:
            return []

        coll = self._collections.get("coding_guidelines")
        if coll is None:
            return []

        rows = self._run_query(coll, query, top_k)
        out: list[GuidelineSearchResult] = []
        for row in rows:
            meta = row.get("metadata") or {}
            out.append(
                GuidelineSearchResult(
                    guideline_id=row.get("id", ""),
                    title=meta.get("title", ""),
                    rule_text=meta.get("rule_text", ""),
                    similarity_score=self._distance_to_similarity(row.get("distance")),
                    metadata=meta,
                )
            )
        return out

    def hybrid_search(
        self,
        query: str,
        collection: str,
        keyword_weight: float = 0.3,
        top_k: int = 10,
    ) -> list[CodeSearchResult]:
        """Combine vector similarity with keyword matching.

        Parameters
        ----------
        query:
            Natural-language query.
        collection:
            Collection to search.
        keyword_weight:
            Weight for keyword score (0-1). Remainder goes to vector score.
        top_k:
            Maximum results.

        Returns
        -------
        list[CodeSearchResult]
        """
        vector_results = self._search_codes(query, collection, top_k * 2)
        if not vector_results:
            return []

        # Keyword boosting
        query_tokens = set(query.lower().split())
        for r in vector_results:
            desc_tokens = set(r.description.lower().split())
            overlap = len(query_tokens & desc_tokens)
            keyword_score = overlap / max(len(query_tokens), 1)
            r.similarity_score = (
                (1 - keyword_weight) * r.similarity_score
                + keyword_weight * keyword_score
            )

        vector_results.sort(key=lambda r: r.similarity_score, reverse=True)
        return vector_results[:top_k]

    # -- Internal ----------------------------------------------------------

    def _search_codes(self, query: str, collection: str, top_k: int) -> list[CodeSearchResult]:
        """Internal search helper."""
        if not self._initialized:
            return []

        coll = self._collections.get(collection)
        if coll is None:
            return []

        rows = self._run_query(coll, query, top_k)
        out: list[CodeSearchResult] = []
        for row in rows:
            meta = row.get("metadata") or {}
            out.append(
                CodeSearchResult(
                    code=row.get("id", ""),
                    description=meta.get("description", row.get("document", "")),
                    similarity_score=self._distance_to_similarity(row.get("distance")),
                    metadata=meta,
                )
            )
        return out

    def _run_health_check(self) -> bool:
        if self._client is None:
            return False
        try:
            if hasattr(self._client, "heartbeat"):
                self._client.heartbeat()
                return True
            if hasattr(self._client, "list_collections"):
                self._client.list_collections()
                return True
        except Exception as exc:
            logger.warning("ChromaDB client heartbeat failed: %s", exc)
            return False
        return True

    def _run_query(self, collection: Any, query: str, top_k: int) -> list[dict[str, Any]]:
        try:
            raw = collection.query(query_texts=[query], n_results=top_k)
        except Exception as exc:
            logger.error("Vector query failed: %s", exc)
            return []
        return self._normalize_query_response(raw)

    def _normalize_query_response(self, raw: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
        if not raw:
            return []

        ids = self._flatten_result_field(raw.get("ids"))
        metas = self._flatten_result_field(raw.get("metadatas"))
        dists = self._flatten_result_field(raw.get("distances"))
        docs = self._flatten_result_field(raw.get("documents"))

        max_len = max(len(ids), len(metas), len(dists), len(docs))
        rows: list[dict[str, Any]] = []
        for idx in range(max_len):
            rows.append(
                {
                    "id": ids[idx] if idx < len(ids) else "",
                    "metadata": metas[idx] if idx < len(metas) else {},
                    "distance": dists[idx] if idx < len(dists) else None,
                    "document": docs[idx] if idx < len(docs) else "",
                }
            )
        return rows

    def _flatten_result_field(self, value: Optional[Any]) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, tuple):
            value = list(value)
        if isinstance(value, list):
            if value and isinstance(value[0], list):
                return value[0]
            return value
        return [value]

    def _distance_to_similarity(self, distance: Optional[float]) -> float:
        if distance is None:
            return 0.0
        try:
            return max(0.0, 1.0 - float(distance))
        except (TypeError, ValueError):
            return 0.0

    def __repr__(self) -> str:
        return f"MedicalVectorStore(initialized={self._initialized})"
