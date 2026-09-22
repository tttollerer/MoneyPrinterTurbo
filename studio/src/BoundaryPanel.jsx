import React, { useState } from "react";
import { assetUrl, request } from "./api.js";
import { boundarySubmission, initialBoundaries } from "./campaign.js";
export default function BoundaryPanel({
  campaignId,
  motifKey,
  project,
  assets,
  onUpload,
  onApplied,
  onRun,
  busy,
}) {
  const [frames, setFrames] = useState(() => initialBoundaries(project));
  const [prompts, setPrompts] = useState(() =>
    project.scenes.length ? project.scenes.map((scene) => scene.prompt) : [""],
  );
  const [durations, setDurations] = useState(() =>
    project.scenes.length
      ? project.scenes.map((scene) => scene.duration_s)
      : [5],
  );
  const [confirmed, setConfirmed] = useState(false);
  const setFrame = (index, id) => {
    setFrames((current) =>
      current.map((value, i) => (i === index ? id : value)),
    );
    setConfirmed(false);
  };
  let body, error;
  try {
    body = boundarySubmission({
      project,
      assetIds: frames,
      prompts,
      durations,
    });
  } catch (e) {
    error = e.message;
  }
  return (
    <section className="panel boundary-panel">
      <h2>Rahmenbilder als zusammenhängende Folge</h2>
      <p className="muted">
        Bild 1 → Bild 2 bildet Szene 1. Bild 2 → Bild 3 bildet Szene 2. Das
        gemeinsame Bild wird damit Ende der vorherigen und Start der nächsten
        Szene. Nur hochgeladene oder vorhandene Bilddateien zählen als
        zugeordnet.
      </p>
      <div className="boundary-strip">
        {frames.map((id, index) => (
          <div className="boundary-card" key={index}>
            <strong>Bild {index + 1}</strong>
            {id ? (
              <img src={assetUrl(id)} alt={`Rahmenbild ${index + 1}`} />
            ) : (
              <div className="frame-placeholder">Datei fehlt</div>
            )}
            <select
              aria-label={`Bild ${index + 1} auswählen`}
              value={id}
              onChange={(event) => setFrame(index, event.target.value)}
            >
              <option value="">— Bild zuordnen —</option>
              {assets
                .filter((a) => a.kind === "image")
                .map((a) => (
                  <option value={a.id} key={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
            <label className="upload">
              Bild hochladen
              <input
                type="file"
                accept="image/*"
                disabled={busy}
                onChange={async (event) => {
                  const file = event.target.files[0];
                  event.target.value = "";
                  if (file) {
                    const asset = await onUpload(file);
                    if (asset) setFrame(index, asset.id);
                  }
                }}
              />
            </label>
            <small>
              {index === 0
                ? "Start von Szene 1"
                : index === frames.length - 1
                  ? `Ende von Szene ${index}`
                  : `Ende von Szene ${index} / Start von Szene ${index + 1}`}
            </small>
          </div>
        ))}
      </div>
      {!project.scenes.length && (
        <button
          disabled={busy || frames.length >= 21}
          onClick={() => {
            setFrames((current) => [...current, ""]);
            setPrompts((current) => [...current, ""]);
            setDurations((current) => [...current, 5]);
            setConfirmed(false);
          }}
        >
          + Weiteres Rahmenbild
        </button>
      )}
      <datalist id="clip-durations">
        <option value="5" />
        <option value="10" />
      </datalist>
      {project.scenes.some(
        (scene, index) =>
          index > 0 &&
          scene.start_asset_id &&
          project.scenes[index - 1].end_asset_id &&
          scene.start_asset_id !== project.scenes[index - 1].end_asset_id,
      ) && (
        <p className="notice">
          Bisherige Bildpaare sind nicht durchgehend verbunden. Übernehmen setzt
          für jeden Übergang dasselbe Bild als Ende und folgenden Start.
        </p>
      )}
      <div className="boundary-scenes">
        {prompts.map((prompt, index) => (
          <div className="grid" key={index}>
            <label className="field">
              Szene {index + 1} · Bild {index + 1} → {index + 2}
              <textarea
                rows="2"
                value={prompt}
                onChange={(event) => {
                  setPrompts((current) =>
                    current.map((v, i) =>
                      i === index ? event.target.value : v,
                    ),
                  );
                  setConfirmed(false);
                }}
                placeholder="Bewegung zwischen den Bildern beschreiben"
              />
            </label>
            <label className="field">
              Dauer
              <input
                type="number"
                min="0.1"
                max="120"
                step="0.1"
                list="clip-durations"
                value={durations[index]}
                onChange={(event) => {
                  setDurations((current) =>
                    current.map((v, i) =>
                      i === index ? Number(event.target.value) : v,
                    ),
                  );
                  setConfirmed(false);
                }}
              />
              <small>Für Kling sind 5 oder 10 Sekunden vorgesehen.</small>
            </label>
          </div>
        ))}
      </div>
      {error && <p className="muted">{error}</p>}
      <label className="check">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        Diese Bildfolge als Start-/Endbilder übernehmen. Betroffene Freigaben
        und Takes werden neu zu prüfen sein.
      </label>
      <button
        className="primary"
        disabled={busy || !body || !confirmed}
        onClick={() =>
          onRun(async () => {
            const result = await request(
              `/campaigns/${campaignId}/motifs/${encodeURIComponent(motifKey)}/frames`,
              { method: "POST", body },
            );
            onApplied(result.project || result);
          })
        }
      >
        Rahmenbilder zuordnen · keine Generierung
      </button>
    </section>
  );
}
