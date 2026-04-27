"""
DretCat · scraper.py
====================
Script principal d'automatització diària.
Executa cada matí (via GitHub Actions o cron):
  1. Descarrega les entrades del DOGC i BOE del dia
  2. Envia cada entrada a la API de Claude per analitzar-la
  3. Guarda el resultat a data/novetats.json
  4. Actualitza data/estadistiques.json

Requisits:
  pip install requests openai python-dotenv

Variables d'entorn (fitxer .env o GitHub Secrets):
  OPENAI_API_KEY=sk-proj-...
  SOCRATA_APP_TOKEN=xxxx  (opcional però recomanat: 1000 req/h)
"""

import os
import re
import json
import datetime
import hashlib
import requests
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from email.utils import parsedate_to_datetime
from openai import OpenAI
from dotenv import load_dotenv
from email_parser import fetch_from_email

load_dotenv(Path(__file__).parent / ".env", override=True)

# ── Configuració ──────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SOCRATA_APP_TOKEN = os.getenv("SOCRATA_APP_TOKEN", "")
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

client = OpenAI(api_key=OPENAI_API_KEY)

# Categories reconegudes
CATEGORIES = {
    "civil": ["successions", "família", "propietat", "contractes", "obligacions", "drets reals"],
    "penal": ["delicte", "pena", "sanció penal", "presó", "fiscal", "condemna", "absolució"],
    "laboral": ["treball", "acomiadament", "salari", "conveni", "seguretat social", "erto"],
    "administratiu": ["decret", "resolució", "llicència", "urbanisme", "habitatge", "medi ambient"],
    "mercantil": ["societat", "concurs", "fallida", "mercantil", "comerç", "empresa"],
    "fiscal": ["impost", "tribut", "irpf", "iva", "successions", "hisenda"],
    "constitucional": ["constitució", "constitucional", "tribunal constitucional", "drets fonamentals", "garanties", "amparo", "recurs d'inconstitucionalitat", "estatut d'autonomia", "competències"],
}

TODAY = datetime.date.today()
TODAY_STR = TODAY.strftime("%Y-%m-%d")

# Feeds RSS d'actualitat jurídica
FEEDS_ACTUALITAT = [
    ("Abogacía Española",       "https://www.abogacia.es/feed/"),
    ("Legal Today",             "http://www.legaltoday.com/rss/actualidad/"),
    ("vLex Blog",               "https://spanish.vlexblog.com/feed/"),
    ("Derecho Mercantil",       "https://feeds.feedburner.com/DerechoMercantil"),
    ("Economist & Jurist",      "http://www.economistjurist.es/feed/"),
    ("Entre leyes",             "https://www.leyesyjurisprudencia.com/feed/"),
    ("Mundojuridico",           "https://www.mundojuridico.info/feed/"),
    ("Notarios y Registradores","https://www.notariosyregistradores.com/feed/"),
    ("Civil Mercantil",         "https://www.civil-mercantil.com/feed.xml"),
    ("Iustel",                  "https://www.iustel.com/diario_del_derecho/rss.asp"),
    ("Almacén de Derecho",      "http://www.almacendederecho.org/feed/"),
]

# Comarques: mapa de keywords per detectar territori
COMARQUES = {
    "cerdanya": [
        "cerdanya", "puigcerdà", "llívia", "alp", "bellver de cerdanya",
        "das", "ger", "guils de cerdanya", "meranges", "prullans",
        "consell comarcal de la cerdanya", "baixa cerdanya", "alta cerdanya",
    ],
    "berguedà": [
        "berguedà", "berga", "gironella", "puig-reig", "navàs",
        "avià", "casserres", "cercs", "gisclareny", "l'espunyola",
        "olvan", "sant jordi de cercs", "santa maria de marlès",
        "consell comarcal del berguedà", "guardiola de berguedà",
        "la pobla de lillet", "pobla de lillet", "bagà", "castellnou de bages",
        "montmajor", "vallcebre", "saldes", "borredà",
    ],
}

