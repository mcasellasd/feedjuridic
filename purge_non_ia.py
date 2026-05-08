import json

from scraper import es_ia

with open('data/novetats.json', 'r', encoding='utf-8') as f:
    totes = json.load(f)

noves = []
for n in totes:
    if es_ia(n.get('titol', ''), n.get('resum_executiu', '')):
        noves.append(n)

with open('data/novetats.json', 'w', encoding='utf-8') as f:
    json.dump(noves, f, ensure_ascii=False, indent=2)

print(f"Purge fet. De {len(totes)} a {len(noves)}.")
