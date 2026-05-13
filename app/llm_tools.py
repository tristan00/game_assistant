"""Client-side LLM tool declarations.

These tools are dispatched in the Python process and their results are returned
to the model via ``tool_result`` content blocks.
"""

from app.prompts import SEARCH_GAME_RULES_TOOL_DESCRIPTION


def search_game_rules_tool() -> dict:
    """Tool schema for the local corpus search tool."""
    return {
        "name": "search_game_rules",
        "description": SEARCH_GAME_RULES_TOOL_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "2–6 specific terms (not a full sentence) to search the local corpus.",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    }


def format_search_game_rules_result(results: list[dict], query: str) -> str:
    """Render local-corpus search results as a single text block for tool_result.

    Format is deliberately compact: each hit takes ~3 lines. URLs are included
    so the model can cite sources back to the user.
    """
    if not results:
        return (
            f'No results in the local corpus for query: "{query}"\n'
            "(try a different phrasing)"
        )
    lines = [f'Local-corpus results for "{query}" (top {len(results)}):']
    for i, r in enumerate(results, 1):
        title = r.get("title") or "(untitled)"
        url = r.get("url") or ""
        snippet = (r.get("snippet") or "").replace("\n", " ").strip()
        lines.append(f"[{i}] {title}  {url}")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines)