# Feeds RSS comarcals (feed general - la comarca es detecta per keywords)
FEEDS_COMARCALS = [
    ("Regio7",                    "https://www.regio7.cat/rss/"),
    ("NacióDigital",              "https://www.naciodigital.cat/feed"),
    # Aquí Berguedà – feeds per categoria/tag (RSS WordPress vàlid)
    ("AquíBerguedà-Meteorologia", "https://www.aquibergueda.cat/category/meteorologia/feed/"),
    ("AquíBerguedà-Olvan",        "https://www.aquibergueda.cat/tag/ajuntament-dolvan/feed/"),
    ("AquíBerguedà-Guardiola",    "https://www.aquibergueda.cat/category/pobles/alt_bergueda/guardiola/feed/"),
    ("AquíBerguedà-LaPobla",      "https://www.aquibergueda.cat/category/pobles/alt_bergueda/lapobla/feed/"),
]


# ── 1. Obtenir entrades del DOGC ──────────────────────────
def fetch_dogc():
    """Consulta el dataset oficial n6hn-rmy7 via API SODA (analisi.transparenciacatalunya.cat)."""
    print(f"[DOGC] Descarregant entrades de {TODAY_STR}...")
    url = "https://analisi.transparenciacatalunya.cat/resource/n6hn-rmy7.json"
    # SoQL: normes publicades avui, vigents, ordenades per rang
    params = {
        "$where": f"data_de_publicaci_del_diari >= '{TODAY_STR}T00:00:00.000'",
        "$limit": 100,
        "$order": "data_de_publicaci_del_diari DESC",
    }
    headers = {}
    if SOCRATA_APP_TOKEN:
        headers["X-App-Token"] = SOCRATA_APP_TOKEN
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        entrades = resp.json()
        print(f"[DOGC] {len(entrades)} entrades trobades.")
        def _url(camp):
            """Les URLs del dataset Socrata venen com a {'url': '...'} o string."""
            val = camp
            if isinstance(val, dict):
                return val.get("url", "")
            return val or ""

        return [
            {
                "id": e.get("n_mero_de_control", ""),
                "titol": e.get("t_tol_de_la_norma", ""),
                "resum": e.get("t_tol_de_la_norma", ""),
                "url": _url(e.get("format_html", "")),
                "url_pdf": _url(e.get("format_pdf", "")),
                "url_versio_vigent": _url(e.get("url_ltima_versi_format_html", "")),
                "font": "DOGC",
                "tipus": e.get("rang_de_norma", "Disposició"),
                "vigencia": e.get("vig_ncia_de_la_norma", ""),
            }
            for e in entrades
            if e.get("t_tol_de_la_norma")
        ]
    except Exception as ex:
        print(f"[DOGC] Error: {ex}")
        return []


