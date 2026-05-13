"""
Company Profile Scraper — Version 3
Extrait TOUTES les données Google d'un prospect et génère l'intégralité
des fichiers nécessaires au pipeline Claude Code Direct (sans Stitch).

SOURCES :
  - Google Places API  → données structurées (adresse, note, horaires, types, 10 photos)
  - Google Maps scrape → jusqu'à ~40 avis clients dédupliqués

OUTPUT par entreprise (dossier profils/NOM_ENTREPRISE/) :
  ├── CLAUDE.md           → conducteur de build complet (lu automatiquement par Claude Code)
  ├── PRODUCT.md          → contexte métier pour le skill impeccable
  ├── reviews.json        → avis format frontend (name, role, rating, text, image)
  ├── profil.json         → données brutes API (référence)
  ├── profil.txt          → fiche lisible (référence)
  ├── photos/
  │   ├── photo_01.jpg    → API Google Places (auto)
  │   ├── ...
  │   └── photo_XX.jpg    → photos ajoutées manuellement (facultatif)
  └── videos/             → présent si vidéos détectées sur Maps ou ajoutées manuellement
      ├── video_01.mp4    → Google Maps scrape (auto)
      └── video_XX.mp4    → vidéos ajoutées manuellement (facultatif)

WORKFLOW :
  1. python3 company_scraper_bis.py --place_id <ID>
  2. Ouvre Claude Code → workspace = profils/NOM_ENTREPRISE/
  3. Claude lit CLAUDE.md automatiquement
  4. Prompt : "Build the site."  → site complet en une passe
  5. /deploy → Vercel

USAGE :
  python3 company_scraper_bis.py --place_id ChIJxRiWGsjl5EcRjf_0YrmRyIM
  python3 company_scraper_bis.py --nom "SAS GAMARY" --ville "Orléans"
  python3 company_scraper_bis.py --csv prospects.csv --priorite CHAUD
  export GOOGLE_PLACES_API_KEY=ta_cle && python3 company_scraper_bis.py --csv prospects.csv --priorite CHAUD
"""

import os, re, sys, csv, json, time, argparse, requests
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

DETAILS_URL    = "https://maps.googleapis.com/maps/api/place/details/json"
SEARCH_URL     = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PHOTO_URL      = "https://maps.googleapis.com/maps/api/place/photo"
OUTPUT_DIR     = Path("profils")
PHOTO_MAXWIDTH = 1600
MAX_REVIEWS    = 15
MAX_PHOTOS_API = 10

ALL_FIELDS = ",".join([
    "name", "formatted_address", "formatted_phone_number",
    "international_phone_number", "website", "url", "rating",
    "user_ratings_total", "reviews", "photos", "opening_hours",
    "business_status", "types", "editorial_summary",
    "geometry", "place_id",
])


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def safe_dirname(name: str) -> str:
    return re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")[:60]

def stars(r: float) -> str:
    r = float(r or 0)
    return "★" * int(r) + "☆" * (5 - int(r)) + f" ({r}/5)"

