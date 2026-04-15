"""HTML graph visualization using D3.js force-directed layout."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zotero_mcp.knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Zotero Knowledge Graph</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #c9d1d9; overflow: hidden; }
  svg { width: 100vw; height: 100vh; display: block; }
  .node-label { font-size: 10px; fill: #c9d1d9; pointer-events: none; }
  .link { stroke-opacity: 0.4; }
  .citation-link { stroke: #30363d; }
  .coauthor-link { stroke: #58a6ff; stroke-dasharray: 4 2; }
  #info-panel {
    position: fixed; top: 16px; right: 16px; width: 320px;
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 16px; font-size: 13px; display: none; z-index: 10;
    max-height: 80vh; overflow-y: auto;
  }
  #info-panel h3 { margin-bottom: 8px; color: #58a6ff; font-size: 14px; }
  #info-panel .field { margin-bottom: 4px; }
  #info-panel .field-label { color: #8b949e; }
  #legend {
    position: fixed; bottom: 16px; left: 16px; background: #161b22;
    border: 1px solid #30363d; border-radius: 8px; padding: 12px;
    font-size: 12px; z-index: 10;
  }
  #legend .entry { display: flex; align-items: center; margin-bottom: 4px; }
  #legend .swatch { width: 12px; height: 12px; border-radius: 50%;
                    margin-right: 8px; display: inline-block; }
  #legend .diamond { width: 12px; height: 12px; margin-right: 8px;
                     transform: rotate(45deg); display: inline-block; }
</style>
</head>
<body>
<div id="info-panel">
  <h3 id="info-title"></h3>
  <div id="info-body"></div>
</div>
<div id="legend"></div>
<svg></svg>
<script>window.__GRAPH_DATA = __GRAPH_DATA_PLACEHOLDER__;</script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
(function() {
  const data = window.__GRAPH_DATA;
  const width = window.innerWidth;
  const height = window.innerHeight;

  const svg = d3.select("svg")
    .attr("viewBox", [0, 0, width, height]);

  const g = svg.append("g");

  svg.call(d3.zoom()
    .scaleExtent([0.1, 8])
    .on("zoom", (e) => g.attr("transform", e.transform)));

  const colorScale = d3.scaleOrdinal(d3.schemeTableau10);

  const link = g.append("g")
    .selectAll("line")
    .data(data.edges)
    .join("line")
    .attr("class", d => "link " + (d.type === "coauthor" ? "coauthor-link" : "citation-link"))
    .attr("stroke-width", d => d.type === "coauthor" ? Math.min(d.weight || 1, 5) : 0.8);

  const node = g.append("g")
    .selectAll("g")
    .data(data.nodes)
    .join("g")
    .call(d3.drag()
      .on("start", dragstarted)
      .on("drag", dragged)
      .on("end", dragended));

  node.each(function(d) {
    const el = d3.select(this);
    if (d.type === "author") {
      el.append("rect")
        .attr("width", d.size * 2).attr("height", d.size * 2)
        .attr("x", -d.size).attr("y", -d.size)
        .attr("transform", "rotate(45)")
        .attr("fill", colorScale(d.group || 0))
        .attr("stroke", "#c9d1d9").attr("stroke-width", 0.5);
    } else {
      el.append("circle")
        .attr("r", d.size)
        .attr("fill", colorScale(d.group || 0))
        .attr("stroke", "#c9d1d9").attr("stroke-width", 0.5);
    }
  });

  node.append("text")
    .attr("class", "node-label")
    .attr("dx", d => d.size + 4).attr("dy", 3)
    .text(d => d.label.length > 30 ? d.label.slice(0, 27) + "..." : d.label);

  node.on("click", (event, d) => {
    event.stopPropagation();
    const panel = document.getElementById("info-panel");
    document.getElementById("info-title").textContent = d.label;
    let html = "";
    for (const [k, v] of Object.entries(d.meta || {})) {
      html += '<div class="field"><span class="field-label">' + k + ':</span> ' + v + '</div>';
    }
    document.getElementById("info-body").innerHTML = html;
    panel.style.display = "block";
  });

  svg.on("click", () => {
    document.getElementById("info-panel").style.display = "none";
  });

  // Legend
  const groups = [...new Set(data.nodes.map(n => n.group))].sort();
  const legendDiv = document.getElementById("legend");
  const labels = data.group_labels || {};
  groups.forEach(g => {
    const entry = document.createElement("div");
    entry.className = "entry";
    entry.innerHTML = '<span class="swatch" style="background:' + colorScale(g) + '"></span>'
      + (labels[g] || "Group " + g);
    legendDiv.appendChild(entry);
  });

  const simulation = d3.forceSimulation(data.nodes)
    .force("link", d3.forceLink(data.edges).id(d => d.id).distance(80))
    .force("charge", d3.forceManyBody().strength(-120))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collision", d3.forceCollide().radius(d => d.size + 2));

  simulation.on("tick", () => {
    link
      .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
      .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
    node.attr("transform", d => "translate(" + d.x + "," + d.y + ")");
  });

  function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x; d.fy = d.y;
  }
  function dragged(event, d) { d.fx = event.x; d.fy = event.y; }
  function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null; d.fy = null;
  }
})();
</script>
</body>
</html>
"""


