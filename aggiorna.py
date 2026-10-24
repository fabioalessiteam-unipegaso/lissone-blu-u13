#!/usr/bin/env python3
"""Scarica il calendario FIP (PDF), ne legge partite e risultati e rigenera index.html.

Uso:  python aggiorna.py            (scarica da FIP)
      python aggiorna.py --pdf file.pdf   (usa un PDF già scaricato, per prove)
"""
import json, re, subprocess, sys, shutil, urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pdfplumber

QUI = Path(__file__).parent
URL = ("https://backend.fip.it/api/v1/calendario.pdf?comitato_codice=PBG&regione_codice=LO&sesso=M"
       "&codice_campionato=U13S/M&codice_fase=1&codice_girone=85948&codice_andata_ritorno=1&giornata=2")
MESI = ["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto",
        "settembre","ottobre","novembre","dicembre"]

# ---------- lettura del PDF ----------
RE_HEAD = re.compile(r"^(\d+)\s+Giornata\s*-\s*(andata|ritorno)\s+del\s+(\d{4}-\d{2}-\d{2})")
RE_SCORE = re.compile(r"^(\d+)\s*-\s*(\d+)$")
RE_WHEN = re.compile(r"^(\d{2})/(\d{2})/(\d{2})\s*-\s*(\d{2}:\d{2})$")

def nome_squadra(s):
    return re.sub(r"^PALL\.\s+", "", s.strip())

def bel_campo(v):
    """'Palazzetto - Via Caravaggio snc LISSONE (MB)' -> 'CASA' oppure testo leggibile."""
    v = re.sub(r"\s+", " ", v).strip()
    if "Palazzetto" in v and "Caravaggio" in v:
        return "CASA"
    v = v.replace("Marmolada(ang.via monte am", "Marmolada (ang. via Monte Amiata)")
    v = re.sub(r"\s*\(MB\)\s*$", "", v)
    m = re.match(r"^(.*?)\s+((?:[A-ZÀ-Ý'.]+\s?)+)$", v)
    luogo, citta = (m.group(1), m.group(2).strip()) if m else (v, "")
    luogo = luogo.replace(", snc", " snc").replace(" - ", " · ")
    citta = citta.title().replace(" Al ", " al ")
    return f"{luogo}, {citta}" if citta else luogo

def leggi_pdf(percorso):
    gare, giornate = [], {}
    corrente = None      # (andata/ritorno, data)
    ultimo = None
    n_andata = 0
    with pdfplumber.open(percorso) as pdf:
        for pagina in pdf.pages:
            W = pagina.width
            x_num, x_b, x_dx = W * 0.10, W * 0.45, W * 0.80
            parole = pagina.extract_words()
            righe = {}
            for w in parole:
                righe.setdefault(round(w["top"] / 2.5), []).append(w)
            for _, ws in sorted(righe.items()):
                ws.sort(key=lambda w: w["x0"])
                testo = " ".join(w["text"] for w in ws)
                m = RE_HEAD.match(testo)
                if m:
                    k, tipo, data = int(m.group(1)), m.group(2), m.group(3)
                    if tipo == "andata":
                        n_andata = max(n_andata, k)
                    g = k if tipo == "andata" else n_andata + k
                    giornate[g] = data
                    corrente = g
                    ultimo = None
                    continue
                if re.match(r"^pag\.", testo):
                    continue
                numeri = [w["text"] for w in ws if w["x0"] < x_num and re.fullmatch(r"\d{6}", w["text"])]
                colA = " ".join(w["text"] for w in ws if x_num <= w["x0"] < x_b)
                colB = " ".join(w["text"] for w in ws if x_b <= w["x0"] < x_dx)
                dx = " ".join(w["text"] for w in ws if w["x0"] >= x_dx)
                if corrente and colA and colB and dx and (RE_SCORE.match(dx) or RE_WHEN.match(dx)):
                    gara = {"g": corrente, "n": int(numeri[0]) if numeri else None,
                            "a": nome_squadra(colA), "b": nome_squadra(colB),
                            "d": giornate[corrente], "t": None, "pa": None, "pb": None, "v": None}
                    s, q = RE_SCORE.match(dx), RE_WHEN.match(dx)
                    if s:
                        gara["pa"], gara["pb"] = int(s.group(1)), int(s.group(2))
                    else:
                        gara["d"] = f"20{q.group(3)}-{q.group(2)}-{q.group(1)}"
                        gara["t"] = q.group(4)
                    gare.append(gara)
                    ultimo = gara
                    continue
                if ultimo is None:
                    continue
                if numeri and ultimo["n"] is None:
                    ultimo["n"] = int(numeri[0])
                luogo = " ".join(w["text"] for w in ws if w["x0"] >= x_num)
                if luogo and not luogo.startswith("(") and ultimo["v"] is None:
                    ultimo["v"] = bel_campo(luogo)
    return gare, giornate

