from pathlib import Path

index = Path('site/index.html')
html = index.read_text(encoding='utf-8')
html = html.replace(
    'Ripo Team Cloud PC — mobile Linux, local AI, Hermes Agent and private Telegram automation.',
    'Ripo Team Cloud PC — mobile Linux, local AI, Hermes Agent and public-safe Telegram access.'
)
html = html.replace(
    '<article><span>Telegram</span><strong id="ai-telegram">Checking…</strong><small id="ai-telegram-detail">Private access</small></article>',
    '<article><span>Telegram</span><strong id="ai-telegram">Checking…</strong><small id="ai-telegram-detail">Checking access…</small></article>'
)
old_card = '''          <div class="pair-card">
            <div><strong>Private Telegram pairing</strong><p>After the bot sends you a pairing code, enter it here. Unknown users remain denied.</p></div>
            <div class="pair-row"><input id="telegram-pair-code" autocomplete="one-time-code" autocapitalize="characters" placeholder="Pairing code"><button id="approve-pair" type="button">Approve me</button></div>
          </div>'''
new_card = '''          <div id="telegram-access-card" class="pair-card">
            <div><strong id="telegram-access-title">Telegram access</strong><p id="telegram-access-copy">Checking Telegram access mode…</p></div>
            <div id="telegram-pair-row" class="pair-row"><input id="telegram-pair-code" autocomplete="one-time-code" autocapitalize="characters" placeholder="Pairing code"><button id="approve-pair" type="button">Approve me</button></div>
          </div>'''
if old_card in html:
    html = html.replace(old_card, new_card, 1)
elif 'id="telegram-access-card"' not in html:
    raise SystemExit('Could not locate Telegram pairing card')
index.write_text(html, encoding='utf-8')

app = Path('site/app.js')
js = app.read_text(encoding='utf-8')
js = js.replace('const DEPLOYMENT_VERSION = "2026-08-11-local-ai-v1";', 'const DEPLOYMENT_VERSION = "2026-08-11-public-telegram-v1";')
old_refs = '  aiSkills: q("#ai-skills"), aiPlugins: q("#ai-plugins"), pairCode: q("#telegram-pair-code"), secretWarning: q("#telegram-secret-warning"),\n'
new_refs = '  aiSkills: q("#ai-skills"), aiPlugins: q("#ai-plugins"), pairCode: q("#telegram-pair-code"), secretWarning: q("#telegram-secret-warning"),\n  telegramAccessCard: q("#telegram-access-card"), telegramAccessTitle: q("#telegram-access-title"), telegramAccessCopy: q("#telegram-access-copy"), telegramPairRow: q("#telegram-pair-row"),\n'
if 'telegramAccessCard:' not in js:
    if old_refs not in js:
        raise SystemExit('Could not locate app element refs')
    js = js.replace(old_refs, new_refs, 1)

old_detail = '  if (el.aiTelegramDetail) el.aiTelegramDetail.textContent = telegram.access_mode === "allowlist" ? "Restricted allowlist" : "Default deny · pairing";\n'
new_detail = '''  const publicTelegram = telegram.access_mode === "public-safe" || telegram.pairing_required === false;
  if (el.aiTelegramDetail) el.aiTelegramDetail.textContent = publicTelegram ? "Public · no pairing · safe tools" : telegram.access_mode === "allowlist" ? "Restricted allowlist" : "Default deny · pairing";
  if (el.telegramAccessTitle) el.telegramAccessTitle.textContent = publicTelegram ? "Public Telegram" : "Private Telegram pairing";
  if (el.telegramAccessCopy) el.telegramAccessCopy.textContent = publicTelegram
    ? "Anyone can chat with Hermes without a pairing code. Telegram sessions use the safe tool profile; Cloud PC admin and terminal controls remain private."
    : "After the bot sends you a pairing code, enter it here. Unknown users remain denied.";
  if (el.telegramPairRow) el.telegramPairRow.classList.toggle("hidden", publicTelegram);
'''
if 'const publicTelegram = telegram.access_mode === "public-safe"' not in js:
    if old_detail not in js:
        raise SystemExit('Could not locate Telegram status render line')
    js = js.replace(old_detail, new_detail, 1)

# Make the old pairing button harmless if UI is ever absent.
old_click = 'q("#approve-pair").onclick = () => {\n'
new_click = 'if (q("#approve-pair")) q("#approve-pair").onclick = () => {\n'
if old_click in js:
    js = js.replace(old_click, new_click, 1)
app.write_text(js, encoding='utf-8')

sw = Path('site/sw.js')
if sw.exists():
    text = sw.read_text(encoding='utf-8')
    import re
    text = re.sub(r'(CACHE_NAME\s*=\s*["\']).*?(["\'])', r'\1ripo-cloud-public-telegram-v1\2', text, count=1)
    sw.write_text(text, encoding='utf-8')

print('Updated public Telegram dashboard UI.')
