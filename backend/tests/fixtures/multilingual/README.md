# Multilingual fixture set (manual eval)

Sample texts for the manual eval script (`scripts/eval_multilingual.py`).
Each file is a plain-text page; the eval script wraps them into a synthetic
document and reports per-page language, route, damage ratio and fallback
count. Add real scanned PDFs here as they become available.

- `hindi_page.txt` — Hindi (Devanagari), digital text.
- `tamil_page.txt` — Tamil, digital text.
- `mixed_en_ta_page.txt` — mixed English + Tamil (a typical Indian invoice).
- `gibberish_page.txt` — Latin gibberish simulating a broken Indic font
  (should be promoted to HARD/LAYOUT by triage).
