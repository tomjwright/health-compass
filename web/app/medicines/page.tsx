"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import { ApprovalResult, PaperResult, type Approval, type Paper } from "./results";
import styles from "./medicines.module.css";

type Mode = "approvals" | "research";
type Results = { mode: "approvals"; approvals: Approval[] } | { mode: "research"; papers: Paper[] };

export default function Medicines() {
  const [mode, setMode] = useState<Mode>("approvals");
  const [condition, setCondition] = useState("");
  const [medicine, setMedicine] = useState("");
  const [alternateTerms, setAlternateTerms] = useState("");
  const [results, setResults] = useState<Results | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const medicineInput = useRef<HTMLInputElement>(null);

  useEffect(() => () => request.current?.abort(), []);

  function changeMode(next: Mode) {
    if (next === mode) return;
    request.current?.abort();
    setMode(next);
    setResults(null);
    setBusy(false);
    setError("");
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setResults(null);
    const name = condition.trim();
    const ingredient = medicine.trim();
    const terms = alternateTerms.split(";").map(term => term.trim()).filter(Boolean);
    if (name.length < 2 || (mode === "research" && ingredient.length < 2)) {
      setError(mode === "research" ? "Enter a condition and an active ingredient." : "Enter a condition.");
      return;
    }
    if (terms.length > 3 || terms.some(term => term.length < 2 || term.length > 100)) {
      setError("Enter up to three other names for the condition, each 2–100 characters.");
      return;
    }
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    try {
      const response = await fetch(`/api/catalog/${mode}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ condition: name, terms, ...(mode === "research" && { medicine: ingredient, limit: 10 }) }),
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(75_000)]),
      });
      const data = await response.json();
      if (controller.signal.aborted) return;
      if (!response.ok) {
        setError(response.status === 503 && mode === "approvals" ? "Approval data is currently unavailable." :
          typeof data.detail === "string" ? data.detail : "Couldn’t search. Check the fields and try again.");
        return;
      }
      setResults(mode === "approvals" ? { mode, approvals: data.approvals } : { mode, papers: data.papers });
    } catch {
      if (!controller.signal.aborted) setError("Search temporarily unavailable. Try again.");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  const count = results && (results.mode === "approvals" ? results.approvals.length : results.papers.length);
  return (
    <main className={styles.page}>
      <h1>Medicines &amp; research</h1>
      <p className="intro">Find US and EU medicine approvals and published research for a condition.</p>
      <div className="search-modes" role="group" aria-label="Evidence source">
        <button type="button" aria-pressed={mode === "approvals"} onClick={() => changeMode("approvals")}>Approved medicines</button>
        <button type="button" aria-pressed={mode === "research"} onClick={() => changeMode("research")}>Published research</button>
      </div>
      <form onSubmit={search} className={styles.form} autoComplete="off">
        <label>Condition
          <input required minLength={2} maxLength={100} placeholder="e.g. Psoriasis"
            value={condition} onChange={event => setCondition(event.target.value)} />
        </label>
        {mode === "research" && <label>Active ingredient
          <input ref={medicineInput} required minLength={2} maxLength={100} placeholder="e.g. Deucravacitinib"
            value={medicine} onChange={event => setMedicine(event.target.value)} />
        </label>}
        <label>Other names for the condition (optional)
          <input aria-describedby="other-names" value={alternateTerms} maxLength={306}
            onChange={event => setAlternateTerms(event.target.value)} />
          <span className="freshness" id="other-names">Separate up to three names with semicolons.</span>
        </label>
        <button disabled={busy} type="submit">{mode === "approvals" ? "Find approvals" : "Find studies"}</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="result-status" role="status">
        {busy ? "Searching…" : count === null ? "" : count ? `${count} ${mode === "approvals" ? "approvals" : "papers"} found.` : "No matches found."}
      </p>
      {Boolean(count) && <ul className={`trial-list ${styles.results}`}>
        {results?.mode === "approvals" && results.approvals.map((approval, index) => (
          <ApprovalResult key={`${approval.region}-${approval.approval_id}-${index}`} approval={approval} onResearch={ingredient => {
            setMedicine(ingredient);
            changeMode("research");
            requestAnimationFrame(() => medicineInput.current?.focus());
          }} />
        ))}
        {results?.mode === "research" && results.papers.map(paper => <PaperResult key={paper.pmid} paper={paper} />)}
      </ul>}
      <footer>{mode === "approvals" ? "Approvals are specific to the condition and jurisdiction." : "Results include systematic reviews, meta-analyses, and randomised controlled trials from Europe PMC."}</footer>
    </main>
  );
}
