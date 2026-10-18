from pathlib import Path
from datetime import datetime
import sys

p = Path("templates/conversation.html")
s = p.read_text(encoding="utf-8")
backup = p.with_name(f"conversation_before_compact_composer_{datetime.now():%Y%m%d_%H%M%S}.html")
backup.write_text(s, encoding="utf-8")

replacements = [
(
".composer{display:flex;align-items:flex-end;gap:8px;padding:9px 10px;box-sizing:border-box}",
".composer{display:flex;align-items:flex-end;gap:6px;padding:6px 9px;box-sizing:border-box}"
),
(
".composer-icon{width:36px;height:40px;flex:0 0 36px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--blue);border-radius:50%;-webkit-tap-highlight-color:transparent}",
".composer-icon{width:30px;height:34px;flex:0 0 30px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--blue);border-radius:50%;-webkit-tap-highlight-color:transparent}"
),
(
".composer-input-wrap{flex:1;min-width:0;background:#f0f2f5;border-radius:22px;padding:3px 11px;display:flex;flex-direction:column;justify-content:center}",
".composer-input-wrap{flex:1;min-width:0;background:#f0f2f5;border-radius:22px;padding:1px 10px;display:flex;flex-direction:column;justify-content:center}"
),
(
".composer textarea{display:block;width:100%;min-width:0;min-height:28px;max-height:68px;box-sizing:border-box;resize:none;border:0!important;box-shadow:none!important;background:transparent;padding:4px 2px;font:inherit;font-size:16px;line-height:20px;outline:none;overflow-y:auto}",
".composer textarea{display:block;width:100%;min-width:0;height:24px;min-height:24px;max-height:64px;box-sizing:border-box;resize:none;border:0!important;box-shadow:none!important;background:transparent;padding:2px 1px;font:inherit;font-size:16px;line-height:20px;outline:none;overflow-y:hidden}"
),
(
".send{width:40px;height:40px;flex:0 0 40px;padding:0;border-radius:50%;display:flex;align-items:center;justify-content:center}",
".send{width:34px;height:34px;flex:0 0 34px;padding:0;border-radius:50%;display:flex;align-items:center;justify-content:center}"
),
(
" .composer{gap:4px;padding:8px 6px;padding-bottom:max(8px,env(safe-area-inset-bottom))}",
" .composer{gap:3px;padding:5px 5px;padding-bottom:max(5px,env(safe-area-inset-bottom))}"
),
(
" .composer-icon{width:32px;flex-basis:32px;height:40px}",
" .composer-icon{width:28px;flex-basis:28px;height:34px}"
),
(
" .composer-input-wrap{padding:2px 10px}",
" .composer-input-wrap{padding:1px 8px}"
),
(
" .composer textarea{font-size:16px;min-height:28px;max-height:68px;padding:4px 1px}",
" .composer textarea{font-size:16px;height:24px;min-height:24px;max-height:64px;padding:2px 1px}"
),
(
" .send{width:38px;height:38px;flex-basis:38px}",
" .send{width:32px;height:32px;flex-basis:32px}"
),
(
'''input.addEventListener("input", () => {
  input.style.height = "28px";
  input.style.height = Math.min(input.scrollHeight, 68) + "px";
  input.style.overflowY = input.scrollHeight > 68 ? "auto" : "hidden";
});''',
'''input.addEventListener("input", () => {
  input.style.height = "24px";
  const maxHeight = 64;
  input.style.height = Math.min(input.scrollHeight, maxHeight) + "px";
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
});'''
)
]

for old, new in replacements:
    if old not in s:
        print("STOP: Expected code not found; no changes saved.")
        print("Missing:", old[:100].replace("\\n", " "))
        sys.exit(1)
    s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")
print("SUCCESS: Compact composer styles applied.")
print("Backup:", backup)
