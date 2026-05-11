#!/usr/bin/env python3
"""
test_cowork_parser.py
Test de la funció fetch_cowork_daily_summary() sense dependències externes
"""

import re
import hashlib
from pathlib import Path
import json

def fetch_cowork_daily_summary_test():
    """Versió simplificada per testejar el parsing."""
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
    data_resum = match.group(1) + "-" + match.group(2) + "-" + match.group(3) if match else "2026-05-11"
    
    # Parsejar els titulars de la secció "## Titulares del día"
    entrades = []
    
    # Trobar la secció "## Titulares del día"
    titulars_match = re.search(r"## Titulares del día\n(.*?)(?:\n## |\Z)", contingut, re.DOTALL)
    if not titulars_match:
        print(f"[COWORK] No s'ha trobat la secció 'Titulares del día'.")
        return []
    
    titulars_secció = titulars_match.group(1)
    
    # Extreure cada titular
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
            "data": data_resum,
        }
        entrades.append(entrada)
        print(f"  ✓ [{id_entrada}] {titol[:60]}...")
    
    print(f"[COWORK] {len(entrades)} titulars extrets de {resum_file.name}")
    return entrades


if __name__ == "__main__":
    print("════════════════════════════════════════════════════════════")
    print("  TEST: fetch_cowork_daily_summary()")
    print("════════════════════════════════════════════════════════════\n")
    
    entrades = fetch_cowork_daily_summary_test()
    
    print("\n════════════════════════════════════════════════════════════")
    print(f"RESULTAT: {len(entrades)} titulars parseados")
    print("════════════════════════════════════════════════════════════\n")
    
    if entrades:
        print("📋 Primeres 3 entrades:\n")
        for e in entrades[:3]:
            print(json.dumps(e, indent=2, ensure_ascii=False))
            print()
