# COWORK DAILY SUMMARIES · Rutina automàtica

## Descripció

El scraper.py ara integra automàticament els resums diaris que genera cowork. Cada dia:

1. **Busca el resum més recent** a `daily-summaries/`
2. **Parselja els titulars principals** de la secció "Titulares del día"
3. **Extreu títol, descripció i font** de cada titular
4. **Els afegeix a `novetats.json`** com a novetats de font "Cowork"
5. **Els processa amb IA** per extreure: resum executiu, impacte pràctic, categoria, paraules clau, urgència

## Funcionament

### Paràmetres detectats automàticament

| Dada | Origen |
|------|--------|
| **Títol** | Entre `**` `**` del titular |
| **Descripció** | Text fins a `[Fuente](URL)` |
| **URL** | Entre parèntesis de `[Fuente](URL)` |
| **Font** | "Cowork" |
| **Tipus** | "Cowork" |
| **Data** | Del nom del fitxer (resumen-juridico-**AAAA-MM-DD**.md) |
| **ID** | Hash MD5 de (titol + data) per evitar duplicats |

### Exemple de processament

**Input (resum MD):**
```markdown
## Titulares del día

1. **AI Omnibus: UE simplifica AI Act** — El 7 de mayo, Parlamento y Consejo cerraron acuerdo...
   [Fuente](https://www.consilium.europa.eu/...)
```

**Output (novetats.json):**
```json
{
  "id": "COWORK-a1b2c3d4",
  "data": "2026-05-11",
  "titol": "AI Omnibus: UE simplifica AI Act",
  "font": "Cowork",
  "tipus": "Cowork",
  "url": "https://www.consilium.europa.eu/...",
  "resum_executiu": "[generat per IA]",
  "impacte_practic": "[generat per IA]",
  "categoria": "administratiu",
  "paraules_clau": [...],
  "urgencia": "alta"
}
```

## Configuració de l'escalonament automàtic

### 🍎 macOS (RECOMANAT)

Usa launchd per a escalonament nàtiu:

```bash
cd feedjuridic
./setup_daily_task.sh
# Trià opció 2 (launchd)
```

Això instal·la un agent que s'executa cada dia a les 09:00 (hora local).

**Per verificar:**
```bash
launchctl list | grep feedjuridic
```

**Per desactivar:**
```bash
launchctl unload ~/Library/LaunchAgents/com.feedjuridic.scraper.plist
```

### 🐧 Linux (systemd)

```bash
cd feedjuridic
./setup_daily_task.sh
# Trià opció 3 (systemd)
```

**Per verificar:**
```bash
systemctl --user status feedjuridic-scraper.timer
```

### ⏰ Alternativa universal: cron

```bash
cd feedjuridic
./setup_daily_task.sh
# Trià opció 1 (cron)
```

**Edit manual:**
```bash
crontab -e
# Afegeix: 0 9 * * * cd /path/to/feedjuridic && /path/to/.venv/bin/python scraper.py
```

## Prova manual

Executa el scraper directament per veure els resums de cowork processats:

```bash
cd feedjuridic
source .venv/bin/activate
python scraper.py
```

A la sortida, hauríeu de veure:

```
[COWORK] Processant resums diaris...
[COWORK] Llegint: resumen-juridico-2026-05-11.md
[COWORK] 7 titulars extrets de resumen-juridico-2026-05-11.md
[COWORK-a1b2c3d4] Analitzant: AI Omnibus: UE simplifica AI Act...
   ✓ Categoria: administratiu | Urgència: alta
...
```

## Estructura de fitxers

```
feedjuridic/
├── scraper.py                 ← Afegida: fetch_cowork_daily_summary()
├── setup_daily_task.sh        ← Nou: configurador d'escalonament
├── daily-summaries/           ← Resums diaris de cowork (llegits cada dia)
│   ├── resumen-juridico-2026-05-11.md
│   ├── resumen-juridico-2026-05-10.md
│   └── INDEX.md
├── data/
│   ├── novetats.json          ← Conté ara novetats de Cowork
│   └── estadistiques.json
└── logs/
    ├── cron_scraper.log       ← Si usa cron
    ├── systemd_scraper.log    ← Si usa systemd
    └── launchd_scraper.log    ← Si usa launchd
```

## Freqüència

- **Escalonament**: Cada dia a les 09:00 (UTC+2 / hora local)
- **Resum processat**: El resum **més recent** disponible a `daily-summaries/`
- **Duplicats evitats**: ID únic per (titol + data) evita duplicar el mateix titular

## Filtratge per IA

Els titulars de Cowork es processen de la mateixa manera que altres entrades:

1. Es verifica si contenen paraules clau sobre IA o dret digital
2. Si **no** contenen AI keywords → es descarten per mantenir la carpeta focus'd
3. Si **sí** contenen → es processen amb IA per extraure metadades

Paraules clau IA: `intel·ligència artificial`, `algoritme`, `machine learning`, `RGPD`, `blockchain`, `deepfake`, etc.

## Logs i monitoratge

Els logs es guarden a `feedjuridic/logs/`:

```bash
# Ver logs recents
tail -f logs/launchd_scraper.log

# Ver errors
cat logs/launchd_scraper_error.log
```

## Troubleshooting

### "Cap resum diari trobat"

→ Assegura't que hi ha fitxers a `daily-summaries/` amb nom `resumen-juridico-AAAA-MM-DD.md`

### "No s'ha trobat la secció 'Titulares del día'"

→ El format del resum MD ha canviat. Actualitza la regex a `scraper.py`:
```python
titulars_match = re.search(r"## TIT[UA]LAR.*?\n(.*?)(?:\n## |\Z)", contingut, re.DOTALL)
```

### Tasca no s'executa

- **macOS**: `launchctl list | grep feedjuridic` → comprovem que está carregat
- **Linux**: `systemctl --user status feedjuridic-scraper.timer`
- **cron**: `crontab -l` → verifiquem la entrada

---

**Versió**: 2026-05-11  
**Responsable de la rutina**: Marc Casellas  
**Última revisió**: 11 de maig de 2026
