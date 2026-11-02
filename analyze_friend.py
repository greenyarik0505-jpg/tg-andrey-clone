import sys
import re
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')

with open('SelfMessages_6412968509_20260528_1623.html', 'r', encoding='utf-8', errors='ignore') as f:
    html = f.read()

texts = re.findall(r'<td class="c-text">(.*?)</td>', html)
print(f'Total messages extracted: {len(texts)}')

# Clean html entities
clean_texts = []
for t in texts:
    t = t.replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    t = t.strip()
    if t:
        clean_texts.append(t)

print(f'Clean messages: {len(clean_texts)}')

lengths = [len(t.split()) for t in clean_texts]
avg_len = sum(lengths) / len(lengths) if lengths else 0
print(f'Average words: {avg_len:.2f}')

# Word frequencies
words = []
for t in clean_texts:
    for w in re.findall(r'[a-zA-Zа-яА-ЯёЁіІїЇєЄґҐ]+', t.lower()):
        if len(w) > 2:
            words.append(w)

c = Counter(words)
print('\nTop 40 words:')
for w, cnt in c.most_common(40):
    print(f'{w}: {cnt}')

print('\nSample 30 messages:')
for t in clean_texts[200:230]:
    print('>', t)
