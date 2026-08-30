---
files:
  - AGENTS.md
  - README.md
  - src/zotero_mcp/config.py
  - src/zotero_mcp/graph_store.py
  - src/zotero_mcp/knowledge_graph.py
  - src/zotero_mcp/server.py
  - tests/test_config.py
  - tests/test_entities.py
  - tests/test_graph_store.py
  - tests/test_knowledge_graph.py
  - tests/test_server.py
---

# Bound graph indexing resource use

### Changed

- **Bounded full-text indexing.** PDF extraction now keeps at most twice the worker count in flight and commits successful text in 100-record SQLite batches, limiting both memory growth and transaction overhead on large libraries.
- **Batched entity persistence.** `store_entities` now writes entity and paper links in one transaction, with creation status and identifier lookup resolved without a preliminary existence query.
- **Safe graph materialization.** NetworkX graph builds now refuse persisted input above a configurable 100,000-record ceiling and rebuild author state from one paper-author snapshot while preserving public MCP shapes and the existing positional `Config` argument order.