# ---------- unione con i dati precedenti ----------
def unisci(nuove, vecchie):
    prec = {g["n"]: g for g in vecchie}
    for g in nuove:
        p = prec.get(g["n"], {})
        for campo in ("t", "v"):
            if g[campo] is None:
                g[campo] = p.get(campo)
    return nuove

def main():
    pdf_path = None
    if "--pdf" in sys.argv:
        pdf_path = sys.argv[sys.argv.index("--pdf") + 1]
    else:
        pdf_path = QUI / "calendario_fip.pdf"
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (aggiornamento calendario)"})
        with urllib.request.urlopen(req, timeout=60) as r:
            pdf_path.write_bytes(r.read())

    nuove, giornate = leggi_pdf(pdf_path)
    cache_file = QUI / "dati.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {"gare": [], "aggiornato": ""}

    # controlli di sicurezza: se la lettura non torna, non tocchiamo il sito
    if len(nuove) < 10 or any(g["n"] is None for g in nuove):
        sys.exit(f"Lettura del PDF non riuscita (gare lette: {len(nuove)}). Il sito non è stato modificato.")
    if cache["gare"] and len(nuove) < len(cache["gare"]):
        sys.exit("Il PDF contiene meno gare di prima. Il sito non è stato modificato.")

    nuove = unisci(nuove, cache["gare"])
    nuove.sort(key=lambda g: (g["g"], (g["d"] or "") + (g["t"] or ""), g["n"]))
    if nuove == cache["gare"] and (QUI / "index.html").exists():
        print("Nessuna novità.")
        return

    ora = datetime.now(ZoneInfo("Europe/Rome"))
    aggiornato = f"{ora.day} {MESI[ora.month - 1]} {ora.year}, {ora:%H:%M}"
    html = (QUI / "template.html").read_text(encoding="utf-8")
    html = html.replace("/*DATI_GARE*/[]", json.dumps(nuove, ensure_ascii=False, indent=1).replace("</", "<\\/"))
    html = html.replace("/*DATI_GIORNATE*/{}", json.dumps({str(k): v for k, v in giornate.items()}))
    html = html.replace('/*DATI_AGGIORNATO*/""', json.dumps(aggiornato, ensure_ascii=False))
    (QUI / "build.html").write_text(html, encoding="utf-8")

    # pre-generazione del contenuto (richiede Node + jsdom); se manca, si usa la pagina così com'è
    if shutil.which("node") and (QUI / "node_modules" / "jsdom").exists():
        subprocess.run(["node", str(QUI / "prerender.js"), str(QUI / "build.html"), str(QUI / "index.html")], check=True)
    else:
        shutil.copy(QUI / "build.html", QUI / "index.html")

    cache_file.write_text(json.dumps({"gare": nuove, "aggiornato": aggiornato}, ensure_ascii=False, indent=1), encoding="utf-8")
    giocate = sum(1 for g in nuove if g["pa"] is not None)
    print(f"Pagina aggiornata: {len(nuove)} gare, {giocate} giocate.")

if __name__ == "__main__":
    main()
