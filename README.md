# DretCat 🏛️

Web de novetats jurídiques catalanes analitzades per intel·ligència artificial.
Actualització automàtica cada matí a partir del DOGC i el BOE.

---

## Estructura del projecte

```
dretcat/
├── index.html                        ← La web (llegeix data/novetats.json)
├── scraper.py                        ← Script d'anàlisi diari
├── .github/
│   └── workflows/
│       └── actualitzar.yml           ← Automatització diària (GitHub Actions)
├── data/
│   ├── novetats.json                 ← Generat pel scraper (no editar a mà)
│   └── estadistiques.json            ← Generat pel scraper (no editar a mà)
├── .env.example                      ← Plantilla de variables d'entorn
└── requirements.txt                  ← Dependències Python
```

---

## Configuració pas a pas

### 1. Obtenir la clau de la API d'Anthropic

1. Ves a [console.anthropic.com](https://console.anthropic.com)
2. Crea un compte o entra
3. Vés a **API Keys** → **Create Key**
4. Copia la clau (comença per `sk-ant-...`)

> 💡 Cost estimat: analitzar 10-20 disposicions diàries costa aproximadament 0,20-0,50 € al dia.

---

### 2. Crear el repositori a GitHub

1. Ves a [github.com](https://github.com) → **New repository**
2. Nom: `dretcat`
3. Visibilitat: **Public** (necessari per a GitHub Pages gratuïtes)
4. Crea el repositori

---

### 3. Pujar els fitxers

Pots fer-ho des del navegador:
1. Arrossega tots els fitxers a la pàgina del repositori
2. O usa el botó **Add file → Upload files**

Assegura't de pujar:
- `index.html`
- `scraper.py`
- `.github/workflows/actualitzar.yml`
- `requirements.txt`

---

### 4. Configurar la clau secreta

1. Al repositori de GitHub → **Settings**
2. A la columna esquerra: **Secrets and variables → Actions**
3. Botó **New repository secret**
4. Nom: `ANTHROPIC_API_KEY`
5. Valor: la teva clau `sk-ant-...`
6. **Add secret**

---

### 5. Crear la carpeta `data/`

GitHub no permet pujar carpetes buides. Crea un fitxer buit temporal:

1. Al repositori → **Add file → Create new file**
2. Nom del fitxer: `data/.gitkeep`
3. Deixa el contingut buit
4. Commit

---

### 6. Executar el scraper per primera vegada

1. Al repositori → pestanya **Actions**
2. A l'esquerra: **Actualització diària DretCat**
3. Botó **Run workflow** → **Run workflow**
4. Espera 2-3 minuts fins que acabi (veuràs una marca ✓ verda)

Això generarà els fitxers `data/novetats.json` i `data/estadistiques.json`.

---

### 7. Publicar la web amb GitHub Pages

1. Al repositori → **Settings**
2. A la columna esquerra: **Pages**
3. A **Branch**: selecciona `main`, carpeta `/ (root)`
4. **Save**

En 1-2 minuts, la teva web estarà disponible a:
```
https://EL-TEU-USUARI.github.io/dretcat
```

---

## Execució local (opcional, per fer proves)

```bash
# Instal·lar dependències
pip install -r requirements.txt

# Crear fitxer .env
cp .env.example .env
# Edita .env i posa la teva ANTHROPIC_API_KEY

# Executar el scraper
python scraper.py
```

---

## Personalització

### Canviar l'horari d'execució
Edita `.github/workflows/actualitzar.yml` i modifica la línia `cron`:
```yaml
- cron: "0 6 * * 1-5"   # 07:00h CET de dilluns a divendres
```

### Afegir nous filtres de categoria
A `scraper.py`, modifica el diccionari `CATEGORIES` per afegir paraules clau.

### Canviar el disseny de la web
Edita `index.html` — és un fitxer HTML estàndard, sense frameworks complicats.

---

## Fonts d'informació

| Font | Tipus | API disponible |
|------|-------|---------------|
| DOGC | Legislació catalana | ✅ Sí (JSON oficial) |
| BOE | Legislació estatal | ✅ Sí (JSON oficial) |
| CENDOJ | Jurisprudència | ❌ Scraping web |

---

## Avisos legals

- El contingut generat per IA és **orientatiu** i no constitueix assessorament jurídic.
- Les dades del DOGC i BOE són de domini públic.
- Consulta sempre un professional del dret per a decisions jurídiques concretes.

---

*Projecte de LegalTech català · Fet amb Claude (Anthropic) i GitHub Actions*
