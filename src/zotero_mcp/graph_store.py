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
        self._migrate()

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
            CREATE TABLE IF NOT EXISTS entities (
                entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                UNIQUE(name, entity_type)
            );
            CREATE TABLE IF NOT EXISTS paper_entities (
                doi TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                confidence REAL,
                PRIMARY KEY (doi, entity_id)
            );
            CREATE INDEX IF NOT EXISTS idx_paper_entities_entity
                ON paper_entities(entity_id);
        """)
        # FTS5 virtual tables must be created outside executescript
        # because executescript may not handle virtual table syntax reliably.
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS paper_fulltext
                USING fts5(doi UNINDEXED, content, tokenize='porter unicode61')
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS fulltext_state (
                doi TEXT PRIMARY KEY,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                page_count INTEGER,
                char_count INTEGER
            )
        """)
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the initial schema."""
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(papers)").fetchall()
        }
        if "publication_date" not in cols:
            self._conn.execute(
                "ALTER TABLE papers ADD COLUMN publication_date TEXT"
            )
        if "abstract" not in cols:
            self._conn.execute(
                "ALTER TABLE papers ADD COLUMN abstract TEXT"
            )
        self._conn.commit()

    def upsert_paper(
        self,
        doi: str,
        zotero_key: str,
        title: str,
        year: int,
        authors: str,
        openalex_id: str,
        publication_date: str = "",
        abstract: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO papers
                   (doi, zotero_key, title, year, authors, openalex_id,
                    publication_date, abstract)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(doi) DO UPDATE SET
                   zotero_key=excluded.zotero_key,
                   title=excluded.title,
                   year=excluded.year,
                   authors=excluded.authors,
                   openalex_id=excluded.openalex_id,
                   publication_date=excluded.publication_date,
                   abstract=COALESCE(excluded.abstract, papers.abstract),
                   updated_at=CURRENT_TIMESTAMP""",
            (doi, zotero_key, title, year, authors, openalex_id,
             publication_date, abstract),
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

    def upsert_fulltext(
        self,
        doi: str,
        content: str,
        page_count: int,
        char_count: int,
    ) -> None:
        """Insert or replace full-text content for a paper.

        Args:
            doi: Paper DOI.
            content: Extracted text content.
            page_count: Number of pages (approximate).
            char_count: Character count of the extracted text.
        """
        # Delete existing FTS entry if present (FTS5 doesn't support ON CONFLICT)
        self._conn.execute(
            "DELETE FROM paper_fulltext WHERE doi = ?", (doi,)
        )
        self._conn.execute(
            "INSERT INTO paper_fulltext(doi, content) VALUES (?, ?)",
            (doi, content),
        )
        self._conn.execute(
            """INSERT OR REPLACE INTO fulltext_state
                   (doi, indexed_at, page_count, char_count)
               VALUES (?, CURRENT_TIMESTAMP, ?, ?)""",
            (doi, page_count, char_count),
        )
        self._conn.commit()

    def get_indexed_dois(self) -> set[str]:
        """Return the set of DOIs that have been full-text indexed.

        Returns:
            Set of DOI strings present in fulltext_state.
        """
        rows = self._conn.execute("SELECT doi FROM fulltext_state").fetchall()
        return {r[0] for r in rows}

    def search_fulltext(self, query: str, limit: int = 20) -> list[dict]:
        """Search full-text index using FTS5 with BM25 ranking.

        Args:
            query: FTS5 search query string.
            limit: Maximum number of results.

        Returns:
            List of dicts with doi, title, zotero_key, snippet, rank.
        """
        rows = self._conn.execute(
            """SELECT
                   pf.doi,
                   p.title,
                   p.zotero_key,
                   snippet(paper_fulltext, 1, '<b>', '</b>', '...', 30) AS snippet,
                   rank
               FROM paper_fulltext pf
               LEFT JOIN papers p ON p.doi = pf.doi
               WHERE paper_fulltext MATCH ?
               ORDER BY rank ASC
               LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_fulltext(self, doi: str) -> None:
        """Remove full-text data for a paper from both tables.

        Args:
            doi: Paper DOI to remove.
        """
        self._conn.execute(
            "DELETE FROM paper_fulltext WHERE doi = ?", (doi,)
        )
        self._conn.execute(
            "DELETE FROM fulltext_state WHERE doi = ?", (doi,)
        )
        self._conn.commit()

    # -- Entity CRUD methods --

    def upsert_entity(self, name: str, entity_type: str) -> int:
        """Insert or find an entity, returning its entity_id.

        Name is normalized to lowercase/stripped before storage.

        Args:
            name: Entity name (e.g. "CDX2", "metformin").
            entity_type: Entity category (e.g. "biomarker", "drug").

        Returns:
            The entity_id (existing or newly created).
        """
        normalized = name.strip().lower()
        self._conn.execute(
            "INSERT OR IGNORE INTO entities (name, entity_type) VALUES (?, ?)",
            (normalized, entity_type),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT entity_id FROM entities WHERE name = ? AND entity_type = ?",
            (normalized, entity_type),
        ).fetchone()
        return row[0]

    def upsert_paper_entity(
        self, doi: str, entity_id: int, confidence: float | None = None
    ) -> None:
        """Link a paper to an entity.

        Args:
            doi: Paper DOI.
            entity_id: Entity identifier from upsert_entity().
            confidence: Optional extraction confidence score.
        """
        self._conn.execute(
            """INSERT OR REPLACE INTO paper_entities
                   (doi, entity_id, confidence)
               VALUES (?, ?, ?)""",
            (doi, entity_id, confidence),
        )
        self._conn.commit()

    def get_entities_for_doi(self, doi: str) -> list[dict]:
        """Return all entities linked to a paper.

        Args:
            doi: Paper DOI.

        Returns:
            List of dicts with entity_id, name, entity_type, confidence.
        """
        rows = self._conn.execute(
            """SELECT e.entity_id, e.name, e.entity_type, pe.confidence
               FROM paper_entities pe
               JOIN entities e ON e.entity_id = pe.entity_id
               WHERE pe.doi = ?""",
            (doi,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_papers_for_entity(self, entity_id: int) -> list[dict]:
        """Return all papers linked to an entity.

        Args:
            entity_id: Entity identifier.

        Returns:
            List of paper dicts.
        """
        rows = self._conn.execute(
            """SELECT p.* FROM paper_entities pe
               JOIN papers p ON p.doi = pe.doi
               WHERE pe.entity_id = ?""",
            (entity_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unextracted_dois(self) -> list[dict]:
        """Return papers with abstracts but no extracted entities.

        Returns:
            List of dicts with doi, title, abstract.
        """
        rows = self._conn.execute(
            """SELECT doi, title, abstract FROM papers
               WHERE abstract IS NOT NULL AND abstract != ''
               AND doi NOT IN (SELECT DISTINCT doi FROM paper_entities)"""
        ).fetchall()
        return [dict(r) for r in rows]

    def search_entities_by_name(self, name: str, limit: int = 20) -> list[dict]:
        """Search entities by name (case-insensitive LIKE).

        Args:
            name: Search term.
            limit: Max results.

        Returns:
            List of entity dicts with entity_id, name, entity_type.
        """
        normalized = name.strip().lower()
        rows = self._conn.execute(
            """SELECT entity_id, name, entity_type FROM entities
               WHERE name LIKE ? LIMIT ?""",
            (f"%{normalized}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_entity_co_occurrence(self, entity_id: int, limit: int = 20) -> list[dict]:
        """Find entities that co-occur in papers with the given entity.

        Args:
            entity_id: Entity to find co-occurring entities for.
            limit: Max results.

        Returns:
            List of dicts with entity_id, name, entity_type, shared_papers count.
        """
        rows = self._conn.execute(
            """SELECT e.entity_id, e.name, e.entity_type, COUNT(*) as shared_papers
               FROM paper_entities pe1
               JOIN paper_entities pe2 ON pe1.doi = pe2.doi
               JOIN entities e ON e.entity_id = pe2.entity_id
               WHERE pe1.entity_id = ? AND pe2.entity_id != ?
               GROUP BY pe2.entity_id
               ORDER BY shared_papers DESC
               LIMIT ?""",
            (entity_id, entity_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_shared_entities(self, doi_a: str, doi_b: str) -> list[dict]:
        """Return entities shared by both papers.

        Args:
            doi_a: First paper DOI.
            doi_b: Second paper DOI.

        Returns:
            List of entity dicts with entity_id, name, entity_type.
        """
        rows = self._conn.execute(
            """SELECT e.entity_id, e.name, e.entity_type
               FROM paper_entities pe1
               JOIN paper_entities pe2 ON pe1.entity_id = pe2.entity_id
               JOIN entities e ON e.entity_id = pe1.entity_id
               WHERE pe1.doi = ? AND pe2.doi = ?""",
            (doi_a, doi_b),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_entity_types(self) -> list[dict]:
        """Return entity type summary with counts.

        Returns:
            List of dicts with entity_type and count.
        """
        rows = self._conn.execute(
            """SELECT entity_type, COUNT(*) as count
               FROM entities
               GROUP BY entity_type
               ORDER BY count DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