def _paper_node(doi: str, paper: dict, pagerank: float, group: int) -> dict:
    """Build a node dict for a paper."""
    size = 4 + pagerank * 3000  # scale PR to visible radius
    size = min(max(size, 4), 24)
    return {
        "id": doi,
        "label": paper.get("title", doi)[:60],
        "type": "paper",
        "group": group,
        "size": round(size, 1),
        "meta": {
            "DOI": doi,
            "Title": paper.get("title", ""),
            "Year": paper.get("year", ""),
            "Authors": paper.get("authors", ""),
        },
    }


def _author_node(author_id: str, author: dict, paper_count: int, group: int) -> dict:
    """Build a node dict for an author."""
    size = 4 + paper_count * 2
    size = min(max(size, 4), 20)
    return {
        "id": author_id,
        "label": author.get("display_name", author_id),
        "type": "author",
        "group": group,
        "size": round(size, 1),
        "meta": {
            "Name": author.get("display_name", ""),
            "Institution": author.get("institution", ""),
            "ORCID": author.get("orcid", ""),
            "Papers": str(paper_count),
        },
    }


def render_citations_view(kg: KnowledgeGraph) -> tuple[str, dict]:
    """Render paper nodes + citation edges, colored by cluster.

    Args:
        kg: A built KnowledgeGraph instance.

    Returns:
        Tuple of (html_string, stats_dict).
    """
    import networkx as nx

    graph = kg._graph
    if not graph.nodes:
        return _render_html({"nodes": [], "edges": [], "group_labels": {}}), {
            "nodes": 0,
            "edges": 0,
            "view": "citations",
        }

    pr = nx.pagerank(graph)

    # Assign clusters for coloring
    clusters = kg.get_clusters()
    doi_to_group: dict[str, int] = {}
    group_labels: dict[int, str] = {}
    for c in clusters:
        gid = c["cluster_id"]
        group_labels[gid] = c.get("label", f"Cluster {gid}")
        for p in c["papers"]:
            doi_to_group[p.get("doi", "")] = gid

    nodes = []
    for doi in graph.nodes:
        paper = kg._paper_data.get(doi, {"doi": doi})
        group = doi_to_group.get(doi, 0)
        nodes.append(_paper_node(doi, paper, pr.get(doi, 0), group))

    edges = [
        {"source": u, "target": v, "type": "citation"}
        for u, v in graph.edges()
    ]

    data = {"nodes": nodes, "edges": edges, "group_labels": {str(k): v for k, v in group_labels.items()}}
    stats = {"nodes": len(nodes), "edges": len(edges), "view": "citations"}
    return _render_html(data), stats


