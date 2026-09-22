import React, { useEffect, useRef, useState } from "react";
import { request, assetUrl } from "./api.js";
import { jobLabel, jobStateLabel } from "./workflow.js";

export default function WorkflowPanel({
  project,
  jobs,
  busy,
  onRun,
  onProjectChange,
  onJobCreated,
  onRefresh,
}) {
  const [brief, setBrief] = useState(project.brief || "");
  const [script, setScript] = useState(project.script || "");
  const prior = useRef({
    brief: project.brief || "",
    script: project.script || "",
  });
  const [capabilities, setCapabilities] = useState(null),
    [capabilityError, setCapabilityError] = useState("");
  const [voice, setVoice] = useState(""),
    [rate, setRate] = useState(145),
    [operation, setOperation] = useState(null);
  const [provider, setProvider] = useState(""),
    [query, setQuery] = useState(""),
    [candidates, setCandidates] = useState([]),
    [searched, setSearched] = useState(false),
    [targetScene, setTargetScene] = useState("");
  const [searchOrigin, setSearchOrigin] = useState(null);
  const dirty =
    brief !== (project.brief || "") || script !== (project.script || "");
  useEffect(() => {
    const previous = prior.current;
    setBrief((current) =>
      current === previous.brief ? project.brief || "" : current,
    );
    setScript((current) =>
      current === previous.script ? project.script || "" : current,
    );
    prior.current = {
      brief: project.brief || "",
      script: project.script || "",
    };
  }, [project.brief, project.script]);
  useEffect(() => {
    let active = true;
    request("/workflow/capabilities")
      .then((result) => {
        if (!active) return;
        setCapabilities(result);
        setVoice(
          result.speech?.default_voice || result.speech?.voices?.[0]?.id || "",
        );
        setProvider(
          result.stock?.find((p) => p.configured)?.id ||
            result.stock?.[0]?.id ||
            "",
        );
      })
      .catch((error) => {
        if (active) setCapabilityError(error.message);
      });
    return () => {
      active = false;
    };
  }, []);
  const perform = async (kind, fn) => {
    setOperation(kind);
    try {
      return await onRun(fn);
    } finally {
      setOperation(null);
    }
  };
  const savedSpeech = project.script?.trim();
  const stockProvider = capabilities?.stock?.find(
    (item) => item.id === provider,
  );
  const workflowJobs = jobs.filter((job) =>
    ["speech", "stock_import"].includes(job.kind),
  );
  const speechRunning = workflowJobs.some(
    (job) =>
      job.kind === "speech" &&
      ["queued", "submitting", "running"].includes(job.state),
  );
  const reload = () =>
    perform("reload", async () => {
      const fresh = await request(`/projects/${project.id}`);
      onProjectChange(fresh);
      await onRefresh();
    });
  return (
    <section className="panel workflow-panel">
      <div className="row spread">
        <h2>Briefing → Skript → Material</h2>
        <span className="muted">
          {project.brand_snapshot
            ? `${project.brand_snapshot.name} · v${project.brand_snapshot.version}`
            : "Noch kein Markenprofil"}
        </span>
      </div>
      <p className="muted">
        Speichere Idee und gesprochenen Text. Die Sprachaufnahme entsteht auf
        deinem Mac; Stock-Material suchst und importierst du gezielt.
      </p>
      <div className="grid">
        <label className="field">
          Briefing
          <textarea
            rows="4"
            maxLength="8000"
            value={brief}
            onChange={(event) => setBrief(event.target.value)}
            placeholder="Ziel, Zielgruppe, Kernbotschaft und gewünschter Stil"
          />
          <small>{brief.length}/8000 Zeichen</small>
        </label>
        <label className="field">
          Sprechskript
          <textarea
            rows="4"
            maxLength="12000"
            value={script}
            onChange={(event) => setScript(event.target.value)}
            placeholder="Der genaue Text, den die Stimme sprechen soll."
          />
          <small>{script.length}/12000 Zeichen</small>
        </label>
      </div>
      <div className="row">
        <button
          className="primary"
          disabled={busy || !dirty}
          onClick={() =>
            perform("save", async () => {
              const saved = await request(`/projects/${project.id}`, {
                method: "PATCH",
                body: { brief, script, expected_revision: project.revision },
              });
              onProjectChange(saved);
            })
          }
        >
          {operation === "save"
            ? "Wird gespeichert …"
            : "Briefing & Skript speichern"}
        </button>
        {dirty && <span className="muted">Ungespeicherte Änderungen</span>}
      </div>
      <p className="muted">
        Textänderungen setzen betroffene Freigaben zurück. Skriptvorschläge
        durch einen Online-KI-Dienst sind hier noch nicht aktiviert.
      </p>
      {capabilityError && (
        <p className="error">
          Sprach-/Stock-Funktionen nicht erreichbar: {capabilityError}
        </p>
      )}
      <div className="workflow-columns">
        <div className="workflow-section">
          <h3>1. Lokale Sprachaufnahme</h3>
          <p className="muted">
            Installierte macOS-Stimme, ohne API-Key und ohne kostenpflichtigen
            Aufruf. Verwendet das gespeicherte Sprechskript und erstellt
            Satzuntertitel mit gemessenen Zeitstempeln.
          </p>
          <div className="grid">
            <label className="field">
              Stimme
              <select
                value={voice}
                onChange={(event) => setVoice(event.target.value)}
                disabled={!capabilities?.speech?.available || busy}
              >
                {!voice && <option value="">Keine Stimme verfügbar</option>}
                {capabilities?.speech?.voices?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                    {item.language ? ` · ${item.language}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Sprechtempo
              <input
                type="number"
                min="80"
                max="250"
                value={rate}
                onChange={(event) => setRate(Number(event.target.value))}
              />
              <small>80–250 Wörter pro Minute</small>
            </label>
          </div>
          {capabilities && !capabilities.speech?.available && (
            <p className="notice">
              {capabilities.speech?.reason ||
                "Die lokale Spracherzeugung ist auf diesem System nicht verfügbar."}
            </p>
          )}
          {project.recipe === "spot" && (
            <p className="muted">
              Skriptfreigabe: {project.gates.script || "offen"}. Erforderliche
              Freigaben werden vor dem Start geprüft.
            </p>
          )}
          <button
            disabled={
              busy ||
              dirty ||
              speechRunning ||
              !savedSpeech ||
              !voice ||
              !capabilities?.speech?.available ||
              rate < 80 ||
              rate > 250
            }
            onClick={() =>
              perform("speech", async () => {
                const job = await request(`/projects/${project.id}/speech`, {
                  method: "POST",
                  body: {
                    confirmed: true,
                    expected_revision: project.revision,
                    voice,
                    rate,
                  },
                });
                onJobCreated(job);
              })
            }
          >
            {operation === "speech"
              ? "Wird gestartet …"
              : speechRunning
                ? "Sprache wird erzeugt …"
                : "Sprache lokal erzeugen"}
          </button>
          {dirty ? (
            <p className="muted">Zuerst Briefing und Skript speichern.</p>
          ) : (
            !savedSpeech && (
              <p className="muted">Trage zuerst ein Sprechskript ein.</p>
            )
          )}
        </div>
        <div className="workflow-section">
          <h3>2. Stock-Material finden</h3>
          <p className="muted">
            Die Suche übermittelt deinen Suchbegriff an den gewählten Anbieter.
            Ein Clip wird erst nach deinem Klick auf „Importieren“
            heruntergeladen.
          </p>
          <label className="field">
            Stock-Anbieter
            <select
              value={provider}
              onChange={(event) => {
                setProvider(event.target.value);
                setCandidates([]);
                setSearched(false);
              }}
            >
              {!provider && <option value="">Anbieter werden geladen …</option>}
              {capabilities?.stock?.map((item) => (
                <option
                  key={item.id}
                  value={item.id}
                  disabled={!item.configured}
                >
                  {item.id}
                  {item.configured ? "" : " · API-Key fehlt"}
                </option>
              ))}
            </select>
          </label>
          {!stockProvider?.configured && capabilities && (
            <p className="notice">
              Für Stock-Suche ist ein API-Key des Anbieters nötig. Die lokale
              Sprache und eigene Medien funktionieren weiterhin ohne Key.
            </p>
          )}
          <label className="field">
            Suchbegriff
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Zum Beispiel: coastal sunrise"
              maxLength="200"
            />
          </label>
          <button
            disabled={busy || !stockProvider?.configured || !query.trim()}
            onClick={() =>
              perform("search", async () => {
                const list = await request("/stock/search", {
                  method: "POST",
                  body: {
                    provider,
                    query: query.trim(),
                    confirmed: true,
                    orientation:
                      project.format.width > project.format.height
                        ? "landscape"
                        : "portrait",
                  },
                });
                setCandidates(list);
                setSearched(true);
                setSearchOrigin({ provider, query: query.trim() });
              })
            }
          >
            {operation === "search"
              ? "Suche läuft …"
              : `Bei ${provider || "Anbieter"} suchen`}
          </button>
        </div>
      </div>
      {searched && (
        <div className="stock-results">
          <div className="row spread">
            <h3>
              Ergebnisse: {searchOrigin?.query} · {searchOrigin?.provider}
            </h3>
            <label className="field">
              Importziel
              <select
                value={targetScene}
                onChange={(event) => setTargetScene(event.target.value)}
              >
                <option value="">Als neue Szene hinzufügen</option>
                {project.scenes.map((scene) => (
                  <option value={scene.id} key={scene.id}>
                    Material ersetzen: {scene.title}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {!candidates.length ? (
            <p className="muted">
              Keine passenden Clips gefunden. Versuche einen anderen
              Suchbegriff.
            </p>
          ) : (
            <div className="stock-grid">
              {candidates.map((candidate) => (
                <article className="stock-card" key={candidate.selection_id}>
                  {candidate.preview_url ? (
                    <img
                      src={candidate.preview_url}
                      alt={candidate.title || "Stock-Vorschau"}
                      loading="lazy"
                      referrerPolicy="no-referrer"
                    />
                  ) : (
                    <div className="stock-placeholder">Keine Bildvorschau</div>
                  )}
                  <h4>{candidate.title || candidate.provider}</h4>
                  <p className="muted">
                    {candidate.duration_s}s · {candidate.width}×
                    {candidate.height}
                  </p>
                  <div className="row">
                    <a
                      href={candidate.source_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Quelle & Nutzung ansehen ↗
                    </a>
                    <button
                      disabled={busy || dirty}
                      onClick={() =>
                        perform("import", async () => {
                          const job = await request(
                            `/projects/${project.id}/stock/import`,
                            {
                              method: "POST",
                              body: {
                                selection_id: candidate.selection_id,
                                confirmed: true,
                                expected_revision: project.revision,
                                ...(targetScene
                                  ? { scene_id: targetScene }
                                  : {}),
                              },
                            },
                          );
                          onJobCreated(job);
                        })
                      }
                    >
                      Importieren
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      )}
      {!!workflowJobs.length && (
        <div className="workflow-results">
          <h3>Sprach- und Materialaufträge</h3>
          {workflowJobs.map((job) => (
            <div className="job" key={job.id}>
              <div className="row spread">
                <strong>{jobLabel(job.kind)}</strong>
                <span>{jobStateLabel(job.state)}</span>
              </div>
              <progress max="1" value={job.progress} />
              {job.error && <p className="error">{job.error}</p>}
              {job.state === "complete" && (
                <>
                  <p
                    className={
                      job.result?.applied === false ? "notice" : "muted"
                    }
                  >
                    {job.result?.applied === false
                      ? "Das Projekt wurde während der Verarbeitung geändert. Das Ergebnis wurde erhalten, aber nicht automatisch übernommen."
                      : "Ergebnis erstellt. Lade den aktuellen Projektstand, um die Änderungen in Vorschau und Szenen zu sehen."}
                    {job.result?.duration_s
                      ? ` Dauer: ${job.result.duration_s.toFixed(1)} s.`
                      : ""}
                  </p>
                  {job.result?.warning && (
                    <p className="notice">{job.result.warning}</p>
                  )}
                  {job.kind === "speech" && job.result?.asset_id && (
                    <audio
                      controls
                      src={assetUrl(job.result.asset_id)}
                      preload="none"
                    />
                  )}
                  {job.result?.asset_id && (
                    <a
                      className="download"
                      href={assetUrl(job.result.asset_id)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Erzeugte Datei öffnen ↗
                    </a>
                  )}
                  {!!job.result?.captions?.length && (
                    <details>
                      <summary>Satzuntertitel anzeigen</summary>
                      <pre className="caption-result">
                        {JSON.stringify(job.result.captions, null, 2)}
                      </pre>
                    </details>
                  )}
                  <button disabled={busy || dirty} onClick={reload}>
                    Aktuellen Projektstand laden
                  </button>
                  {dirty && (
                    <p className="muted">
                      Speichere zuerst deine Texte, damit sie beim Laden
                      erhalten bleiben.
                    </p>
                  )}
                  {job.result?.applied === false && (
                    <p className="muted">
                      Du kannst die erhaltene Audiodatei bzw. den Clip in den
                      Medienfeldern auswählen und Untertitel aus dem Ergebnis
                      übernehmen. Bestehende Eingaben bleiben erhalten.
                    </p>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
