#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SCAN BOAMP — genere le fichier de consultations pour basecore.fr.
Extrait les vrais noms de lots (eFORMS ou schema Boamp legacy). Sortie JSON."""

import json
import sys
import base64
import re
import urllib.request
import urllib.parse
import datetime as dt
from pathlib import Path

API = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"

SECTEURS = {
    "Communication": ["supports de communication", "impression", "imprime", "imprimes", "flyer", "depliant", "brochure", "affiche", "affiches", "plaquette", "faconnage"],
    "Edition": ["magazine", "bulletin municipal", "journal", "tabloid", "tabloide", "livret", "edition", "catalogue", "revue", "periodique"],
    "Signaletique": ["signaletique", "banderole", "kakemono", "enseigne", "adhesif", "akilux", "dibond", "totem", "PLV"],
    "Objets & textiles": ["objets publicitaires", "goodies", "objet promotionnel", "textile", "vetement", "t-shirt", "tote bag", "magnet", "porte-cles", "cadeaux"],
}

BRUIT = ["photocopieur", "copieur", "imprimante", "traceur", "reprographie", "parc d'impression", "location et maintenance", "logiciel", "maintenance de", "impression 3d", "mobilier urbain", "cartouche", "toner", "panneau radiant", "rayonnement", "chauffage"]


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
        "select": "idweb,nomacheteur,objet,dateparution,datelimitereponse,type_marche,descripteur_libelle,code_departement_prestation,donnees,url_avis",
        "order_by": "datelimitereponse asc",
        "where": where,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "basecore-scan/1.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
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


def _text_of(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        t = v.get("#text")
        if isinstance(t, str):
            return t.strip()
    return None


def guess_format(name):
    n = (name or "").lower()
    for f in ["a0", "a1", "a2", "a3", "a4", "a5", "a6"]:
        if re.search(r"\b" + f + r"\b", n):
            return f.upper()
    if "banderole" in n or "kakemono" in n or "bache" in n:
        return "A0"
    if "magnet" in n or "carte" in n or "billet" in n:
        return "10x15"
    if "enveloppe" in n or "depliant" in n or "3 volets" in n:
        return "DL"
    if "affiche" in n:
        return "A2"
    if "flyer" in n or "invitation" in n:
        return "A5"
    if "magazine" in n or "livret" in n or "brochure" in n or "catalogue" in n or "journal" in n:
        return "A4"
    if "tabloid" in n or "tabloide" in n:
        return "Tabloid"
    return "A4"


def _lot_defaults(name):
    fmt = guess_format(name)
    pages = 1
    n = (name or "").lower()
    if any(w in n for w in ["magazine", "livret", "brochure", "catalogue", "journal", "revue"]):
        pages = 16
    if "depliant" in n or "3 volets" in n:
        pages = 6
    return {"format": fmt, "grammage": 135, "pages": pages, "quantite": 1000}


def extract_lots(donnees_str):
    if not donnees_str:
        return None
    try:
        d = json.loads(donnees_str)
    except Exception:
        return None
    names = []
    if isinstance(d, dict) and "EFORMS" in d:
        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "cac:ProcurementProjectLot":
                        entries = v if isinstance(v, list) else [v]
                        for e in entries:
                            proj = e.get("cac:ProcurementProject", {}) if isinstance(e, dict) else {}
                            nm = _text_of(proj.get("cbc:Name")) if isinstance(proj, dict) else None
                            if nm:
                                names.append(nm)
                    walk(v)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(d)
    elif isinstance(d, dict):
        objet = d.get("OBJET", {})
        if isinstance(objet, dict):
            L = objet.get("LOTS", {})
            L = L.get("LOT") if isinstance(L, dict) else None
            if L:
                entries = L if isinstance(L, list) else [L]
                for e in entries:
                    if isinstance(e, dict):
                        nm = _text_of(e.get("INTITULE") or e.get("DESCRIPTION") or e.get("TITRE"))
                        if nm:
                            names.append(nm)
    clean = []
    seen = set()
    for nm in names:
        nm = re.sub(r"\s+", " ", nm).strip()
        if len(nm) < 3:
            continue
        key = nm.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        clean.append(nm[:90])
    if not clean:
        return None
    return [{"lot": i + 1, "designation": nm, **_lot_defaults(nm)} for i, nm in enumerate(clean)]


def payload_for(rec, lots):
    p = {"ref": rec["idweb"], "acheteur": rec.get("nomacheteur", ""), "objet": rec.get("objet", ""),
         "datelimite": (rec.get("datelimitereponse") or "")[:10], "lots": lots}
    raw = json.dumps(p, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def vague_label(sec):
    return {"Communication": "Consultation - supports de communication",
            "Edition": "Consultation - edition periodique",
            "Signaletique": "Consultation - signaletique & affichage",
            "Objets & textiles": "Consultation - objets & textiles personnalises"}.get(sec, "Consultation - prestation")


def short_id(rec, seq):
    d = (rec.get("dateparution") or "2026-01-01").replace("-", "")[2:6]
    return "OPE-" + d + "-" + chr(65 + (seq % 26))


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
    for seq, (idw, (rec, sec)) in enumerate(sorted(seen.items(), key=lambda kv: kv[1][0].get("datelimitereponse") or "")):
        lots = curated.get(idw)
        source = "curee" if lots else None
        if not lots:
            lots = extract_lots(rec.get("donnees"))
            source = "detail" if lots else None
        if not lots:
            lots = [{"lot": 1, "designation": (rec.get("objet") or "Prestation")[:90], "format": "A4", "grammage": 135, "pages": 1, "quantite": 1000}]
            source = "generique"
        items.append({"id": short_id(rec, seq), "label": vague_label(sec), "categorie": sec,
                      "region": region_from_dep(rec.get("code_departement_prestation")),
                      "echeance": (rec.get("datelimitereponse") or "")[:10],
                      "nb_lots": len(lots), "source_lots": source, "payload": payload_for(rec, lots)})
    payload_out = {"maj": dt.datetime.utcnow().isoformat() + "Z", "count": len(items), "opportunites": items}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload_out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    n_detail = sum(1 for it in items if it["source_lots"] == "detail")
    n_curee = sum(1 for it in items if it["source_lots"] == "curee")
    print("OK ->", out, ":", len(items), "consultations (", n_curee, "curees,", n_detail, "detaillees )")


if __name__ == "__main__":
    main()
