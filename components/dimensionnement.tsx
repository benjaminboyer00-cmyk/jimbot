"use client";

import { useEffect, useState } from "react";

import { fmtNum, fmtPrice } from "@/lib/format";
import type { Signal } from "@/lib/data";
import { conviction, taillePosition, type ReglagesRisque } from "@/lib/sizing";

const CLE_CAPITAL = "jimbot-capital";
const CLE_RISQUE = "jimbot-risque";

const CAPITAL_DEFAUT = 10000;
const RISQUE_DEFAUT = 1;

/** Ligne à dimensionner : un signal du moteur, ou une saisie libre. */
type Plan = {
  cle: string;
  symbol: string;
  label: string;
  klass: string;
  score: number;
  direction: "long" | "short";
  entree: number;
  stop: number;
  objectif: number;
};

/**
 * Dimensionnement de position.
 *
 * C'est la seule partie interactive du site, et c'est justifié : le calcul
 * dépend du capital du lecteur, que le serveur ne connaît pas et ne doit pas
 * connaître. Les deux valeurs saisies restent dans le navigateur — rien n'est
 * envoyé, rien n'est journalisé.
 *
 * Le tableau applique exactement la règle du moteur (`engine/jimbot/risk.py`,
 * dont les préréglages arrivent par l'instantané) : le risque nominal saisi
 * est modulé par la conviction, puis la position est plafonnée en exposition
 * pour la classe d'actif. Un dimensionnement plus généreux affiché ici
 * décrirait une autre stratégie que celle que le site mesure par ailleurs.
 */
