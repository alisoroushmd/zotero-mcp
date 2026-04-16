"""Knowledge graph analysis using NetworkX over Zotero citation data."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime
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
        self._topic_data: dict[str, list[dict]] = {}
        self._author_data: dict[str, dict] = {}
        self._author_papers: dict[str, set[str]] = {}
        self._coauthor_graph: nx.Graph = nx.Graph()

    def build_from_store(self, store: GraphStore) -> dict:
        """Build the graph from persisted data."""
        self._graph.clear()
        self._paper_data.clear()
        self._topic_data.clear()

        papers = store.get_all_papers()
        for p in papers:
            doi = p["doi"]
            self._graph.add_node(doi)
            self._paper_data[doi] = p

        for citing, cited in store.get_all_citations():
            if citing in self._graph and cited in self._graph:
                self._graph.add_edge(citing, cited)

        # Load topic data for cluster labeling
        for t in store.get_all_topics():
            doi = t["doi"]
            self._topic_data.setdefault(doi, []).append(t)

        # Load author data
        self._author_data.clear()
        self._author_papers.clear()
        self._coauthor_graph.clear()

        for a in store.get_all_authors():
            aid = a["openalex_author_id"]
            self._author_data[aid] = a
            self._coauthor_graph.add_node(aid)

        for doi, author_id, position in store.get_all_paper_authors():
            self._author_papers.setdefault(author_id, set()).add(doi)

        # Build co-authorship edges (for each paper, connect all co-author pairs)
        papers_to_authors: dict[str, list[str]] = defaultdict(list)
        for doi, author_id, _ in store.get_all_paper_authors():
            papers_to_authors[doi].append(author_id)

        for doi, author_ids in papers_to_authors.items():
            for i, a1 in enumerate(author_ids):
                for a2 in author_ids[i + 1 :]:
                    if self._coauthor_graph.has_edge(a1, a2):
                        self._coauthor_graph[a1][a2]["weight"] += 1
                    else:
                        self._coauthor_graph.add_edge(a1, a2, weight=1)

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

            # Label cluster by dominant subfield from topic data
            topic_counts: Counter[str] = Counter()
            for doi in community:
                for t in self._topic_data.get(doi, []):
                    sf = t.get("subfield")
                    if sf:
                        topic_counts[sf] += 1

            label = topic_counts.most_common(1)[0][0] if topic_counts else "Unlabeled"
            total = sum(topic_counts.values())
            secondary = [
                sf
                for sf, c in topic_counts.most_common()
                if sf != label and total > 0 and c / total > 0.2
            ]

            clusters.append(
                {
                    "cluster_id": i,
                    "size": len(community),
                    "label": label,
                    "secondary_labels": secondary,
                    "topic_distribution": dict(topic_counts.most_common(10)),
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

    # -- Author analysis methods --

    def _resolve_author(self, name: str) -> str:
        """Resolve an author name to an openalex_author_id.

        First tries case-insensitive substring match, then SequenceMatcher > 0.85.
        Raises ValueError if no match or ambiguous (multiple matches).
        """
        from difflib import SequenceMatcher

        name_lower = name.lower()

        # Pass 1: case-insensitive substring
        matches = [
            aid
            for aid, data in self._author_data.items()
            if name_lower in data.get("display_name", "").lower()
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Try exact match first
            exact = [
                aid
                for aid in matches
                if self._author_data[aid].get("display_name", "").lower() == name_lower
            ]
            if len(exact) == 1:
                return exact[0]
            names = [self._author_data[m].get("display_name", "") for m in matches[:5]]
            raise ValueError(f"Ambiguous author name '{name}'. Matches: {names}")

        # Pass 2: fuzzy matching
        best_id = None
        best_ratio = 0.0
        for aid, data in self._author_data.items():
            ratio = SequenceMatcher(
                None, name_lower, data.get("display_name", "").lower()
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_id = aid
        if best_ratio > 0.85 and best_id:
            return best_id

        raise ValueError(f"No author matching '{name}' found in knowledge graph")

    def get_prolific_authors(self, top_n: int = 10) -> list[dict]:
        """Return authors ranked by paper count in the library."""
        ranked = sorted(
            self._author_papers.items(),
            key=lambda x: len(x[1]),
            reverse=True,
        )[:top_n]
        return [
            {**self._author_data.get(aid, {"openalex_author_id": aid}), "paper_count": len(dois)}
            for aid, dois in ranked
        ]

    def get_influential_authors(self, top_n: int = 10) -> list[dict]:
        """Return authors ranked by summed PageRank of their papers."""
        if not self._graph.nodes:
            return []
        pr = nx.pagerank(self._graph)
        author_scores: dict[str, float] = {}
        for aid, dois in self._author_papers.items():
            author_scores[aid] = sum(pr.get(doi, 0) for doi in dois)
        ranked = sorted(author_scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [
            {
                **self._author_data.get(aid, {"openalex_author_id": aid}),
                "influence_score": round(score, 6),
            }
            for aid, score in ranked
        ]

    def get_coauthors_of(self, author_id: str, top_n: int = 10) -> list[dict]:
        """Return co-authors of a given author, ranked by shared paper count."""
        if author_id not in self._coauthor_graph:
            return []
        neighbors = [
            (neighbor, self._coauthor_graph[author_id][neighbor].get("weight", 1))
            for neighbor in self._coauthor_graph.neighbors(author_id)
        ]
        ranked = sorted(neighbors, key=lambda x: x[1], reverse=True)[:top_n]
        return [
            {**self._author_data.get(aid, {"openalex_author_id": aid}), "shared_papers": w}
            for aid, w in ranked
        ]

    def get_author_clusters(self) -> list[dict]:
        """Detect author communities via greedy modularity on co-authorship graph."""
        if self._coauthor_graph.number_of_nodes() < 2:
            return []
        try:
            from networkx.algorithms.community import greedy_modularity_communities

            communities = greedy_modularity_communities(self._coauthor_graph)
        except Exception:
            return []

        clusters = []
        for i, community in enumerate(communities):
            members = [
                self._author_data.get(aid, {"openalex_author_id": aid})
                for aid in community
            ]
            clusters.append({"cluster_id": i, "size": len(community), "authors": members})
        return sorted(clusters, key=lambda c: c["size"], reverse=True)

    def get_author_network(self, author_id: str, depth: int = 1) -> dict:
        """Get ego network for an author: co-authors within N hops, shared papers."""
        if author_id not in self._coauthor_graph:
            return {"center": author_id, "authors": [], "edges": []}
        neighbors = nx.single_source_shortest_path_length(
            self._coauthor_graph, author_id, cutoff=depth
        )
        authors = [
            {**self._author_data.get(aid, {"openalex_author_id": aid}), "distance": dist}
            for aid, dist in neighbors.items()
        ]
        subgraph = self._coauthor_graph.subgraph(neighbors.keys())
        edges = [
            {"from": u, "to": v, "shared_papers": d.get("weight", 1)}
            for u, v, d in subgraph.edges(data=True)
        ]
        return {"center": author_id, "authors": authors, "edges": edges}

    # -- Temporal analytics methods --

    def _filter_by_date_range(
        self,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> list[str]:
        """Return DOIs of papers within the given year range."""
        dois = []
        for doi, data in self._paper_data.items():
            pub_date = data.get("publication_date", "") or ""
            year = data.get("year", 0) or 0
            if pub_date:
                try:
                    y = int(pub_date[:4])
                except (ValueError, IndexError):
                    y = year
            else:
                y = year
            if start_year and y < start_year:
                continue
            if end_year and y > end_year:
                continue
            dois.append(doi)
        return dois

    def get_timeline(
        self,
        topic: str | None = None,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> list[dict]:
        """Count papers per month, optionally filtered by subfield.

        Args:
            topic: Subfield name to filter by (case-insensitive substring match).
            start_year: Earliest year to include.
            end_year: Latest year to include.

        Returns:
            List of {month, count} dicts sorted chronologically.
        """
        counts: Counter[str] = Counter()
        for doi in self._filter_by_date_range(start_year, end_year):
            if topic:
                topics = self._topic_data.get(doi, [])
                match = any(
                    topic.lower() in (t.get("subfield") or "").lower()
                    for t in topics
                )
                if not match:
                    continue
            pub_date = (self._paper_data[doi].get("publication_date") or "")[:7]
            if pub_date:
                counts[pub_date] += 1
        return [
            {"month": m, "count": c}
            for m, c in sorted(counts.items())
        ]

    def get_topic_evolution(
        self,
        start_year: int | None = None,
        end_year: int | None = None,
        limit: int = 10,
    ) -> dict[str, list[dict]]:
        """Per-subfield paper counts by month.

        Args:
            start_year: Earliest year to include.
            end_year: Latest year to include.
            limit: Max number of subfields to return (ranked by total count).

        Returns:
            Dict mapping subfield name to list of {month, count} dicts.
        """
        subfield_month: dict[str, Counter[str]] = defaultdict(Counter)
        subfield_totals: Counter[str] = Counter()

        for doi in self._filter_by_date_range(start_year, end_year):
            pub_date = (self._paper_data[doi].get("publication_date") or "")[:7]
            if not pub_date:
                continue
            for t in self._topic_data.get(doi, []):
                sf = t.get("subfield")
                if sf:
                    subfield_month[sf][pub_date] += 1
                    subfield_totals[sf] += 1

        top_subfields = [sf for sf, _ in subfield_totals.most_common(limit)]
        return {
            sf: [{"month": m, "count": c} for m, c in sorted(subfield_month[sf].items())]
            for sf in top_subfields
        }

    def get_citation_velocity(self, doi: str) -> list[dict]:
        """Month-by-month citation count for a specific paper.

        Args:
            doi: DOI of the paper to analyze.

        Returns:
            List of {month, citations} dicts sorted chronologically.
        """
        if doi not in self._graph:
            return []
        citing_dois = list(self._graph.predecessors(doi))
        counts: Counter[str] = Counter()
        for citing_doi in citing_dois:
            pub_date = (self._paper_data.get(citing_doi, {}).get("publication_date") or "")[:7]
            if pub_date:
                counts[pub_date] += 1
        return [
            {"month": m, "citations": c}
            for m, c in sorted(counts.items())
        ]

    def get_trending(self, top_n: int = 10, years: int = 3) -> list[dict]:
        """Papers ranked by citation growth rate in recent N years.

        Compares citation rate in the recent window to lifetime average.
        Papers with fewer than 2 total citations are excluded.

        Args:
            top_n: Number of trending papers to return.
            years: Size of the recent window in years.

        Returns:
            List of paper dicts with velocity_ratio, recent_citations,
            total_citations fields, sorted by velocity_ratio descending.
        """
        now_year = datetime.now().year
        cutoff_month = f"{now_year - years}-01"

        results = []
        for doi in self._paper_data:
            citing_dois = list(self._graph.predecessors(doi))
            if len(citing_dois) < 2:
                continue

            total = len(citing_dois)
            recent = 0
            for citing_doi in citing_dois:
                pub_date = (
                    self._paper_data.get(citing_doi, {}).get("publication_date") or ""
                )[:7]
                if pub_date >= cutoff_month:
                    recent += 1

            if recent == 0:
                continue

            # Velocity ratio: recent per-year rate / lifetime per-year rate
            paper_date = (self._paper_data[doi].get("publication_date") or "")[:7]
            if not paper_date:
                continue
            try:
                paper_year = int(paper_date[:4])
            except (ValueError, IndexError):
                continue
            lifetime_years = max(now_year - paper_year, 1)
            lifetime_rate = total / lifetime_years
            recent_rate = recent / years
            velocity_ratio = recent_rate / lifetime_rate if lifetime_rate > 0 else 0

            results.append({
                **self._paper_data[doi],
                "velocity_ratio": round(velocity_ratio, 2),
                "recent_citations": recent,
                "total_citations": total,
            })

        results.sort(key=lambda x: x["velocity_ratio"], reverse=True)
        return results[:top_n]
