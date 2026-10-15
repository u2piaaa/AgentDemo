import { useState } from "react";
import { reindexDocument } from "../../api";
import type { KnowledgeDocument } from "../../types";

export function DocumentCard({ document, onUpdated }: {
  document: KnowledgeDocument;
  onUpdated: (document: KnowledgeDocument) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  async function reindex() {
    if (busy) return;
    setBusy(true);
    setNotice("");
    try {
      const updated = await reindexDocument(document.id);
      onUpdated(updated);
      setNotice(updated.retrieval_mode === "keyword" ? "Context updated. Embeddings unavailable; keyword search is ready." : "Context and embeddings updated.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Reindex failed");
    } finally {
      setBusy(false);
    }
  }
  return <div className="mini-card">
    <strong>{document.title}</strong>
    <span>{document.source_type}</span>
    <p>{document.status}{document.retrieval_mode === "hybrid" ? " · Semantic + keyword" : document.retrieval_mode === "keyword" ? " · Keyword only" : ""}</p>
    <button type="button" className="icon-button text-button" onClick={() => void reindex()} disabled={busy}>
      {busy ? "Reindexing…" : "Reindex document"}
    </button>
    {notice && <p role="status">{notice}</p>}
  </div>;
}
