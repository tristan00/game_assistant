from app.prompts import STRATEGY_INSTRUCTIONS, SYSTEM_PROMPT


def test_system_prompt_is_nonempty_string():
    assert isinstance(SYSTEM_PROMPT, str)
    assert SYSTEM_PROMPT.strip() != ""


def test_strategy_instructions_is_nonempty_string():
    assert isinstance(STRATEGY_INSTRUCTIONS, str)
    assert STRATEGY_INSTRUCTIONS.strip() != ""
