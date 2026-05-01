"""
DretCat · email_parser.py
=========================
Llegeix la bústia IMAP de feed@weedisplay.com, detecta correus del DOGC
(i futurs BOPs), extreu els títols/URLs i els retorna com a entrades
en el format estàndard de novetats.json.

Ús independent:
  python3 email_parser.py

O importat des de scraper.py:
  from email_parser import fetch_from_email
"""

import imaplib
import email
import email.header
import re
import os
import datetime
import hashlib
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", 993))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASS = os.getenv("IMAP_PASS", "")

# Remitents coneguts de butlletins normatius
SENDERS_DOGC = [
    "dogc@gencat.cat",
    "butlleti@dogc.cat",
    "no-reply@dogc.gencat.cat",
    "m.casellas.deig@gencat.cat",
    "m.casellas.deig@gmail.com",
]
SENDERS_BOE = ["no-responder@boe.es"]
# Ampliar quan tinguem subscripció a BOPs


def _decode_header(value: str) -> str:
    """Decodifica capçaleres MIME (utf-8, latin-1, etc.)."""
    parts = email.header.decode_header(value)
    decoded = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            decoded.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(chunk)
    return " ".join(decoded)


def _extract_body(msg) -> str:
    """Extreu el text pla d'un missatge MIME."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                body += payload.decode(charset, errors="replace")
                break
            elif ctype == "text/html" and not body:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                # Eliminem tags HTML bàsics per quedar-nos amb text
                raw = payload.decode(charset, errors="replace")
                body += re.sub(r"<[^>]+>", " ", raw)
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        body = payload.decode(charset, errors="replace") if payload else ""
    return body


def _parse_dogc_email(subject: str, body: str, date_str: str) -> list[dict]:
    """
    Extreu entrades d'un correu d'alerta del DOGC.
    El DOGC envia un correu amb línies del tipus:
      Títol de la norma
      https://portaldogc.gencat.cat/...
    """
    resum = re.sub(r"\s+", " ", body).strip()[:1200]
    titol = re.sub(r"^\s*Fwd:\s*", "", subject, flags=re.IGNORECASE).strip()

    urls = re.findall(
        r"https?://\S*(?:portaldogc|dogc|portaljuridic)\.gencat\.cat\S*",
        body,
    )
    # Evitem links de gestió/subscripció que generen soroll.
    urls_utils = [
        u for u in urls
        if "gestio-del-serveis-subscrits" not in u
        and "unsubscribe" not in u.lower()
    ]
    url = urls_utils[0] if urls_utils else ""

    base_id = url or (titol + date_str)
    entry_id = "EMAIL-DOGC-" + hashlib.md5(base_id.encode()).hexdigest()[:10]
    return [{
        "id": entry_id,
        "data": date_str,
        "titol": titol or subject,
        "resum": resum,
        "font": "DOGC-email",
        "tipus": "alerta",
        "url": url,
    }]


def _parse_boe_email(subject: str, body: str, date_str: str) -> list[dict]:
    """
    Extreu entrades d'un correu d'alerta de Mi BOE (no-responder@boe.es).
    Format: blocs amb Títol / Departamento / Ver documento: URL
    """
    entrades = []
    # Determina la font (BOE o DOUE)
    font = "DOUE-email" if subject.startswith("DOUE:") else "BOE-email"

    # Extreu blocs: Título + URL
    titols = re.findall(r"Título:\s*\n-\s*(.+?)(?=\nDepartamento:|\n={10})", body, re.DOTALL)
    urls = re.findall(r"Ver documento:\s*\n-\s*(https?://\S+)", body)
    departaments = re.findall(r"Departamento:\s*\n-\s*(.+?)\n", body)

    for i, titol in enumerate(titols):
        titol_net = re.sub(r"\s+", " ", titol).strip()
        url = urls[i] if i < len(urls) else ""
        dep = departaments[i].strip() if i < len(departaments) else ""
        entry_id = font + "-" + hashlib.md5(url.encode() if url else titol_net.encode()).hexdigest()[:10]
        entrades.append({
            "id": entry_id,
            "data": date_str,
            "titol": titol_net,
            "resum": titol_net,
            "font": font,
            "tipus": dep or "Disposició",
            "url": url,
        })

    if not entrades:
        entry_id = font + "-" + hashlib.md5((subject + date_str).encode()).hexdigest()[:10]
        entrades.append({
            "id": entry_id,
            "data": date_str,
            "titol": subject,
            "resum": subject,
            "font": font,
            "tipus": "alerta",
            "url": "",
        })

    return entrades


def fetch_from_email(dies_enrere: int = 1) -> list[dict]:
    """
    Connecta a la bústia IMAP, llegeix els correus dels últims `dies_enrere` dies
    de remitents normatius i retorna una llista d'entrades en format estàndard.
    """
    if not IMAP_USER or not IMAP_PASS:
        print("[EMAIL] Variables IMAP_USER/IMAP_PASS no configurades. Saltant.")
        return []

    entrades = []
    since_date = (datetime.date.today() - datetime.timedelta(days=dies_enrere)).strftime("%d-%b-%Y")

    print(f"[EMAIL] Connectant a {IMAP_HOST} com {IMAP_USER}...")

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASS)
        mail.select("INBOX")

        # Cerca correus de remitents coneguts dels últims N dies
        all_senders = [(s, "dogc") for s in SENDERS_DOGC] + [(s, "boe") for s in SENDERS_BOE]
        ids_vistos = set()

        for sender, tipus in all_senders:
            _, data = mail.search(None, f'(FROM "{sender}" SINCE {since_date})')
            msg_ids = data[0].split() if data[0] else []
            for msg_id in msg_ids:
                if msg_id in ids_vistos:
                    continue
                ids_vistos.add(msg_id)
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                subject = _decode_header(msg.get("Subject", "(sense assumpte)"))
                date_header = msg.get("Date", "")
                # Convertim la data del correu a YYYY-MM-DD
                try:
                    parsed_date = email.utils.parsedate_to_datetime(date_header)
                    date_str = parsed_date.strftime("%Y-%m-%d")
                except Exception:
                    date_str = datetime.date.today().strftime("%Y-%m-%d")

                body = _extract_body(msg)
                if tipus == "boe":
                    noves = _parse_boe_email(subject, body, date_str)
                else:
                    noves = _parse_dogc_email(subject, body, date_str)
                entrades.extend(noves)
                print(f"[EMAIL]   {sender}: {len(noves)} entrada(es) de '{subject[:50]}'")

        mail.logout()
        print(f"[EMAIL] Total entrades des de correu: {len(entrades)}")

    except imaplib.IMAP4.error as e:
        print(f"[EMAIL] Error IMAP: {e}")
        print("[EMAIL] NOTA: Si fas servir Gmail, necessites una 'Contrasenya d'aplicació'")
        print("  → myaccount.google.com/apppasswords (requereix 2FA activat)")

    return entrades


if __name__ == "__main__":
    resultats = fetch_from_email(dies_enrere=7)
    print(f"\nEntrades trobades: {len(resultats)}")
    for r in resultats:
        print(f"  [{r['data']}] {r['titol'][:70]}")
