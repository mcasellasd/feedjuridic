import os, json, datetime, requests, time
import xml.etree.ElementTree as ET
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

DIES_ENRERE = 7
data_inici = (datetime.date.today() - datetime.timedelta(days=DIES_ENRERE)).strftime("%Y-%m-%d")
print(f"Recuperant des de {data_inici}...\n")

# ── DOGC via SODA ──
url = "https://analisi.transparenciacatalunya.cat/resource/n6hn-rmy7.json"
params = {
    "$where": f"data_de_publicaci_del_diari >= '{data_inici}T00:00:00.000'",
    "$limit": 200,
    "$order": "data_de_publicaci_del_diari DESC",
}
resp = requests.get(url, params=params, timeout=15)
entrades_raw = resp.json()
print(f"[DOGC] {len(entrades_raw)} entrades trobades.")

def _url(camp):
    if isinstance(camp, dict): return camp.get("url", "")
    return camp or ""

entrades_dogc = [
    {
        "id": e.get("n_mero_de_control", ""),
        "data": e.get("data_de_publicaci_del_diari", "")[:10],
        "titol": e.get("t_tol_de_la_norma", ""),
        "resum": e.get("t_tol_de_la_norma", ""),
        "url": _url(e.get("format_html", "")),
        "url_pdf": _url(e.get("format_pdf", "")),
        "url_versio_vigent": _url(e.get("url_ltima_versi_format_html", "")),
        "font": "DOGC",
        "tipus": e.get("rang_de_norma", "Disposicio"),
        "vigencia": e.get("vig_ncia_de_la_norma", ""),
    }
    for e in entrades_raw if e.get("t_tol_de_la_norma")
]

# ── BOE: loop per dia ──
entrades_boe = []
for i in range(1, DIES_ENRERE + 1):
    dia = (datetime.date.today() - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"https://www.boe.es/datosabiertos/api/boe/dias/{dia}",
            headers={"Accept": "application/json"}, timeout=10
        )
        if r.status_code != 200:
            print(f"  [BOE {dia}] HTTP {r.status_code}")
            continue
        data = r.json()
        seccions = data.get("data", {}).get("sumario", {}).get("diario", {}).get("seccion", [])
        if isinstance(seccions, dict): seccions = [seccions]
        for seccio in seccions:
            deps = seccio.get("departamento", [])
            if isinstance(deps, dict): deps = [deps]
            for dep in deps:
                nom = dep.get("@nombre", "").lower()
                if any(k in nom for k in [
                    "cataluna", "generalitat",
                    "tribunal superior de justicia de cataluna",
                    "ministerio de justicia", "ministerio de trabajo",
                    "tribunal constitucional", "tribunal supremo"
                ]):
                    items = dep.get("item", [])
                    if isinstance(items, dict): items = [items]
                    for item in items:
                        entrades_boe.append({
                            "id": item.get("@id", ""),
                            "data": dia,
                            "titol": item.get("titulo", ""),
                            "resum": item.get("titulo", ""),
                            "url": f"https://www.boe.es/diario_boe/txt.php?id={item.get('@id','')}",
                            "font": "BOE",
                            "tipus": dep.get("@nombre", "Disposicio estatal"),
                        })
    except Exception as ex:
        print(f"  [BOE {dia}] {ex}")

print(f"[BOE] {len(entrades_boe)} entrades trobades.")

# ── Parlament de Catalunya: lleis i decrets llei ──
entrades_parlament = []
for feed_url, tipus_default in [
    ("https://www.parlament.cat/rss/RSS1_EXP_LLEIS.XML", "Llei"),
    ("https://www.parlament.cat/rss/RSS1_EXP_DECRETS_LLEI.XML", "Decret llei"),
]:
    try:
        r = requests.get(feed_url, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for item in root.findall(".//item"):
            titol = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            guid = (item.findtext("guid") or link).strip()
            if not titol or not guid:
                continue
            entrades_parlament.append({
                "id": f"PARLAMENT-{guid}",
                "data": datetime.date.today().strftime("%Y-%m-%d"),
                "titol": titol,
                "resum": desc or titol,
                "url": link,
                "font": "Parlament",
                "tipus": tipus_default,
            })
    except Exception as ex:
        print(f"  [Parlament] Error ({feed_url}): {ex}")
print(f"[Parlament] {len(entrades_parlament)} entrades trobades.")

# ── Analitzar amb Claude ──
fitxer = DATA_DIR / "novetats.json"
existents = json.load(open(fitxer)) if fitxer.exists() and fitxer.stat().st_size > 2 else []
ids_existents = {n["id"] for n in existents}
totes = entrades_dogc + entrades_boe + entrades_parlament
noves = [e for e in totes if e["id"] and e["id"] not in ids_existents]
print(f"\n-> {len(noves)} entrades noves per analitzar.\n")

PROMPT_TPL = (
    "Ets un expert en dret catala. Analitza la disposicio i retorna UNICAMENT un JSON valid:\n\n"
    '{{\n'
    '  "resum_executiu": "2-3 frases per a un jurista catala",\n'
    '  "impacte_practic": "1-2 frases sobre l\'efecte real",\n'
    '  "categoria": "una de: civil, penal, laboral, administratiu, mercantil, fiscal",\n'
    '  "paraules_clau": ["max 4 paraules clau"],\n'
    '  "urgencia": "alta | mitjana | baixa"\n'
    '}}\n\n'
    "Titol: {titol}\nFont: {font}\nTipus: {tipus}"
)

analitzades = []
for i, e in enumerate(noves, 1):
    print(f"[{i}/{len(noves)}] {e['titol'][:80]}...")
    try:
        msg = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=600,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": PROMPT_TPL.format(
                titol=e["titol"], font=e["font"], tipus=e["tipus"]
            )}]
        )
        text = msg.choices[0].message.content.strip()
        analisi = json.loads(text)
    except Exception as ex:
        print(f"  Error: {ex}")
        analisi = {
            "resum_executiu": e["resum"][:300],
            "impacte_practic": "",
            "categoria": "administratiu",
            "paraules_clau": [],
            "urgencia": "baixa"
        }

    analitzades.append({**e, **{
        "resum_executiu": analisi.get("resum_executiu", ""),
        "impacte_practic": analisi.get("impacte_practic", ""),
        "categoria": analisi.get("categoria", "administratiu"),
        "paraules_clau": analisi.get("paraules_clau", []),
        "urgencia": analisi.get("urgencia", "baixa"),
    }})
    print(f"   OK {analitzades[-1]['categoria']} | {analitzades[-1]['urgencia']}")
    time.sleep(0.5)

resultat = analitzades + existents
with open(fitxer, "w", encoding="utf-8") as f:
    json.dump(resultat, f, ensure_ascii=False, indent=2)

print(f"\nFet! {len(analitzades)} novetats guardades a {fitxer}")
