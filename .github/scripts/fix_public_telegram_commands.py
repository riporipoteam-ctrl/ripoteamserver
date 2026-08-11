from pathlib import Path

path = Path("hf-space/ai_stack.py")
text = path.read_text(encoding="utf-8")
old = (
    '            "        user_allowed_commands:\\n"\n'
    '            "          - status\\n"\n'
    '            "          - model\\n"\n'
    '            "          - history\\n"\n'
)
new = (
    '            "        user_allowed_commands:\\n"\n'
    '            "          - new\\n"\n'
    '            "          - reset\\n"\n'
    '            "          - status\\n"\n'
    '            "          - model\\n"\n'
    '            "          - commands\\n"\n'
    '            "          - help\\n"\n'
    '            "          - whoami\\n"\n'
)
if old not in text:
    raise SystemExit("Expected Telegram user_allowed_commands block not found")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("Updated public Telegram messaging commands.")
