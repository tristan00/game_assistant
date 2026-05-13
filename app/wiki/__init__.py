"""Game knowledge layer: per-game wiki crawl + index + quick-ref.

Subpackage layout:
- ``storage`` — path helpers under ``~/game_assistant/wikis/<game_id>/``.
- ``api_client`` — MediaWiki action-API client (httpx, rate-limited).
- ``discovery`` — LLM-driven wiki discovery + endpoint probe.
- ``crawler`` — 2-hop BFS daemon-thread crawler.
- ``quick_ref`` — post-crawl LLM pass producing ``_quick_ref.md``.
- ``search`` — sqlite FTS5 index over the per-game corpus.
"""
