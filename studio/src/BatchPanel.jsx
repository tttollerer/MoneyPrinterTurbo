import React, { useEffect, useState } from "react";
import { request } from "./api.js";
const states = {
  preview: "Vorschau",
  running: "Produktion läuft",
  pausing: "Wird nach dem laufenden Auftrag pausiert",
  paused: "Pausiert",
  cancelling: "Wird nach dem laufenden Auftrag beendet",
  cancelled: "Abgebrochen",
  complete: "Abgeschlossen",
};
const itemStates = {
  pending: "Ausstehend",
  skipped: "Vorhanden · übersprungen",
  blocked: "Blockiert",
  running: "Läuft",
  complete: "Fertig",
  needs_review: "Abnahme nötig",
  failed: "Fehlgeschlagen",
  interrupted: "Unterbrochen",
  unknown: "Status unklar",
  cancelled: "Abgebrochen",
};
export default function BatchPanel({ projects, onError, onChanged }) {
  const [selected, setSelected] = useState([]),
    [batches, setBatches] = useState([]),
    [batch, setBatch] = useState(null),
    [busy, setBusy] = useState(false),
    [confirmed, setConfirmed] = useState(false);
  const projectKey = projects.map((p) => p.id).join(",");
  useEffect(() => {
    setSelected([]);
    setBatch(null);
    setConfirmed(false);
  }, [projectKey]);
  useEffect(() => {
    let alive = true;
    const poll = () =>
      request("/batches")
        .then((list) => {
          if (!alive) return;
          setBatches(list);
          setBatch((current) =>
            current ? list.find((b) => b.id === current.id) || current : null,
          );
        })
        .catch(() => {});
    poll();
    const timer = setInterval(poll, 2500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    setConfirmed(false);
  }, [batch?.input_digest, batch?.state]);
  const act = async (fn) => {
    setBusy(true);
    try {
      const next = await fn();
      if (next?.id) {
        setBatch(next);
        setBatches((list) => [next, ...list.filter((b) => b.id !== next.id)]);
      }
      await onChanged?.();
    } catch (error) {
      onError(error.message);
    } finally {
      setBusy(false);
    }
  };
  const stale =
    !!batch &&
    Object.entries(batch.project_revisions || {}).some(([id, revision]) => {
      const p = projects.find((p) => p.id === id);
      return p && p.revision !== revision;
    });
  const relevant = batches.filter((item) =>
    item.project_ids?.some((id) => projects.some((p) => p.id === id)),
  );
  const generateCount =
    batch?.items?.filter((item) => item.action === "generate").length || 0;
  return (
    <section className="panel batch-panel">
      <div className="row spread">
        <h2>Gemeinsame Clips produzieren</h2>
        <span className="muted">
          Sequenziell · vorhandene Takes werden geprüft
        </span>
      </div>
      <p className="muted">
        Wähle Motive für die Produktion. Zuerst entsteht eine kostenfreie
        Auftragsvorschau. Erst die Bestätigung startet kostenpflichtige
        Generierungen.
      </p>
      <div className="batch-selection">
        {projects.map((project) => (
          <label className="check" key={project.id}>
            <input
              type="checkbox"
              checked={selected.includes(project.id)}
              disabled={busy}
              onChange={(event) => {
                setSelected((ids) =>
                  event.target.checked
                    ? [...ids, project.id]
                    : ids.filter((id) => id !== project.id),
                );
                setBatch(null);
                setConfirmed(false);
              }}
            />
            {project.title}
          </label>
        ))}
      </div>
      {selected.length > 20 && (
        <p className="notice">
          Bitte höchstens 20 Motivprojekte pro Batch wählen.
        </p>
      )}
      <div className="row">
        <button
          disabled={busy || !selected.length || selected.length > 20}
          onClick={() =>
            act(() =>
              request("/batches/preview", {
                method: "POST",
                body: { project_ids: selected },
              }),
            )
          }
        >
          {busy ? "Wird geprüft …" : "Produktionsvorschau erstellen"}
        </button>
        {!!relevant.length && (
          <label className="field batch-history">
            Gespeicherter Batch
            <select
              value={batch?.id || ""}
              onChange={(event) => {
                setBatch(
                  relevant.find((b) => b.id === event.target.value) || null,
                );
                setConfirmed(false);
              }}
            >
              <option value="">— Batch wählen —</option>
              {relevant.map((item) => (
                <option value={item.id} key={item.id}>
                  {states[item.state] || item.state} ·{" "}
                  {new Date(item.created_at).toLocaleString("de-DE")}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {batch && (
        <div className="batch-preview">
          <div className="row spread">
            <h3>{states[batch.state] || batch.state}</h3>
            <span className="status-pill">
              {generateCount} Generierungen · {batch.skipped} übersprungen ·{" "}
              {batch.blocked} blockiert
            </span>
          </div>
          {batch.error && <p className="error">{batch.error}</p>}
          <p className="notice">
            {batch.cost?.amount == null
              ? "Die Kosten sind nicht verlässlich beziffert. Der Anbieter kann jeden gestarteten Auftrag berechnen."
              : `Geschätzte Kosten: ${batch.cost.amount} ${batch.cost.currency || ""}.`}
          </p>
          <div className="batch-items">
            {batch.items.map((item) => (
              <div className="batch-item" key={item.id}>
                <div>
                  <strong>
                    {item.project_title} / {item.scene_title}
                  </strong>
                  <small>
                    {item.model} · {item.duration_s}s
                  </small>
                  {item.reason && <p className="muted">{item.reason}</p>}
                </div>
                <span
                  className={`status-pill ${item.status === "complete" ? "ready" : ""}`}
                >
                  {itemStates[item.status] || item.status}
                </span>
              </div>
            ))}
          </div>
          {batch.blocked > 0 && (
            <p className="notice">
              Blockierte Motive zuerst bearbeiten oder freigeben und
              anschließend eine neue Vorschau erstellen.
            </p>
          )}
          {stale && batch.state === "preview" && (
            <p className="notice">
              Projekte wurden seit dieser Vorschau geändert. Erstelle eine neue
              Vorschau vor dem Start.
            </p>
          )}
          {["preview", "paused"].includes(batch.state) && (
            <div className="confirm">
              <label className="check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                Ich bestätige die gezeigten Aufträge und mögliche
                Anbietergebühren.
              </label>
              <button
                className="primary"
                disabled={
                  busy ||
                  !confirmed ||
                  batch.blocked > 0 ||
                  (batch.state === "preview" && stale)
                }
                onClick={() =>
                  act(() =>
                    request(
                      `/batches/${batch.id}/${batch.state === "paused" ? "resume" : "start"}`,
                      {
                        method: "POST",
                        body: {
                          confirmed: true,
                          input_digest: batch.input_digest,
                        },
                      },
                    ),
                  )
                }
              >
                {batch.state === "paused"
                  ? "Bestätigt fortsetzen"
                  : "Kostenpflichtige Produktion starten"}
              </button>
            </div>
          )}
          <div className="row">
            {batch.state === "running" && (
              <button
                disabled={busy}
                onClick={() =>
                  act(() =>
                    request(`/batches/${batch.id}/pause`, { method: "POST" }),
                  )
                }
              >
                Nach aktuellem Auftrag pausieren
              </button>
            )}
            {["running", "paused", "pausing"].includes(batch.state) && (
              <button
                disabled={busy}
                onClick={() =>
                  act(() =>
                    request(`/batches/${batch.id}/cancel`, { method: "POST" }),
                  )
                }
              >
                Offene Aufträge abbrechen
              </button>
            )}
          </div>
          {["running", "pausing", "cancelling"].includes(batch.state) && (
            <p className="muted">
              Ein bereits abgesendeter Anbieterauftrag läuft weiter. Pause und
              Abbruch verhindern den nächsten Start.
            </p>
          )}
          {batch.state === "complete" && (
            <p className="muted">
              Produktion beendet. Öffne jedes Motiv zur Take-Abnahme. Varianten
              nutzen anschließend die freigegebenen gemeinsamen Clips.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
