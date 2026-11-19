export type Approval = {
  region: "US" | "EU";
  approval_id: string;
  medicine_name: string | null;
  active_substance: string | null;
  holder: string | null;
  status: string;
  approved_on: string | null;
  is_conditional: boolean | null;
  is_orphan: boolean | null;
  indications: string | null;
  source_url: string | null;
};

export type Paper = {
  pmid: string;
  title: string;
  journal: string | null;
  published_on: string | null;
  paper_type: string;
  abstract: string;
  source_url: string;
};

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleDateString("en", { dateStyle: "medium", timeZone: "UTC" }) : null;
}

export function ApprovalResult({ approval, onResearch }: { approval: Approval; onResearch: (ingredient: string) => void }) {
  const agency = approval.region === "US" ? "US · FDA" : "EU · EMA";
  const ingredient = approval.active_substance;
  return <li>
    <span className="badge">{agency}</span>
    <h3>{approval.medicine_name ?? ingredient ?? approval.approval_id}</h3>
    <p>{[approval.status, formatDate(approval.approved_on), approval.holder].filter(Boolean).join(" · ")}</p>
    {ingredient && <p>Active ingredient: {ingredient}</p>}
    {(approval.is_conditional || approval.is_orphan) && <p>{[
      approval.is_conditional && "Conditional approval", approval.is_orphan && "Orphan designation",
    ].filter(Boolean).join(" · ")}</p>}
    {approval.indications && <details className="trial-places">
      <summary>Approved indications</summary><p>{approval.indications}</p>
    </details>}
    {approval.source_url?.startsWith("https://") && <p><a href={approval.source_url}>
      View approval {approval.approval_id}
    </a></p>}
    {ingredient && <button type="button" className="secondary" onClick={() => onResearch(ingredient.slice(0, 100))}>
      Find research on {approval.medicine_name ?? ingredient}
    </button>}
  </li>;
}

export function PaperResult({ paper }: { paper: Paper }) {
  return <li>
    <span className="badge">{paper.paper_type}</span>
    <h3><a href={paper.source_url}>{paper.title}</a></h3>
    <p>{[paper.journal, formatDate(paper.published_on), `PMID ${paper.pmid}`].filter(Boolean).join(" · ")}</p>
    {paper.abstract && <details className="trial-places">
      <summary>Read abstract</summary><p>{paper.abstract}</p>
    </details>}
  </li>;
}
