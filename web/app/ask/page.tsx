"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useRef, useState } from "react";
import type { Coordinates } from "../trial-map";
import LocationFields from "./location-fields";
import styles from "./ask.module.css";

type Trial = { nct_id: string; title: string | null; note: string | null };
type Approval = { approval_id: string; region: string; medicine_name: string | null; source_url: string | null; note: string | null };
type Paper = { pmid: string; title: string; medicine: string; source_url: string; note: string | null };
type Answer = { message: string; trials: Trial[]; approvals: Approval[]; research: Paper[] };
type Run = { run_id: string; status: "running" | "done"; steps_done: string[]; answer: Answer | null };

const stepLabels: Record<string, string> = {
  route: "Question reviewed",
  find_trials: "Trial search complete",
  find_approvals: "Medicine approval search complete",
  find_research: "Research search complete",
  write_answer: "Draft prepared",
  check_output: "Source checks performed",
};

export default function Ask() {
  const [question, setQuestion] = useState("");
  const [location, setLocation] = useState<Coordinates | null>(null);
  const [locating, setLocating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState("");
  const submitRequest = useRef<AbortController | null>(null);
  const runId = run?.status === "running" ? run.run_id : null;
  const answer = run?.answer;
  const steps = run?.steps_done.flatMap(step => stepLabels[step] ? [stepLabels[step]] : []);

  useEffect(() => () => submitRequest.current?.abort(), []);
  useEffect(() => {
    if (!runId || paused) return;
    const endpoint = `/api/ask/${encodeURIComponent(runId)}`;
    const controller = new AbortController();
    let timer: number;
    async function poll() {
      try {
        const response = await fetch(endpoint, {
          cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10_000)]),
        });
        if (controller.signal.aborted) return;
        if ([404, 500, 503].includes(response.status)) {
          setRun(null);
          setError(response.status === 404 ? "This question has expired. Submit it again." :
            response.status === 503 ? "The data needed to answer this question is currently unavailable." :
              "Couldn’t answer your question. Please try again.");
          return;
        }
        if (!response.ok) throw new Error("Could not check progress");
        const result: Run = await response.json();
        if (controller.signal.aborted) return;
        setRun(result);
        if (result.status === "running") timer = window.setTimeout(poll, 1000);
      } catch {
        if (!controller.signal.aborted) setPaused(true);
      }
    }
    void poll();
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [runId, paused]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (starting || runId || locating) return;
    const text = question.trim();
    if (text.length < 3) {
      setError("Enter a question with at least three characters.");
      return;
    }
    const controller = new AbortController();
    submitRequest.current = controller;
    setError("");
    setRun(null);
    setStarting(true);
    try {
      const response = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, latitude: location?.latitude, longitude: location?.longitude }),
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
      });
      if (!response.ok) throw new Error("Could not start question");
      const result: Run = await response.json();
      if (!controller.signal.aborted) setRun(result);
    } catch {
      if (!controller.signal.aborted) setError("Couldn’t start your question. Please try again.");
    } finally {
      if (!controller.signal.aborted) setStarting(false);
    }
  }

  return <main className={styles.page}>
    <h1>Ask Health Compass</h1>
    <p className="intro">Ask about nearby trials or approved medicines and the research behind them.</p>
    <form onSubmit={submit} autoComplete="off">
      <fieldset className={styles.fields} disabled={locating || starting || Boolean(runId)}>
        <label>Your question
          <textarea required minLength={3} maxLength={500} rows={3} value={question}
            placeholder="Which medicines are approved for psoriasis?" onChange={event => setQuestion(event.target.value)} />
        </label>
        <details>
          <summary>Location for nearby trials (optional)</summary>
          <div className={styles.location}><LocationFields onChange={setLocation} onLocating={setLocating} /></div>
        </details>
        <button type="submit">Ask a question</button>
        <p className="freshness">Your question and any location you provide are stored temporarily for this answer.</p>
      </fieldset>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    <p className="result-status" role="status">{starting ? "Submitting question…" : runId && !paused ?
      steps?.at(-1) ?? "Working on your answer…" : answer ? "Answer ready." : ""}</p>
    {paused && <p className="error" role="alert">Couldn’t check progress.{" "}
      <button type="button" className="secondary" onClick={() => setPaused(false)}>Retry status</button>
    </p>}
    {answer && <section className={styles.answer} aria-label="Answer">
      <h2>Answer</h2>
      <p className={styles.message}>{answer.message}</p>
      <Sources title="ClinicalTrials.gov sources" items={answer.trials.map(trial => ({
        id: trial.nct_id, title: trial.title ?? trial.nct_id, note: trial.note,
        url: `https://clinicaltrials.gov/study/${encodeURIComponent(trial.nct_id)}`,
      }))} />
      <Sources title="FDA and EMA sources" items={answer.approvals.map((approval, index) => ({
        id: `${approval.region}-${approval.approval_id}-${index}`, title: approval.medicine_name ?? approval.approval_id,
        note: approval.note, url: approval.source_url,
      }))} />
      <Sources title="Research papers" items={answer.research.map(paper => ({
        id: `${paper.medicine}-${paper.pmid}`, title: paper.title, note: paper.note, url: paper.source_url,
      }))} />
    </section>}
    <footer><Link href="/">Find clinical trials</Link></footer>
  </main>;
}

function Sources({ title, items }: {
  title: string; items: { id: string; title: string; note: string | null; url: string | null }[];
}) {
  if (!items.length) return null;
  return <>
    <h2>{title}</h2>
    <ol className={styles.sources}>{items.map(item => <li key={item.id}>
      <h3>{item.url?.startsWith("https://") ? <a href={item.url}>{item.title}</a> : item.title}</h3>
      {item.note && <p>{item.note}</p>}
    </li>)}</ol>
  </>;
}
