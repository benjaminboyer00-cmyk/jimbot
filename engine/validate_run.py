#!/usr/bin/env python3
"""Validation hors échantillon des pondérations.

Répond à la question qui conditionne toutes les autres : les coefficients
mesurés survivent-ils sur des données jamais vues ?

Usage :
    python engine/validate_run.py                # utilise data/probe_raw.json
    python engine/validate_run.py --horizon 24
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jimbot import validation as V  # noqa: E402
from jimbot.config import DATA_DIR  # noqa: E402
from jimbot.store import now_iso, read, write  # noqa: E402

log = logging.getLogger("jimbot.validation")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validation hors échantillon")
    parser.add_argument("--horizon", type=int, default=V.HORIZON)
    parser.add_argument("--blocs", type=int, default=5)
    parser.add_argument("--train-frac", type=float, default=0.6)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    brut = read("probe_raw", None)
    if not brut or not brut.get("rows"):
        log.error("data/probe_raw.json introuvable — lancez d'abord "
                  "`python engine/probe_run.py`")
        return 1
    rows = brut["rows"]
    if "t" not in rows[0]:
        log.error("observations sans horodatage — relancez la sonde")
        return 1

    log.info("%d observations, horizon %d bougies\n", len(rows), args.horizon)

    calendaire = V.holdout_calendaire(rows, args.train_frac, args.horizon)
    par_actif = V.holdout_par_actif(rows, args.train_frac, args.horizon)
    wf = V.walk_forward(rows, args.blocs, args.horizon)
    ref = V.baseline_naive(rows, args.horizon)

    write("validation", {
        "generated_at": now_iso(),
        "horizon": args.horizon,
        "holdout_calendaire": calendaire,
        "holdout_par_actif": par_actif,
        "walk_forward": wf,
        "reference_retour_a_la_moyenne": ref,
    })

    def bloc(titre: str, res: dict) -> None:
        print("=" * 76)
        print(f"  {titre}")
        print("=" * 76)
        if "note" in res:
            print(f"  {res['note']}")
            return
        tr, te = res.get("train", {}), res.get("test", {})
        if "coupure" in res:
            print(f"  coupure : {res['coupure'][:10]}")
        print(f"  {'':<22} {'obs':>7} {'IC':>9} {'t':>8}")
        for nom, d in (("dans l'échantillon", tr), ("HORS ÉCHANTILLON", te)):
            ic = d.get("ic")
            print(f"  {nom:<22} {d.get('observations', 0):>7} "
                  f"{(f'{ic:+.4f}' if ic is not None else '—'):>9} "
                  f"{(f'{d.get(chr(116), 0):+.2f}' if ic is not None else '—'):>8}"
                  f"{'  *' if d.get('significatif') else ''}")
        poids = res.get("poids_ajustes", {})
        retenus = {k: v for k, v in poids.items() if v != 0}
        print(f"  poids ajustés : {retenus if retenus else 'aucun facteur significatif'}")
        print()

    print()
    bloc("1. DÉCOUPAGE CALENDAIRE STRICT", calendaire)
    bloc("2. DÉCOUPAGE PAR ACTIF", par_actif)

    print("=" * 76)
    print("  3. WALK-FORWARD GLISSANT")
    print("=" * 76)
    if "note" in wf:
        print(f"  {wf['note']}")
    else:
        print(f"  {'bloc':<6} {'période de test':<26} {'IS':>9} {'HORS ÉCH.':>11} {'t':>8}")
        for p in wf["plis"]:
            ins = p["in_sample"].get("ic")
            oos = p["hors_echantillon"].get("ic")
            tt = p["hors_echantillon"].get("t", 0)
            periode = f"{p['periode_test'][0]} → {p['periode_test'][1]}"
            print(f"  {p['bloc']:<6} {periode:<26} "
                  f"{(f'{ins:+.4f}' if ins is not None else '—'):>9} "
                  f"{(f'{oos:+.4f}' if oos is not None else '—'):>11} "
                  f"{(f'{tt:+.2f}' if oos is not None else '—'):>8}"
                  f"{'  *' if p['hors_echantillon'].get('significatif') else ''}")
        r = wf.get("resume", {})
        if r:
            print()
            print(f"  IC moyen hors échantillon : {r['ic_moyen_hors_echantillon']:+.4f}")
            print(f"  blocs positifs            : {r['blocs_positifs']}")
            print(f"  écart-type entre blocs    : {r['ecart_type']:.4f}")

        st = wf.get("stabilite_des_poids", {})
        if "par_facteur" in st:
            print()
            print("  STABILITÉ DES POIDS")
            print(f"  {'facteur':<16} {'moyenne':>9} {'écart-type':>11}  signe  valeurs")
            for nom, d in sorted(st["par_facteur"].items(),
                                 key=lambda kv: -abs(kv[1]["moyenne"])):
                stable = "stable" if d["signe_stable"] else "CHANGE"
                print(f"  {nom:<16} {d['moyenne']:>+9.4f} {d['ecart_type']:>11.4f}  "
                      f"{stable:<6} {d['valeurs']}")
            if "correlation_entre_blocs" in st:
                print(f"\n  corrélation entre blocs consécutifs : "
                      f"{st['correlation_entre_blocs']:+.3f}")
    print()
    print("=" * 76)
    print("  4. RÉFÉRENCE — retour à la moyenne seul, sans ajustement")
    print("=" * 76)
    ic = ref.get("ic")
    print(f"  IC {ic:+.4f}  t={ref.get('t', 0):+.2f}  sur {ref.get('observations', 0)} obs"
          if ic is not None else f"  {ref.get('note')}")
    print("=" * 76)
    log.info("\nrapport écrit dans %s", DATA_DIR / "validation.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
