"use client";

import { type FormEvent, useEffect, useState } from "react";
import LocationFields from "../location-fields";
import type { Coordinates } from "../trial-map";

type Answer = {
  message: string;
  trials: { nct_id: string; title: string | null; note: string | null }[];
};

type Run = {
  run_id: string;
  status: "running" | "done";
  steps_done: string[];
  answer: Answer | null;
};

const stepLabels: Record<string, string> = {
  check_input: "Reviewing your question",
  find_trials: "Searching ClinicalTrials.gov",
  write_answer: "Drafting the answer",
  check_output: "Checking the answer against the trial records",
};

export default function Ask() {
  const [question, setQuestion] = useState("");
  const [location, setLocation] = useState<Coordinates | null>(null);
  const [locating, setLocating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [pollPaused, setPollPaused] = useState(false);
  const [error, setError] = useState("");
  const runId = run?.status === "running" ? run.run_id : null;
  const nextStep = Object.keys(stepLabels).find((step) => !run?.steps_done.includes(step));
  const progress = locating ? "Getting your location…" : starting ? "Starting your question…"
    : runId && !pollPaused ? nextStep ? stepLabels[nextStep] : "Finishing the answer…" : "";

  useEffect(() => {
    if (!runId || pollPaused) return;
    const endpoint = `/api/ask/${encodeURIComponent(runId)}`;
    const controller = new AbortController();
    let timer: number;

    async function poll() {
      try {
        const response = await fetch(endpoint, {
          cache: "no-store",
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
        });
        if (controller.signal.aborted) return;
        if (response.status === 404 || response.status === 500 || response.status === 503) {
          setRun(null);
          setError(response.status === 404
            ? "This question has expired. Submit it again to get a new answer."
            : "Couldn’t answer your question right now. Try again in a moment.");
          return;
        }
        if (!response.ok) throw new Error("Could not check progress");
        const result: Run = await response.json();
        if (controller.signal.aborted) return;
        setRun(result);
        if (result.status === "running") timer = window.setTimeout(poll, 1000);
      } catch {
        if (!controller.signal.aborted) setPollPaused(true);
      }
    }

    timer = window.setTimeout(poll, 1000);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [runId, pollPaused]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (starting || runId || locating) return;
    const text = question.trim();
    if (text.length < 3) {
      setError("Enter a question with at least three characters.");
      return;
    }
    if (!location) {
      setError("Choose a city from the suggestions, or use your current location.");
      return;
    }
    setRun(null);
    setError("");
    setStarting(true);
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, latitude: location.latitude, longitude: location.longitude }),
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) throw new Error("Could not start the question");
      setRun(await response.json());
    } catch {
      setError("Couldn’t start your question. Try again in a moment.");
    } finally {
      setStarting(false);
    }
  }

  return (
    <main className="ask-page">
      <h1>Ask about clinical trials</h1>
      <p className="intro">Ask about trials for a condition near you and review the ClinicalTrials.gov sources.</p>
      <form onSubmit={submit} autoComplete="off">
        <fieldset className="ask-fields" disabled={locating || starting || Boolean(runId)}>
          <label className="question-field">Your question
            <textarea value={question} maxLength={500} rows={3} placeholder="Are there any hair loss trials near me?"
              onChange={(event) => { setQuestion(event.target.value); setError(""); }} />
          </label>
          <LocationFields onChange={(coordinates) => { setLocation(coordinates); setError(""); }}
            onLocating={setLocating} />
          <button type="submit" className="search-button">Ask Health Compass</button>
        </fieldset>
        <p className="freshness">Your question and location are temporarily stored while the answer is generated and available.</p>
        {error && <p className="error" role="alert">{error}</p>}
      </form>
      <p className="result-status" role="status">{progress || (run?.answer ? "Answer ready." : "")}</p>
      {pollPaused && (
        <p className="error" role="alert">Couldn’t check progress.{" "}
          <button type="button" className="secondary" onClick={() => setPollPaused(false)}>Retry status</button>
        </p>
      )}
      {run?.answer && (
        <section className="ask-answer" aria-labelledby="answer-heading">
          <h2 id="answer-heading">Answer</h2>
          <p className="answer-message">{run.answer.message}</p>
          {run.answer.trials.length > 0 && (
            <>
              <h2>ClinicalTrials.gov sources</h2>
              <ul className="trial-list">
                {run.answer.trials.map((trial) => (
                  <li key={trial.nct_id}>
                    <h3><a href={`https://clinicaltrials.gov/study/${encodeURIComponent(trial.nct_id)}`}>
                      {trial.title ?? trial.nct_id}
                    </a></h3>
                    <p>{trial.nct_id}</p>
                    {trial.note && <p>{trial.note}</p>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
      <footer>Confirm recruiting status and eligibility with the study team.</footer>
    </main>
  );
}
