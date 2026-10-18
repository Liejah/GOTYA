from pathlib import Path
from datetime import datetime
import re
import sys

path = Path("templates/conversation.html")
html = path.read_text(encoding="utf-8")

# Keep a backup before making changes.
backup = path.with_name(
    f"conversation_before_3line_box_{datetime.now():%Y%m%d_%H%M%S}.html"
)
backup.write_text(html, encoding="utf-8")

# Set the composer textarea to one line initially and three lines maximum.
old_css = """.composer textarea{display:block;width:100%;min-width:0;min-height:36px;max-height:120px;box-sizing:border-box;resize:none;border:0!important;box-shadow:none!important;background:transparent;padding:8px 2px;font:inherit;font-size:16px;line-height:20px;outline:none;overflow-y:auto}"""
new_css = """.composer textarea{display:block;width:100%;min-width:0;min-height:28px;max-height:68px;box-sizing:border-box;resize:none;border:0!important;box-shadow:none!important;background:transparent;padding:4px 2px;font:inherit;font-size:16px;line-height:20px;outline:none;overflow-y:auto}"""

if old_css not in html:
    print("STOP: Main textarea CSS was not found exactly. No changes saved.")
    sys.exit(1)

html = html.replace(old_css, new_css, 1)

old_mobile = ".composer textarea{font-size:16px;min-height:36px;max-height:110px;padding:8px 1px}"
new_mobile = ".composer textarea{font-size:16px;min-height:28px;max-height:68px;padding:4px 1px}"

if old_mobile not in html:
    print("STOP: Mobile textarea CSS was not found exactly. No changes saved.")
    sys.exit(1)

html = html.replace(old_mobile, new_mobile, 1)

old_js = '''input.addEventListener("input", () => {
  input.style.height = "44px";
  input.style.height = Math.min(input.scrollHeight, 130) + "px";
});'''

new_js = '''input.addEventListener("input", () => {
  input.style.height = "28px";
  input.style.height = Math.min(input.scrollHeight, 68) + "px";
  input.style.overflowY = input.scrollHeight > 68 ? "auto" : "hidden";
});'''

if old_js not in html:
    print("STOP: Textarea resize JavaScript was not found exactly. No changes saved.")
    sys.exit(1)

html = html.replace(old_js, new_js, 1)
path.write_text(html, encoding="utf-8")

print("SUCCESS: Message box now starts at one line and expands to three lines.")
print(f"Backup saved: {backup}")
