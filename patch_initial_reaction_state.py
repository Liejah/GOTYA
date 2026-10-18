from pathlib import Path
from datetime import datetime
import shutil

path = Path("templates/conversation.html")
source = path.read_text(encoding="utf-8")

old = '''    {% for emoji in reaction_emojis %}
    <button type="button" class="reaction-button" data-emoji="{{ emoji }}" aria-label="React {{ emoji }}">{{ emoji }}</button>
    {% endfor %}'''

new = '''    {% for emoji in reaction_emojis %}
    {% set selected = reaction_map.get(item.id, [])|selectattr('emoji', 'equalto', emoji)|selectattr('reacted_by_me')|list %}
    <button type="button" class="reaction-button{% if selected %} reacted-by-me{% endif %}" data-emoji="{{ emoji }}" data-reacted-by-me="{{ 'true' if selected else 'false' }}" aria-pressed="{{ 'true' if selected else 'false' }}" aria-label="React {{ emoji }}">{{ emoji }}</button>
    {% endfor %}'''

if source.count(old) != 1:
    raise SystemExit("STOP: Expected HTML was not found exactly once. No changes made.")

backup = Path(
    "templates/conversation.html.before_initial_reaction_state_"
    + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
)
shutil.copy2(path, backup)

updated = source.replace(old, new, 1)
path.write_text(updated, encoding="utf-8", newline="")

if path.read_text(encoding="utf-8") != updated:
    raise SystemExit(f"Verification failed. Backup: {backup}")

print("Initial reaction state fixed and verified.")
print(f"Backup: {backup}")
