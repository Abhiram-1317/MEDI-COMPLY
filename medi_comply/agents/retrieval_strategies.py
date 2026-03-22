"""
MEDI-COMPLY — Retrieval Strategies.

Multi-strategy retrieval system for matching clinical text to medical codes.
Combines Vector (semantic), Keyword (exact/partial), Direct Mapping, and
Hierarchy Traversal using Reciprocal Rank Fusion (RRF).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Optional
import logging

from medi_comply.schemas.retrieval import RankedCodeCandidate
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.knowledge.vector_store import MedicalVectorStore
from medi_comply.agents.clinical_code_mapper import ClinicalCodeMapper


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base Strategy Interface
# ---------------------------------------------------------------------------

class RetrievalStrategy(ABC):
    """Base class for code retrieval strategies."""
    
    @abstractmethod
    async def retrieve(
        self, query: str, code_type: str, top_k: int
    ) -> list[RankedCodeCandidate]:
        """Execute retrieval strategy.
        
        Parameters
        ----------
        query: str
            Clinical text query.
        code_type: str
            "ICD10" or "CPT".
        top_k: int
            Number of results to return.

        Returns
        -------
        list[RankedCodeCandidate]
        """
        pass


# ---------------------------------------------------------------------------
# Semantic Vector Search
# ---------------------------------------------------------------------------

class VectorRetrievalStrategy(RetrievalStrategy):
    """Semantic similarity search using ChromaDB vector store."""
    
    def __init__(self, vector_store: MedicalVectorStore):
        self.vector_store = vector_store
    
    async def retrieve(
        self, query: str, code_type: str, top_k: int = 15
    ) -> list[RankedCodeCandidate]:
        if not getattr(self.vector_store, "is_initialized", False):
            logger.info("Vector store unavailable — skipping vector retrieval")
            return []

        if code_type.upper() == "ICD10":
            search_func = self.vector_store.search_icd10
        else:
            search_func = self.vector_store.search_cpt
            
        # Async wrap since vector search is sync in our current Knowledge manager
        loop = asyncio.get_running_loop()
        try:
            results = await loop.run_in_executor(
                None,
                search_func,
                query,
                top_k
            )
        except Exception as exc:
            logger.error("Vector retrieval failed: %s", exc)
            return []
        
        candidates = []
        if not results:
            return candidates

        for res in results:
            v_score = max(0.0, res.similarity_score)
            metadata = res.metadata or {}
            cand = RankedCodeCandidate(
                code=res.code,
                description=metadata.get("description", res.description),
                long_description=metadata.get("long_description"),
                code_type=code_type.upper(),
                relevance_score=v_score,
                vector_score=v_score,
                is_billable=metadata.get("is_valid_for_submission", True),
                specificity_level=int(metadata.get("depth", 3)),
                parent_code=metadata.get("parent_code"),
                retrieval_source="VECTOR"
            )
            candidates.append(cand)
            
        return candidates


# ---------------------------------------------------------------------------
# Keyword Search
# ---------------------------------------------------------------------------

class KeywordRetrievalStrategy(RetrievalStrategy):
    """Keyword-based search using exact and partial matching.
    Looks for term matches within descriptions."""
    
    def __init__(self, knowledge_manager: KnowledgeManager):
        self.km = knowledge_manager
    
    async def retrieve(
        self, query: str, code_type: str, top_k: int = 15
    ) -> list[RankedCodeCandidate]:
        candidates = []
        query_terms = set(word.lower() for word in query.split() if len(word) > 2)
        
        if not query_terms:
            return []
            
        # Linear sweep across KM objects (quick logic approximation for Hackathon)
        # Assuming we don't have a true inverted index in KM, we'll scan cache
        
        if code_type.upper() == "ICD10":
            registry = self.km.icd10_db._codes
        else:
            registry = self.km.cpt_db._codes
            
        results = []
        for code, data in registry.items():
            desc_terms = set(word.lower() for word in data.description.split())
            overlap = query_terms.intersection(desc_terms)
            
            if overlap:
                score = len(overlap) / len(query_terms)
                results.append((score, code, data))
                
        # Sort by score desc, take top_k
        results.sort(key=lambda x: x[0], reverse=True)
        top_results = results[:top_k]
        
        for score, code, data in top_results:
            candidates.append(RankedCodeCandidate(
                code=code,
                description=data.description,
                long_description=getattr(data, "long_description", None),
                code_type=code_type.upper(),
                relevance_score=score,
                keyword_score=score,
                is_billable=getattr(data, "is_billable", getattr(data, "is_valid_for_submission", True)),
                specificity_level=data.depth if hasattr(data, "depth") else 3,
                parent_code=data.parent_code if hasattr(data, "parent_code") else None,
                retrieval_source="KEYWORD"
            ))
            
        return candidates


# ---------------------------------------------------------------------------
# Direct Mapping
# ---------------------------------------------------------------------------

class DirectMapStrategy(RetrievalStrategy):
    """Direct lookup from clinical text to predefined mappings."""
    
    def __init__(self, mapper: ClinicalCodeMapper, knowledge_manager: KnowledgeManager):
        self.mapper = mapper
        self.km = knowledge_manager
    
    async def retrieve(
        self, query: str, code_type: str, top_k: int = 10
    ) -> list[RankedCodeCandidate]:
        candidates = []
        
        if code_type.upper() == "ICD10":
            matches = self.mapper.lookup_condition(query)
            registry = self.km.icd10_db._codes
        else:
            matches = self.mapper.lookup_procedure(query)
            registry = self.km.cpt_db._codes
            
        for code, conf in matches:
            if code not in registry:
                continue
                
            data = registry[code]
            candidates.append(RankedCodeCandidate(
                code=code,
                description=data.description,
                long_description=getattr(data, "long_description", None),
                code_type=code_type.upper(),
                relevance_score=conf,
                direct_map_score=conf,
                is_billable=getattr(data, "is_billable", getattr(data, "is_valid_for_submission", True)),
                specificity_level=len(data.code),
                parent_code=data.parent_code if hasattr(data, "parent_code") else None,
                retrieval_source="DIRECT_MAP"
            ))
            
        # Top_k slice not strictly needed if dictionary mappings are short, but applied for safety
        return candidates[:top_k]


# ---------------------------------------------------------------------------
# Hierarchy Traversal
# ---------------------------------------------------------------------------

class HierarchyTraversalStrategy(RetrievalStrategy):
    """Traverse ICD-10/CPT hierarchy to find optimal specificity level."""
    
    def __init__(self, knowledge_manager: KnowledgeManager):
        self.km = knowledge_manager
    
    async def retrieve(
        self, query: str, code_type: str, top_k: int = 10,
        seed_codes: Optional[list[str]] = None
    ) -> list[RankedCodeCandidate]:
        if not seed_codes:
            return []
            
        candidates = []
        registry = self.km.icd10_db if code_type.upper() == "ICD10" else self.km.cpt_db
            
        explored = set(seed_codes)
        for seed in seed_codes:
            # Add parents and children
            if not registry.code_exists(seed):
                continue
                
            data = registry._codes[seed]
            rel_codes = []
            
            # Parents
            if hasattr(data, "parent_code") and data.parent_code:
                rel_codes.append(data.parent_code)
                
            # Children
            if hasattr(data, "children_codes"):
                rel_codes.extend(data.children_codes)
                
            for rc in rel_codes:
                if rc not in explored and registry.code_exists(rc):
                    explored.add(rc)
                    rc_data = registry._codes[rc]
                    
                    # Assume moderate score for hierarchy neighbors
                    score = 0.5 
                    
                    candidates.append(RankedCodeCandidate(
                        code=rc,
                        description=rc_data.description,
                        long_description=getattr(rc_data, "long_description", None),
                        code_type=code_type.upper(),
                        relevance_score=score,
                        graph_score=score,
                        is_billable=getattr(rc_data, "is_billable", getattr(rc_data, "is_valid_for_submission", True)),
                        specificity_level=rc_data.depth if hasattr(rc_data, "depth") else 3,
                        parent_code=rc_data.parent_code if hasattr(rc_data, "parent_code") else None,
                        retrieval_source="GRAPH"
                    ))
                    
        candidates.sort(key=lambda x: x.relevance_score, reverse=True)
        return candidates[:top_k]


# ---------------------------------------------------------------------------
# Retrieval Fusion (RRF)
# ---------------------------------------------------------------------------

class RetrievalFusion:
    """Combines results from multiple strategies using Reciprocal Rank Fusion."""
    
    def __init__(self, strategies: list[RetrievalStrategy], k: int = 60):
        self.strategies = strategies
        self.k = k
    
    async def retrieve(
        self, query: str, code_type: str, top_k: int = 10
    ) -> list[RankedCodeCandidate]:
        """Run all strategies, fuse, and deduplicate."""
        # 1. Run all strategies in parallel
        # Note: HierarchyStrategy isn't naturally run in this parallel block without seeds,
        # but for simplicity, we pass empty text seeds or let it return [] if no seeds provided.
        coros = [
            strat.retrieve(query, code_type, top_k) for strat in self.strategies
            # Filter out HierarchyTraversal if it requires seeds explicitly
            if not isinstance(strat, HierarchyTraversalStrategy)
        ]
        
        nested_results = await asyncio.gather(*coros)
        
        # Pull hierarchy if we have initial results
        seed_codes = [c.code for sublist in nested_results for c in sublist]
        for strat in self.strategies:
            if isinstance(strat, HierarchyTraversalStrategy):
                h_results = await strat.retrieve(query, code_type, top_k, seed_codes)
                nested_results.append(h_results)
        
        # 2. Reciprocal Rank Fusion
        fused = self._reciprocal_rank_fusion(nested_results)
        
        # 3. Compile and Deduplicate
        final_candidates = self._deduplicate(fused)
        
        # 4. Sort and trim
        final_candidates.sort(key=lambda x: x.relevance_score, reverse=True)
        return final_candidates[:top_k]
    
    def _reciprocal_rank_fusion(self, ranked_lists: list[list[RankedCodeCandidate]]) -> list[RankedCodeCandidate]:
        """Calculate RRF scores."""
        scores: dict[str, float] = {}
        unified_objects: dict[str, RankedCodeCandidate] = {}
        
        for rlist in ranked_lists:
            for rank_idx, cand in enumerate(rlist):
                # Rank starts at 1
                rank = rank_idx + 1
                rrf_contrib = 1.0 / (self.k + rank)
                
                code = cand.code
                scores[code] = scores.get(code, 0.0) + rrf_contrib
                
                # Merge logic (keep highest score base object, merge sub-scores)
                if code not in unified_objects:
                    unified_objects[code] = cand
                else:
                    existing = unified_objects[code]
                    if cand.vector_score is not None:
                        existing.vector_score = cand.vector_score
                    if cand.keyword_score is not None:
                        existing.keyword_score = cand.keyword_score
                    if cand.graph_score is not None:
                        existing.graph_score = cand.graph_score
                    if cand.direct_map_score is not None:
                        existing.direct_map_score = cand.direct_map_score
                        
        # Re-assign fused scores
        for code, obj in unified_objects.items():
            # Normalized RRF scores are often low (<0.1), so we scale them slightly 
            # for readability but maintain relative ranking
            norm_score = scores[code] * 20.0 
            obj.relevance_score = min(1.0, norm_score)
            obj.retrieval_source = "FUSION"
            
        return list(unified_objects.values())
    
    def _deduplicate(self, candidates: list[RankedCodeCandidate]) -> list[RankedCodeCandidate]:
        """Keep highest-scored version of each unique code."""
        # Already handled gracefully in the RRF dictionary merging, 
        # but exposed as requested
        return candidates