# ── 2. Obtenir entrades del BOE ───────────────────────────
def fetch_boe():
    """Crida l'API del BOE filtrant per Catalunya."""
    print(f"[BOE] Descarregant entrades de {TODAY_STR}...")
    url = f"https://www.boe.es/datosabiertos/api/boe/dias/{TODAY_STR}"
    headers = {"Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # El BOE retorna una estructura anidada per seccions
        entrades = []
        seccions = data.get("data", {}).get("sumario", {}).get("diario", {}).get("seccion", [])
        if isinstance(seccions, dict):
            seccions = [seccions]
        for seccio in seccions:
            departaments = seccio.get("departamento", [])
            if isinstance(departaments, dict):
                departaments = [departaments]
            for dep in departaments:
                # Filtrar per departaments rellevants per Catalunya
                nom_dep = dep.get("@nombre", "").lower()
                if any(k in nom_dep for k in ["cataluña", "generalitat", "tribunal superior de justicia de cataluña"]):
                    items = dep.get("item", [])
                    if isinstance(items, dict):
                        items = [items]
                    for item in items:
                        entrades.append({
                            "id": item.get("@id", ""),
                            "titol": item.get("titulo", ""),
                            "resum": item.get("titulo", ""),
                            "url": f"https://www.boe.es/diario_boe/txt.php?id={item.get('@id','')}",
                            "font": "BOE",
                            "tipus": dep.get("@nombre", "Disposició estatal"),
                        })
        print(f"[BOE] {len(entrades)} entrades catalanes trobades.")
        return entrades
    except Exception as ex:
        print(f"[BOE] Error: {ex}")
        return []


# ── 3. Obtenir entrades del Parlament de Catalunya ─────────
def fetch_parlament():
    """Llegeix els feeds RSS del Parlament: lleis aprovades i decrets llei."""
    feeds = [
        ("https://www.parlament.cat/rss/RSS1_EXP_LLEIS.XML", "Llei"),
        ("https://www.parlament.cat/rss/RSS1_EXP_DECRETS_LLEI.XML", "Decret llei"),
    ]
    entrades = []
    for feed_url, tipus_default in feeds:
        try:
            resp = requests.get(feed_url, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            for item in items:
                titol = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()
                guid = (item.findtext("guid") or link).strip()
                if not titol or not guid:
                    continue
                entrades.append({
                    "id": f"PARLAMENT-{guid}",
                    "titol": titol,
                    "resum": desc or titol,
                    "url": link,
                    "font": "Parlament",
                    "tipus": tipus_default,
                })
        except Exception as ex:
            print(f"[Parlament] Error ({feed_url}): {ex}")
    print(f"[Parlament] {len(entrades)} entrades trobades.")
    return entrades


# ── 4. Obtenir articles d'actualitat jurídica (RSS externs) ──
def fetch_actualitat_juridica(dies_enrere: int = 2):
    """Llegeix els feeds RSS de webs d'actualitat jurídica i retorna els articles recents."""
    cutoff = TODAY - datetime.timedelta(days=dies_enrere)
    entrades = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    for nom_web, feed_url in FEEDS_ACTUALITAT:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            comptador = 0
            for item in items:
                titol = (item.findtext("title") or "").strip()
                link  = (item.findtext("link")  or "").strip()
                guid  = (item.findtext("guid")  or link).strip()
                desc_raw  = (item.findtext("description") or "").strip()
                pub_date_str = (item.findtext("pubDate") or "").strip()

                if not titol or not guid:
                    continue

                # Filtrar per data de publicació
                if pub_date_str:
                    try:
                        pub_date = parsedate_to_datetime(pub_date_str).date()
                    except Exception:
                        try:
                            pub_date = datetime.date.fromisoformat(pub_date_str[:10])
                        except Exception:
                            pub_date = TODAY
                    if pub_date < cutoff:
                        continue

                # Netejar descripció HTML
                desc = re.sub(r"<[^>]+>", " ", desc_raw)
                desc = re.sub(r"\s+", " ", desc).strip()[:600]

                entrades.append({
                    "id": f"ACT-{guid[:200]}",
                    "titol": titol,
                    "resum": desc or titol,
                    "url": link,
                    "font": f"Actualitat",
                    "font_web": nom_web,
                    "tipus": "Article",
                })
                comptador += 1

            print(f"[Actualitat] {nom_web}: {comptador} articles recents.")
        except Exception as ex:
            print(f"[Actualitat] Error ({nom_web}): {ex}")

    print(f"[Actualitat] Total: {len(entrades)} articles d'actualitat.")
    return entrades


# ── 5. Obtenir entrades d'EUR-Lex i TJUE ─────────────────
def detectar_comarques(text: str) -> list:
    """Retorna la llista de comarques detectades al text (en minúscules)."""
    text_lower = text.lower()
    trobades = []
    for comarca, keywords in COMARQUES.items():
        if any(kw in text_lower for kw in keywords):
            trobades.append(comarca)
    return trobades


def fetch_comarques(dies_enrere: int = 3) -> list:
    """Llegeix els RSS de premsa comarcal i retorna entrades recents amb comarca detectada."""
    cutoff = TODAY - datetime.timedelta(days=dies_enrere)
    entrades = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    urls_vistes = set()

    for nom_font, url in FEEDS_COMARCALS:
        try:
            resp = requests.get(url, timeout=12,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            items = root.findall(".//item") or root.findall(".//atom:entry", ns)
            count = 0
            for item in items:
                titol = (
                    item.findtext("title")
                    or item.findtext("atom:title", ns)
                    or ""
                ).strip()
                link = (
                    item.findtext("link")
                    or item.findtext("atom:link", ns)
                    or ""
                ).strip()
                if isinstance(link, str) and not link.startswith("http"):
                    el = item.find("atom:link", ns)
                    link = (el.get("href", "") if el is not None else "").strip()
                guid = item.findtext("guid") or link or titol
                desc_raw = (
                    item.findtext("description")
                    or item.findtext("atom:summary", ns)
                    or ""
                ).strip()
                pub_str = (
                    item.findtext("pubDate")
                    or item.findtext("atom:updated", ns)
                    or item.findtext("atom:published", ns)
                    or ""
                ).strip()

                if not titol or not guid:
                    continue
                if guid in urls_vistes:
                    continue

                if pub_str:
                    try:
                        pub_date = parsedate_to_datetime(pub_str).date()
                    except Exception:
                        try:
                            pub_date = datetime.date.fromisoformat(pub_str[:10])
                        except Exception:
                            pub_date = TODAY
                    if pub_date < cutoff:
                        continue

                desc = re.sub(r"<[^>]+>", " ", desc_raw)
                desc = re.sub(r"\s+", " ", desc).strip()[:500]
                text_check = (titol + " " + desc).lower()

                # Detectar comarca per keywords; si no, usar defecte de la font
                FONT_COMARCA_DEFAULT = {
                    "AquíBerguedà-Meteorologia": "berguedà",
                    "AquíBerguedà-Olvan":        "berguedà",
                    "AquíBerguedà-Guardiola":    "berguedà",
                    "AquíBerguedà-LaPobla":      "berguedà",
                }
                comarques_art = detectar_comarques(text_check)
                if not comarques_art and nom_font in FONT_COMARCA_DEFAULT:
                    comarques_art = [FONT_COMARCA_DEFAULT[nom_font]]
                if not comarques_art:
                    continue

                # Filtrar: només notícies amb contingut jurídic/legal rellevant
                paraules_legals = [
                    "llei", "decret", "resolució", "ordenança", "reglament",
                    "ajuntament", "consell comarcal", "tribunal", "judici",
                    "sentència", "multa", "sanci", "urbanisme", "habitatge",
                    "licitaci", "contracte", "subvenci", "pressupost",
                ]
                if not any(p in text_check for p in paraules_legals):
                    continue

                urls_vistes.add(guid)
                entrades.append({
                    "id": f"COM-{hashlib.md5(guid.encode()).hexdigest()[:12]}",
                    "titol": titol,
                    "resum": desc or titol,
                    "url": link,
                    "font": nom_font,
                    "tipus": "Premsa comarcal",
                    "comarques": comarques_art,
                })
                count += 1
            print(f"[COMARCA] {nom_font}: {count} articles rellevants.")
        except Exception as ex:
            print(f"[COMARCA] Error ({nom_font}): {ex}")

    return entrades


def fetch_eurlex(dies_enrere: int = 2):
    """
    Llegeix els RSS d'EUR-Lex (DO L – legislació) i del TJUE (sentències)
    i retorna les entrades dels últims `dies_enrere` dies.
    """
    feeds = [
        ("https://eur-lex.europa.eu/rss/rss_10.xml",         "EUR-Lex", "Legislació UE (DO L)"),
        ("https://curia.europa.eu/jcms/jcms/rss_1023559/en/", "TJUE",    "Sentència TJUE"),
    ]
    cutoff = TODAY - datetime.timedelta(days=dies_enrere)
    entrades = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DretCat/1.0)"}

    for feed_url, font, tipus_default in feeds:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)
            comptador = 0
            for item in items:
                titol = (
                    item.findtext("title")
                    or item.findtext("atom:title", namespaces=ns)
                    or ""
                ).strip()
                link_el = item.find("atom:link", ns)
                link = (
                    item.findtext("link")
                    or (link_el.get("href") if link_el is not None else "")
                    or ""
                ).strip()
                guid = (
                    item.findtext("guid")
                    or item.findtext("atom:id", namespaces=ns)
                    or link
                ).strip()
                desc_raw = (
                    item.findtext("description")
                    or item.findtext("atom:summary", namespaces=ns)
                    or ""
                ).strip()
                pub_date_str = (
                    item.findtext("pubDate")
                    or item.findtext("atom:updated", namespaces=ns)
                    or item.findtext("atom:published", namespaces=ns)
                    or ""
                ).strip()

                if not titol or not guid:
                    continue

                if pub_date_str:
                    try:
                        pub_date = parsedate_to_datetime(pub_date_str).date()
                    except Exception:
                        try:
                            pub_date = datetime.date.fromisoformat(pub_date_str[:10])
                        except Exception:
                            pub_date = TODAY
                    if pub_date < cutoff:
                        continue

                desc = re.sub(r"<[^>]+>", " ", desc_raw)
                desc = re.sub(r"\s+", " ", desc).strip()[:600]

                entrades.append({
                    "id": f"EURLEX-{hashlib.md5(guid.encode()).hexdigest()[:12]}",
                    "titol": titol,
                    "resum": desc or titol,
                    "url": link,
                    "font": font,
                    "tipus": tipus_default,
                })
                comptador += 1

            print(f"[EUR-Lex] {font}: {comptador} entrades recents.")
        except Exception as ex:
            print(f"[EUR-Lex] Error ({font}): {ex}")

    return entrades


# ── 6. Rascat de contingut real ───────────────────────────
def fetch_contingut(entrada: dict, max_chars: int = 6000) -> str:
    """
    Descarrega el text complet del document jurídic.
    - BOE: usa l'API XML oficial
    - DOGC: usa l'API de Transparència (text via camp resum/descriptors)
    - Altres: rascat HTML genèric
    Retorna el text net (màxim max_chars caràcters).
    """
    font = entrada.get("font", "")
    url = entrada.get("url", "")
    resum_base = entrada.get("resum", entrada.get("titol", ""))

    if not url:
        return resum_base

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

    try:
        # ─ BOE / DOUE: usar l'API XML oficial (és text pla i net)
        boe_id_match = re.search(r"id=(BOE-[A-Z]-\d{4}-\d+|DOUE-[A-Z]-\d{4}-\d+)", url)
        if boe_id_match:
            boe_id = boe_id_match.group(1)
            xml_url = f"https://www.boe.es/diario_boe/xml.php?id={boe_id}"
            resp = requests.get(xml_url, headers=headers, timeout=15)
            resp.raise_for_status()
            raw = re.sub(r"<\?xml[^>]+>", "", resp.text)
            text = re.sub(r"<[^>]+>", " ", raw)
            text = re.sub(r"&[a-z]+;", " ", text)
            text = re.sub(r"[ \t]+", " ", text)
            return re.sub(r"\n{3,}", "\n\n", text).strip()[:max_chars]

        # ─ DOGC: el portal és Angular (SPA), no rasca bé.
        # Usem el camp 'resum' del DOGC que ja ve de l'API de Transparència
        # com a base, i intentem el portaljuridic com a fallback simple
        if "dogc" in font.lower() or "portaldogc" in url or "portaljuridic" in url:
            # Intenta llegir el text del document ELI com a text pla
            eli_txt = url.replace("/html", "/html").replace("/cat/html", "/cat")
            # Alguns formen la URL text: canviem html per res
            for try_url in [url, url.replace("/html", ""), url.rstrip("/") + "/text"]:
                try:
                    r = requests.get(try_url, headers=headers, timeout=10)
                    if r.ok and "html" in r.headers.get("Content-Type", ""):
                        raw = r.text
                        raw = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
                        text = re.sub(r"<[^>]+>", " ", raw)
                        text = re.sub(r"&[a-z]+;", " ", text)
                        text = re.sub(r"[ \t]+", " ", text)
                        text = re.sub(r"\n{3,}", "\n\n", text).strip()
                        # Si hi ha poc contingut (és SPA buit) retorna el resum
                        if len(text) > 500:
                            return text[:max_chars]
                except Exception:
                    pass
            return resum_base

        # ─ Genèric: rascat HTML
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        raw = resp.text
        raw = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"&[a-z]+;", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()[:max_chars]

    except Exception as ex:
        print(f"   [contingut] No s'ha pogut descarregar: {ex}")
        return resum_base


# ── 5. Analitzar amb OpenAI ──────────────────────────────
def analitzar_amb_ia(entrada: dict) -> dict:
    """Envia una entrada al DOGC/BOE a OpenAI i obté un anàlisi estructurat."""
    text_complet = fetch_contingut(entrada)
    text_label = "Text complet" if len(text_complet) > len(entrada.get("resum", "")) + 50 else "Resum"

    prompt = f"""Ets un expert en dret català. Analitza la següent disposició jurídica i retorna ÚNICAMENT un JSON vàlid, sense cap text addicional, amb exactament aquesta estructura:

{{
  "resum_executiu": "2-3 frases clares per a un jurista català",
  "impacte_practic": "1-2 frases sobre l'efecte real en ciutadans o empreses",
  "categoria": "una de: civil, penal, laboral, administratiu, mercantil, fiscal, constitucional",
  "paraules_clau": ["max 4 paraules clau"],
  "urgencia": "alta | mitjana | baixa"
}}

Disposició a analitzar:
Títol: {entrada['titol']}
Font: {entrada['font']}
Tipus: {entrada['tipus']}
{text_label}: {text_complet}
"""

    try:
        resposta = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=600,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        text = resposta.choices[0].message.content.strip()
        analisi = json.loads(text)
        return analisi
    except json.JSONDecodeError as ex:
        print(f"  [IA] Error de JSON: {ex}")
        return {
            "resum_executiu": entrada["resum"][:300],
            "impacte_practic": "Pendent d'anàlisi.",
            "categoria": "administratiu",
            "paraules_clau": [],
            "urgencia": "baixa",
        }
    except Exception as ex:
        print(f"  [IA] Error: {ex}")
        return None


# ── 4. Pipeline principal ─────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"  DretCat · Actualització {TODAY_STR}")
    print(f"{'='*50}\n")

    # Carregar novetats anteriors (per no duplicar)
    fitxer_sortida = DATA_DIR / "novetats.json"
    if fitxer_sortida.exists():
        with open(fitxer_sortida) as f:
            totes_novetats = json.load(f)
    else:
        totes_novetats = []

    ids_existents = {n["id"] for n in totes_novetats}

    # Obtenir entrades noves
    entrades_dogc = fetch_dogc()
    entrades_boe = fetch_boe()
    entrades_parlament = fetch_parlament()
    entrades_email = fetch_from_email(dies_enrere=1)
    entrades_actualitat = fetch_actualitat_juridica(dies_enrere=2)
    entrades_eurlex = fetch_eurlex(dies_enrere=2)
    entrades_comarcals = fetch_comarques(dies_enrere=3)
    totes_entrades = entrades_dogc + entrades_boe + entrades_parlament + entrades_email + entrades_actualitat + entrades_eurlex + entrades_comarcals

    noves_per_id = [e for e in totes_entrades if e["id"] not in ids_existents]
    # Deduplicar per títol normalitzat (mateix document pot venir de BOE + DOUE email)
    titols_vistos = set()
    noves = []
    for e in noves_per_id:
        titol_norm = re.sub(r"\s+", " ", e["titol"]).strip().lower()[:120]
        if titol_norm not in titols_vistos:
            titols_vistos.add(titol_norm)
            noves.append(e)
    print(f"\n→ {len(noves)} entrades noves per analitzar.\n")

    novetats_analitzades = []
    for i, entrada in enumerate(noves, 1):
        print(f"[{i}/{len(noves)}] Analitzant: {entrada['titol'][:70]}...")
        analisi = analitzar_amb_ia(entrada)
        if analisi is None:
            continue

        novetat = {
            "id": entrada["id"],
            "data": TODAY_STR,
            "titol": entrada["titol"],
            "font": entrada["font"],
            "tipus": entrada["tipus"],
            "url": entrada["url"],
            "resum_executiu": analisi.get("resum_executiu", ""),
            "impacte_practic": analisi.get("impacte_practic", ""),
            "categoria": analisi.get("categoria", "administratiu").strip().lower().replace("mercanitl", "mercantil").replace("adminsitrau", "administratiu").replace("constitutional", "constitucional").replace("constitucional ", "constitucional"),
            "paraules_clau": analisi.get("paraules_clau", []),
            "urgencia": analisi.get("urgencia", "baixa"),
            "font_web": entrada.get("font_web", ""),
            "comarques": entrada.get("comarques") or detectar_comarques(
                entrada["titol"] + " " + entrada.get("resum", "") + " " + analisi.get("resum_executiu", "")
            ),
        }
        novetats_analitzades.append(novetat)
        print(f"   ✓ Categoria: {novetat['categoria']} | Urgència: {novetat['urgencia']}")

        # Pausa per respectar límits de la API
        time.sleep(1.5)

    # Afegir les noves a l'historial i guardar (les més recents primer)
    totes_novetats = novetats_analitzades + totes_novetats
    with open(fitxer_sortida, "w", encoding="utf-8") as f:
        json.dump(totes_novetats, f, ensure_ascii=False, indent=2)

    # Guardar estadístiques del dia
    stats = {
        "data": TODAY_STR,
        "total_avui": len(novetats_analitzades),
        "dogc": sum(1 for n in novetats_analitzades if n["font"] in ("DOGC", "DOGC-email")),
        "boe": sum(1 for n in novetats_analitzades if n["font"] in ("BOE", "BOE-email")),
        "doue": sum(1 for n in novetats_analitzades if n["font"] == "DOUE-email"),
        "parlament": sum(1 for n in novetats_analitzades if n["font"] == "Parlament"),
        "email": sum(1 for n in novetats_analitzades if "email" in n["font"].lower()),
        "actualitat": sum(1 for n in novetats_analitzades if n["font"] == "Actualitat"),
        "eurlex": sum(1 for n in novetats_analitzades if n["font"] in ("EUR-Lex", "TJUE")),
        "per_categoria": {},
        "urgents": sum(1 for n in novetats_analitzades if n["urgencia"] == "alta"),
        "resum_diari": "",
    }
    for n in novetats_analitzades:
        cat = n["categoria"]
        stats["per_categoria"][cat] = stats["per_categoria"].get(cat, 0) + 1

    # ── Generar resum diari narratiu ──────────────────────
    if novetats_analitzades:
        print("\n[RESUM] Generant resum diari del dia...")
        urgents = [n for n in novetats_analitzades if n["urgencia"] == "alta"]
        categories_avui = list(stats["per_categoria"].keys())
        titols_resum = "\n".join(
            f"- [{n['categoria'].upper()}] {n['titol']}: {n['resum_executiu'][:120]}"
            for n in novetats_analitzades[:15]
        )
        prompt_resum = f"""Ets el redactor en cap d'un diari jurídic català especialitzat. 
Avui, {TODAY_STR}, has analitzat {len(novetats_analitzades)} novetats jurídiques de diverses fonts (DOGC, BOE, Parlament, actualitat jurídica).

Aquí tens les principals novetats d'avui:
{titols_resum}

Redacta un resum diari en català de 3-5 paràgrafs, amb to professional però accessible, dirigit a juristes catalans. 
Ha d'explicar les tendències del dia, destacar les novetats més importants (especialment les d'urgència alta: {len(urgents)} avui), 
i fer referència a les àrees del dret afectades: {', '.join(categories_avui)}.
Estructura: 1) Titular introductori breu en negreta, 2) cos narratiu. 
No facis llistes de punts. Escriu en català estàndard formal."""

        try:
            resp_resum = client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt_resum}]
            )
            stats["resum_diari"] = resp_resum.choices[0].message.content.strip()
            print("[RESUM] ✓ Resum diari generat.")
        except Exception as ex:
            print(f"[RESUM] Error: {ex}")
            stats["resum_diari"] = ""

    # ── Generar resums per categoria ──────────────────────
    stats["resums_categoria"] = {}
    if novetats_analitzades:
        print("\n[RESUM-CAT] Generant resums per categoria...")
        for cat, count in stats["per_categoria"].items():
            if count == 0:
                continue
            articles_cat = [n for n in novetats_analitzades if n["categoria"] == cat]
            titols_cat = "\n".join(
                f"- {n['titol']}: {n['resum_executiu'][:120]}"
                for n in articles_cat[:6]
            )
            prompt_cat = f"""Ets un expert en dret català. Resumeix en 1-2 paràgrafs breus (màx 150 paraules) \
les novetats del dia {TODAY_STR} en matèria de dret {cat}, dirigit a juristes catalans.
Novetats:
{titols_cat}

Escriu en català formal. Sense llistes de punts. Si hi ha una novetat especialment important, destaca-la al primer paràgraf."""
            try:
                resp_cat = client.chat.completions.create(
                    model="gpt-4o-mini",
                    max_tokens=250,
                    messages=[{"role": "user", "content": prompt_cat}]
                )
                stats["resums_categoria"][cat] = resp_cat.choices[0].message.content.strip()
                print(f"[RESUM-CAT] ✓ {cat} ({count} articles)")
            except Exception as ex:
                print(f"[RESUM-CAT] Error {cat}: {ex}")

    with open(DATA_DIR / "estadistiques.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"  ✅ Fet! {len(novetats_analitzades)} novetats analitzades i guardades.")
    print(f"     Fitxer: {fitxer_sortida}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