export function Dimensionnement({
  signaux,
  reglages,
  seuilSignal,
  horsSeuil = false,
}: {
  signaux: Signal[];
  reglages: ReglagesRisque;
  seuilSignal: number;
  /** Les lignes proposées viennent de la liste de surveillance, pas des
   *  configurations retenues : elles n'atteignent pas le seuil de conviction.
   *  Le tableau doit le dire, sans quoi il ressemble à une recommandation. */
  horsSeuil?: boolean;
}) {
  // Les champs gardent la saisie telle quelle, pas le nombre qu'on en tire.
  // Sinon vider le champ pour retaper y réécrit aussitôt un « 0 », et l'on ne
  // peut plus corriger un montant sans tout sélectionner d'abord.
  const [capitalSaisi, setCapitalSaisi] = useState(String(CAPITAL_DEFAUT));
  const [risqueSaisi, setRisqueSaisi] = useState(String(RISQUE_DEFAUT));
  const [monte, setMonte] = useState(false);

  const capital = nombre(capitalSaisi, CAPITAL_DEFAUT);
  const risque = nombre(risqueSaisi, RISQUE_DEFAUT);

  // Saisie libre : le site doit rester utile un jour sans signal, et permettre
  // de dimensionner un plan qu'on tient d'ailleurs.
  const [libreEntree, setLibreEntree] = useState("");
  const [libreStop, setLibreStop] = useState("");

  useEffect(() => {
    setMonte(true);
    try {
      const c = Number(localStorage.getItem(CLE_CAPITAL));
      const r = Number(localStorage.getItem(CLE_RISQUE));
      if (Number.isFinite(c) && c > 0) setCapitalSaisi(String(c));
      if (Number.isFinite(r) && r > 0) setRisqueSaisi(String(r));
    } catch {
      /* navigation privée : on garde les valeurs par défaut */
    }
  }, []);

  /** Retient la saisie, et ne mémorise que ce qui a un sens. */
  function saisir(
    valeur: string,
    poser: (v: string) => void,
    cle: string,
  ) {
    poser(valeur);
    const v = Number(valeur.replace(",", "."));
    if (!Number.isFinite(v) || v <= 0) return;
    try {
      localStorage.setItem(cle, String(v));
    } catch {
      /* l'écriture peut échouer, le calcul reste juste */
    }
  }

  const plans: Plan[] = signaux
    // Un plan sans niveaux exploitables ne se dimensionne pas : l'écarter ici
    // évite une ligne de tirets qui n'apprend rien.
    .filter((s) => s.entry > 0 && s.stop > 0 && s.entry !== s.stop)
    .map((s) => ({
      cle: s.symbol,
      symbol: s.symbol,
      label: s.label,
      klass: s.klass,
      score: s.score,
      // Hors seuil, `direction` vaut « neutre » : c'est le biais qui porte
      // l'orientation du plan, et c'est lui qu'il faut dimensionner.
      direction: (s.direction !== "neutre" ? s.direction : s.bias) === "short"
        ? "short"
        : "long",
      entree: s.entry,
      stop: s.stop,
      objectif: s.target,
    }));

  const e = nombre(libreEntree, 0);
  const st = nombre(libreStop, 0);
  const libreValide = e > 0 && st > 0 && e !== st;
  const libre = libreValide
    ? taillePosition(capital, e, st, "crypto", 100, risque, reglages)
    : null;

  const total = plans.reduce((somme, p) => {
    const t = taillePosition(capital, p.entree, p.stop, p.klass, p.score, risque, reglages);
    return somme + (t?.perte_au_stop ?? 0);
  }, 0);
  const plafondPortefeuille = capital * reglages.risque_portefeuille_max;

  return (
    <section>
      <h2>Taille de position</h2>
      <p className="note" style={{ marginTop: 0, marginBottom: 14, maxWidth: "76ch" }}>
        Un plan d’entrée et de stop ne dit pas <em>combien</em> en prendre, et
        c’est pourtant la seule décision qui détermine ce qu’on perd quand le
        plan échoue. Le raisonnement est inversé par rapport à l’intuition&nbsp;:
        on ne décide pas «&nbsp;j’achète pour 1 000&nbsp;€&nbsp;», on décide
        «&nbsp;je perds au maximum tant si le stop saute&nbsp;», et le volume en
        découle de la distance au stop.
        <br />
        <strong>Vos deux chiffres restent dans votre navigateur.</strong> Ils ne
        sont envoyés nulle part et le serveur ne les voit jamais.
      </p>

      <div className="saisie">
        <label className="champ">
          <span className="champ-label">Capital</span>
          <input
            type="text"
            inputMode="decimal"
            value={capitalSaisi}
            aria-label="Capital du compte"
            onChange={(ev) => saisir(ev.target.value, setCapitalSaisi, CLE_CAPITAL)}
          />
        </label>
        <label className="champ">
          <span className="champ-label">Risque nominal par trade</span>
          <input
            type="text"
            inputMode="decimal"
            value={risqueSaisi}
            aria-label="Risque nominal par trade, en pourcentage du capital"
            onChange={(ev) => saisir(ev.target.value, setRisqueSaisi, CLE_RISQUE)}
          />
          <span className="champ-unite">%</span>
        </label>
        <p className="champ-note">
          Le risque nominal est celui d’une conviction maximale. Il est ensuite
          modulé&nbsp;: un score de {seuilSignal.toFixed(0)} n’en engage que{" "}
          {(conviction(seuilSignal) * 100).toFixed(0)}&nbsp;%, un score de 100
          l’engage entièrement.
        </p>
      </div>

      {plans.length ? (
        <>
          {horsSeuil && (
            <div className="warn" style={{ maxWidth: "76ch", marginBottom: 14 }}>
              <strong>Aucune configuration n’atteint le seuil de conviction.</strong>{" "}
              Les lignes ci-dessous sont celles de la liste de surveillance,
              dimensionnées à titre d’exercice&nbsp;: elles montrent ce que
              coûterait la position si vous décidiez de la prendre, elles ne
              disent pas de la prendre. Le moteur, lui, reste à l’écart.
            </div>
          )}
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Symbole</th>
                  <th>Sens</th>
                  <th className="num">Entrée</th>
                  <th className="num">Stop</th>
                  <th className="num">Distance</th>
                  <th className="num">Volume</th>
                  <th className="num">Position</th>
                  <th className="num">Perte si stop</th>
                </tr>
              </thead>
              <tbody>
                {plans.map((p) => {
                  const t = taillePosition(
                    capital, p.entree, p.stop, p.klass, p.score, risque, reglages,
                  );
                  return (
                    <tr key={p.cle}>
                      <td style={{ fontWeight: 600 }}>{p.symbol}</td>
                      <td className={p.direction === "long" ? "up" : "down"}>
                        {p.direction === "long" ? "achat" : "vente"}
                      </td>
                      <td className="num">{fmtPrice(p.entree)}</td>
                      <td className="num">{fmtPrice(p.stop)}</td>
                      <td className="num muted">
                        {t ? `${fmtNum(t.distance_pct)} %` : "—"}
                      </td>
                      <td className="num">{t ? volume(t.unites) : "—"}</td>
                      <td className="num muted">
                        {t ? fmtNum(t.notionnel, 0) : "—"}
                        {t?.plafonne && (
                          <span className="pill" style={{ marginLeft: 6 }} title="Plafond d'exposition de la classe atteint : la position est réduite, donc le risque réel est inférieur au risque demandé.">
                            plafonné
                          </span>
                        )}
                      </td>
                      <td className="num down">
                        {t ? `−${fmtNum(t.perte_au_stop, 2)}` : "—"}
                        <span className="muted" style={{ marginLeft: 6 }}>
                          {t ? `(${fmtNum(t.perte_pct)} %)` : ""}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className={`note${total > plafondPortefeuille ? " depasse" : ""}`}>
            Prendre {plans.length === 1 ? "cette position" : `ces ${plans.length} positions`}{" "}
            engagerait <strong>{fmtNum(total, 2)}</strong> au total, soit{" "}
            {fmtNum((total / capital) * 100)} % du capital.
            {total > plafondPortefeuille ? (
              <>
                {" "}
                <strong>
                  C’est au-delà du plafond de{" "}
                  {(reglages.risque_portefeuille_max * 100).toFixed(0)} % que le
                  moteur s’impose
                </strong>{" "}
                — il n’en prendrait qu’une partie, par ordre de conviction
                décroissante, et refuserait le reste.
              </>
            ) : (
              <>
                {" "}
                Le moteur s’interdit de dépasser{" "}
                {(reglages.risque_portefeuille_max * 100).toFixed(0)} % de risque
                simultané, et {(reglages.risque_correle_max * 100).toFixed(1)} %
                sur un groupe d’actifs corrélés — deux longs sur BTC et ETH ne
                sont pas deux paris.
              </>
            )}
          </p>
        </>
      ) : (
        <div className="empty">
          Aucune configuration retenue à dimensionner. Le calculateur ci-dessous
          reste utilisable sur n’importe quel plan.
        </div>
      )}

      <h3 style={{ marginTop: 24 }}>Sur un plan quelconque</h3>
      <div className="saisie">
        <label className="champ">
          <span className="champ-label">Entrée</span>
          <input
            type="text"
            inputMode="decimal"
            placeholder="4 358,10"
            value={libreEntree}
            onChange={(ev) => setLibreEntree(ev.target.value)}
          />
        </label>
        <label className="champ">
          <span className="champ-label">Stop</span>
          <input
            type="text"
            inputMode="decimal"
            placeholder="4 325,15"
            value={libreStop}
            onChange={(ev) => setLibreStop(ev.target.value)}
          />
        </label>
      </div>
      {libre ? (
        <p className="note">
          Distance au stop <strong>{fmtNum(libre.distance_pct)} %</strong> —
          volume <strong>{volume(libre.unites)}</strong>, position{" "}
          <strong>{fmtNum(libre.notionnel, 2)}</strong>, perte au stop{" "}
          <strong className="down">−{fmtNum(libre.perte_au_stop, 2)}</strong>.
          La conviction n’est pas modulée ici&nbsp;: le plan ne vient pas du
          moteur, donc il n’a pas de score.
        </p>
      ) : (
        <p className="note">
          Saisissez une entrée et un stop pour obtenir le volume correspondant à
          votre capital.
        </p>
      )}

      <p className="note" style={{ maxWidth: "76ch" }}>
        Le volume est une quantité de l’instrument, pas un nombre de lots&nbsp;:
        un lot ne vaut pas la même chose d’un courtier à l’autre, et en inventer
        un ici serait faux pour la plupart des lecteurs. Sur MetaTrader,{" "}
        <code>/api/mt</code> transmet la fraction de risque et l’Expert Advisor
        en déduit le volume avec la taille de contrat réelle de votre compte.
        {!monte && " Le calcul s’ajuste dès que la page est active."}
      </p>
    </section>
  );
}

/**
 * Lecture d'un champ de saisie.
 *
 * Tolère la virgule décimale et les espaces de milliers, parce qu'un lecteur
 * francophone tape « 10 000,50 » et qu'un champ qui refuse sa propre langue
 * est un champ cassé. Une saisie vide ou incomplète retombe sur le défaut :
 * le tableau continue d'afficher des chiffres pendant qu'on tape.
 */
function nombre(saisie: string, defaut: number): number {
  const v = Number(saisie.replace(/[\s\u00a0]/g, "").replace(",", "."));
  return Number.isFinite(v) && v > 0 ? v : defaut;
}

/**
 * Volume affiché avec une précision adaptée à son ordre de grandeur.
 *
 * 0,00 unité de Bitcoin ne veut rien dire, 12 345,678900 unités d'une paire
 * de devises non plus.
 */
function volume(unites: number): string {
  if (!Number.isFinite(unites) || unites <= 0) return "—";
  const decimales = unites >= 1000 ? 0 : unites >= 10 ? 2 : unites >= 1 ? 3 : 6;
  return fmtNum(unites, decimales);
}
