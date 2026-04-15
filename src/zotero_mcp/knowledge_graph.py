"""Knowledge graph analysis using NetworkX over Zotero citation data."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    import networkx as nx

    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

if TYPE_CHECKING:
    from zotero_mcp.graph_store import GraphStore

logger = logging.getLogger(__name__)


class KnowledgeGraph:
    """Citation network analysis for a Zotero library.

    Builds a NetworkX DiGraph from the GraphStore and provides
    graph analytics: PageRank, community detection, betweenness
    centrality, shortest paths, and neighborhood queries.
    """

    def __init__(self) -> None:
        if not HAS_NETWORKX:
            raise ImportError(
                "Knowledge graph requires networkx. "
                "Install with: pip install zotero-mcp[graph]"
            )
        self._graph: nx.DiGraph = nx.DiGraph()
        self._paper_data: dict[str, dict] = {}

    def build_from_store(self, store: GraphStore) -> dict:
        """Build the graph from persisted data."""
        self._graph.clear()
        self._paper_data.clear()

        papers = store.get_all_papers()
        for p in papers:
            doi = p["doi"]
            self._graph.add_node(doi)
            self._paper_data[doi] = p

        for citing, cited in store.get_all_citations():
            if citing in self._graph and cited in self._graph:
                self._graph.add_edge(citing, cited)

        return self.get_stats()

    def get_stats(self) -> dict:
        """Return graph summary statistics."""
        g = self._graph
        return {
            "nodes": g.number_of_nodes(),
            "edges": g.number_of_edges(),
            "density": round(nx.density(g), 4) if g.number_of_nodes() > 1 else 0,
            "components": nx.number_weakly_connected_components(g),
        }

    def get_influential_papers(self, top_n: int = 10) -> list[dict]:
        """Return papers ranked by PageRank (most influential first)."""
        if not self._graph.nodes:
            return []
        pr = nx.pagerank(self._graph)
        ranked = sorted(pr.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [
            {**self._paper_data.get(doi, {"doi": doi}), "pagerank": round(score, 6)}
            for doi, score in ranked
        ]

    def get_clusters(self) -> list[dict]:
        """Detect research clusters via greedy modularity on undirected projection."""
        if self._graph.number_of_nodes() < 2:
            return []
        undirected = self._graph.to_undirected()
        try:
            from networkx.algorithms.community import greedy_modularity_communities

            communities = greedy_modularity_communities(undirected)
        except Exception:
            return []

        clusters = []
        for i, community in enumerate(communities):
            papers = [self._paper_data.get(doi, {"doi": doi}) for doi in community]
            clusters.append(
                {
                    "cluster_id": i,
                    "size": len(community),
                    "papers": papers,
                }
            )
        return sorted(clusters, key=lambda c: c["size"], reverse=True)

    def get_bridge_papers(self, top_n: int = 10) -> list[dict]:
        """Return papers with highest betweenness centrality (bridge papers)."""
        if self._graph.number_of_nodes() < 3:
            return []
        bc = nx.betweenness_centrality(self._graph)
        ranked = sorted(bc.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [
            {**self._paper_data.get(doi, {"doi": doi}), "betweenness": round(score, 6)}
            for doi, score in ranked
            if score > 0
        ]

    def get_path(self, doi_a: str, doi_b: str) -> list[dict]:
        """Find shortest citation path between two papers."""
        undirected = self._graph.to_undirected()
        try:
            path = nx.shortest_path(undirected, doi_a, doi_b)
            return [self._paper_data.get(doi, {"doi": doi}) for doi in path]
        except (nx.NodeNotFound, nx.NetworkXNoPath):
            return []

    def get_neighborhood(self, doi: str, depth: int = 1) -> dict:
        """Get papers within N citation hops of a given paper."""
        if doi not in self._graph:
            return {"center": doi, "papers": [], "edges": []}
        undirected = self._graph.to_undirected()
        neighbors = nx.single_source_shortest_path_length(undirected, doi, cutoff=depth)
        papers = [
            {**self._paper_data.get(d, {"doi": d}), "distance": dist}
            for d, dist in neighbors.items()
        ]
        subgraph = self._graph.subgraph(neighbors.keys())
        edges = [{"from": u, "to": v} for u, v in subgraph.edges()]
        return {"center": doi, "papers": papers, "edges": edges}
