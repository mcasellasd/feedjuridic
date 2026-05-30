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
CLAUDE_SCHEDULED_DIR = Path("/Users/marccasellas/Documents/Claude/Scheduled")
CLAUDE_EXTRA_DIRS = [
    Path("/Users/marccasellas/Documents/Claude/Projects/legaltech"),
    Path("/Users/marccasellas/Documents/Claude/Projects/legaltech/resumen-juridico-diario"),
]

# Feeds RSS d'actualitat jurídica
FEEDS_ACTUALITAT = [
    ("Abogacía Española",       "https://www.abogacia.es/feed/"),
    ("Legal Today",             "http://www.legaltoday.com/rss/actualidad/"),
    ("vLex Blog",               "https://spanish.vlexblog.com/feed/"),
    ("Derecho Mercantil",       "https://feeds.feedburner.com/DerechoMercantil"),
    ("Economist & Jurist",      "http://www.economistjurist.es/feed/"),
    ("Mundojuridico",           "https://www.mundojuridico.info/feed/"),
    ("Iustel",                  "https://www.iustel.com/diario_del_derecho/rss.asp"),
    ("Almacén de Derecho",      "http://www.almacendederecho.org/feed/"),
]

# Comarques: mapa de keywords per detectar territori
COMARQUES = {
    "catalunya": [
        "catalunya", "generalitat", "parlament", "govern de la generalitat",
        "diari oficial de la generalitat de catalunya"
    ],
    "girona": [
        "girona", "gironès", "salt", "sarrià de ter", "cassà de la selva",
        "llagostera", "celrà", "diputació de girona", "ajuntament de girona",
        "audiència provincial de girona"
    ],
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

# Paraules clau IA per filtrar el feed
KW_IA = [
    'intel·ligència artificial', 'intel·ligencia artificial',
    r'\bia\b', r'\b(?:a\.i\.|ai)\b',
    'algoritme', 'algorisme', 'algorítmic',
    'automatitzaci', 'automatizaci',
    'machine learning', 'aprenentatge automàtic',
    'model de llenguatge', 'chatgpt', 'openai', 'llm',
    'dades personals', 'rgpd', 'reglament general de protecci',
    'plataforma digital', 'mercat digital', 'dsa', 'dma',
    'ciberseguretat', 'ciberseguridad',
    'regulació tecnol', 'regulacion tecnol',
    'reglament ia', 'eu ai act', 'ai act',
    'decisió automatitzada', 'decision automatizada',
    'blockchain', 'contracte intel·ligent',
    'deepfake', 'biometria'
]

def es_ia(titol: str, resum: str) -> bool:
    """Detecta si l'entrada tracta sobre IA o dret digital."""
    text = f"{titol} {resum}".lower()
    for kw in KW_IA:
        if kw.startswith(r'\b'):
            if re.search(kw, text, re.IGNORECASE):
                return True
        else:
            if kw in text:
                return True
    return False


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
    # Nova API: sumario/YYYYMMDD (sense guions)
    date_nodash = TODAY_STR.replace("-", "")
    url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{date_nodash}"
    headers = {"Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # Nova estructura: diario és una llista de dicts
        entrades = []
        diaris = data.get("data", {}).get("sumario", {}).get("diario", [])
        if isinstance(diaris, dict):
            diaris = [diaris]
        for diari in diaris:
            seccions = diari.get("seccion", [])
            if isinstance(seccions, dict):
                seccions = [seccions]
            for seccio in seccions:
                departaments = seccio.get("departamento", [])
                if isinstance(departaments, dict):
                    departaments = [departaments]
                for dep in departaments:
                    if not isinstance(dep, dict):
                        continue
                    nom_dep = dep.get("nombre", "").lower()
                    if not any(k in nom_dep for k in ["cataluña", "generalitat", "tribunal superior de justicia de catal"]):
                        continue
                    # Nova estructura: items sota "epigrafe" → "item"
                    epigrafes = dep.get("epigrafe", [])
                    if isinstance(epigrafes, dict):
                        epigrafes = [epigrafes]
                    for ep in epigrafes:
                        items = ep.get("item", [])
                        if isinstance(items, dict):
                            items = [items]
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            boe_id = item.get("identificador", "")
                            # URL: preferir html, fallback pdf
                            url_html_obj = item.get("url_html", {})
                            url_html = (url_html_obj.get("texto", "") if isinstance(url_html_obj, dict) else url_html_obj) or ""
                            url_pdf_obj = item.get("url_pdf", {})
                            url_pdf = (url_pdf_obj.get("texto", "") if isinstance(url_pdf_obj, dict) else url_pdf_obj) or ""
                            entrades.append({
                                "id": boe_id,
                                "titol": item.get("titulo", ""),
                                "resum": item.get("titulo", ""),
                                "url": url_html or f"https://www.boe.es/diario_boe/txt.php?id={boe_id}",
                                "url_pdf": url_pdf,
                                "font": "BOE",
                                "tipus": dep.get("nombre", "Disposició estatal"),
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
    trobades = []
    for comarca, keywords in COMARQUES.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', text, re.IGNORECASE):
                trobades.append(comarca)
                break
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
                    or item.findtext("atom:title", namespaces=ns)
                    or ""
                ).strip()
                link = (
                    item.findtext("link")
                    or item.findtext("atom:link", namespaces=ns)
                    or ""
                ).strip()
                if isinstance(link, str) and not link.startswith("http"):
                    el = item.find("atom:link", ns)
                    link = (el.get("href", "") if el is not None else "").strip()
                guid = item.findtext("guid") or link or titol
                desc_raw = (
                    item.findtext("description")
                    or item.findtext("atom:summary", namespaces=ns)
                    or ""
                ).strip()
                pub_str = (
                    item.findtext("pubDate")
                    or item.findtext("atom:updated", namespaces=ns)
                    or item.findtext("atom:published", namespaces=ns)
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


# ── 6. Fetch Cowork Daily Summaries ───────────────────────
def fetch_cowork_daily_summary():
    """
    Llegeix els resums diaris de cowork (daily-summaries/) i extreu els titulars
    com a novetats. Busca el fitxer més recent i parselja la secció "Titulares del día".
    """
    print(f"[COWORK] Processant resums diaris...")
    daily_dir = Path("daily-summaries")
    if not daily_dir.exists():
        print(f"[COWORK] Carpeta daily-summaries no trobada.")
        return []
    
    # Trobar el fitxer de resum més recent
    resums = sorted(daily_dir.glob("resumen-juridico-*.md"), reverse=True)
    if not resums:
        print(f"[COWORK] Cap resum diari trobat.")
        return []
    
    resum_file = resums[0]
    print(f"[COWORK] Llegint: {resum_file.name}")
    
    try:
        with open(resum_file, "r", encoding="utf-8") as f:
            contingut = f.read()
    except Exception as ex:
        print(f"[COWORK] Error llegint {resum_file}: {ex}")
        return []
    
    # Extreure la data del nom del fitxer: resumen-juridico-AAAA-MM-DD.md
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", resum_file.name)
    data_resum = match.group(1) + "-" + match.group(2) + "-" + match.group(3) if match else TODAY_STR
    
    # Parsejar els titulars de la secció "## Titulares del día"
    # Format: números. **Títol** — Descripció [Fuente](URL)
    entrades = []
    
    # Trobar la secció "## Titulares del día"
    titulars_match = re.search(r"## Titulares del día\n(.*?)(?:\n## |\Z)", contingut, re.DOTALL)
    if not titulars_match:
        print(f"[COWORK] No s'ha trobat la secció 'Titulares del día'.")
        return []
    
    titulars_secció = titulars_match.group(1)
    
    # Extreure cada titular: \d+\. \*\*Títol\*\* — Descripció [Fuente](URL)
    titular_pattern = r"^\d+\.\s+\*\*(.+?)\*\*\s+—\s+(.+?)\s+\[Fuente\]\((.+?)\)"
    
    for linea in titulars_secció.split("\n"):
        linea = linea.strip()
        if not linea:
            continue
        
        titular_match = re.match(titular_pattern, linea)
        if not titular_match:
            continue
        
        titol = titular_match.group(1).strip()
        descripcio = titular_match.group(2).strip()
        url = titular_match.group(3).strip()
        
        # Crear un ID únic
        id_entrada = f"COWORK-{hashlib.md5((titol + data_resum).encode()).hexdigest()[:8]}"
        
        entrada = {
            "id": id_entrada,
            "titol": titol,
            "resum": descripcio,
            "url": url,
            "font": "Cowork",
            "tipus": "Cowork",
            "font_web": "Cowork",
            "comarques": [],
        }
        entrades.append(entrada)
    
    print(f"[COWORK] {len(entrades)} titulars extrets de {resum_file.name}")
    return entrades


def fetch_claude_scheduled() -> list:
    """
        Llegeix els fitxers Markdown de Claude Scheduled i extreu titulars juridics
    en dos formats suportats:
            1) "## Titulares del dia" amb linies numerades i [Fuente](URL)
      2) "### n. **...**" amb bullets i [Consultar norma](URL)
    """
    print("[CLAUDE] Processant outputs programats...")
    all_dirs = [CLAUDE_SCHEDULED_DIR] + CLAUDE_EXTRA_DIRS
    existing_dirs = [d for d in all_dirs if d.exists()]
    if not existing_dirs:
        print("[CLAUDE] Cap carpeta de fonts Claude trobada.")
        return []

    md_files = []
    for root_dir in existing_dirs:
        for p in root_dir.rglob("*.md"):
            name = p.name.lower()
            if name in {"skill.md", "claude.md"}:
                continue
            # Evitar markdowns de projecte no periodistics
            if not (name.startswith("resumen-") or name.startswith("resum-")):
                continue
            md_files.append(p)

    md_files = sorted(md_files, reverse=True)
    if not md_files:
        print("[CLAUDE] Cap fitxer markdown trobat.")
        return []

    entrades = []
    used_ids = set()
    used_files = 0

    for md_file in md_files:
        try:
            contingut = md_file.read_text(encoding="utf-8")
        except Exception as ex:
            print(f"[CLAUDE] Error llegint {md_file.name}: {ex}")
            continue

        used_files += 1
        file_date_match = re.search(r"(\d{4}-\d{2}-\d{2})", md_file.name)
        data_fitxer = file_date_match.group(1) if file_date_match else TODAY_STR
        font_web = f"Claude/{md_file.parent.name}"

        # Format 1: 1. **Titol** - Descripcio [Fuente](URL)
        patro_titulars = re.compile(
            r"^\d+\.\s+\*\*(.+?)\*\*\s+[—-]\s+(.+?)\s+\[Fuente\]\((https?://[^)]+)\)",
            re.MULTILINE,
        )
        for m in patro_titulars.finditer(contingut):
            titol = m.group(1).strip()
            resum = m.group(2).strip()
            url = m.group(3).strip()
            id_entrada = f"CLAUDE-{hashlib.md5((titol + url + data_fitxer).encode()).hexdigest()[:10]}"
            if id_entrada in used_ids:
                continue
            used_ids.add(id_entrada)
            entrades.append({
                "id": id_entrada,
                "data": data_fitxer,
                "titol": titol,
                "resum": resum or titol,
                "url": url,
                "font": "Claude-Scheduled",
                "tipus": "Resum jurídic",
                "font_web": font_web,
                "comarques": [],
            })

        # Format 2: ### n. **Titular** ... [Consultar norma](URL)
        patro_blocs = re.compile(
            r"^###\s+\d+\.\s+\*\*(.+?)\*\*(.*?)(?=^###\s+\d+\.\s+\*\*|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        for m in patro_blocs.finditer(contingut):
            titol_heading = m.group(1).strip()
            bloc = m.group(2)
            titol_match = re.search(r"-\s+\*\*T[ií]tol\*\*:\s*(.+)", bloc)
            titol = titol_match.group(1).strip() if titol_match else titol_heading
            resum_match = re.search(r"-\s+\*\*Significaci[oó]\*\*:\s*(.+)", bloc)
            resum = resum_match.group(1).strip() if resum_match else titol_heading
            url_match = re.search(
                r"\[(?:Consultar norma|Enlla[cç]|Fuente)\]\((https?://[^)]+)\)",
                bloc,
                re.IGNORECASE,
            )
            url = url_match.group(1).strip() if url_match else ""
            if not url:
                continue

            id_entrada = f"CLAUDE-{hashlib.md5((titol + url + data_fitxer).encode()).hexdigest()[:10]}"
            if id_entrada in used_ids:
                continue
            used_ids.add(id_entrada)
            entrades.append({
                "id": id_entrada,
                "data": data_fitxer,
                "titol": titol,
                "resum": resum or titol,
                "url": url,
                "font": "Claude-Scheduled",
                "tipus": "Resum DOGC",
                "font_web": font_web,
                "comarques": [],
            })

    print(f"[CLAUDE] {len(entrades)} entrades extretes de {used_files} fitxers.")
    return entrades


# ── 7. Pipeline principal ─────────────────────────────────
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
    entrades_cowork = fetch_cowork_daily_summary()
    entrades_claude = fetch_claude_scheduled()

    # Normalitzar dates de Claude-Scheduled ja guardades, segons la data del fitxer font
    claude_dates = {e["id"]: e.get("data", TODAY_STR) for e in entrades_claude}
    for n in totes_novetats:
        if n.get("font") == "Claude-Scheduled" and n.get("id") in claude_dates:
            n["data"] = claude_dates[n["id"]]

    totes_entrades = entrades_dogc + entrades_boe + entrades_parlament + entrades_email + entrades_actualitat + entrades_eurlex + entrades_comarcals + entrades_cowork + entrades_claude

    noves_per_id = [e for e in totes_entrades if e["id"] not in ids_existents]
    # Deduplicar per títol normalitzat (mateix document pot venir de BOE + DOUE email)
    titols_vistos = set()
    noves = []
    for e in noves_per_id:
        titol_norm = re.sub(r"\s+", " ", e["titol"]).strip().lower()[:120]
        if titol_norm not in titols_vistos:
            titols_vistos.add(titol_norm)
            # Mantenir filtre IA general, pero incloure sempre el material programat de Claude
            if e.get("font") == "Claude-Scheduled" or es_ia(e["titol"], e.get("resum", "")):
                noves.append(e)
    print(f"\n→ {len(noves)} entrades noves (IA + Claude Scheduled) per analitzar.\n")

    novetats_analitzades = []
    for i, entrada in enumerate(noves, 1):
        print(f"[{i}/{len(noves)}] Analitzant: {entrada['titol'][:70]}...")
        analisi = analitzar_amb_ia(entrada)
        if analisi is None:
            continue

        novetat = {
            "id": entrada["id"],
            "data": entrada.get("data", TODAY_STR),
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

    # Estadístiques del dia: usar totes les entrades del dia (no només les noves)
    novetats_avui = [n for n in totes_novetats if n.get("data") == TODAY_STR]

    stats = {
        "data": TODAY_STR,
        "total_avui": len(novetats_avui),
        "dogc": sum(1 for n in novetats_avui if n["font"] in ("DOGC", "DOGC-email")),
        "boe": sum(1 for n in novetats_avui if n["font"] in ("BOE", "BOE-email")),
        "doue": sum(1 for n in novetats_avui if n["font"] == "DOUE-email"),
        "parlament": sum(1 for n in novetats_avui if n["font"] == "Parlament"),
        "email": sum(1 for n in novetats_avui if "email" in n["font"].lower()),
        "actualitat": sum(1 for n in novetats_avui if n["font"] == "Actualitat"),
        "eurlex": sum(1 for n in novetats_avui if n["font"] in ("EUR-Lex", "TJUE")),
        "claude_scheduled": sum(1 for n in novetats_avui if n["font"] == "Claude-Scheduled"),
        "per_categoria": {},
        "urgents": sum(1 for n in novetats_avui if n["urgencia"] == "alta"),
        "resum_diari": "",
    }
    for n in novetats_avui:
        cat = n["categoria"]
        stats["per_categoria"][cat] = stats["per_categoria"].get(cat, 0) + 1

    # ── Generar resum diari narratiu ──────────────────────
    if novetats_avui:
        print("\n[RESUM] Generant resum diari del dia...")
        urgents = [n for n in novetats_avui if n["urgencia"] == "alta"]
        categories_avui = list(stats["per_categoria"].keys())
        titols_resum = "\n".join(
            f"- [{n['categoria'].upper()}] {n['titol']}: {n['resum_executiu'][:120]}"
            for n in novetats_avui[:15]
        )
        prompt_resum = f"""Ets el redactor en cap d'un diari jurídic català especialitzat. 
Avui, {TODAY_STR}, has analitzat {len(novetats_avui)} novetats jurídiques de diverses fonts (DOGC, BOE, Parlament, actualitat jurídica).

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
    else:
        stats["resum_diari"] = (
            f"**Jornada sense novetats IA noves ({TODAY_STR})**\n\n"
            "En la finestra d'actualització d'avui no s'han detectat noves peces jurídiques sobre IA o dret digital "
            "que complissin els criteris del feed. S'ha completat igualment el procés de control diari, "
            "i es reprendrà la vigilància automàtica a la propera execució per incorporar qualsevol novetat rellevant."
        )
        print("[RESUM] Sense novetats avui: resum de continuïtat generat.")

    # ── Generar resums per categoria ──────────────────────
    stats["resums_categoria"] = {}
    if novetats_avui:
        print("\n[RESUM-CAT] Generant resums per categoria...")
        for cat, count in stats["per_categoria"].items():
            if count == 0:
                continue
            articles_cat = [n for n in novetats_avui if n["categoria"] == cat]
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

    # ── Guardar/actualitzar històric de digests ───────────────────────────
    digest_history_file = DATA_DIR / "digest_history.json"
    try:
        if digest_history_file.exists():
            with open(digest_history_file, "r", encoding="utf-8") as f:
                digest_history = json.load(f)
                if not isinstance(digest_history, list):
                    digest_history = []
        else:
            digest_history = []
    except Exception:
        digest_history = []

    resum_plain = re.sub(r"\*\*", "", stats["resum_diari"]).strip()
    titular = resum_plain.split("\n", 1)[0][:220] if resum_plain else f"Resum diari {TODAY_STR}"
    digest_entry = {
        "data": TODAY_STR,
        "total_avui": stats["total_avui"],
        "urgents": stats["urgents"],
        "per_categoria": stats["per_categoria"],
        "resum_diari": stats["resum_diari"],
        "titular": titular,
        "top_peces": [
            {
                "titol": n.get("titol", ""),
                "categoria": n.get("categoria", "administratiu"),
                "font": n.get("font_web") or n.get("font", ""),
                "urgencia": n.get("urgencia", "baixa"),
                "url": n.get("url", ""),
            }
            for n in novetats_avui[:5]
        ],
    }

    digest_history = [d for d in digest_history if d.get("data") != TODAY_STR]
    digest_history.insert(0, digest_entry)
    digest_history.sort(key=lambda d: d.get("data", ""), reverse=True)
    digest_history = digest_history[:180]

    with open(digest_history_file, "w", encoding="utf-8") as f:
        json.dump(digest_history, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"  ✅ Fet! {len(novetats_analitzades)} novetats analitzades i guardades.")
    print(f"     Fitxer: {fitxer_sortida}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
