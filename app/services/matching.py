from typing import Any


async def find_matching_rules(text: str, db) -> list[dict[str, Any]]:
    """
    Return all rules whose keyword appears anywhere in text (case-insensitive).
    """
    text_lower = text.lower()
    matching: list[dict[str, Any]] = []
    async for rule in db.rules.find({}):
        if rule["keyword"].lower() in text_lower:
            matching.append(rule)
    return matching