def render_authors_view(kg: KnowledgeGraph) -> tuple[str, dict]:
    """Render author nodes + co-authorship edges.

    Args:
        kg: A built KnowledgeGraph instance.

    Returns:
        Tuple of (html_string, stats_dict).
    """
    coauthor = kg._coauthor_graph
    if not coauthor.nodes:
        return _render_html({"nodes": [], "edges": [], "group_labels": {}}), {
            "nodes": 0,
            "edges": 0,
            "view": "authors",
        }

    # Cluster authors for coloring
    clusters = kg.get_author_clusters()
    aid_to_group: dict[str, int] = {}
    group_labels: dict[int, str] = {}
    for c in clusters:
        gid = c["cluster_id"]
        group_labels[gid] = f"Group {gid}"
        for a in c["authors"]:
            aid_to_group[a.get("openalex_author_id", "")] = gid

    nodes = []
    for aid in coauthor.nodes:
        author = kg._author_data.get(aid, {"openalex_author_id": aid})
        pcount = len(kg._author_papers.get(aid, set()))
        group = aid_to_group.get(aid, 0)
        nodes.append(_author_node(aid, author, pcount, group))

    edges = [
        {
            "source": u,
            "target": v,
            "type": "coauthor",
            "weight": d.get("weight", 1),
        }
        for u, v, d in coauthor.edges(data=True)
    ]

    data = {"nodes": nodes, "edges": edges, "group_labels": {str(k): v for k, v in group_labels.items()}}
    stats = {"nodes": len(nodes), "edges": len(edges), "view": "authors"}
    return _render_html(data), stats


def render_full_view(kg: KnowledgeGraph, max_papers: int = 200) -> tuple[str, dict]:
    """Render both papers and authors, capped by PageRank.

    Args:
        kg: A built KnowledgeGraph instance.
        max_papers: Maximum paper nodes to include (ranked by PageRank).

    Returns:
        Tuple of (html_string, stats_dict).
    """
    import networkx as nx

    graph = kg._graph
    coauthor = kg._coauthor_graph

    if not graph.nodes and not coauthor.nodes:
        return _render_html({"nodes": [], "edges": [], "group_labels": {}}), {
            "nodes": 0,
            "edges": 0,
            "view": "full",
        }

    # Papers: top N by PageRank
    pr = nx.pagerank(graph) if graph.nodes else {}
    top_dois = sorted(pr, key=pr.get, reverse=True)[:max_papers]
    top_set = set(top_dois)

    clusters = kg.get_clusters()
    doi_to_group: dict[str, int] = {}
    group_labels: dict[int, str] = {}
    for c in clusters:
        gid = c["cluster_id"]
        group_labels[gid] = c.get("label", f"Cluster {gid}")
        for p in c["papers"]:
            doi_to_group[p.get("doi", "")] = gid

    # Author group IDs offset to avoid collision with paper cluster IDs
    offset = max(group_labels.keys(), default=-1) + 1
    author_clusters = kg.get_author_clusters()
    aid_to_group: dict[str, int] = {}
    for c in author_clusters:
        gid = c["cluster_id"] + offset
        group_labels[gid] = f"Author Group {c['cluster_id']}"
        for a in c["authors"]:
            aid_to_group[a.get("openalex_author_id", "")] = gid

    nodes = []
    for doi in top_dois:
        paper = kg._paper_data.get(doi, {"doi": doi})
        group = doi_to_group.get(doi, 0)
        nodes.append(_paper_node(doi, paper, pr.get(doi, 0), group))

    for aid in coauthor.nodes:
        author = kg._author_data.get(aid, {"openalex_author_id": aid})
        pcount = len(kg._author_papers.get(aid, set()))
        group = aid_to_group.get(aid, offset)
        nodes.append(_author_node(aid, author, pcount, group))

    edges = []
    for u, v in graph.edges():
        if u in top_set and v in top_set:
            edges.append({"source": u, "target": v, "type": "citation"})

    for u, v, d in coauthor.edges(data=True):
        edges.append({
            "source": u,
            "target": v,
            "type": "coauthor",
            "weight": d.get("weight", 1),
        })

    data = {"nodes": nodes, "edges": edges, "group_labels": {str(k): v for k, v in group_labels.items()}}
    stats = {"nodes": len(nodes), "edges": len(edges), "view": "full"}
    return _render_html(data), stats


def _render_html(data: dict) -> str:
    """Inject graph data into the HTML template."""
    json_str = json.dumps(data, ensure_ascii=False)
    return _HTML_TEMPLATE.replace("__GRAPH_DATA_PLACEHOLDER__", json_str)
