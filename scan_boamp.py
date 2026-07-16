#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCAN BOAMP — genere le fichier de consultations pour basecore.fr.

Autonome : aucune dependance externe (urllib de la lib standard). Concu pour
tourner en boucle sur GitHub Actions (cron) sans rien installer.

Sortie .json => JSON pur (lu en direct par la page via fetch).
"""

import json
import sys
import base64
import urllib.request
import urllib.parse
import datetime as dt
from pathlib import Path

API = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"

SECTEURS = {
    "Communication": [
        "supports de communication", "impression", "imprime", "imprimes", "flyer",
        "depliant", "brochure", "affiche", "affiches", "plaquette", "faconnage",
    ],
    "Edition": [
        "magazine", "bulletin municipal", "journal", "tabloid", "tabloide", "livret",
        "edition", "catalogue", "revue", "periodique",
    ],
    "Signaletique": [
        "signaletique", "panneau", "panneaux", "banderole", "kakemono",
        "enseigne", "adhesif", "akilux", "dibond", "totem", "PLV", "affichage",
    ],
    "Objets & textiles": [
        "objets publicitaires", "goodies", "objet promotionnel", "textile", "vetement",
        "t-shirt", "tote bag", "magnet", "porte-cles", "cadeaux",
    ],
}

BRUIT = [
    "photocopieur", "copieur", "imprimante", "traceur", "reprographie", "parc d'impression",
    "location et maintenance", "logiciel", "maintenance de", "impression 3d",
    "mobilier urbain", "cartouche", "toner",
]


def region_from_dep(dep):
    if not dep:
        return "France"
    d = str(dep)[:2].zfill(2)
    grandes = {
        "Auvergne-Rhone-Alpes": {"01","03","07","15","26","38","42","43","63","69","73","74"},
        "Bourgogne-Franche-Comte": {"21","25","39","58","70","71","89","90"},
        "Bretagne": {"22","29","35","56"},
        "Centre-Val de Loire": {"18","28","36","37","41","45"},
        "Corse": {"2A","2B","20"},
        "Grand Est": {"08","10","51","52","54","55","57","67","68","88"},
        "Hauts-de-France": {"02","59","60","62","80"},
        "Ile-de-France": {"75","77","78","91","92","93","94","95"},
        "Normandie": {"14","27","50","61","76"},
        "Nouvelle-Aquitaine": {"16","17","19","23","24","33","40","47","64","79","86","87"},
        "Occitanie": {"09","11","12","30","31","32","34","46","48","65","66","81","82"},
        "Pays de la Loire": {"44","49","53","72","85"},
        "PACA": {"04","05","06","13","83","84"},
        "Outre-mer": {"97","98"},
    }
    for reg, deps in grandes.items():
        if d in deps:
            return reg + " / " + d
    return "France / " + d


def eur_json_query(where, limit=100):
    params = {
        "limit": str(limit),
        "select": "idweb,nomacheteur,objet,dateparution,datelimitereponse,type_marche,descripteur_libelle,code_departement_prestation,url_avis",
        "order_by": "datelimitereponse asc",
        "where": where,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "basecore-scan/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def detect_sector(objet, descripteurs):
    text = (objet or "").lower() + " " + " ".join(descripteurs or []).lower()
    for sec, kws in SECTEURS.items():
        if any(k.lower() in text for k in kws):
            return sec
    return None


def is_noise(objet, descripteurs):
    text = (objet or "").lower() + " " + " ".join(descripteurs or []).lower()
    return any(b.lower() in text for b in BRUIT)


def payload_for(rec, lots=None):
    p = {
        "ref": rec["idweb"],
        "acheteur": rec.get("nomacheteur", ""),
        "objet": rec.get("objet", ""),
        "datelimite": (rec.get("datelimitereponse") or "")[:10],
        "lots": lots or [{
            "lot": 1,
            "designation": (rec.get("objet") or "Prestation")[:90],
            "format": "A4", "grammage": 135, "pages": 1, "quantite": 5000,
        }],
    }
    raw = json.dumps(p, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def vague_label(sec):
    return {
        "Communication": "Consultation - supports de communication",
        "Edition": "Consultation - edition periodique",
        "Signaletique": "Consultation - signaletique & affichage",
        "Objets & textiles": "Consultation - objets & textiles personnalises",
    }.get(sec, "Consultation - prestation")


def short_id(rec, seq):
    d = (rec.get("dateparution") or "2026-01-01").replace("-", "")[2:6]
    letter = chr(65 + (seq % 26))
    return "OPE-" + d + "-" + letter


def load_curated():
    p = Path(__file__).with_name("curated_opportunites.json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("opportunites-data.json")

    curated = load_curated()
    seen = {}
    kw = []
    for kws in SECTEURS.values():
        kw += kws
    kw = list(dict.fromkeys(kw))
    clauses = ['search(objet,"' + k + '")' for k in kw]
    clauses.append('search(descripteur_libelle,"imprimerie")')
    where = "(" + " or ".join(clauses) + ") and datelimitereponse > now()"

    try:
        data = eur_json_query(where, limit=100)
    except Exception as e:
        print("Erreur API BOAMP:", e, file=sys.stderr)
        sys.exit(1)

    for rec in data.get("results", []):
        objet = rec.get("objet", "")
        desc = rec.get("descripteur_libelle") or []
        if is_noise(objet, desc):
            continue
        sec = detect_sector(objet, desc)
        if not sec:
            continue
        idw = rec["idweb"]
        if idw in seen:
            continue
        seen[idw] = (rec, sec)

    items = []
    for seq, (idw, (rec, sec)) in enumerate(sorted(seen.items(),
                key=lambda kv: kv[1][0].get("datelimitereponse") or "")):
        lots = curated.get(idw)
        items.append({
            "id": short_id(rec, seq),
            "label": vague_label(sec),
            "categorie": sec,
            "region": region_from_dep(rec.get("code_departement_prestation")),
            "echeance": (rec.get("datelimitereponse") or "")[:10],
            "nb_lots": len(lots) if lots else 1,
            "payload": payload_for(rec, lots),
        })

    payload_out = {
        "maj": dt.datetime.utcnow().isoformat() + "Z",
        "count": len(items),
        "opportunites": items,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload_out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("OK ->", out, "(", len(items), "consultations )")


if __name__ == "__main__":
    main()
