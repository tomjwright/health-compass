"use client";

import { type FormEvent, useState } from "react";

type Page = {
  trials: { nct_id: string; title: string }[];
  total_count: number | null;
  next_page_token: string | null;
};

export default function WorldwideTrials({ condition, onConditionChange }: {
  condition: string;
  onConditionChange: (value: string) => void;
}) {
  const [results, setResults] = useState<Page | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function search(pageToken: string | null) {
    setBusy(true);
    setError("");
    if (!pageToken) setResults(null);
    try {
      const response = await fetch("/api/trials/worldwide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ condition: condition.trim(), page_token: pageToken }),
      });
      if (!response.ok) throw new Error("Registry search failed");
      const page: Page = await response.json();
      setResults((previous) => pageToken && previous ? {
        ...page,
        trials: [...previous.trials, ...page.trials],
        total_count: previous.total_count,
      } : page);
    } catch {
      setError("Couldn’t reach ClinicalTrials.gov. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (condition.trim().length < 2) {
      setError("Enter a condition with at least two characters.");
      return;
    }
    void search(null);
  }

  const count = results?.trials.length ?? 0;
  const status = count === 0
    ? "No recruiting studies found."
    : `Showing ${count}${results?.total_count == null ? "" : ` of ${results.total_count}`} recruiting studies.`;

  return (
    <>
      <form onSubmit={submit} autoComplete="off">
        <fieldset className="worldwide-form" disabled={busy}>
          <label>Condition
            <input value={condition} maxLength={100} placeholder="e.g. Hair loss" onChange={(event) => {
              onConditionChange(event.target.value);
              setResults(null);
              setError("");
            }} />
          </label>
          <button type="submit">Search trials</button>
        </fieldset>
        {error && !results && <p className="error" role="alert">{error}</p>}
      </form>
      <section className="explorer worldwide-results" aria-labelledby="worldwide-heading">
        <div className="results">
          <h2 id="worldwide-heading">Trial results</h2>
          <p className="result-status" role="status">{busy
            ? (results ? "Loading more studies…" : "Searching ClinicalTrials.gov…")
            : (results ? status : "Enter a condition to find recruiting studies worldwide.")}</p>
          <ul className="trial-list">
            {results?.trials.map(({ nct_id, title }) => (
              <li key={nct_id}>
                <h3>{title}</h3>
                <p><a href={`https://clinicaltrials.gov/study/${encodeURIComponent(nct_id)}`}>
                  {nct_id} on ClinicalTrials.gov
                </a></p>
              </li>
            ))}
          </ul>
          {error && results && <p className="error" role="alert">{error}</p>}
          {results?.next_page_token && (
            <button type="button" className="secondary" disabled={busy}
              onClick={() => void search(results.next_page_token)}>Load more studies</button>
          )}
        </div>
      </section>
    </>
  );
}
