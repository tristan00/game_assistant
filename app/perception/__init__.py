"""Two-stage perception pipeline.

Stage 1 (``stage1.enumerate_image``): per-screenshot LLM call that enumerates
visible game-state slots and caches the result as a sidecar JSON next to the
screenshot. Runs at most once per screenshot file.

Stage 2 (``stage2.synthesize``): at question time, takes the N most recent
screenshots + their cached enumerations and produces a unified current-state
report (markdown). The reasoning call downstream consumes the synthesis
output as primary state, plus only the most recent screenshot as a visual
fallback.

Schema: ``schema.BASE_SCHEMA`` (game-agnostic). When a game corpus exists,
``schema_builder.build_perception_schema`` extends it with game-specific
slots and writes ``_perception_schema.md`` to the game's wiki dir.
"""
