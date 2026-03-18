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
                self._collections[name] = self._client.get_or_create_collection(
                    name=name,
                    metadata={"hnsw:space": "cosine"},
                )
            self._initialized = True
            logger.info("MedicalVectorStore initialized with %d collections", len(self._collections))
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
            coll.upsert(ids=ids, documents=documents, metadatas=metadatas)
            logger.info("Upserted %d items into %s", len(ids), collection)

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
            coll.upsert(ids=ids, documents=documents, metadatas=metadatas)

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

        try:
            results = coll.query(query_texts=[query], n_results=top_k)
        except Exception as exc:
            logger.error("Guideline search failed: %s", exc)
            return []

        out: list[GuidelineSearchResult] = []
        if results and results["ids"]:
            for i, gid in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                dist = results["distances"][0][i] if results["distances"] else 1.0
                similarity = max(0.0, 1.0 - dist)
                out.append(
                    GuidelineSearchResult(
                        guideline_id=gid,
                        title=meta.get("title", ""),
                        rule_text=meta.get("rule_text", ""),
                        similarity_score=similarity,
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

        try:
            results = coll.query(query_texts=[query], n_results=top_k)
        except Exception as exc:
            logger.error("Code search failed in %s: %s", collection, exc)
            return []

        out: list[CodeSearchResult] = []
        if results and results["ids"]:
            for i, code_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                dist = results["distances"][0][i] if results["distances"] else 1.0
                similarity = max(0.0, 1.0 - dist)
                out.append(
                    CodeSearchResult(
                        code=code_id,
                        description=meta.get("description", ""),
                        similarity_score=similarity,
                        metadata=meta,
                    )
                )
        return out

    def __repr__(self) -> str:
        return f"MedicalVectorStore(initialized={self._initialized})"
