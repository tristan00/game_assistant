from app.prompts import GOAL_INSTRUCTIONS, SYSTEM_PROMPT


def test_system_prompt_is_nonempty_string():
    assert isinstance(SYSTEM_PROMPT, str)
    assert SYSTEM_PROMPT.strip() != ""


def test_goal_instructions_is_nonempty_string():
    assert isinstance(GOAL_INSTRUCTIONS, str)
    assert GOAL_INSTRUCTIONS.strip() != ""
