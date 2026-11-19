"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import { ApprovalResult, PaperResult, type Approval, type Paper } from "./results";
import styles from "./medicines.module.css";

type Query = { condition: string; terms: string[] };
type ApprovalResults = { approvals: Approval[]; search: Query };
type Research = { index: number; papers?: Paper[]; error?: string };

async function searchCatalog<T>(resource: "approvals" | "research", body: object, controller: AbortController): Promise<T> {
  const response = await fetch(`/api/catalog/${resource}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.any([controller.signal, AbortSignal.timeout(75_000)]),
  });
  if (!response.ok) throw new Error(`Search failed (${response.status})`);
  return response.json();
}

export default function Medicines() {
  const [condition, setCondition] = useState("");
  const [results, setResults] = useState<{ query: Query; approvals: Approval[] } | null>(null);
  const [research, setResearch] = useState<Research | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const approvalsRequest = useRef<AbortController | null>(null);
  const researchRequest = useRef<AbortController | null>(null);

  useEffect(() => () => {
    approvalsRequest.current?.abort();
    researchRequest.current?.abort();
  }, []);

  async function loadResearch(query: Query, index: number, medicine: string) {
    researchRequest.current?.abort();
    const controller = new AbortController();
    researchRequest.current = controller;
    setResearch({ index });
    try {
      const { papers } = await searchCatalog<{ papers: Paper[] }>("research", { ...query, medicine, limit: 10 }, controller);
      if (!controller.signal.aborted) setResearch({ index, papers });
    } catch {
      if (!controller.signal.aborted) setResearch({ index, error: "Research is temporarily unavailable." });
    }
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = condition.trim();
    if (name.length < 2) {
      setError("Enter a condition.");
      return;
    }
    approvalsRequest.current?.abort();
    researchRequest.current?.abort();
    const controller = new AbortController();
    approvalsRequest.current = controller;
    setError("");
    setResults(null);
    setResearch(null);
    setBusy(true);
    try {
      const { approvals, search } = await searchCatalog<ApprovalResults>(
        "approvals", { condition: name, expand_terms: true }, controller,
      );
      if (controller.signal.aborted) return;
      const query = { condition: search.condition, terms: search.terms };
      setResults({ query, approvals });
      const first = approvals.findIndex(approval => approval.research_medicine);
      const medicine = approvals[first]?.research_medicine;
      if (medicine) void loadResearch(query, first, medicine);
    } catch {
      if (!controller.signal.aborted) setError("Couldn’t load medicine approvals. Try again.");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  const count = results?.approvals.length ?? 0;
  return (
    <main className={styles.page}>
      <h1>Medicines &amp; research</h1>
      <p className="intro">Search a condition to see US and EU medicine approvals and related studies.</p>
      <form onSubmit={search} className={styles.form} autoComplete="off">
        <label>Condition
          <input required minLength={2} maxLength={100} placeholder="e.g. Psoriasis"
            value={condition} onChange={event => setCondition(event.target.value)} />
        </label>
        <button disabled={busy} type="submit">Find medicines and research</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="result-status" role="status">
        {busy ? "Searching for approvals…" : results && (
          count ? `${count} ${count === 1 ? "approval" : "approvals"} found for ${results.query.condition}.` : "No matching approvals found."
        )}
      </p>
      {results && count > 0 && <ul className={`trial-list ${styles.results}`}>
        {results.approvals.map((approval, index) => {
          const medicine = approval.research_medicine;
          const selected = research?.index === index;
          return <ApprovalResult key={`${approval.region}-${approval.approval_id}-${index}`} approval={approval}>
            {medicine && <>
              <button className="secondary" type="button" aria-expanded={selected} aria-controls={`research-${index}`}
                onClick={() => {
                  if (selected) {
                    researchRequest.current?.abort();
                    setResearch(null);
                  } else void loadResearch(results.query, index, medicine);
                }}>
                {selected ? "Hide" : "Show"} research on {approval.medicine_name ?? medicine}
              </button>
              <section id={`research-${index}`} className={styles.research} hidden={!selected}>
                {selected && <>
                  <h4>Published research on {medicine}</h4>
                  {research.error ? <>
                    <p className="error" role="alert">{research.error}</p>
                    <button className="secondary" type="button" onClick={() => void loadResearch(results.query, index, medicine)}>
                      Retry research
                    </button>
                  </> : <p role="status">{!research.papers ? "Searching Europe PMC…" :
                    research.papers.length === 0 ? "No matching studies found." : `${research.papers.length} from Europe PMC.`}</p>}
                  {Boolean(research.papers?.length) && <ul className="trial-list">
                    {research.papers?.map(paper => <PaperResult key={paper.pmid} paper={paper} />)}
                  </ul>}
                </>}
              </section>
            </>}
          </ApprovalResult>;
        })}
      </ul>}
      <footer>Research includes systematic reviews, meta-analyses, and randomised controlled trials.</footer>
    </main>
  );
}
