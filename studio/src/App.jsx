import React, { useCallback, useEffect, useState } from "react";
import { Player } from "@remotion/player";
import { VideoComposition } from "../../renderer/src/Composition.jsx";
import { request, assetUrl } from "./api.js";
const brandModules = import.meta.glob("./BrandPanel.jsx", { eager: true });
const BrandPanel = Object.values(brandModules)[0]?.default;
const MODES = {
  local: "Eigenes Material",
  text: "Nur Text",
  start: "Startbild",
  end: "Nur Endbild",
  start_end: "Start + Ende",
};
const GATES = {
  concept: "Konzept",
  script: "Skript",
  storyboard: "Storyboard",
  clips: "Clips",
  final: "Endabnahme",
};
function AssetSelect({
  assets,
  kind,
  value,
  onChange,
  label,
  empty = "— Keines —",
}) {
  return (
    <label className="field">
      {label}
      <select
        value={value || ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">{empty}</option>
        {assets
          .filter((a) => (Array.isArray(kind) ? kind : [kind]).includes(a.kind))
          .map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
      </select>
    </label>
  );
}
function Upload({ onUpload, accept, label = "Datei importieren" }) {
  return (
    <label className="upload">
      {label}
      <input
        type="file"
        accept={accept}
        onChange={async (e) => {
          const file = e.target.files[0];
          if (file) await onUpload(file);
          e.target.value = "";
        }}
      />
    </label>
  );
}
function SceneEditor({
  scene,
  project,
  assets,
  models,
  busy,
  onSave,
  onUpload,
  onGenerate,
  onSelect,
  onDelete,
}) {
  const [draft, setDraft] = useState(scene);
  const [confirm, setConfirm] = useState(false);
  const edit = (key, value) => setDraft((d) => ({ ...d, [key]: value }));
  const changed = JSON.stringify(draft) !== JSON.stringify(scene);
  const model = models.find((m) => m.id === draft.model);
  const supported = !!model?.modes?.includes(draft.mode);
  const configured = model?.configured !== false;
  const patch = () =>
    Object.fromEntries(
      [
        "title",
        "mode",
        "prompt",
        "duration_s",
        "start_asset_id",
        "end_asset_id",
        "predecessor_scene_id",
        "source_asset_id",
        "model",
        "onscreen_text",
      ].map((k) => [k, draft[k]]),
    );
  const imageField = (which, label) => (
    <div className="frame-input">
      <AssetSelect
        assets={assets}
        kind="image"
        label={label}
        value={draft[which]}
        onChange={(v) => {
          edit(which, v);
          if (which === "start_asset_id" && v)
            edit("predecessor_scene_id", null);
        }}
      />
      {draft[which] && <img alt={label} src={assetUrl(draft[which])} />}
      <Upload
        label={`${label} hochladen`}
        accept="image/*"
        onUpload={async (file) => {
          const asset = await onUpload(file);
          if (asset) {
            edit(which, asset.id);
            if (which === "start_asset_id") edit("predecessor_scene_id", null);
          }
        }}
      />
    </div>
  );
  return (
    <section className="panel scene-editor">
      <div className="row spread">
        <h2>Szene bearbeiten</h2>
        <button className="danger subtle" disabled={busy} onClick={onDelete}>
          Entfernen
        </button>
      </div>
      {scene.stale && (
        <p className="notice">
          Diese Szene ist veraltet. Eingaben oder die Vorgängerszene wurden
          geändert. Bitte neuen Take erzeugen bzw. prüfen.
        </p>
      )}
      <label className="field">
        Name
        <input
          value={draft.title}
          onChange={(e) => edit("title", e.target.value)}
        />
      </label>
      <div className="grid">
        <label className="field">
          Modus
          <select
            value={draft.mode}
            onChange={(e) => edit("mode", e.target.value)}
          >
            {Object.entries(MODES).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Dauer in Sekunden
          <input
            type="number"
            min="0.1"
            max="120"
            step="0.1"
            value={draft.duration_s}
            onChange={(e) => edit("duration_s", +e.target.value)}
          />
        </label>
      </div>
      {draft.mode === "local" ? (
        <>
          <AssetSelect
            assets={assets}
            kind={["video", "image"]}
            label="Lokales Video / Bild"
            value={draft.source_asset_id}
            onChange={(v) => edit("source_asset_id", v)}
          />
          <Upload
            accept="video/*,image/*"
            onUpload={async (file) => {
              const a = await onUpload(file);
              if (a) edit("source_asset_id", a.id);
            }}
          />
        </>
      ) : (
        <>
          <label className="field">
            Bewegung und Bildinhalt
            <textarea
              rows="4"
              value={draft.prompt}
              onChange={(e) => edit("prompt", e.target.value)}
              placeholder="Beschreibe Szene, Bewegung, Kamera und Licht."
            />
          </label>
          <label className="field">
            Videomodell
            <select
              value={draft.model}
              onChange={(e) => edit("model", e.target.value)}
            >
              {!models.some((m) => m.id === draft.model) && (
                <option value={draft.model}>{draft.model}</option>
              )}
              {models.map((m) => (
                <option
                  key={m.id}
                  value={m.id}
                  disabled={!m.modes?.includes(draft.mode)}
                >
                  {m.label}
                  {m.modes?.includes(draft.mode)
                    ? ""
                    : " · Modus nicht unterstützt"}
                </option>
              ))}
            </select>
          </label>
          {draft.mode === "end" && (
            <p className="notice">
              Nur ein Endbild funktioniert nur mit einem Modell, das diesen
              Modus unterstützt. Alternativ zusätzlich ein Startbild hochladen
              und „Start + Ende“ wählen. Ein Endbild wird niemals
              stillschweigend weggelassen.
            </p>
          )}
          {!configured && (
            <p className="notice">
              Der Anbieter ist noch nicht konfiguriert. Für die erste Vorschau
              kannst du eigenes Material verwenden.
            </p>
          )}
          {!supported && (
            <p className="notice">
              Das gewählte Modell unterstützt diesen Modus nicht. Wähle ein
              passendes Modell oder ändere den Modus.
            </p>
          )}
          {["start", "start_end"].includes(draft.mode) &&
            imageField("start_asset_id", "Startbild")}
          {["end", "start_end"].includes(draft.mode) &&
            imageField("end_asset_id", "Endbild")}
          {["start", "start_end"].includes(draft.mode) && (
            <label className="field">
              Alternativ: letzter Frame einer Vorgängerszene
              <select
                value={draft.predecessor_scene_id || ""}
                onChange={(e) => {
                  edit("predecessor_scene_id", e.target.value || null);
                  if (e.target.value) edit("start_asset_id", null);
                }}
              >
                <option value="">— Keine Verknüpfung —</option>
                {project.scenes
                  .slice(
                    0,
                    project.scenes.findIndex((s) => s.id === scene.id),
                  )
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.title}
                    </option>
                  ))}
              </select>
            </label>
          )}
        </>
      )}
      <label className="field">
        Text im Video
        <input
          value={draft.onscreen_text}
          onChange={(e) => edit("onscreen_text", e.target.value)}
        />
      </label>
      <div className="row">
        <button
          className="primary"
          disabled={busy || !changed}
          onClick={() => onSave(patch())}
        >
          Änderungen speichern
        </button>
        {draft.mode !== "local" && (
          <button
            disabled={busy || changed || !supported || !configured}
            onClick={() => setConfirm(true)}
          >
            Video erzeugen …
          </button>
        )}
      </div>
      {changed && (
        <p className="muted">Vor einer Generierung Änderungen speichern.</p>
      )}
      {confirm && (
        <div
          role="dialog"
          aria-label="Kostenpflichtige Generierung bestätigen"
          className="confirm"
        >
          <h3>Generierung bestätigen</h3>
          <p>
            Dieser Aufruf an {draft.model} kann Kosten verursachen. Er verwendet
            die gespeicherten Bilder, Markenregeln und Projektrevision{" "}
            {project.revision}. Es wird genau ein Auftrag gestartet.
          </p>
          <div className="row">
            <button
              className="primary"
              disabled={busy}
              onClick={() => {
                setConfirm(false);
                onGenerate();
              }}
            >
              Kostenpflichtig erzeugen
            </button>
            <button onClick={() => setConfirm(false)}>Abbrechen</button>
          </div>
        </div>
      )}
      {!!scene.takes?.length && (
        <>
          <h3>Takes</h3>
          {scene.takes.map((take, i) => (
            <div className="take" key={take.id}>
              <span>
                Take {i + 1} · {take.model}
              </span>
              <a
                href={assetUrl(take.asset_id)}
                target="_blank"
                rel="noreferrer"
              >
                Ansehen
              </a>
              <button
                disabled={busy || scene.selected_take_id === take.id}
                onClick={() => onSelect(take.id)}
              >
                {scene.selected_take_id === take.id
                  ? "Ausgewählt"
                  : "Auswählen"}
              </button>
            </div>
          ))}
        </>
      )}
    </section>
  );
}
function AudioPanel({ project, assets, onSave, onUpload, busy }) {
  const [audio, setAudio] = useState(project.audio);
  const [captions, setCaptions] = useState(
    JSON.stringify(project.captions, null, 2),
  );
  const [error, setError] = useState("");
  return (
    <section className="panel">
      <h2>Ton & Untertitel</h2>
      <Upload accept="audio/*" onUpload={onUpload} label="Audio importieren" />
      <AssetSelect
        assets={assets}
        kind="audio"
        label="Sprecheraufnahme"
        value={audio.narration_asset_id}
        onChange={(v) => setAudio({ ...audio, narration_asset_id: v })}
      />
      <AssetSelect
        assets={assets}
        kind="audio"
        label="Musik"
        value={audio.music_asset_id}
        onChange={(v) => setAudio({ ...audio, music_asset_id: v })}
      />
      <div className="grid">
        <label className="field">
          Sprecherlautstärke
          <input
            type="number"
            min="0"
            max="2"
            step="0.05"
            value={audio.narration_gain}
            onChange={(e) =>
              setAudio({ ...audio, narration_gain: +e.target.value })
            }
          />
        </label>
        <label className="field">
          Musiklautstärke
          <input
            type="number"
            min="0"
            max="1"
            step="0.05"
            value={audio.music_gain}
            onChange={(e) =>
              setAudio({ ...audio, music_gain: +e.target.value })
            }
          />
        </label>
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={audio.clip_audio}
          onChange={(e) => setAudio({ ...audio, clip_audio: e.target.checked })}
        />{" "}
        Originalton der Clips verwenden
      </label>
      <label className="field">
        Untertitel mit Zeitstempeln (JSON)
        <textarea
          rows="7"
          value={captions}
          onChange={(e) => setCaptions(e.target.value)}
          spellCheck="false"
        />
      </label>
      <p className="muted">
        Liste aus text, start_ms, end_ms. Für Karaoke zusätzlich words mit
        eigenen Zeitstempeln. Keine geschätzten Wortzeiten.
      </p>
      {error && <p className="error">{error}</p>}
      <button
        disabled={busy}
        onClick={() => {
          try {
            const cues = JSON.parse(captions);
            if (!Array.isArray(cues))
              throw new Error("Untertitel müssen eine Liste sein.");
            setError("");
            onSave({ audio, captions: cues });
          } catch (e) {
            setError(e.message);
          }
        }}
      >
        Ton & Untertitel speichern
      </button>
    </section>
  );
}
function BrandAssignment({ project, brands, busy, onApply }) {
  const [id, setId] = useState(project.brand_snapshot?.id || "");
  const [version, setVersion] = useState("");
  return (
    <div>
      <label className="field">
        Markenprofil
        <select
          value={id}
          onChange={(e) => {
            setId(e.target.value);
            setVersion("");
          }}
        >
          <option value="">— Marke wählen —</option>
          {brands.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name} · aktuell v{b.version}
            </option>
          ))}
        </select>
      </label>
      <div className="row">
        <input
          aria-label="Markenversion"
          type="number"
          min="1"
          placeholder="Neueste Version"
          value={version}
          onChange={(e) => setVersion(e.target.value)}
          style={{ width: 125 }}
        />
        <button
          disabled={busy || !id}
          onClick={() =>
            onApply({ brand_id: id, ...(version ? { version: +version } : {}) })
          }
        >
          Anwenden
        </button>
      </div>
      <p className="muted">
        {project.brand_snapshot
          ? `Gespeichert: ${project.brand_snapshot.name} v${project.brand_snapshot.version}. `
          : ""}
        Anwenden aktualisiert das Markenprofil und setzt betroffene Freigaben
        und den Export zurück.
      </p>
    </div>
  );
}
function RecoveryControls({ job, busy, onResume, onCancel }) {
  const [providerId, setProviderId] = useState(job.provider_request_id || "");
  if (job.kind === "render" && ["queued", "running"].includes(job.state)) {
    return (
      <button disabled={busy} onClick={onCancel}>
        Export abbrechen
      </button>
    );
  }
  if (
    job.kind !== "generation" ||
    !["unknown", "interrupted"].includes(job.state)
  )
    return null;
  return (
    <div className="recovery">
      <p className="muted">
        Nur einen vorhandenen Anbieterauftrag abgleichen. Dabei wird kein neuer
        Generierungsauftrag erstellt.
      </p>
      {!job.provider_request_id && (
        <label className="field">
          Vorhandene Auftrags-ID beim Anbieter
          <input
            value={providerId}
            onChange={(e) => setProviderId(e.target.value)}
            placeholder="Auftrags-ID aus dem Anbieter-Dashboard"
          />
        </label>
      )}
      <button
        disabled={busy || !providerId.trim()}
        onClick={() => onResume(providerId.trim())}
      >
        Bekannten Auftrag prüfen
      </button>
    </div>
  );
}
function Preview({ manifest, error }) {
  if (!manifest)
    return (
      <div className="preview-empty">
        <span>Vorschau</span>
        <p>{error || "Szene und Material auswählen, dann Vorschau laden."}</p>
      </div>
    );
  return (
    <Player
      key={`${manifest.project_id}-${manifest.project_revision}`}
      component={VideoComposition}
      inputProps={{ manifest }}
      durationInFrames={manifest.duration_frames}
      fps={manifest.format.fps}
      compositionWidth={manifest.format.width}
      compositionHeight={manifest.format.height}
      controls
      style={{
        width: "100%",
        maxHeight: "62vh",
        aspectRatio: `${manifest.format.width}/${manifest.format.height}`,
      }}
      errorFallback={({ error }) => (
        <div className="error">Vorschau fehlgeschlagen: {error.message}</div>
      )}
    />
  );
}
export default function App() {
  const [projects, setProjects] = useState([]),
    [project, setProject] = useState(null),
    [assets, setAssets] = useState([]),
    [brands, setBrands] = useState([]),
    [models, setModels] = useState([]),
    [jobs, setJobs] = useState([]);
  const [active, setActive] = useState(""),
    [tab, setTab] = useState("edit"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [manifest, setManifest] = useState(null),
    [previewError, setPreviewError] = useState("");
  const [newTitle, setNewTitle] = useState(""),
    [recipe, setRecipe] = useState("free");
  const refresh = useCallback(async () => {
    const [p, a, b, m, j] = await Promise.all([
      request("/projects"),
      request("/assets"),
      request("/brands"),
      request("/models"),
      request("/jobs"),
    ]);
    setProjects(p);
    setAssets(a);
    setBrands(b);
    setModels(Array.isArray(m) ? m : m.models || []);
    setJobs(j);
  }, []);
  const run = async (fn) => {
    setBusy(true);
    setError("");
    try {
      return await fn();
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, [refresh]);
  useEffect(() => {
    let alive = true;
    const timer = setInterval(
      () =>
        request("/jobs")
          .then((j) => {
            if (alive) setJobs(j);
          })
          .catch(() => {}),
      2500,
    );
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  const updateProject = (p) => {
    setProject(p);
    setManifest(null);
    setPreviewError("Änderungen gespeichert. Vorschau neu laden.");
    setProjects((list) => [p, ...list.filter((x) => x.id !== p.id)]);
  };
  const loadProject = (id) =>
    run(async () => {
      const p = await request(`/projects/${id}`);
      updateProject(p);
      setActive(p.scenes[0]?.id || "");
    });
  const change = async (path, body, method = "POST") =>
    run(async () => {
      const p = await request(`/projects/${project.id}${path}`, {
        method,
        body,
      });
      updateProject(p);
      return p;
    });
  const upload = (file) =>
    run(async () => {
      const form = new FormData();
      form.append("file", file);
      const asset = await request("/assets", { method: "POST", body: form });
      setAssets((a) => [asset, ...a.filter((x) => x.id !== asset.id)]);
      return asset;
    });
  const scene = project?.scenes.find((s) => s.id === active);
  const projectJobs = jobs.filter((j) => j.project_id === project?.id);
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brandmark">
          S<span>SpotForge</span>
        </div>
        <div className="subbrand">LOCAL VIDEO STUDIO</div>
        <nav>
          <button
            className={tab === "edit" ? "selected" : ""}
            onClick={() => setTab("edit")}
          >
            Projekte
          </button>
          <button
            className={tab === "brands" ? "selected" : ""}
            onClick={() => setTab("brands")}
          >
            Marken & Styleguides
          </button>
        </nav>
        <h3>Deine Projekte</h3>
        <div className="project-list">
          {projects.map((p) => (
            <button
              key={p.id}
              className={project?.id === p.id ? "selected" : ""}
              onClick={() => {
                setTab("edit");
                loadProject(p.id);
              }}
            >
              {p.title}
              <small>
                {p.scenes.length} Szenen · Rev. {p.revision}
              </small>
            </button>
          ))}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run(async () => {
              const p = await request("/projects", {
                method: "POST",
                body: { title: newTitle || "Neues Video", recipe },
              });
              updateProject(p);
              setActive("");
              setNewTitle("");
              setTab("edit");
            });
          }}
        >
          <label className="field">
            Neues Projekt
            <input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="Titel"
            />
          </label>
          <select
            aria-label="Rezept"
            value={recipe}
            onChange={(e) => setRecipe(e.target.value)}
          >
            <option value="free">Freies Video</option>
            <option value="spot">Spot mit Freigaben</option>
          </select>
          <button className="primary full" disabled={busy}>
            Projekt erstellen
          </button>
        </form>
        <p className="sidebar-note">
          Lokal auf deinem Mac.
          <br />
          Generierung nur nach Bestätigung.
        </p>
      </aside>
      <main>
        <header>
          <div>
            <h1>
              {tab === "brands"
                ? "Marken & Styleguides"
                : project?.title || "Dein nächstes Video"}
            </h1>
            <p className="muted">
              {tab === "brands"
                ? "Corporate Identity als versioniertes Profil."
                : "Szenen steuern. Marke bewahren. Gemeinsam komponieren."}
            </p>
          </div>
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                await refresh();
                if (project) {
                  const p = await request(`/projects/${project.id}`);
                  updateProject(p);
                }
              })
            }
          >
            Aktualisieren
          </button>
        </header>
        {error && (
          <div role="alert" className="error banner">
            {error}
            <button aria-label="Fehler schließen" onClick={() => setError("")}>
              ×
            </button>
          </div>
        )}
        {tab === "brands" ? (
          BrandPanel ? (
            <BrandPanel
              brands={brands}
              assets={assets}
              onRefresh={refresh}
              onError={(e) => setError(typeof e === "string" ? e : e.message)}
            />
          ) : (
            <section className="panel">
              Die Markenverwaltung wird mit dem Markenmodul bereitgestellt.
            </section>
          )
        ) : !project ? (
          <section className="welcome panel">
            <h2>Vom Material zum fertigen Video</h2>
            <p>
              Projekt anlegen, eigene Bilder oder Clips importieren und die
              erste Vorschau mit Logo, Schrift, Ton und Untertiteln ansehen.
            </p>
          </section>
        ) : (
          <>
            <div className="project-controls panel">
              <label className="field">
                Titel
                <input
                  key={`${project.id}-${project.title}`}
                  defaultValue={project.title}
                  onBlur={(e) => {
                    if (e.target.value && e.target.value !== project.title)
                      change(
                        "",
                        {
                          title: e.target.value,
                          expected_revision: project.revision,
                        },
                        "PATCH",
                      );
                  }}
                />
              </label>
              <label className="field">
                Format
                <select
                  value={`${project.format.width}x${project.format.height}`}
                  onChange={(e) => {
                    const [width, height] = e.target.value
                      .split("x")
                      .map(Number);
                    change(
                      "",
                      {
                        format: { width, height, fps: project.format.fps },
                        expected_revision: project.revision,
                      },
                      "PATCH",
                    );
                  }}
                >
                  <option value="1080x1920">Hochformat · 9:16</option>
                  <option value="1920x1080">Querformat · 16:9</option>
                  <option value="1080x1080">Quadratisch · 1:1</option>
                  {!["1080x1920", "1920x1080", "1080x1080"].includes(
                    `${project.format.width}x${project.format.height}`,
                  ) && (
                    <option
                      value={`${project.format.width}x${project.format.height}`}
                    >
                      {project.format.width}×{project.format.height}
                    </option>
                  )}
                </select>
              </label>
              <BrandAssignment
                key={`${project.id}-${project.brand_snapshot?.version}`}
                project={project}
                brands={brands}
                busy={busy}
                onApply={(body) => change("/brand", body)}
              />
            </div>
            <div className="workspace">
              <div>
                <section className="panel preview">
                  <div className="row spread">
                    <h2>Videovorschau</h2>
                    <button
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          try {
                            const m = await request(
                              `/projects/${project.id}/manifest`,
                            );
                            setManifest(m);
                            setPreviewError("");
                          } catch (e) {
                            setManifest(null);
                            setPreviewError(e.message);
                          }
                        })
                      }
                    >
                      Vorschau laden
                    </button>
                  </div>
                  <Preview manifest={manifest} error={previewError} />
                </section>
                <section className="panel">
                  <div className="row spread">
                    <h2>Szenen</h2>
                    <button
                      disabled={busy}
                      onClick={async () => {
                        const p = await change("/scenes", {
                          title: `Szene ${project.scenes.length + 1}`,
                          mode: "local",
                        });
                        if (p) setActive(p.scenes.at(-1).id);
                      }}
                    >
                      + Szene
                    </button>
                  </div>
                  <div className="scene-strip">
                    {project.scenes.map((s, i) => (
                      <button
                        key={s.id}
                        className={`scene-card ${active === s.id ? "selected" : ""}`}
                        onClick={() => setActive(s.id)}
                      >
                        <span>{String(i + 1).padStart(2, "0")}</span>
                        <strong>{s.title}</strong>
                        <small>
                          {s.duration_s}s · {MODES[s.mode]}{" "}
                          {s.stale ? "· Veraltet" : ""}
                        </small>
                      </button>
                    ))}
                    {!project.scenes.length && (
                      <p className="muted">Füge die erste Szene hinzu.</p>
                    )}
                  </div>
                </section>
                <AudioPanel
                  key={`${project.id}-${project.revision}`}
                  project={project}
                  assets={assets}
                  busy={busy}
                  onUpload={upload}
                  onSave={(body) =>
                    change(
                      "",
                      { ...body, expected_revision: project.revision },
                      "PATCH",
                    )
                  }
                />
              </div>
              <div>
                {scene ? (
                  <SceneEditor
                    key={`${scene.id}-${project.revision}`}
                    scene={scene}
                    project={project}
                    assets={assets}
                    models={models}
                    busy={busy}
                    onUpload={upload}
                    onSave={(body) =>
                      change(`/scenes/${scene.id}`, body, "PATCH")
                    }
                    onSelect={(id) =>
                      change(`/scenes/${scene.id}/select`, { take_id: id })
                    }
                    onDelete={async () => {
                      const p = await change(
                        `/scenes/${scene.id}`,
                        undefined,
                        "DELETE",
                      );
                      if (p) setActive(p.scenes[0]?.id || "");
                    }}
                    onGenerate={() =>
                      run(async () => {
                        await request(
                          `/projects/${project.id}/scenes/${scene.id}/generate`,
                          {
                            method: "POST",
                            body: {
                              confirmed: true,
                              expected_revision: project.revision,
                            },
                          },
                        );
                        await refresh();
                      })
                    }
                  />
                ) : (
                  <section className="panel muted">
                    Wähle eine Szene zum Bearbeiten.
                  </section>
                )}
                {project.recipe === "spot" && (
                  <section className="panel">
                    <h2>Freigaben</h2>
                    {Object.entries(GATES).map(([id, label]) => (
                      <div className="gate" key={id}>
                        <span>{label}</span>
                        <span className="muted">
                          {project.gates[id] || "offen"}
                        </span>
                        <button
                          disabled={busy}
                          onClick={() =>
                            change(`/gates/${id}`, { approve: true })
                          }
                        >
                          Freigeben
                        </button>
                      </div>
                    ))}
                  </section>
                )}
                <section className="panel">
                  <h2>Export & Aufträge</h2>
                  <p className="muted">
                    Der Export nutzt vorhandene Takes und startet keine neue
                    KI-Generierung.
                  </p>
                  <button
                    className="primary full"
                    disabled={busy || !project.scenes.length}
                    onClick={() =>
                      run(async () => {
                        await request(`/projects/${project.id}/render`, {
                          method: "POST",
                        });
                        await refresh();
                      })
                    }
                  >
                    Video rendern
                  </button>
                  {projectJobs.map((j) => (
                    <div className="job" key={j.id}>
                      <div className="row spread">
                        <strong>
                          {j.kind === "render" ? "Export" : "Generierung"}
                        </strong>
                        <span>{j.state}</span>
                      </div>
                      <progress max="1" value={j.progress} />
                      {j.error && <p className="error">{j.error}</p>}
                      {j.state === "complete" && j.kind === "render" && (
                        <div className="row">
                          <a
                            className="download"
                            href={`/api/outputs/${j.id}/preview`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Export ansehen ↗
                          </a>
                          <a
                            className="download"
                            href={`/api/outputs/${j.id}`}
                            download
                          >
                            MP4 herunterladen ↗
                          </a>
                        </div>
                      )}
                      {j.state === "complete" && j.kind === "generation" && (
                        <button onClick={() => loadProject(project.id)}>
                          Take im Projekt laden
                        </button>
                      )}
                      <RecoveryControls
                        job={j}
                        busy={busy}
                        onCancel={() =>
                          run(async () => {
                            await request(`/jobs/${j.id}/cancel`, {
                              method: "POST",
                            });
                            await refresh();
                          })
                        }
                        onResume={(providerId) =>
                          run(async () => {
                            await request(`/jobs/${j.id}/resume`, {
                              method: "POST",
                              body: {
                                confirmed: true,
                                ...(j.provider_request_id
                                  ? {}
                                  : { provider_request_id: providerId }),
                              },
                            });
                            await refresh();
                          })
                        }
                      />
                    </div>
                  ))}
                </section>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