def clean_text(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


# ─────────────────────────────────────────────────────────────
# GOOGLE PLACES API
# ─────────────────────────────────────────────────────────────

def api_search(nom: str, ville: str, api_key: str):
    params = {"query": f"{nom} {ville}", "language": "fr", "region": "fr", "key": api_key}
    data = requests.get(SEARCH_URL, params=params, timeout=10).json()
    if data.get("status") != "OK" or not data.get("results"):
        print(f"  Introuvable : {nom} {ville}")
        return None
    r = data["results"][0]
    print(f"  Trouve : {r.get('name')} — {r.get('formatted_address','')[:60]}")
    return r["place_id"]


def api_details(place_id: str, api_key: str):
    params = {
        "place_id": place_id, "fields": ALL_FIELDS,
        "language": "fr", "reviews_no_translations": "true", "key": api_key,
    }
    data = requests.get(DETAILS_URL, params=params, timeout=15).json()
    if data.get("status") == "REQUEST_DENIED":
        print(f"  Cle API refusee : {data.get('error_message','')}")
        sys.exit(1)
    if data.get("status") != "OK":
        print(f"  API status : {data.get('status')}")
        return None
    return data.get("result", {})


def api_download_photos(photo_refs: list, dest: Path, api_key: str) -> list:
    dest.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, ref_obj in enumerate(photo_refs[:MAX_PHOTOS_API], 1):
        ref = ref_obj.get("photo_reference", "")
        if not ref:
            continue
        try:
            resp = requests.get(
                PHOTO_URL,
                params={"maxwidth": PHOTO_MAXWIDTH, "photo_reference": ref, "key": api_key},
                timeout=20, stream=True
            )
            ct  = resp.headers.get("Content-Type", "image/jpeg")
            ext = "jpg" if "jpeg" in ct else ct.split("/")[-1]
            fp  = dest / f"photo_{i:02d}.{ext}"
            with open(fp, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            size = fp.stat().st_size // 1024
            print(f"    [API] photo_{i:02d}.{ext}  {size} KB")
            paths.append(str(fp))
        except Exception as e:
            print(f"    photo {i} erreur : {e}")
        time.sleep(0.2)
    return paths


# ─────────────────────────────────────────────────────────────
# GOOGLE MAPS SCRAPING
# ─────────────────────────────────────────────────────────────

def _click_reviews_tab(page) -> bool:
    """
    Clique sur l'onglet 'Avis' de Google Maps.
    Essaie plusieurs stratégies dans l'ordre :
      1. page.click() avec aria-label CSS (lève une exception si absent → try/except)
      2. Parcours de tous les [role=tab] et inspection du texte visible
      3. Playwright locator get_by_role
    Retourne True si le tab a été cliqué.
    """
    # Stratégie 1 : CSS aria-label (le plus rapide)
    try:
        page.click('button[aria-label*="Avis"]', timeout=4000)
        time.sleep(2)
        print("    Tab Avis : cliqué via aria-label")
        return True
    except Exception:
        pass

    # Stratégie 2 : parcourir tous les role=tab et vérifier le texte
    try:
        tabs = page.query_selector_all('[role="tab"]')
        for tab in tabs:
            label = (tab.get_attribute("aria-label") or "").lower()
            inner = ""
            try:
                inner = tab.inner_text().lower()
            except Exception:
                pass
            if "avis" in label or "avis" in inner or "review" in label:
                tab.click()
                time.sleep(2)
                print(f"    Tab Avis : cliqué via scan des tabs (label='{label[:40]}')")
                return True
    except Exception:
        pass

    # Stratégie 3 : Playwright locator (API native, supporte :has-text)
    try:
        loc = page.locator('[role="tab"]').filter(has_text="Avis")
        if loc.count() > 0:
            loc.first.click(timeout=3000)
            time.sleep(2)
            print("    Tab Avis : cliqué via locator filter")
            return True
    except Exception:
        pass

    return False


def scrape_maps(maps_url: str, dest_photos: Path, existing_count: int):
    """
    Scrape Google Maps pour extraire les avis clients (~40 avis).
    Photos : uniquement via API (10 photos haute qualite — voir api_download_photos).
    Pour ajouter des photos supplementaires, les deposer manuellement dans photos/.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  Playwright non disponible — installer avec : pip install playwright && playwright install chromium")
        return [], []

    reviews = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        # ── Charger la page Maps ──────────────────────────────
        # "load" (pas domcontentloaded) pour que le JS Maps ait rendu les tabs
        try:
            page.goto(maps_url, wait_until="load", timeout=35000)
            time.sleep(2.5)
            # Rejeter cookies si présents
            for sel in [
                'button[aria-label*="Refuser"]',
                'button[aria-label*="Reject"]',
                'button[aria-label*="Tout refuser"]',
                'button[jsname="tWT92d"]',
            ]:
                btn = page.query_selector(sel)
                if btn:
                    btn.click()
                    time.sleep(1)
                    break
        except Exception as e:
            print(f"  Chargement Maps : {e}")
            browser.close()
            return [], []

        # ─────────────────────────────────────────────────────
        # AVIS — onglet Avis + scroll container
        # ─────────────────────────────────────────────────────
        try:
            tab_ok = _click_reviews_tab(page)

            if not tab_ok:
                print("    Onglet avis inaccessible — 0 avis Maps")
            else:
                # Trier par "Les plus récents" pour avoir de la diversité
                for sort_sel in [
                    'button[aria-label*="Trier"]',
                    'button[aria-label*="Sort"]',
                    'button[data-value*="sort"]',
                ]:
                    btn = page.query_selector(sort_sel)
                    if btn:
                        btn.click()
                        time.sleep(1)
                        # Choisir option 2 (Plus récents) via scan
                        opts = page.query_selector_all('[role="menuitemradio"], [role="option"]')
                        if len(opts) >= 2:
                            opts[1].click()
                            time.sleep(2)
                        break

                # ── Scroll dans le panneau latéral ───────────────────────
                # .m6QErb.DxyBCb est le div scrollable (confirmé par test B)
                SCROLL_SELECTORS = [
                    ".m6QErb.DxyBCb",
                    ".m6QErb[tabindex]",
                    'div[role="feed"]',
                ]
                scroll_el = None
                for sel in SCROLL_SELECTORS:
                    scroll_el = page.query_selector(sel)
                    if scroll_el:
                        print(f"    Conteneur scroll trouvé : {sel}")
                        break

                if scroll_el:
                    for i in range(15):
                        page.evaluate("el => el.scrollTop += 800", scroll_el)
                        time.sleep(0.6)
                        # Déployer les textes tronqués au fur et à mesure
                        for expand_btn in page.query_selector_all(".w8nwRe, button.M77dve"):
                            try:
                                expand_btn.click()
                                time.sleep(0.08)
                            except Exception:
                                pass
                else:
                    print("    Conteneur scroll non trouvé — fallback page scroll")
                    for _ in range(10):
                        page.keyboard.press("End")
                        time.sleep(0.9)

                # ── Parser le HTML ────────────────────────────────────────
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page.content(), "lxml")

                # data-review-id est l'attribut le plus stable
                review_blocks = soup.find_all("div", attrs={"data-review-id": True})
                if not review_blocks:
                    review_blocks = soup.find_all("div", class_=re.compile(r"\bjftiEf\b"))

                for block in review_blocks:
                    # Auteur
                    author_el = (
                        block.find(class_=re.compile(r"\bd4r55\b"))
                        or block.find("button", class_=re.compile(r"\bal6Kxe\b"))
                        or block.find(class_=re.compile(r"reviewer", re.I))
                    )
                    author = clean_text(author_el.get_text()) if author_el else "Client"

                    # Note (aria-label "X étoile(s)" sur un span)
                    note = 0
                    note_el = block.find(attrs={"aria-label": re.compile(r"étoile|star", re.I)})
                    if note_el:
                        m = re.search(r"(\d)", note_el.get("aria-label", ""))
                        if m:
                            note = int(m.group(1))

                    # Texte de l'avis (span.wiI7pd contient le texte complet après "Voir plus")
                    text_el = block.find(class_=re.compile(r"\bwiI7pd\b"))
                    text = ""
                    if text_el:
                        text = clean_text(text_el.get_text())
                        text = re.sub(r"\b(Plus|Moins|More|Less|Voir plus|Voir moins)\b", "", text).strip()

                    # Date relative ("il y a 3 semaines", etc.)
                    date_el = block.find(class_=re.compile(r"\brsqaWe\b"))
                    date_str = clean_text(date_el.get_text()) if date_el else ""

                    if author and (text or note > 0):
                        reviews.append({
                            "author": author,
                            "rating": note,
                            "text":   text,
                            "date":   date_str,
                        })

                print(f"    {len(reviews)} avis extraits via Maps (avant déduplication)")

        except Exception as e:
            print(f"    Avis Maps erreur : {e}")

        browser.close()

    # new_photos est toujours [] — les photos viennent de l'API uniquement
    return [], reviews



# ─────────────────────────────────────────────────────────────
# GENERATION DES FICHIERS OUTPUT
# ─────────────────────────────────────────────────────────────

def merge_reviews(api_reviews: list, scraped: list) -> list:
    """
    Fusionne et deduplique les avis des deux sources.
    Google Maps duplique chaque bloc de review dans le DOM (layout mobile + desktop),
    donc on utilise (auteur_normalisé, debut_texte) comme cle de deduplication.
    """
    normalized = [
        {"author": r.get("author_name",""), "rating": r.get("rating",0),
         "text": r.get("text",""), "date": r.get("relative_time_description","")}
        for r in api_reviews
    ] + scraped

    seen = set()
    result = []
    for rev in normalized:
        # Normaliser l'auteur : supprimer ponctuation parasite, guillemets, parenthèses
        # ex: "Claire \"claire\"" et "Claire (\"claire\")" → "claire claire"
        raw_author = rev.get("author", "")
        author_key = re.sub(r'[\"\'\(\)\[\]«»]', ' ', raw_author)
        author_key = re.sub(r"\s+", " ", author_key).lower().strip()
        # Ne garder que les 3 premiers mots (prénom + nom) pour ignorer les suffixes Maps
        author_key = " ".join(author_key.split()[:3])

        text_key = re.sub(r"\s+", " ", rev.get("text", "")).lower().strip()[:60]

        # Clé composite auteur + début texte → élimine doublons DOM et homonymes avec textes différents
        key = (author_key, text_key)
        if author_key and key not in seen:
            seen.add(key)
            result.append(rev)
    return result


def build_profile_txt(details: dict, all_reviews: list, all_photos: list) -> str:
    name      = details.get("name", "")
    address   = details.get("formatted_address", "")
    phone     = details.get("formatted_phone_number", "")
    intl      = details.get("international_phone_number", "")
    website   = details.get("website") or "Aucun site web"
    rating    = details.get("rating", 0)
    n_rev     = details.get("user_ratings_total", 0)
    maps_url  = details.get("url", "")
    geo       = details.get("geometry", {}).get("location", {})
    editorial = details.get("editorial_summary", {}).get("overview", "")
    hours     = details.get("opening_hours", {}).get("weekday_text", [])
    types     = [t for t in details.get("types", [])
                 if t not in ("point_of_interest", "establishment")]

    L = []
    L.append("=" * 65)
    L.append(f"  PROFIL COMPLET — {name.upper()}")
    L.append(f"  Genere le {datetime.now().strftime('%d/%m/%Y a %Hh%M')}")
    L.append("=" * 65)
    L.append(f"\nNom            : {name}")
    L.append(f"Adresse        : {address}")
    L.append(f"Tel            : {phone}  ({intl})")
    L.append(f"Site web       : {website}")
    L.append(f"Note           : {stars(rating)}  sur {n_rev} avis")
    L.append(f"Google Maps    : {maps_url}")
    if geo:
        L.append(f"Coordonnees    : {geo.get('lat')}, {geo.get('lng')}")
    if types:
        L.append(f"Categories     : {', '.join(types)}")
    if editorial:
        L.append(f"Description    : {editorial}")

    if hours:
        L.append("\nHoraires :")
        for h in hours:
            L.append(f"  {h}")

    L.append(f"\n── AVIS CLIENTS ({len(all_reviews)} recuperes / {n_rev} au total) ─────────────")
    for i, rev in enumerate(all_reviews, 1):
        L.append(f"\n  [{i:02d}] {rev['author']}  {stars(rev['rating'])}  {rev.get('date','')}")
        if rev.get("text"):
            txt = rev["text"]
            display = txt if len(txt) <= 500 else txt[:500] + "..."
            L.append(f'       "{display}"')

    L.append(f"\n── PHOTOS ({len(all_photos)} telechargees) ────────────────────────────────")
    for p in all_photos:
        size = Path(p).stat().st_size // 1024 if Path(p).exists() else 0
        L.append(f"  {p}  ({size} KB)")

    L.append("\n" + "=" * 65)
    return "\n".join(L)


def build_claude_brief(details: dict, all_reviews: list, all_photos: list, out_dir: Path) -> str:
    name      = details.get("name", "")
    address   = details.get("formatted_address", "")
    phone     = details.get("formatted_phone_number", "")
    website   = details.get("website") or ""
    rating    = details.get("rating", 0)
    n_rev     = details.get("user_ratings_total", 0)
    hours     = details.get("opening_hours", {}).get("weekday_text", [])
    editorial = details.get("editorial_summary", {}).get("overview", "")
    types     = [t for t in details.get("types", [])
                 if t not in ("point_of_interest", "establishment")]

    addr_parts  = address.split(",")
    ville       = addr_parts[-2].strip() if len(addr_parts) >= 2 else ""
    cp_match    = re.search(r"\b\d{5}\b", address)
    code_postal = cp_match.group() if cp_match else ""

    top_reviews = [r for r in all_reviews if r.get("rating",0) >= 4 and r.get("text","").strip()][:8]
    photo_rel   = [str(Path(p).relative_to(out_dir)) for p in all_photos if Path(p).exists()]

    L = []
    L.append("=" * 65)
    L.append("  BRIEF CLAUDE CODE — GENERATION PROMPT STITCH")
    L.append(f"  Business : {name}  |  {ville}  |  {datetime.now().strftime('%d/%m/%Y')}")
    L.append("=" * 65)

    L.append(f"""
INSTRUCTION POUR CLAUDE CODE :
Tu es un expert en design web haute conversion pour artisans locaux francais.

Analyse attentivement :
  1. Toutes les informations ci-dessous sur l'entreprise
  2. Chaque photo dans le dossier photos/ (equipe, vehicules, chantiers, materiel, logo)
  3. Les avis clients reels pour identifier les points forts a mettre en avant

Puis genere un prompt Google Stitch complet, precis et optimise pour creer
le meilleur site web possible pour cette entreprise.

Le prompt Stitch doit etre redige en anglais, etre tres detaille sur :
  - L'analyse des photos disponibles et leur utilisation par section
  - Le design (couleurs inspirees des photos, style, ambiance)
  - La structure exacte de chaque page
  - Les vrais avis clients a integrer verbatim
  - Tous les elements de conversion specifiques au metier
  - Les trust signals adaptes a cette entreprise precise
{"=" * 65}""")

    L.append(f"\n── DONNEES ENTREPRISE ────────────────────────────────────────")
    L.append(f"Nom                : {name}")
    L.append(f"Adresse            : {address}")
    L.append(f"Ville / CP         : {ville}  {code_postal}")
    L.append(f"Telephone          : {phone}")
    L.append(f"Site actuel        : {website if website else '>>> AUCUN SITE WEB <<<'}")
    L.append(f"Note Google        : {rating}/5  ({n_rev} avis)")
    L.append(f"Type de business   : {', '.join(types)}")
    if editorial:
        L.append(f"Description Google : {editorial}")

    if hours:
        L.append("\nHoraires :")
        for h in hours:
            L.append(f"  {h}")

    L.append(f"\n── AVIS A INTEGRER SUR LE SITE ({len(top_reviews)} selectionnes) ─────────────")
    if top_reviews:
        for i, rev in enumerate(top_reviews, 1):
            L.append(f"\n  [{i}] {rev['author']} — {rev.get('rating',5)}/5 — {rev.get('date','')}")
            if rev.get("text"):
                L.append(f'      "{rev["text"][:450]}"')
    else:
        L.append("  Aucun avis avec texte — utiliser des temoignages generiques.")

    L.append(f"\n── PHOTOS DISPONIBLES ({len(all_photos)} fichiers) ────────────────────────")
    L.append(f"Dossier : {out_dir}/photos/\n")
    for p in photo_rel:
        size = ""
        fp = out_dir / p
        if fp.exists():
            kb = fp.stat().st_size // 1024
            size = f"  ({kb} KB)"
        L.append(f"  {p}{size}")

    L.append(f"""
Consigne photos :
  Analyse le CONTENU de chaque photo avant de generer le prompt.
  Identifie : equipe visible ? vehicules avec logo ? chantiers/realisations ?
  materiel professionnel ? facade du local ? logo de l'entreprise ?
  Specifie dans le prompt Stitch quelle photo va dans quelle section du site.
{"=" * 65}""")

    L.append(f"\n── STRUCTURE DU SITE A GENERER (5 pages) ────────────────────")
    L.append(f"""
PAGE 1 — HOMEPAGE :
  Hero section :
    - Headline puissant : "[Metier] a {ville} — [promesse principale]"
    - Sous-titre : benefice cle identifie dans les avis
    - CTA principal : bouton "Devis gratuit" + telephone {phone} visible
    - Utiliser la meilleure photo de realisation ou d'equipe en hero

  Barre de confiance (sous le hero) :
    - Disponibilite (24/7 si applicable selon horaires)
    - Delai de reponse
    - Nombre d'annees d'experience (si mentionnee dans les avis)

  Section services :
    - 3 a 6 cards avec icone + nom service + description courte

  Section avis Google :
    - Afficher NOTE : {rating}/5 avec {n_rev} avis
    - Afficher les {len(top_reviews)} vrais avis ci-dessus verbatim

  Section zone d'intervention :
    - {ville} et villes environnantes ({code_postal[:2]} + departements adjacents)

  Formulaire de contact :
    - Champs : Prenom, Telephone, Email, Type de prestation, Description
    - Bouton : "Envoyer ma demande de devis"

  Footer :
    - Adresse : {address}
    - Tel : {phone}
    - Horaires
    - Liens : Mentions legales, Politique de confidentialite

PAGE 2 — SERVICES :
  Liste detaillee de tous les services avec descriptions et benefices

PAGE 3 — ZONE D'INTERVENTION :
  Carte ou liste des villes couvertes autour de {ville}

PAGE 4 — A PROPOS :
  Histoire entreprise, equipe, certifications, garanties, assurance decennale

PAGE 5 — CONTACT :
  Formulaire complet + adresse + embed carte + telephone + horaires
{"=" * 65}""")

    L.append(f"\n── ELEMENTS DE CONVERSION OBLIGATOIRES ─────────────────────")
    L.append(f"""
Trust signals a inclure :
  - "{n_rev} avis Google verifies ({rating}/5)"
  - "Devis gratuit et sans engagement"
  - "Artisan local" + mention ville {ville}
  - Assurance et garanties (decennale si applicable)
  - Telephone {phone} dans le header, le hero, ET le footer

Design guidelines :
  - Mobile-first absolu (tester chaque section en vue mobile)
  - Couleurs : a determiner en analysant les photos (si vehicule/logo = reprendre ces couleurs)
  - Style : professionnel, de confiance, sobre — pas corporate
  - Langue : francais integralement
  - Appels a l'action : contraste fort, visibles immediatement

SEO :
  - Meta title : "[Service principal] a {ville} — {name} | Devis gratuit"
  - Meta description : mentionner ville, service, disponibilite, telephone
  - JSON-LD LocalBusiness avec toutes les vraies donnees
  - H1 doit contenir "{ville}"
  - Footer avec mentions legales et politique de confidentialite
{"=" * 65}""")

    if not website:
        L.append(f"\nCONTEXTE IMPORTANT :")
        L.append(f"Ce business a {n_rev} avis Google et AUCUN site web.")
        L.append(f"C'est leur toute premiere presence web professionnelle.")
        L.append(f"L'objectif est de capter immediatement tous les clients qui les cherchent sur Google.")
    else:
        L.append(f"\nCONTEXTE : Refonte du site existant {website}")

    L.append("\n" + "=" * 65)
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────
# NOUVEAUX GENERATEURS — WORKFLOW CLAUDE CODE DIRECT
# ─────────────────────────────────────────────────────────────

def parse_hours_to_js_object(weekday_text: list) -> str:
    """
    Convertit la liste Google Places weekday_text en objet JS pour le widget
    temps-réel des horaires.

    Format Google : ["Lundi: 08:00 – 18:00", "Mardi: Fermé", ...]
    Format JS cible :
      const BUSINESS_HOURS = {
        1: { open: "08:00", close: "18:00" },   // Lundi
        2: null,                                  // Mardi fermé
        ...
        0: null,                                  // Dimanche
      };
    JS convention : 0=Dimanche, 1=Lundi, ..., 6=Samedi
    """
    DAY_MAP = {
        "lundi": 1, "mardi": 2, "mercredi": 3, "jeudi": 4,
        "vendredi": 5, "samedi": 6, "dimanche": 0,
        "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
        "friday": 5, "saturday": 6, "sunday": 0,
    }

    parsed = {}
    for line in weekday_text:
        line_lower = line.lower()
        day_num = None
        for day_name, num in DAY_MAP.items():
            if line_lower.startswith(day_name):
                day_num = num
                break
        if day_num is None:
            continue

        # Chercher des horaires de type HH:MM – HH:MM ou HH:MM - HH:MM
        time_match = re.search(
            r"(\d{1,2})[h:H](\d{0,2})\s*[–\-–]\s*(\d{1,2})[h:H](\d{0,2})",
            line
        )
        if time_match:
            oh, om, ch, cm = time_match.groups()
            open_t  = f"{int(oh):02d}:{om.zfill(2) if om else '00'}"
            close_t = f"{int(ch):02d}:{cm.zfill(2) if cm else '00'}"
            parsed[day_num] = {"open": open_t, "close": close_t}
        else:
            parsed[day_num] = None

    # Construire la chaîne JS
    lines = ["const BUSINESS_HOURS = {"]
    day_labels = {
        0: "Dimanche", 1: "Lundi", 2: "Mardi", 3: "Mercredi",
        4: "Jeudi", 5: "Vendredi", 6: "Samedi",
    }
    for num in range(7):
        label = day_labels[num]
        if num in parsed:
            entry = parsed[num]
            if entry:
                lines.append(f'  {num}: {{ open: "{entry["open"]}", close: "{entry["close"]}" }},  // {label}')
            else:
                lines.append(f"  {num}: null,  // {label} — Fermé")
        else:
            lines.append(f"  {num}: null,  // {label}")
    lines.append("};")
    return "\n".join(lines)


def generate_reviews_json(all_reviews: list, out_dir: Path) -> list:
    """
    Génère reviews.json dans le format attendu par le frontend.
    Sélectionne les meilleurs avis (4-5 étoiles, avec texte).
    Retourne la liste des avis exportés.
    """
    MONTH_MAP = {
        "janvier": "Janvier", "février": "Février", "mars": "Mars",
        "avril": "Avril", "mai": "Mai", "juin": "Juin",
        "juillet": "Juillet", "août": "Août", "septembre": "Septembre",
        "octobre": "Octobre", "novembre": "Novembre", "décembre": "Décembre",
        "january": "Janvier", "february": "Février", "march": "Mars",
        "april": "Avril", "may": "Mai", "june": "Juin",
        "july": "Juillet", "august": "Août", "september": "Septembre",
        "october": "Octobre", "november": "Novembre", "december": "Décembre",
    }

    # Sélectionner les avis de qualité
    quality = [r for r in all_reviews if r.get("rating", 0) >= 4 and len(r.get("text", "").strip()) > 30]
    if not quality:
        quality = [r for r in all_reviews if r.get("text", "").strip()]
    best = quality[:12]  # Max 12 avis

    exported = []
    for rev in best:
        author = rev.get("author", "Client")
        date_raw = rev.get("date", "")

        # Construire le champ "role" = "Ville · Mois Année" ou juste la date relative
        role_parts = []
        # Essayer de normaliser la date
        date_norm = date_raw
        for fr, display in MONTH_MAP.items():
            date_norm = re.sub(fr, display, date_norm, flags=re.IGNORECASE)
        if date_norm:
            role_parts.append(date_norm)
        role = " · ".join(role_parts) if role_parts else "Avis vérifié"

        text = rev.get("text", "")
        # Tronquer à 300 chars max pour l'affichage carte
        if len(text) > 300:
            text = text[:297] + "…"

        # Avatar : URL Google si disponible, sinon placeholder SVG basé sur initiales
        image_url = rev.get("profile_photo_url", "")
        if not image_url:
            initial = author[0].upper() if author else "C"
            image_url = f"https://ui-avatars.com/api/?name={quote_plus(author)}&background=random&color=fff&size=64"

        exported.append({
            "name":   author,
            "role":   role,
            "rating": rev.get("rating", 5),
            "text":   text,
            "image":  image_url,
        })

    out_path = out_dir / "reviews.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(exported, f, ensure_ascii=False, indent=2)
    print(f"    reviews.json   — {len(exported)} avis exportés")
    return exported


def generate_reviews_meta_json(details: dict, out_dir: Path) -> None:
    """
    Génère reviews_meta.json — source de vérité pour le compteur d'avis dynamique.
    Le site JS fetche ce fichier au chargement et met à jour tous les .js-review-count.
    """
    place_id   = details.get("place_id", "")
    rating     = details.get("rating", 0)
    n_rev      = details.get("user_ratings_total", 0)
    review_url = (
        f"https://search.google.com/local/writereview?placeid={place_id}"
        if place_id else ""
    )
    meta = {
        "rating":     rating,
        "count":      n_rev,
        "place_id":   place_id,
        "review_url": review_url,
    }
    with open(out_dir / "reviews_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def build_product_md(details: dict, all_photos: list, out_dir: Path) -> str:
    """
    Génère PRODUCT.md au format exact attendu par le skill 'impeccable'.
    Sections requises (ordre fixe) :
        Register · Users · Product Purpose · Brand Personality
        Anti-references · Design Principles · Accessibility & Inclusion
    """
    name      = details.get("name", "")
    address   = details.get("formatted_address", "")
    phone     = details.get("formatted_phone_number", "")
    rating    = details.get("rating", 0)
    n_rev     = details.get("user_ratings_total", 0)
    editorial = details.get("editorial_summary", {}).get("overview", "")
    types     = [t for t in details.get("types", [])
                 if t not in ("point_of_interest", "establishment")]
    website   = details.get("website") or ""

    addr_parts  = address.split(",")
    ville       = addr_parts[-2].strip() if len(addr_parts) >= 2 else address
    cp_match    = re.search(r"\b\d{5}\b", address)
    code_postal = cp_match.group() if cp_match else ""

    METIER_MAP = {
        "plumber":              "Plomberie & Chauffage",
        "electrician":          "Électricité",
        "roofing_contractor":   "Couverture & Toiture",
        "painter":              "Peinture & Décoration",
        "general_contractor":   "Entreprise Générale du Bâtiment",
        "landscaper":           "Paysagisme & Jardins",
        "locksmith":            "Serrurerie",
        "hvac_contractor":      "Chauffage / Climatisation",
        "carpenter":            "Menuiserie & Charpente",
        "tiler":                "Carrelage & Sols",
        "moving_company":       "Déménagement",
        "cleaning_service":     "Nettoyage & Entretien",
    }
    metier = next(
        (METIER_MAP[t] for t in types if t in METIER_MAP),
        types[0].replace("_", " ").title() if types else "Artisan"
    )

    # Description : éditoriale Google ou inférée
    desc = editorial if editorial else (
        f"Artisan local en {metier} basé à {ville}, "
        f"noté {rating}/5 sur {n_rev} avis Google vérifiés."
    )

    parts = [
        f"# Product",
        "",
        "## Register",
        "brand",
        "",
        "## Users",
        (
            f"Particuliers à {ville} ({code_postal}) et communes voisines cherchant un "
            f"{metier.lower()} de confiance. Deux profils coexistent : urgence immédiate "
            f"(fuite, panne, dépannage) et projet planifié (rénovation, installation). "
            f"Mobile-first — 70 % des visites viennent de smartphone. "
            f"Le visiteur décide d'appeler en moins de 30 secondes : il ne lit pas, il scanne."
        ),
        "",
        "## Product Purpose",
        (
            f"Site vitrine de conversion pour {name}, {metier.lower()} basé à {ville}. "
            f"{desc} "
            f"L'objectif unique est de transformer les visiteurs Google en appels téléphoniques "
            f"et demandes de devis. Ce n'est pas un portfolio — c'est un outil de prospection. "
            f"Le succès se mesure au nombre d'appels générés, pas au temps passé sur le site."
        ),
        "",
        "## Brand Personality",
        (
            f"Professionnel local, direct, rassurant. Ni corporate ni décontracté. "
            f"La marque repose sur trois piliers : proximité géographique réelle ({ville}), "
            f"preuve sociale tangible ({n_rev} avis Google vérifiés à {rating}/5), "
            f"et réactivité (devis rapide, dépannage urgent). "
            f"Ton : un artisan compétent qui parle vrai, pas un commercial."
        ),
        "",
        "## Anti-references",
        "- Site WordPress template générique avec stock photos",
        "- Palette bleue/blanche générique 'plombier du coin'",
        "- Hero avec photo de gouttelettes d'eau ou ampoule",
        "- Texte plein de « Qualité », « Excellence », « Expertise » sans preuve",
        "- 3 colonnes égales de services identiques (card grid uniforme)",
        "- Formulaire de contact invisible en bas de page",
        "- Footer chargé de liens inutiles",
        "- Animations de chargement sans raison",
        "- Dégradé texte décoratif (background-clip: text)",
        "- Bordure colorée latérale sur les cards (side-stripe)",
        "",
        "## Design Principles",
        (
            "1. **Appel avant tout** — le numéro de téléphone est visible en permanence, "
            "dès le header sticky. Chaque section possède un CTA explicite."
        ),
        (
            "2. **Authenticité locale** — les photos réelles de l'artisan, de son véhicule "
            "et de ses chantiers priment sur tout stock photo. Le design découle des couleurs "
            "extraites de ces photos, pas d'un template secteur."
        ),
        (
            f"3. **Preuve sociale au premier plan** — les {n_rev} avis Google ({rating}/5) "
            f"apparaissent dans le hero et jalonnent le parcours de conversion, pas uniquement en bas de page."
        ),
        (
            "4. **Mobile-first sans compromis** — chaque décision de layout, de typographie "
            "et de CTA est validée d'abord sur 390px. Les breakpoints supérieurs enrichissent, "
            "ils ne rattrapent pas."
        ),
        (
            "5. **Personnalité forte, non générique** — le site doit être immédiatement "
            "reconnaissable comme différent d'un template artisan standard. "
            "La palette couleur sort des clichés bleu/blanc du secteur."
        ),
        "",
        "## Accessibility & Inclusion",
        (
            "WCAG 2.1 AA minimum. Contraste texte/fond ≥ 4.5:1 (corps), ≥ 3:1 (grands titres). "
            "Tous les CTAs téléphoniques utilisent href=\"tel:...\" natif (accessibilité + iOS tap-to-call). "
            "Animations soumises à prefers-reduced-motion. "
            "Images avec attribut alt descriptif. Formulaire avec labels associés aux champs."
        ),
        "",
        "---",
        "",
        "## Référence Métier",
        f"- **Entreprise** : {name}",
        f"- **Adresse** : {address}",
        f"- **Téléphone** : {phone}",
        f"- **Note Google** : {rating}/5 sur {n_rev} avis",
        f"- **Site existant** : {website if website else 'Aucun — première présence web'}",
    ]

    if types:
        parts.append(f"- **Types Google** : {', '.join(types)}")

    parts += [
        "",
        "## Photos & Vidéos",
        "Voir CLAUDE.md §4bis — protocole complet d'analyse médias.",
    ]

    return "\n".join(parts)


def build_claude_md(details: dict, all_reviews: list, all_photos: list, out_dir: Path) -> str:
    """
    Génère le CLAUDE.md — document conducteur complet pour Claude Code.
    Claude Code lit ce fichier au démarrage et suit toutes ses instructions
    pour produire le site final parfait en une seule passe.
    """
    name      = details.get("name", "")
    address   = details.get("formatted_address", "")
    phone     = details.get("formatted_phone_number", "")
    intl      = details.get("international_phone_number", "")
    website   = details.get("website") or ""
    rating    = details.get("rating", 0)
    n_rev     = details.get("user_ratings_total", 0)
    maps_url  = details.get("url", "")
    geo       = details.get("geometry", {}).get("location", {})
    lat       = geo.get("lat", "")
    lng       = geo.get("lng", "")
    editorial = details.get("editorial_summary", {}).get("overview", "")
    hours_raw = details.get("opening_hours", {}).get("weekday_text", [])
    types     = [t for t in details.get("types", [])
                 if t not in ("point_of_interest", "establishment")]

    addr_parts  = address.split(",")
    ville       = addr_parts[-2].strip() if len(addr_parts) >= 2 else address
    dept_raw    = addr_parts[-1].strip() if len(addr_parts) >= 1 else ""
    cp_match    = re.search(r"\b\d{5}\b", address)
    code_postal = cp_match.group() if cp_match else ""
    dept_num    = code_postal[:2] if code_postal else ""

    METIER_MAP = {
        "plumber": ("Plomberie & Chauffage", "plombier", "plomberie chauffage sanitaire"),
        "electrician": ("Électricité", "électricien", "électricité installation dépannage"),
        "roofing_contractor": ("Couverture & Toiture", "couvreur", "couverture toiture zinguerie"),
        "painter": ("Peinture & Décoration", "peintre", "peinture intérieure extérieure"),
        "general_contractor": ("Bâtiment", "artisan", "rénovation construction travaux"),
        "landscaper": ("Paysagisme", "paysagiste", "jardins espaces verts entretien"),
        "locksmith": ("Serrurerie", "serrurier", "serrurerie dépannage urgence"),
        "hvac_contractor": ("Chauffage / Clim", "chauffagiste", "chauffage climatisation VMC"),
        "carpenter": ("Menuiserie", "menuisier", "menuiserie charpente bois"),
        "tiler": ("Carrelage", "carreleur", "carrelage sol mur faïence"),
        "moving_company": ("Déménagement", "déménageur", "déménagement transport stockage"),
        "cleaning_service": ("Nettoyage", "agent d'entretien", "nettoyage entretien ménage"),
    }
    metier_tuple = next((METIER_MAP[t] for t in types if t in METIER_MAP),
                        ("Artisan BTP", "artisan", "travaux rénovation"))
    metier_label, metier_singulier, metier_keywords = metier_tuple

    # Heures JS
    hours_js = parse_hours_to_js_object(hours_raw)

    # Top avis pour insertion verbatim
    top_reviews = [r for r in all_reviews if r.get("rating", 0) >= 4 and len(r.get("text", "").strip()) > 40][:5]

    # Photos relatives
    photo_rel = [f"photos/{Path(p).name}" for p in all_photos if Path(p).exists()]

    # Département/zone
    zone_str = f"{ville} et les communes du {dept_num}" if dept_num else ville

    # Formsubmit email (utiliser l'email courant ou placeholder)
    formsubmit_email = "contact@example.com"  # Sera remplacé manuellement

    # ── Construction du document ────────────────────────────────────────────────

    doc = f"""# CLAUDE.md — {name}
# Conducteur de build : site vitrine haute conversion pour artisan local
# Généré automatiquement le {datetime.now().strftime("%d/%m/%Y à %Hh%M")}
# ⚠️  Ne pas modifier manuellement — regénérer via company_scraper_bis.py

---

## 0. MISSION & BARRE QUALITÉ

Tu vas construire un site vitrine **production-ready** pour **{name}**, {metier_singulier} à {ville}.

**Critère de succès** : Un visiteur smartphone qui arrive sur ce site depuis Google doit pouvoir :
1. Comprendre le métier et la zone en **< 3 secondes**
2. Appeler ou demander un devis en **< 2 clics**
3. Être convaincu par la preuve sociale en **< 30 secondes** de scroll

**Valeur cible** : Site professionnel vendu **800–1200€**. Chaque décision technique et design doit justifier ce prix.

**Aucune correction ne doit être nécessaire après la première génération.**

---

## 1. SÉQUENCE DE SKILLS — ORDRE STRICT, JAMAIS SIMULTANÉ

Invoquer les skills dans cet ordre exact. Attendre la fin complète de chacun avant de passer au suivant.

| Étape | Skill | Commande | Rôle |
|-------|-------|----------|------|
| 1 | `full-output-enforcement` | (auto) | Interdit toute troncature — activer en premier |
| 2 | `stitch-design-taste` | (auto) | Générer DESIGN.md après analyse photos |
| 3 | `impeccable` | `teach` → `craft` → `polish` → `audit` | Build principal du site |
| 4 | `emil-design-eng` | (auto dans polish) | Animations, spring physics, micro-interactions |

**Règle absolue** : Ne jamais activer deux skills simultanément. Chaque skill modifie le contexte de génération — les superposer produit des conflits et du slop.

---

## 2. DONNÉES ENTREPRISE

```
Nom                : {name}
Métier             : {metier_label}
Adresse            : {address}
Ville / CP         : {ville} {code_postal}
Téléphone FR       : {phone}
Téléphone Intl     : {intl}
Note Google        : {rating}/5 sur {n_rev} avis
Google Maps URL    : {maps_url}
Coordonnées GPS    : {lat}, {lng}
Site existant      : {website if website else "AUCUN — première présence web"}
```
"""

    if editorial:
        doc += f"""
Description Google : {editorial}
"""

    doc += f"""
---

## 3. HORAIRES D'OUVERTURE

### Format texte (pour affichage HTML)
"""
    for h in hours_raw:
        doc += f"- {h}\n"

    doc += f"""
### Objet JS (pour le widget statut temps-réel)
Coller tel quel dans le script du widget horaires :

```javascript
{hours_js}

// Fonction de vérification du statut
function getBusinessStatus() {{
  const now = new Date();
  const day = now.getDay();  // 0=Dim, 1=Lun...
  const timeStr = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
  const hours = BUSINESS_HOURS[day];
  if (!hours) return {{ open: false, label: 'Fermé aujourd\\'hui' }};
  if (timeStr >= hours.open && timeStr < hours.close) {{
    return {{ open: true, label: `Ouvert · Ferme à ${{hours.close}}` }};
  }}
  if (timeStr < hours.open) {{
    return {{ open: false, label: `Ouvre à ${{hours.open}}` }};
  }}
  return {{ open: false, label: `Fermé · Ouvre {{}}` }};
}}
```

---

## 4. PROTOCOLE D'ANALYSE PHOTOS (PHASE OBLIGATOIRE)

**Avant d'écrire la première ligne de HTML**, analyser visuellement chaque photo dans `photos/`.

Pour chaque photo, noter :
- **Couleurs dominantes** : extraire les hex codes des couleurs de marque (véhicule, logo, combinaison, panneau)
- **Contenu** : équipe ? véhicule avec logo ? chantier ? matériel ? local commercial ?
- **Qualité** : nette et utilisable en production ? ou trop sombre / floue ?
- **Affectation** : quelle section du site cette photo illustre le mieux ?

### Photos disponibles — analyser TOUT le dossier `photos/`

> ⚠️ **Ne pas supposer que seules les photos listées ci-dessous existent.**
> Des photos supplémentaires ont pu être ajoutées manuellement après la génération de ce fichier.
>
> **Action obligatoire** : lancer `ls photos/` au démarrage pour connaître la liste réelle,
> puis analyser visuellement **CHAQUE fichier présent** dans `photos/`.

Photos confirmées au moment de la génération ({len(photo_rel)} fichiers via API Google Places) :
"""
    for p in photo_rel:
        doc += f"- `{p}`\n"

    doc += """
Des photos supplémentaires ajoutées manuellement seront aussi dans `photos/` — toutes analyser.

---

## 4bis. PROTOCOLE MÉDIAS — PHOTOS & VIDÉOS (PHASE OBLIGATOIRE)

> **Avant d'écrire la première ligne de HTML**, analyser visuellement l'intégralité
> des médias disponibles dans `photos/` ET `videos/`.

---

### Étape 0 — Détection du logo (prioritaire)

**Première action — avant tout autre chose** :
```bash
ls photos/logo.png 2>/dev/null && echo "LOGO PRESENT" || echo "LOGO ABSENT"
```

- **Si `photos/logo.png` existe** → noter immédiatement. Ce fichier sera l'unique source du logo dans tout le site : header, footer, favicon éventuel. Affectation dans manifest.json : `"logo"`. Ne jamais utiliser `logo.png` comme fond ou en galerie.
- **Si absent** → utiliser le nom de l'entreprise en texte (font display, couleur de marque) dans header et footer.

---

### Étape 1 — Inventaire complet

Lancer ces deux commandes :
```bash
ls photos/    # toutes les photos disponibles
ls videos/    # toutes les vidéos disponibles (peut être vide — pas grave)
```

> ⚠️ **GATE VIDÉOS — VÉRIFICATION BLOQUANTE**
> Documenter le résultat de `ls videos/` avant de continuer — même si tu penses que le dossier est vide.
> Si des fichiers `.mp4` sont présents → l'Étape 3 (analyse vidéos) est **obligatoire et non-optionnelle**.
> Ne jamais sauter cette commande. Ne jamais supposer que le dossier est vide sans l'avoir vérifié.

Traiter tout ce qui apparaît dans ces deux dossiers. Jamais ignorer `videos/` sous prétexte
que le dossier était vide au moment de la génération de ce fichier — des vidéos ont pu
être ajoutées manuellement depuis.

---

### Étape 2 — Analyse des photos

Pour chaque fichier dans `photos/`, noter :
- **Couleurs dominantes** : hex codes des couleurs de marque (véhicule, logo, combinaison, panneau)
- **Contenu** : équipe ? véhicule avec logo ? chantier ? matériel ? local commercial ?
- **Qualité** : nette et utilisable en production ? ou trop sombre / floue ?
- **Affectation** : hero | à-propos | réalisations | fond overlay

### Règles d'affectation photos
- **Photo hero** : meilleure réalisation OU équipe en action — jamais photo d'outil isolé
- **Section À propos** : équipe, local, véhicule de marque
- **Section Réalisations** : chantiers avant/après ou travaux terminés
- **Photos de fond** : `background-image` avec overlay sombre 50 % pour lisibilité

---

### Étape 3 — Analyse des vidéos (si `videos/` contient des fichiers)

Pour chaque `video_XX.mp4`, extraire **1 frame toutes les 2 secondes** (couverture complète — aucun angle mort) :

```bash
DURATION=$(ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 videos/video_01.mp4 2>/dev/null | cut -d. -f1)

# Intervalle adaptatif : 2s si vidéo ≤ 60s, 4s si > 60s — toujours 15-20 frames
INTERVAL=$(( DURATION / 15 ))
[ "$INTERVAL" -lt 2 ] && INTERVAL=2

T=0
IDX=0
while [ "$T" -le "$DURATION" ]; do
  ffmpeg -i videos/video_01.mp4 -ss $T -vframes 1 \
    /tmp/frame_v01_$(printf '%02d' $IDX)_${T}s.jpg -y 2>/dev/null
  T=$(( T + INTERVAL ))
  IDX=$(( IDX + 1 ))
done
```

> Pourquoi cette approche : les pourcentages fixes ratent les frames cruciales entre deux checkpoints
> (ex : camionnette de marque visible à 4s sur une vidéo de 26s, invisible avec 5%/20%/35%).
> 1 frame / 2s garantit qu'aucun élément de branding (véhicule, logo, tenue) n'est ignoré.

Analyser **chaque frame** comme une photo :
- **Couleurs** → ajouter à la palette de marque si nouvelles couleurs détectées
- **Contenu** : équipe en action ? chantier ? véhicule avec logo ? client qui témoigne ?
- **Qualité** : frame nette et exploitable, ou trop floue / sombre ?

> Si `ffmpeg` indisponible : inférer le contenu depuis le nom/poids du fichier et appliquer
> les règles d'affectation par défaut ci-dessous.

### Règles d'affectation vidéos et composants HTML

**Décision d'affectation — dans l'ordre :**

1. **Hero vidéo background** — si la vidéo montre un chantier ou l'équipe en action, ≥ 720p
```html
<section class="relative min-h-[100dvh] flex items-center overflow-hidden">
  <video autoplay muted loop playsinline
    class="absolute inset-0 w-full h-full object-cover"
    poster="photos/photo_01.jpg">
    <source src="videos/video_01.mp4" type="video/mp4">
    <img src="photos/photo_01.jpg" alt="Artisan au travail — {name}">
  </video>
  <div class="absolute inset-0 bg-black/55"></div>
  <div class="relative z-10"><!-- contenu hero --></div>
</section>
```

2. **Galerie Réalisations** — si la vidéo montre des chantiers/travaux terminés
```html
<video controls playsinline preload="metadata"
  class="w-full rounded-2xl shadow-xl aspect-video object-cover"
  poster="photos/photo_02.jpg">
  <source src="videos/video_01.mp4" type="video/mp4">
</video>
```

3. **Témoignage client vidéo** — si un client parle face caméra
```html
<div class="relative rounded-2xl overflow-hidden bg-gray-900 aspect-video">
  <video controls playsinline preload="metadata" class="w-full h-full object-cover">
    <source src="videos/video_01.mp4" type="video/mp4">
  </video>
  <div class="absolute bottom-4 left-4 text-white">
    <p class="font-semibold text-sm">Avis vidéo vérifié</p>
  </div>
</div>
```

**Règles techniques (BLOQUANTES) :**
- ✅ `autoplay` → toujours accompagné de `muted` (bloqué par tous les navigateurs sinon)
- ✅ `playsinline` → obligatoire pour iOS Safari
- ✅ `poster` → toujours renseigné (affichage avant chargement)
- ✅ `preload="metadata"` → sur toutes les vidéos avec `controls` (pas d'autoplay)
- ❌ Autoplay avec son → UX catastrophique, jamais
- ❌ Vidéo de mauvaise qualité en hero → dégrader en photo background avec `poster`

---

### Étape 4 — Palette de marque consolidée

Après analyse photos + frames vidéos, définir la palette finale :
- **Couleur principale** : couleur la plus présente sur véhicule / logo / tenue
- **Couleur secondaire** : contraste ou couleur d'accroche (souvent un jaune, orange, rouge)
- **Fallback** : `#1E3A5F` (bleu marine) + `#F59E0B` (ambre) si aucune couleur de marque détectable

---

### Étape 5 — Écriture du manifest photos (OBLIGATOIRE avant toute construction)

Après avoir analysé toutes les photos et frames vidéo, écrire `photos/manifest.json`.

Ce fichier est la **seule source de vérité** pour l'affectation des photos.
Interdiction absolue d'insérer une photo sans avoir consulté ce fichier.

**Format requis** :
```json
[
  {
    "file": "photo_01.jpg",
    "description": "Description précise en une phrase de ce que montre réellement cette photo",
    "service_category": "chauffage | plomberie | recherche_fuite | climatisation | depannage | equipe | vehicule | chantier_general | logo",
    "quality": "high | medium | low",
    "affectation": "hero | services/chauffage | services/plomberie | services/recherche_fuite | services/climatisation | gallery | about | background | logo",
    "usable_as_hero": true
  }
]
```

**Règles d'affectation strictes** :
- `logo` → réservé à `photos/logo.png` uniquement. Ne jamais servir comme fond ou galerie.
- `hero` → meilleure photo de chantier impressionnant OU équipe en action. `"usable_as_hero": true` obligatoire.
- `services/[categorie]` → photo qui illustre DIRECTEMENT ce service. Jamais approximatif.
  - `services/recherche_fuite` → uniquement si la photo montre une fuite enterrée, excavation, tuyau percé, sol ouvert.
  - `services/chauffage` → uniquement chaudière, radiateur, ballon, plancher chauffant.
  - `services/plomberie` → tuyauterie, robinetterie, évier, WC, salle de bain.
- `gallery` → réalisations sans catégorie précise, photos de bonne qualité.
- `about` → équipe, véhicule, local commercial, artisan en portrait.
- `background` → fond acceptable avec overlay sombre.

**Règle absolue** : si aucune photo ne correspond à un service → utiliser `gallery` ou `chantier_general`.
**Jamais** insérer une photo incorrecte pour "remplir" une section.

**Pendant la construction (§6 PHASE 3)** — pour chaque section :
1. Lire `photos/manifest.json`
2. Filtrer par `service_category` ou `affectation` exact
3. Choisir la photo avec `quality: "high"` en priorité
4. Si aucun match exact → utiliser une photo `gallery` plutôt qu'une photo de mauvaise catégorie

---

## 4ter. CONCEPT DESIGN — DÉCISION ESTHÉTIQUE (PHASE OBLIGATOIRE)

> ⚠️ Compléter AVANT §4quat et §4quin.
> Sans ce bloc documenté et relu, la construction produit un site générique.

### Objectif
Transformer les données brutes de §4bis en une intention esthétique cohérente.
Un designer ne commence pas par choisir des couleurs — il commence par décider ce que le site doit *ressentir*.

---

### 1. Phrase-concept

Une seule phrase qui capture l'intention esthétique complète.
Structure : `[Personnalité de marque] — [source de la palette] — [énergie typographique] — [niveau de décoration]`

Exemple : *"Artisan-maître direct et sans fioritures — palette extraite du gris anthracite du van et du cuivre des tuyaux — typographie lourde et affirmée — aucune décoration qui ne serve pas la conversion."*

Règle : si la phrase ne permet pas d'éliminer des options visuelles concrètes, elle n'est pas assez précise — la réécrire.

---

### 2. Stratégie couleur — choisir UN niveau

| Stratégie | Description | Quand l'utiliser |
|---|---|---|
| **Restrained** | Neutrals dominant, un accent ≤ 10 % | Marques sobres, palette photos très peu saturée |
| **Committed** | Une couleur occupe 30–60 % de la surface | **Défaut recommandé artisans** — personnalité sans agressivité |
| **Full palette** | 3–4 couleurs avec rôles distincts | Services très différenciés visuellement |
| **Drenched** | La surface EST la couleur | Heroes de campagne uniquement |

> Committed est le bon choix pour la grande majorité des artisans locaux.
> Restrained uniquement si les photos sont très sobres et le concept l'exige explicitement.
> Ne jamais choisir Restrained par défaut ou par prudence.

---

### 3. Direction typographique — choisir UNE direction

- **Éditoriale** : contrastes de taille extrêmes (display 96–120 px, body 16 px), police display à forte personnalité → marques affirmées et confiantes
- **Fonctionnelle** : hiérarchie par graisse plutôt que par taille, haute lisibilité → marques techniques et précises
- **Expressive** : typographie comme élément visuel fort → marques créatives ou artistiques

La direction doit découler directement de la phrase-concept.
Si le concept dit "direct et sans fioritures", la direction ne peut pas être Expressive.

Polices autorisées (non-bannie) : Space Grotesk, Bricolage Grotesque, Geist, Satoshi, Cabinet Grotesk, Unbounded, Lexend, Manrope, Figtree, Nunito Sans.
Polices bannies : Inter, Outfit, DM Sans, Plus Jakarta Sans, Instrument Sans, Syne, IBM Plex Sans, Lora, Playfair Display.

---

### 4. Phrase de scène — justification clair / sombre

Écrire UNE phrase concrète :
*Qui utilise ce site, où, dans quel état d'esprit, sous quelle lumière ambiante, à quel moment.*

Si la phrase ne force pas le choix clair/sombre, elle n'est pas assez concrète — ajouter du détail.

Force vers clair : *"Un particulier en panique un dimanche matin dans son salon éclairé, smartphone en main, avec une fuite sous l'évier."*
Force vers sombre : *"Un responsable maintenance BTP consultant depuis un bureau en soirée avec lumière artificielle froide."*

---

### 5. Output obligatoire — à documenter avant §4quat

```
CONCEPT DESIGN
─────────────────────────────────────────────────────────
Phrase-concept    : [...]
Stratégie couleur : Restrained | Committed | Full palette | Drenched
Palette retenue   :
  primary   = oklch(__ __ __deg)  [rôle : couleur de marque dominante]
  secondary = oklch(__ __ __deg)  [rôle : contraste / accroche]
  accent    = oklch(__ __ __deg)  [rôle : CTA, highlights]
  bg        = oklch(__ __ __deg)  [fond principal — jamais #fff pur]
  surface   = oklch(__ __ __deg)  [cards, sections alternées]
  text-main = oklch(__ __ __deg)  [corps — jamais #000 pur]
  text-muted= oklch(__ __ __deg)  [métadonnées, captions]
Direction typo    : Éditoriale | Fonctionnelle | Expressive
Police display    : [nom] — [justification en 1 mot]
Police corps      : [nom] — [justification en 1 mot]
Phrase de scène   : [...]
Thème             : Clair | Sombre
─────────────────────────────────────────────────────────
```

> ⚠️ STOP — ce bloc doit être complété, relu et cohérent avec la phrase-concept avant d'ouvrir §4quat.

---

## 4quat. GÉNÉRATION DESIGN.md — CONSTITUTION VISUELLE (PHASE OBLIGATOIRE)

> Prérequis : bloc CONCEPT DESIGN de §4ter complété et validé.

### Objectif
Produire DESIGN.md — la loi visuelle du site.
Chaque décision visuelle pendant la construction doit être justifiable par DESIGN.md.
Si une décision n'y a pas de réponse → l'ajouter avant de continuer.

### Commande
```
$impeccable document
```

### Ce que DESIGN.md doit contenir

- **Tokens couleur** : valeurs OKLCH complètes pour chaque rôle (primary, secondary, accent, bg, surface, text-main, text-muted, border, error, success)
- **Échelle typographique** : pour chaque niveau (display, h1, h2, h3, body-lg, body, body-sm, caption) — taille, graisse, interligne, tracking — avec ratio ≥ 1.25 entre chaque niveau
- **Système d'espacement** : progression logique avec variation de rythme — sections différentes respirent différemment
- **Patterns composants** : boutons (default, hover, active, focus, disabled), cards, badges, inputs, liens

### Validation post-génération

Relire DESIGN.md et vérifier :
- [ ] Palette issue des photos, pas d'un cliché sectoriel (pas de bleu #3B82F6 par défaut, pas de gris-500 comme texte principal)
- [ ] Fonts non-bannies : Inter, Outfit, DM Sans, Plus Jakarta Sans, Instrument Sans — aucune de celles-là
- [ ] Contraste typographique réellement fort : display ≥ 72 px, h1 ≥ 48 px, body = 16–18 px
- [ ] Stratégie couleur de §4ter respectée dans les tokens (ex : Committed = primary visible sur 30–60 % des surfaces)
- [ ] Cohérence avec la phrase-concept de §4ter

Si un écart est détecté entre DESIGN.md et le CONCEPT DESIGN → corriger DESIGN.md avant de continuer.

---

## 4quin. ARCHITECTURE DE LAYOUT — ANTI-TEMPLATE (PHASE OBLIGATOIRE)

> Prérequis : DESIGN.md généré et validé (§4quat).
> Décider la structure de chaque section AVANT d'écrire une ligne de HTML.
> Un layout décidé à l'avance = cohérent. Un layout improvisé en cours de code = template.

---

### Hero — choisir UNE option

| Option | Quand l'utiliser |
|---|---|
| **Full-bleed photo** — photo en fond pleine largeur, texte superposé | Photo exceptionnelle, bien cadrée, haute résolution |
| **Split 60/40** — photo droite (60 %), CTA + texte gauche (40 %) | Photo et texte méritent le même poids visuel |
| **Color field + photo inset** — fond couleur de marque, photo en insert | Stratégie Committed/Drenched ou photos de qualité moyenne |
| **Vidéo background** — vidéo en fond, poster photo en fallback | Vidéo ≥ 720p disponible montrant l'artisan en action |
| **Text dominant** — typographie comme élément visuel, image minimale | Photos toutes de qualité insuffisante |

Interdit par défaut : hero centré avec texte blanc centré sur overlay sombre générique.
C'est le pattern le plus répandu du web artisan — éviter sauf si le concept l'exige explicitement.

---

### Services — choisir UNE option

| Option | Quand l'utiliser |
|---|---|
| **Liste éditoriale numérotée** — 1. Service + description + hiérarchie typo forte | 4+ services, direction Éditoriale ou Fonctionnelle |
| **Grille asymétrique 2+1** — deux services côte à côte + un service mis en avant | Un service est clairement le service principal |
| **Rangées alternées** — image gauche/droite en alternance avec texte | Photos disponibles pour chaque type de prestation |
| **Liste avec icônes Material Symbols** | 8+ services de poids équivalent |

Interdit : 3 colonnes égales de cards avec icône + titre + 2 lignes identiques.
C'est le pattern le plus associé aux templates WordPress génériques.

---

### Avis clients — choisir UNE option

| Option | Quand l'utiliser |
|---|---|
| **Inline dans le flux** — 2–3 avis forts intégrés entre les sections | Avis très forts, < 100 mots, formulation mémorable |
| **Section dédiée avec photos de profil** — carousel + badge note globale | Standard, fonctionne toujours |
| **Citations visuelles** — grande typographie sur fond coloré | Avis avec formulation particulièrement percutante |

Règle absolue : les avis ne sont jamais uniquement en bas de page.
Ils sont un argument de vente — ils doivent apparaître avant la section contact.

---

### Architecture CTA — règles non-négociables

- **Barre sticky mobile** : sur mobile (< 768 px), une barre fixée en bas affiche en permanence le numéro de téléphone + bouton "Appeler". Ne disparaît jamais au scroll.
- **Téléphone visible 3 fois minimum** : header sticky + hero + section contact. Jamais forcer le visiteur à chercher le numéro.
- **Micro-CTA par section** : chaque section se termine ou contient un appel à l'action contextuel — pas uniquement le hero et le bas de page.
- **Formulaire mid-page** : le formulaire apparaît une première fois après les avis, pas uniquement en bas. Le visiteur convaincu agit immédiatement — ne pas l'obliger à scroller jusqu'en bas.

---

### Rythme des sections — définir l'alternance Dense / Aérée

Règle : jamais 3 sections denses consécutives.

- **Dense** : contenu riche, espacement serré, information haute densité
- **Aérée** : peu d'éléments, beaucoup de négatif, respiration volontaire

Exemple de rythme équilibré :
```
Header      → Dense   (navigation, téléphone, identité)
Hero        → Aérée   (un message, un CTA, une image — pas plus)
Trust bar   → Dense   (4 chips de réassurance)
Services    → [D/A]   (selon option choisie)
Photo break → Aérée   (une image pleine largeur, texte minimal)
Avis        → Dense   (carousel, note globale, profils)
Formulaire  → Aérée   (champs simples, espace généreux)
Zone inter  → Dense   (liste villes, carte)
Footer      → Dense   (infos pratiques, liens légaux)
```

---

### Output obligatoire — brief de layout

```
ARCHITECTURE LAYOUT
────────────────────────────────────────────────────────
Hero            : [option] — [justification 1 ligne]
Services        : [option] — [justification 1 ligne]
Avis            : [option] — [justification 1 ligne]
Barre mobile    : sticky bottom bar (OUI — non-négociable)
Formulaire      : mid-page (OUI — non-négociable)
Rythme sections : Header(D) → Hero(A) → TrustBar(D) → ...
────────────────────────────────────────────────────────
```

> ⚠️ STOP — ce brief doit être écrit et validé avant §6 PHASE 3 (Build HTML).
> Commencer le HTML sans ce brief = construire sans plan = produire un template.


## 5. AVIS CLIENTS — INTÉGRATION VERBATIM

Fichier : `reviews.json` (généré automatiquement — charger avec fetch ou import)

### Top {len(top_reviews)} avis sélectionnés pour intégration prioritaire
"""
    if top_reviews:
        for i, rev in enumerate(top_reviews, 1):
            doc += f"""
**[{i}] {rev.get("author", "Client")}** — {rev.get("rating", 5)}/5 — {rev.get("date", "")}
> "{rev.get("text", "")[:400]}"
"""
    else:
        doc += "\n_Aucun avis texte disponible — utiliser les données de reviews.json_\n"

    doc += f"""
### Affichage avis
- Afficher la note globale **{rating}/5** avec **{n_rev} avis** en badge de confiance
- Masquer les avis sans texte (rating seul)
- Maximum 6 avis visibles simultanément — scroll horizontal sur mobile

---

## 6. SÉQUENCE DE BUILD — 6 PHASES

### PHASE 1 — Concept + Design System (§4ter → §4quat → §4quin)
1. Compléter le bloc CONCEPT DESIGN de §4ter (phrase-concept, stratégie couleur, direction typo, thème)
2. Générer DESIGN.md via `$impeccable document` (§4quat) — valider la cohérence avec le concept
3. Compléter le brief ARCHITECTURE LAYOUT de §4quin (hero, services, avis, rythme sections)
4. ⚠️ Ne pas ouvrir PHASE 3 sans les deux blocs outputs documentés (CONCEPT DESIGN + ARCHITECTURE LAYOUT)

### PHASE 2 — Architecture fichiers
Créer la structure exacte :
```
{safe_dirname(name)}/
├── index.html          → Homepage
├── services.html       → Page Services détaillée
├── realisations.html   → Portfolio / Galerie
├── a-propos.html       → Histoire, équipe, certifications
├── contact.html        → Formulaire + carte + horaires
├── mentions-legales.html
├── politique-confidentialite.html
├── reviews.json        → (déjà généré par le scraper)
├── reviews_meta.json   → (déjà généré par le scraper — compteur dynamique)
└── photos/             → (déjà présent, manifest.json à créer en §4bis Étape 5)
```

### PHASE 3 — Build HTML (skill: impeccable → craft)
Construire chaque page en suivant STRICTEMENT :
- Le brief ARCHITECTURE LAYOUT de §4quin (hero, services, avis, rythme) — jamais dévier sans justification
- Le DESIGN.md généré en Phase 1 (tokens couleur, échelle typo, patterns composants)
- La structure de §7 (specs par page)
- Le stack technique de §8
- Les components obligatoires de §9

### PHASE 4 — Polish interactions + Desktop review (skill: impeccable → polish + emil-design-eng)
- Appliquer les animations `emil-design-eng` sur tous les éléments interactifs
- Vérifier le widget horaires temps-réel (§3)
- Vérifier les micro-interactions (hover, press, reveal)
- Tester le formulaire FormSubmit

**Desktop Quality Gate — vérification obligatoire à chaque breakpoint :**
Après le polish mobile, ouvrir mentalement (ou via DevTools) le site à 768px, 1024px, 1280px et 1440px et valider :
- [ ] Aucun texte ne dépasse 72ch de longueur de ligne sur desktop (`max-w-prose` ou `max-w-2xl` sur les blocs de corps)
- [ ] Le hero ne s'étire pas de façon disgracieuse sur grand écran — hauteur min définie (`lg:min-h-[700px]` ou `lg:h-screen`)
- [ ] Les grilles passent correctement de 1 colonne (mobile) aux colonnes prévues en §4quin (`md:grid-cols-2 lg:grid-cols-3`)
- [ ] La barre sticky mobile (`md:hidden`) ne s'affiche PAS sur desktop — vérifier l'attribut de visibilité
- [ ] Les titres `h1` / `h2` ont un scale responsive (`text-4xl lg:text-6xl`) — pas la même taille mobile/desktop
- [ ] Les sections ont un padding vertical généreux sur desktop (`py-16 lg:py-24`) — le mobile-tight ne suffit pas
- [ ] Les images `object-cover` sont bien cadrées sur les ratios desktop (16:9 ou 4:3) — pas coupées de façon étrange
- [ ] Le formulaire de contact passe en 2 colonnes sur desktop (`md:grid-cols-2`) pour les champs courts
- [ ] Le `max-w-7xl mx-auto` (ou équivalent) est appliqué sur tous les conteneurs — jamais de contenu pleine largeur sans contrainte sur ≥ 1280px
- [ ] La navigation desktop (top bar) est propre, visible et fonctionnelle — pas écrasée par le burger mobile

### PHASE 5 — Audit qualité (skill: impeccable → audit)
- Passer le checklist conversion de §10
- Vérifier le JSON-LD de §11
- Vérifier la navigation mobile bottom bar
- Confirmer zéro "Never Do" de §12

### PHASE 6 — Deploy
```
/deploy
```
Vercel. Pas de build tools. Upload statique direct.

---

## 7. SPECS PAR PAGE

### PAGE 1 — index.html (Homepage)

**Section 1 : Header sticky**
- Logo : vérifier `photos/logo.png` en priorité
  - **Si présent** : `<img src="photos/logo.png" alt="Logo {name}" class="h-10 w-auto object-contain">` — ajouter `class="brightness-0 invert"` sur fond sombre
  - **Si absent** : nom de l'entreprise en texte, font display, couleur de marque
- Téléphone `{phone}` cliquable `tel:` au centre ou droite — toujours visible
- Menu desktop : Accueil | Services | Réalisations | À Propos | Contact
- Mobile : header réduit avec logo + téléphone + burger → navigation bottom bar (cf. §9)

**Section 2 : Hero**
- Headline : `{metier_label} à {ville}` + promesse extraite des avis
- Sous-titre : bénéfice clé (délai, disponibilité, proximité)
- CTA principal : "Demander un devis gratuit" (→ #contact ou contact.html)
- CTA secondaire : "Appeler le {phone}" (tel: link)
- Background : meilleure photo chantier en `background-image` + overlay sombre 50%
- Badge flottant : `{rating}⭐ · {n_rev} avis Google`

**Section 3 : Barre de confiance (trust bar)**
- 3 à 4 chips horizontaux : ✓ Devis gratuit | ✓ Artisan local | ✓ {n_rev} avis vérifiés | ✓ Réponse rapide
- Fond légèrement contrasté — jamais identique au hero

**Section 4 : Services (cards)**
- 2 à 4 cartes minimum — extraire les services depuis les types Google et les avis
- Chaque carte : icône Material Symbols + titre service + description 1 ligne + lien "En savoir plus"
- Layout : 2 colonnes sur mobile, 3-4 sur desktop — **jamais 3 colonnes égales** (anti-pattern)

**Section 5 : Réalisations (aperçu)**
- 4 à 6 photos de chantiers en grille asymétrique
- Lien "Voir toutes les réalisations →" → realisations.html

**Section 6 : Avis Google**
- Badge note globale : grand, visible — `{rating}/5 · {n_rev} avis Google`
- Carousel horizontal d'avis (depuis reviews.json)
- Source : "Avis vérifiés Google Maps"

**Section 7 : Zone d'intervention**
- Titre : "Nous intervenons sur {zone_str}"
- Liste des villes principales du département {dept_num}
- Optionnel : carte SVG ou embed Google Maps iframe

**Section 8 : Formulaire de contact (ancre #contact)**
- Champs : Prénom (required) | Téléphone (required) | Email | Type de prestation | Message
- Submit → FormSubmit.co (voir §8)
- Titre : "Devis gratuit & sans engagement"

**Section 9 : Footer**
- Adresse : {address}
- Téléphone : {phone}
- Horaires synthétiques
- Liens : Mentions légales | Politique de confidentialité
- Copyright © {datetime.now().year} {name}

---

### PAGE 2 — services.html

- Hero compact : "Nos Services" + sous-titre métier
- Grille détaillée de tous les services (6–10 minimum)
- Chaque service : icône + titre + description 3–5 lignes + bullet points des prestations incluses
- CTA en fin de page → formulaire devis

---

### PAGE 3 — realisations.html

- Galerie masonry ou grille des photos de chantiers
- Filtre par catégorie si plusieurs types de travaux
- Chaque photo : alt text descriptif (SEO)
- CTA flottant ou sticky → "Demander un devis"

---

### PAGE 4 — a-propos.html

- Histoire de l'entreprise (à construire depuis les données disponibles)
- Équipe (si visible dans les photos)
- Certifications, assurances, garanties (decennale si BTP)
- Valeurs : local, soigné, transparent, réactif
- Section avis (mini — 3 avis)

---

### PAGE 5 — contact.html

- Formulaire complet (mêmes champs que homepage)
- Embed Google Maps : `https://maps.google.com/maps?q={lat},{lng}&output=embed`
- Bloc horaires avec widget statut temps-réel (§3)
- Téléphone + adresse bien visibles

---

### PAGE 6 — mentions-legales.html & politique-confidentialite.html

- Template standard RGPD France
- Remplir : raison sociale = {name}, adresse = {address}
- Politique : collecte formulaire uniquement, pas de cookies tracking, pas de transfert tiers

---

## 8. STACK TECHNIQUE — EXACT, AUCUNE DÉVIATION

```html
<!-- Tailwind CSS CDN — pas de build, pas de purge -->
<script src="https://cdn.tailwindcss.com"></script>

<!-- Alpine.js — réactivité légère, widget horaires, carousel -->
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>

<!-- Material Symbols Outlined — icônes -->
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" rel="stylesheet">

<!-- Google Fonts — à définir après analyse photos (stitch-design-taste) -->
<!-- Fonts : à valider après analyse photos via impeccable. Défaut : Bricolage Grotesque (titres) + Space Grotesk (corps). Bannir : Outfit, DM Sans, Inter, Plus Jakarta Sans, Instrument Sans -->
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600;12..96,700;12..96,800&family=Space+Grotesk:wght@300;400;500;600;700&display=swap" rel="stylesheet">
```

### Configuration Tailwind (dans <script> after CDN)
```javascript
tailwind.config = {{
  theme: {{
    extend: {{
      colors: {{
        brand: {{
          // Remplir avec couleurs extraites des photos
          primary:   '#XXXXXX',  // Couleur principale de la marque
          secondary: '#XXXXXX',  // Couleur secondaire
          dark:      '#0F172A',  // Quasi-noir — jamais #000000
          light:     '#F8FAFC',  // Quasi-blanc fond
        }}
      }},
      fontFamily: {{
        display: ['Bricolage Grotesque', 'sans-serif'],
        body:    ['Space Grotesk', 'sans-serif'],
      }},
      animation: {{
        'fade-up':    'fadeUp 0.5s cubic-bezier(0.23, 1, 0.32, 1) both',
        'fade-in':    'fadeIn 0.4s cubic-bezier(0.23, 1, 0.32, 1) both',
        'slide-left': 'slideLeft 0.4s cubic-bezier(0.23, 1, 0.32, 1) both',
      }},
      keyframes: {{
        fadeUp:    {{ from: {{ opacity: '0', transform: 'translateY(20px)' }}, to: {{ opacity: '1', transform: 'translateY(0)' }} }},
        fadeIn:    {{ from: {{ opacity: '0' }}, to: {{ opacity: '1' }} }},
        slideLeft: {{ from: {{ opacity: '0', transform: 'translateX(20px)' }}, to: {{ opacity: '1', transform: 'translateX(0)' }} }},
      }}
    }}
  }}
}}
```

### FormSubmit.co (formulaire sans backend)
```html
<form action="https://formsubmit.co/{formsubmit_email}" method="POST">
  <input type="hidden" name="_subject" value="Nouveau devis — {name}">
  <input type="hidden" name="_next" value="merci.html">
  <input type="hidden" name="_captcha" value="false">
  <input type="hidden" name="_template" value="table">
  <!-- champs ici -->
  <button type="submit">Envoyer ma demande</button>
</form>
```
⚠️ Remplacer `{formsubmit_email}` par l'email réel du client avant livraison.

---

## 9. COMPOSANTS OBLIGATOIRES

### 9.1 Widget Statut Horaires (Alpine.js)
```html
<div x-data="hoursWidget()" x-init="init()" class="flex items-center gap-2">
  <span class="w-2 h-2 rounded-full" :class="status.open ? 'bg-green-500' : 'bg-red-400'"></span>
  <span class="text-sm font-medium" x-text="status.label"></span>
</div>

<script>
{hours_js}

function hoursWidget() {{
  return {{
    status: {{ open: false, label: 'Chargement…' }},
    init() {{
      this.status = getBusinessStatus();
      setInterval(() => {{ this.status = getBusinessStatus(); }}, 60000);
    }}
  }}
}}
</script>
```

### 9.2 Navigation Mobile Bottom Bar
Sur mobile (< 768px) : remplacer le menu burger par une barre de navigation fixée en bas.
```html
<!-- Desktop nav (hidden on mobile) -->
<nav class="hidden md:flex gap-6">...</nav>

<!-- Mobile bottom bar (hidden on desktop) -->
<nav class="md:hidden fixed bottom-0 left-0 right-0 bg-white border-t z-50 flex">
  <a href="index.html" class="flex-1 flex flex-col items-center py-2 text-xs">
    <span class="material-symbols-outlined text-xl">home</span>
    Accueil
  </a>
  <a href="services.html" class="flex-1 flex flex-col items-center py-2 text-xs">
    <span class="material-symbols-outlined text-xl">build</span>
    Services
  </a>
  <a href="tel:{intl.replace(' ', '') if intl else phone}" class="flex-1 flex flex-col items-center py-2 text-xs text-brand-primary font-bold">
    <span class="material-symbols-outlined text-xl">call</span>
    Appeler
  </a>
  <a href="realisations.html" class="flex-1 flex flex-col items-center py-2 text-xs">
    <span class="material-symbols-outlined text-xl">photo_library</span>
    Travaux
  </a>
  <a href="contact.html" class="flex-1 flex flex-col items-center py-2 text-xs">
    <span class="material-symbols-outlined text-xl">mail</span>
    Devis
  </a>
</nav>
<!-- Padding bottom sur mobile pour compenser la barre fixe -->
<div class="md:hidden h-16"></div>
```

### 9.3 Animations Scroll (Intersection Observer)
```javascript
// Ajouter class .reveal aux éléments à animer à l'apparition
const observer = new IntersectionObserver((entries) => {{
  entries.forEach(entry => {{
    if (entry.isIntersecting) {{
      entry.target.classList.add('animate-fade-up');
      observer.unobserve(entry.target);
    }}
  }});
}}, {{ threshold: 0.1 }});

document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
```

### 9.4 Interactions Emil Design (micro-interactions)
```css
/* Variables d'easing premium */
:root {{
  --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1);
  --ease-spring: cubic-bezier(0.23, 1, 0.32, 1);
}}

/* Press effect sur tous les boutons CTA */
.btn-cta {{
  transition: transform 150ms var(--ease-spring), box-shadow 150ms var(--ease-spring);
}}
.btn-cta:hover  {{ transform: scale(1.02); }}
.btn-cta:active {{ transform: scale(0.97); }}

/* Zoom subtle sur hover photos */
.photo-card {{
  overflow: hidden;
}}
.photo-card img {{
  transition: transform 400ms var(--ease-out-expo);
}}
.photo-card:hover img {{ transform: scale(1.04); }}

/* Touch device guard — désactiver hover sur touch */
@media (hover: none) {{
  .btn-cta:hover  {{ transform: none; }}
  .photo-card:hover img {{ transform: none; }}
}}
```

### 9.5 Carousel Avis (Alpine.js, sans librairie externe)
```html
<div x-data="reviewCarousel()" class="overflow-hidden">
  <div class="flex gap-4 transition-transform duration-500"
       :style="`transform: translateX(-${{current * (100/visible)}}%)`">
    <template x-for="review in reviews" :key="review.name">
      <div class="min-w-[300px] bg-white rounded-2xl p-6 shadow-sm">
        <div class="flex gap-1 mb-3">
          <template x-for="i in review.rating"><span class="text-yellow-400">★</span></template>
        </div>
        <p class="text-gray-700 text-sm leading-relaxed mb-4" x-text="review.text"></p>
        <div class="flex items-center gap-3">
          <img :src="review.image" :alt="review.name" class="w-10 h-10 rounded-full object-cover">
          <div>
            <p class="font-semibold text-sm" x-text="review.name"></p>
            <p class="text-xs text-gray-500" x-text="review.role"></p>
          </div>
        </div>
      </div>
    </template>
  </div>
</div>

<script>
function reviewCarousel() {{
  return {{
    reviews: [],
    current: 0,
    visible: 1,
    async init() {{
      const res = await fetch('reviews.json');
      this.reviews = await res.json();
      this.visible = window.innerWidth >= 768 ? 3 : 1;
      setInterval(() => {{ this.current = (this.current + 1) % Math.max(1, this.reviews.length - this.visible + 1); }}, 4000);
    }}
  }}
}}
</script>
```

### 9.6 JSON-LD LocalBusiness (SEO)
```html
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "name": "{name}",
  "description": "{editorial if editorial else metier_label + ' à ' + ville}",
  "url": "REMPLACER_PAR_URL_FINALE",
  "telephone": "{intl if intl else phone}",
  "address": {{
    "@type": "PostalAddress",
    "streetAddress": "{addr_parts[0].strip() if addr_parts else address}",
    "addressLocality": "{ville}",
    "postalCode": "{code_postal}",
    "addressCountry": "FR"
  }},
  "geo": {{
    "@type": "GeoCoordinates",
    "latitude": {lat if lat else "0"},
    "longitude": {lng if lng else "0"}
  }},
  "aggregateRating": {{
    "@type": "AggregateRating",
    "ratingValue": "{rating}",
    "reviewCount": "{n_rev}",
    "bestRating": "5"
  }},
  "openingHoursSpecification": [],
  "image": "photos/photo_01.jpg",
  "priceRange": "€€",
  "areaServed": "{zone_str}"
}}
</script>
```

### 9.6 Compteur d'avis dynamique (reviews_meta.json)

Charger `reviews_meta.json` au démarrage et mettre à jour tous les éléments marqués.

**Script à inclure dans chaque page (avant `</body>`) :**
```javascript
async function syncReviewsMeta() {{
  try {{
    const res  = await fetch('reviews_meta.json');
    const meta = await res.json();
    document.querySelectorAll('.js-review-count').forEach(el  => el.textContent = meta.count);
    document.querySelectorAll('.js-review-rating').forEach(el => el.textContent = meta.rating);
    document.querySelectorAll('a.js-review-url').forEach(el   => {{ el.href = meta.review_url; }});
  }} catch (e) {{ /* fallback : valeurs HTML statiques inchangées */ }}
}}
document.addEventListener('DOMContentLoaded', syncReviewsMeta);
```

**Règle d'utilisation :** partout où le nombre d'avis ou la note apparaît dans le HTML — badge hero, headline, footer, trust bar — utiliser les classes `js-review-count` et `js-review-rating` avec la valeur statique en fallback :
```html
<!-- Exemple badge hero -->
<span class="..."><span class="js-review-rating">{rating}</span>/5 · <span class="js-review-count">{n_rev}</span> avis Google</span>

<!-- Exemple headline -->
<h2>...<span class="js-review-count">{n_rev}</span> fois plutôt qu'une...</h2>
```

---

### 9.7 Bouton "Laisser un avis Google"

Placer ce bouton dans la section avis ET dans le footer. L'URL est dans `reviews_meta.json`.

```html
<a href="#" class="js-review-url inline-flex items-center gap-3 px-6 py-3 bg-white border border-gray-200 rounded-xl text-gray-800 font-semibold text-sm shadow-sm hover:shadow-md transition-all duration-200 group">
  <!-- Google "G" logo SVG -->
  <svg width="18" height="18" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
    <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
    <path fill="none" d="M0 0h48v48H0z"/>
  </svg>
  Laisser un avis
  <span class="material-symbols-outlined text-base group-hover:translate-x-0.5 transition-transform">arrow_forward</span>
</a>
```

> Ce bouton dirige vers la page Google Maps de l'entreprise pour déposer un avis directement.
> L'URL est chargée dynamiquement depuis `reviews_meta.json` via la classe `js-review-url`.

---

## 10. CHECKLIST CONVERSION — VÉRIFIER AVANT PHASE 5

- [ ] Téléphone `{phone}` présent dans header (visible sans scroll)
- [ ] Téléphone présent dans hero ET footer
- [ ] Badge `{rating}/5 · {n_rev} avis` visible above the fold
- [ ] CTA "Devis gratuit" présent dans les 3 premières sections
- [ ] Formulaire fonctionnel (FormSubmit) avec tous les champs
- [ ] Widget horaires temps-réel opérationnel
- [ ] Navigation mobile bottom bar (pas hamburger)
- [ ] Photos en `loading="lazy"` sauf hero
- [ ] `alt` text sur chaque `<img>` (contient ville + métier)
- [ ] Embed Google Maps sur contact.html
- [ ] Toutes les pages linkées dans la nav

**Desktop (vérifier à ≥ 1024px) :**
- [ ] Longueur de ligne corps de texte ≤ 72ch sur desktop
- [ ] Grilles de services passent bien en multi-colonnes (`md:grid-cols-2 lg:grid-cols-3`)
- [ ] Barre sticky mobile masquée sur desktop (`md:hidden` confirmé)
- [ ] Titres avec scale responsive — `text-4xl lg:text-6xl` ou équivalent
- [ ] Conteneurs avec `max-w-7xl mx-auto` — rien ne s'étire sur 1440px+
- [ ] Hero lisible et bien cadré à toutes les largeurs (768px / 1280px / 1440px)
- [ ] Mentions légales et politique de confidentialité présentes et liées
- [ ] Meta title sur chaque page (format : `Service à Ville — Nom | Devis gratuit`)
- [ ] Meta description sur chaque page (160 chars max)
- [ ] JSON-LD LocalBusiness sur index.html

---

## 11. SEO — RÈGLES STRICTES

### Meta tags par page
```html
<!-- index.html -->
<title>{metier_label} à {ville} — {name} | Devis gratuit</title>
<meta name="description" content="{name}, {metier_singulier} à {ville}. {n_rev} avis vérifiés. Devis gratuit et sans engagement. Appelez le {phone}.">

<!-- services.html -->
<title>Nos Services {metier_label} à {ville} — {name}</title>

<!-- contact.html -->
<title>Contact & Devis — {name} {metier_singulier} à {ville}</title>
```

### Règles H1
- Une seule balise `<h1>` par page
- L'H1 de index.html DOIT contenir "{ville}"
- Exemple : `{metier_label} à {ville} — {name}`

### Images SEO
- `alt` descriptif : `[description de l'image] — {name} {metier_singulier} {ville}`
- `loading="lazy"` sur toutes sauf la photo hero
- `width` et `height` sur chaque img (évite le layout shift)

---

## 12. NEVER DO LIST — INTERDICTIONS ABSOLUES

### Design
- ❌ Polices Inter, Outfit, DM Sans, Plus Jakarta Sans, Instrument Sans (utiliser Space Grotesk, Bricolage Grotesque, Geist, Satoshi, ou Cabinet Grotesk)
- ❌ `#000000` pur noir (utiliser `#0F172A` ou `#18181B`)
- ❌ `#FFFFFF` blanc pur — toujours teinter légèrement vers la couleur de marque
- ❌ Couleur palette générée sans extraire les photos (bleu #3B82F6 par défaut, gris neutre sans teinte)
- ❌ Dégradé neon / glow violet-bleu générique
- ❌ Dégradé texte décoratif (`background-clip: text`) — jamais
- ❌ Bordure colorée latérale sur les cards (side-stripe > 1px) — reécrire avec fond teinté ou rien
- ❌ 3 colonnes égales de cards identiques pour les services (icon + titre + 2 lignes)
- ❌ Hero centré avec texte blanc centré sur overlay sombre générique — pattern le plus usé du web artisan
- ❌ Avis clients uniquement en bas de page — les avis sont un argument de vente, pas un appendice
- ❌ CTA unique "en bas de page" — micro-CTA obligatoire dans chaque section
- ❌ 3 sections denses consécutives sans section aérée entre elles
- ❌ Photo stock de bricoleur souriant en combinaison bleue générique
- ❌ Emojis dans le contenu (sauf si la marque les utilise explicitement)
- ❌ Toutes les sections avec le même espacement — rythme variable obligatoire
- ❌ Palette choisie avant d'analyser les photos — la couleur vient des médias, jamais d'un color picker

### Desktop
- ❌ Même taille de titre sur mobile et desktop — toujours un scale responsive (`text-3xl md:text-5xl lg:text-7xl`)
- ❌ Contenu pleine largeur sans `max-w-*` sur desktop — jamais de texte qui s'étire sur 1440px+
- ❌ Barre sticky mobile sans `md:hidden` — elle apparaît sur desktop et recouvre le contenu
- ❌ Colonnes identiques mobile/desktop — les grilles doivent progresser (`grid-cols-1 md:grid-cols-2 lg:grid-cols-3`)
- ❌ Padding identique mobile/desktop — desktop a besoin de plus d'espace vertical (`py-12 lg:py-24`)
- ❌ Hero à hauteur fixe mobile qui ne s'adapte pas sur grand écran — toujours `min-h-[100dvh] lg:min-h-[700px]` ou similaire

### Code
- ❌ `h-screen` (utiliser `min-h-[100dvh]` — iOS Safari bug)
- ❌ `calc()` avec pourcentages pour les layouts (utiliser CSS Grid)
- ❌ `localStorage` ou `sessionStorage`
- ❌ jQuery ou toute librairie CDN non listée au §8
- ❌ Inline styles pour les couleurs (toujours via classe Tailwind ou CSS var)
- ❌ `target="_blank"` sans `rel="noopener noreferrer"`

### Photos & Médias
- ❌ Insérer une photo sans consulter `photos/manifest.json` — source de vérité obligatoire
- ❌ Affecter une photo à un service qu'elle ne montre pas réellement (ex : radiateur dans "recherche de fuite")
- ❌ Utiliser `logo.png` comme fond d'image, en galerie, ou dans les réalisations — logo uniquement
- ❌ Ignorer le dossier `videos/` sans avoir exécuté `ls videos/` — vérification bloquante
- ❌ Inventer une description de photo sans l'avoir analysée visuellement
- ❌ Laisser une section de service sans photo sous prétexte qu'aucune ne "correspond parfaitement" — utiliser `gallery` ou `chantier_general`

### Compteur d'avis
- ❌ Coder le nombre d'avis ou la note en dur dans le HTML sans la classe `js-review-count` / `js-review-rating`
- ❌ Omettre le bouton "Laisser un avis Google" (§9.7) dans la section avis et le footer

### Contenu
- ❌ Texte lorem ipsum ou placeholder
- ❌ Nom d'entreprise fictif ("Acme", "Dupont & Co", "Jean Martin")
- ❌ Faux numéros ronds (`99.9%`, `500+ clients`, `20 ans d'expérience` si non vérifiable)
- ❌ Slogan générique ("Qualité, expertise et professionnalisme")
- ❌ Avis inventés (utiliser uniquement reviews.json)
- ❌ Formsubmit email placeholder non remplacé en production

### UX
- ❌ Menu hamburger sur mobile (utiliser bottom bar)
- ❌ Scroll indicator animé ("Scroll to explore ↓")
- ❌ Spinner circulaire de chargement
- ❌ Pop-up ou modal qui s'ouvre au chargement de page
- ❌ Autoplay vidéo avec son
- ❌ Liens morts (`href="#"`) sans action réelle

---

## 13. STRUCTURE DE DEPLOY

Arborescence finale attendue pour Vercel :

```
{safe_dirname(name)}/
├── index.html
├── services.html
├── realisations.html
├── a-propos.html
├── contact.html
├── merci.html                    ← page de confirmation formulaire
├── mentions-legales.html
├── politique-confidentialite.html
├── reviews.json
├── photos/
│   ├── photo_01.jpg   ← API Google Places (générées automatiquement)
│   ├── photo_02.jpg
│   ├── ...
│   └── photo_XX.jpg   ← photos ajoutées manuellement (toutes incluses)
└── videos/            ← présent si des vidéos ont été trouvées ou ajoutées manuellement
    ├── video_01.mp4
    └── video_XX.mp4
```

Commande deploy depuis Claude Code : `/deploy`
Vercel détecte automatiquement le site statique — aucune config nécessaire.

---

_CLAUDE.md généré par company_scraper_bis.py — {datetime.now().strftime("%d/%m/%Y")} — {name}_
"""

    # Écrire le fichier
    out_path = out_dir / "CLAUDE.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"    CLAUDE.md      — {len(doc.splitlines())} lignes")
    return doc


# ─────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────────────────────────

def process(place_id: str, api_key: str, force: bool = False):
    details = api_details(place_id, api_key)
    if not details:
        return None

    name      = details.get("name", place_id)
    out_dir   = OUTPUT_DIR / safe_dirname(name)
    photo_dir = out_dir / "photos"
    video_dir = out_dir / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)

    if out_dir.exists() and not force:
        print(f"  Deja traite : {out_dir}  (--force pour ecraser)")
        return out_dir

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  {name}")
    print(f"  {details.get('formatted_address','')[:70]}")
    print(f"  {details.get('rating',0)}/5  ({details.get('user_ratings_total',0)} avis)  |  Site : {details.get('website','aucun')}")

    # 1. Photos API
    api_photo_refs = details.get("photos", [])
    print(f"\n  [1/4] Photos API ({len(api_photo_refs)} references)...")
    api_photos = api_download_photos(api_photo_refs, photo_dir, api_key)

    # 2. Scraping Maps — avis (photos toujours via API)
    maps_url = details.get("url", "")
    scraped_reviews = []
    if maps_url:
        print(f"\n  [2/4] Scraping Google Maps (avis)...")
        _, scraped_reviews = scrape_maps(maps_url, photo_dir, len(api_photos))
    else:
        print(f"\n  [2/4] URL Maps absente — scraping ignore")

    all_photos = api_photos  # Photos supplementaires : deposer dans photos/ manuellement

    # 3. Fusion avis
    print(f"\n  [3/4] Fusion et deduplication des avis...")
    all_reviews = merge_reviews(details.get("reviews", []), scraped_reviews)
    print(f"    API: {len(details.get('reviews',[]))}  Maps: {len(scraped_reviews)}  Final apres dedup: {len(all_reviews)}")

    # 5. Génération des fichiers
    print(f"\n  [4/4] Generation fichiers workflow Claude Code...")

    with open(out_dir / "profil.json", "w", encoding="utf-8") as f:
        export = {**details, "scraped_reviews": scraped_reviews}
        json.dump(export, f, ensure_ascii=False, indent=2)

    with open(out_dir / "profil.txt", "w", encoding="utf-8") as f:
        f.write(build_profile_txt(details, all_reviews, all_photos))

    # reviews.json — format frontend
    exported_reviews = generate_reviews_json(all_reviews, out_dir)

    # reviews_meta.json — compteur dynamique + URL dépôt d'avis Google
    generate_reviews_meta_json(details, out_dir)

    # PRODUCT.md — pour le skill impeccable
    product_md_content = build_product_md(details, all_photos, out_dir)
    with open(out_dir / "PRODUCT.md", "w", encoding="utf-8") as f:
        f.write(product_md_content)
    print(f"    PRODUCT.md     — contexte metier pour skill impeccable")

    # CLAUDE.md — conducteur de build complet (inclut section vidéos)
    build_claude_md(details, all_reviews, all_photos, out_dir)

    # Resume
    print(f"\n  {'=' * 55}")
    print(f"  ✓  {name}")
    print(f"  {'=' * 55}")
    print(f"    Photos         : {len(all_photos)} (API Google Places)")
    print(f"    Avis           : {len(all_reviews)} uniques exportes dans reviews.json")
    print(f"    Videos         : dossier videos/ cree — deposer les fichiers dedans")
    print(f"")
    print(f"  Fichiers generes :")
    print(f"    profil.json         — donnees brutes API")
    print(f"    profil.txt          — fiche lisible")
    print(f"    reviews.json        — avis format frontend")
    print(f"    PRODUCT.md          — contexte pour skill impeccable")
    print(f"    CLAUDE.md           — conducteur build Claude Code ✦")
    print(f"    videos/             — dossier pret (ajouter video_01.mp4, video_02.mp4, etc.)")
    print(f"")
    print(f"  ► Medias supplementaires :")
    print(f"    Photos  → {photo_dir}/  (nommer photo_11.jpg, photo_12.jpg, etc.)")
    print(f"    Videos  → {video_dir}/  (nommer video_01.mp4, video_02.mp4, etc.)")
    print(f"    CLAUDE.md instruite Claude Code de tout decouvrir via ls.")
    print(f"")
    print(f"  Workflow :")
    print(f"    1. [Optionnel] Ajouter photos/videos dans le dossier profil")
    print(f"    2. Ouvre Claude Code")
    print(f"    3. Definis le workspace sur :  {out_dir}/")
    print(f"    4. Claude lit CLAUDE.md automatiquement au demarrage")
    print(f"    5. Un seul prompt suffit : 'Build the site.'")
    print(f"    6. /deploy → Vercel")

    return out_dir


# ─────────────────────────────────────────────────────────────
# POINT D'ENTREE
# ─────────────────────────────────────────────────────────────


# ─────────────────────────────────
# POINT D'ENTREE
# ─────────────────────────────────


# ─────────────────────────────────────────────────────────────
# POINT D'ENTREE
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Company Scraper — profil complet Google Places")
    parser.add_argument("--key",      help="Cle API Google Places")
    parser.add_argument("--place_id", help="place_id depuis le CSV prospects")
    parser.add_argument("--nom",      help="Nom entreprise")
    parser.add_argument("--ville",    default="", help="Ville (avec --nom)")
    parser.add_argument("--csv",      help="CSV prospects — traitement par lot")
    parser.add_argument("--priorite", help="Filtre priorite : CHAUD | TIEDE | FROID")
    parser.add_argument("--force",    action="store_true")
    args = parser.parse_args()

    api_key = args.key or os.environ.get("GOOGLE_PLACES_API_KEY") or ""
    if not api_key:
        api_key = input("\nCle API Google Places : ").strip()
        if not api_key:
            sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    if args.csv:
        with open(args.csv, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        if args.priorite:
            rows = [r for r in rows if r.get("priorite","").upper() == args.priorite.upper()]
        ok = 0
        for row in rows:
            pid = row.get("place_id","").strip()
            if not pid:
                continue
            result = process(pid, api_key, force=args.force)
            if result:
                ok += 1
        print(f"\n  {ok}/{len(rows)} profils generes dans {OUTPUT_DIR}/")

    elif args.place_id:
        process(args.place_id, api_key, force=args.force)

    elif args.nom:
        pid = api_search(args.nom, args.ville, api_key)
        if pid:
            process(pid, api_key, force=args.force)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
