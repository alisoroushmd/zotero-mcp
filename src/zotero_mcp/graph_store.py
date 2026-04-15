"""SQLite persistence for the Zotero knowledge graph.

Stores papers (nodes) and citations (edges) locally so the graph
survives between MCP sessions and supports incremental updates.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


_DEFAULT_DB_PATH = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "zotero-mcp",
    "knowledge_graph.db",
)


class GraphStore:
    """SQLite-backed storage for knowledge graph nodes and edges.

    Thread safety: each tool call should create its own GraphStore instance.
    sqlite3 connections are not shared across threads.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or os.environ.get(
            "ZOTERO_MCP_GRAPH_DB", _DEFAULT_DB_PATH
        )
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
                doi TEXT PRIMARY KEY,
                zotero_key TEXT,
                title TEXT,
                year INTEGER,
                authors TEXT,
                openalex_id TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS citations (
                citing_doi TEXT NOT NULL,
                cited_doi TEXT NOT NULL,
                PRIMARY KEY (citing_doi, cited_doi)
            );
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_citations_cited
                ON citations(cited_doi);
            CREATE INDEX IF NOT EXISTS idx_papers_zotero_key
                ON papers(zotero_key);
            CREATE TABLE IF NOT EXISTS paper_topics (
                doi TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                topic_name TEXT,
                subfield TEXT,
                field TEXT,
                domain TEXT,
                score REAL,
                PRIMARY KEY (doi, topic_id)
            );
            CREATE TABLE IF NOT EXISTS authors (
                openalex_author_id TEXT PRIMARY KEY,
                display_name TEXT,
                orcid TEXT,
                institution TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS paper_authors (
                doi TEXT NOT NULL,
                openalex_author_id TEXT NOT NULL,
                position INTEGER,
                PRIMARY KEY (doi, openalex_author_id)
            );
            CREATE INDEX IF NOT EXISTS idx_paper_authors_author
                ON paper_authors(openalex_author_id);
        """)
        self._conn.commit()

    def upsert_paper(
        self,
        doi: str,
        zotero_key: str,
        title: str,
        year: int,
        authors: str,
        openalex_id: str,
    ) -> None:
        self._conn.execute(
            """INSERT INTO papers (doi, zotero_key, title, year, authors, openalex_id)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(doi) DO UPDATE SET
                   zotero_key=excluded.zotero_key,
                   title=excluded.title,
                   year=excluded.year,
                   authors=excluded.authors,
                   openalex_id=excluded.openalex_id,
                   updated_at=CURRENT_TIMESTAMP""",
            (doi, zotero_key, title, year, authors, openalex_id),
        )
        self._conn.commit()

    def upsert_citation(self, citing_doi: str, cited_doi: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO citations (citing_doi, cited_doi) VALUES (?, ?)",
            (citing_doi, cited_doi),
        )
        self._conn.commit()

    def get_paper(self, doi: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM papers WHERE doi = ?", (doi,)
        ).fetchone()
        return dict(row) if row else None

    def get_references(self, doi: str) -> list[dict]:
        rows = self._conn.execute(
            """SELECT p.* FROM citations c
               JOIN papers p ON p.doi = c.cited_doi
               WHERE c.citing_doi = ?""",
            (doi,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_citing_papers(self, doi: str) -> list[dict]:
        rows = self._conn.execute(
            """SELECT p.* FROM citations c
               JOIN papers p ON p.doi = c.citing_doi
               WHERE c.cited_doi = ?""",
            (doi,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_papers(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM papers").fetchall()
        return [dict(r) for r in rows]

    def get_all_citations(self) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT citing_doi, cited_doi FROM citations"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def get_doi_set(self) -> set[str]:
        """Return the set of all DOIs in the store (fast membership check)."""
        rows = self._conn.execute("SELECT doi FROM papers").fetchall()
        return {r[0] for r in rows}

    def get_last_sync(self) -> dict | None:
        """Return last sync metadata, or None if never synced."""
        ts = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = 'last_sync'"
        ).fetchone()
        ver = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = 'library_version'"
        ).fetchone()
        if ts is None:
            return None
        return {
            "timestamp": ts[0],
            "library_version": int(ver[0]) if ver else None,
        }

    def set_last_sync(self, timestamp: str, library_version: int | None = None) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sync_state (key, value) VALUES ('last_sync', ?)",
            (timestamp,),
        )
        if library_version is not None:
            self._conn.execute(
                "INSERT OR REPLACE INTO sync_state (key, value) VALUES ('library_version', ?)",
                (str(library_version),),
            )
        self._conn.commit()

    def upsert_topic(
        self,
        doi: str,
        topic_id: str,
        topic_name: str | None,
        subfield: str | None,
        field: str | None,
        domain: str | None,
        score: float | None,
    ) -> None:
        """Insert or update a topic association for a paper.

        Args:
            doi: Paper DOI.
            topic_id: OpenAlex topic identifier.
            topic_name: Human-readable topic name.
            subfield: OpenAlex subfield name.
            field: OpenAlex field name.
            domain: OpenAlex domain name.
            score: Topic relevance score (0-1).
        """
        self._conn.execute(
            """INSERT INTO paper_topics
                   (doi, topic_id, topic_name, subfield, field, domain, score)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(doi, topic_id) DO UPDATE SET
                   topic_name=excluded.topic_name,
                   subfield=excluded.subfield,
                   field=excluded.field,
                   domain=excluded.domain,
                   score=excluded.score""",
            (doi, topic_id, topic_name, subfield, field, domain, score),
        )
        self._conn.commit()

    def upsert_author(
        self,
        openalex_author_id: str,
        display_name: str | None,
        orcid: str | None,
        institution: str | None,
    ) -> None:
        """Insert or update an author record.

        Args:
            openalex_author_id: OpenAlex author identifier.
            display_name: Author display name.
            orcid: ORCID identifier.
            institution: Primary institutional affiliation.
        """
        self._conn.execute(
            """INSERT INTO authors
                   (openalex_author_id, display_name, orcid, institution)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(openalex_author_id) DO UPDATE SET
                   display_name=excluded.display_name,
                   orcid=excluded.orcid,
                   institution=excluded.institution,
                   updated_at=CURRENT_TIMESTAMP""",
            (openalex_author_id, display_name, orcid, institution),
        )
        self._conn.commit()

    def upsert_paper_author(
        self,
        doi: str,
        openalex_author_id: str,
        position: int,
    ) -> None:
        """Insert a paper-author link (ignores duplicates).

        Args:
            doi: Paper DOI.
            openalex_author_id: OpenAlex author identifier.
            position: Author position in the author list (0-indexed).
        """
        self._conn.execute(
            """INSERT OR IGNORE INTO paper_authors
                   (doi, openalex_author_id, position)
               VALUES (?, ?, ?)""",
            (doi, openalex_author_id, position),
        )
        self._conn.commit()

    def get_topics_for_doi(self, doi: str) -> list[dict]:
        """Return all topic associations for a given DOI.

        Args:
            doi: Paper DOI to look up.

        Returns:
            List of topic dicts for the paper.
        """
        rows = self._conn.execute(
            "SELECT * FROM paper_topics WHERE doi = ?", (doi,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_topics(self) -> list[dict]:
        """Return all topic associations across all papers.

        Returns:
            List of all topic dicts.
        """
        rows = self._conn.execute("SELECT * FROM paper_topics").fetchall()
        return [dict(r) for r in rows]

    def get_all_authors(self) -> list[dict]:
        """Return all author records.

        Returns:
            List of author dicts.
        """
        rows = self._conn.execute("SELECT * FROM authors").fetchall()
        return [dict(r) for r in rows]

    def get_all_paper_authors(self) -> list[tuple[str, str, int]]:
        """Return all paper-author links.

        Returns:
            List of (doi, openalex_author_id, position) tuples.
        """
        rows = self._conn.execute(
            "SELECT doi, openalex_author_id, position FROM paper_authors"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def close(self) -> None:
        self._conn.close()
