import React, { useEffect, useState } from "react";
import { request } from "./api.js";
import { campaignExample, motifProgress } from "./campaign.js";
import BatchPanel from "./BatchPanel.jsx";
import VariantEditor from "./VariantEditor.jsx";
const freshVariant = (index) => ({
  key: `fassung-${crypto.randomUUID().slice(0, 8)}`,
  title: `Fassung ${index + 1}`,
  audience: "",
  cta: "",
  endcard_text: "",
  duration_s: 3,
});
function MotifForm({ busy, onSave, onCancel }) {
  const [title, setTitle] = useState(""),
    [brief, setBrief] = useState(""),
    [script, setScript] = useState(""),
    [count, setCount] = useState(3),
    [variants, setVariants] = useState([]);
  const updateVariant = (index, key, value) =>
    setVariants((list) =>
      list.map((item, i) => (i === index ? { ...item, [key]: value } : item)),
    );
  return (
    <form
      className="panel motif-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({
          key: `motiv-${crypto.randomUUID().slice(0, 8)}`,
          title,
          brief,
          script,
          shots: Array.from({ length: count }, (_, index) => ({
            title: `Szene ${index + 1}`,
            prompt: "",
            duration_s: 5,
          })),
          variants,
        });
      }}
    >
      <h2>Neues Motiv</h2>
      <div className="grid">
        <label className="field">
          Motivname
          <input
            required
            maxLength="120"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label className="field">
          Szenen vorbereiten
          <input
            type="number"
            min="0"
            max="100"
            value={count}
            onChange={(event) => setCount(Number(event.target.value))}
          />
          <small>Leere Szenen, noch ohne Bilder oder generierte Clips.</small>
        </label>
      </div>
      <label className="field">
        Motiv-Briefing
        <textarea
          rows="3"
          maxLength="8000"
          value={brief}
          onChange={(event) => setBrief(event.target.value)}
        />
      </label>
      <label className="field">
        Sprechskript
        <textarea
          rows="3"
          maxLength="12000"
          value={script}
          onChange={(event) => setScript(event.target.value)}
        />
      </label>
      <div className="row spread">
        <h3>Geplante Zielgruppenfassungen</h3>
        <button
          type="button"
          disabled={variants.length >= 20}
          onClick={() =>
            setVariants((list) => [...list, freshVariant(list.length)])
          }
        >
          + Fassung
        </button>
      </div>
      {variants.map((variant, index) => (
        <div className="variant-form" key={index}>
          <div className="row spread">
            <strong>Fassung {index + 1}</strong>
            <button
              type="button"
              className="subtle"
              onClick={() =>
                setVariants((list) => list.filter((_, i) => i !== index))
              }
            >
              Entfernen
            </button>
          </div>
          <div className="grid">
            <label className="field">
              Titel
              <input
                required
                value={variant.title}
                maxLength="100"
                onChange={(event) =>
                  updateVariant(index, "title", event.target.value)
                }
              />
            </label>
            <label className="field">
              Zielgruppe
              <input
                value={variant.audience}
                maxLength="500"
                onChange={(event) =>
                  updateVariant(index, "audience", event.target.value)
                }
              />
            </label>
          </div>
          <label className="field">
            Handlungsaufforderung (CTA)
            <input
              value={variant.cta}
              maxLength="300"
              onChange={(event) =>
                updateVariant(index, "cta", event.target.value)
              }
            />
          </label>
          <label className="field">
            Text der Schlusskarte
            <textarea
              rows="2"
              value={variant.endcard_text}
              maxLength="500"
              onChange={(event) =>
                updateVariant(index, "endcard_text", event.target.value)
              }
            />
          </label>
          <label className="field">
            Schlusskarte in Sekunden
            <input
              type="number"
              min="1"
              max="10"
              step="0.5"
              value={variant.duration_s}
              onChange={(event) =>
                updateVariant(index, "duration_s", Number(event.target.value))
              }
            />
          </label>
        </div>
      ))}
      <p className="muted">
        Fassungen übernehmen später die freigegebenen Motiv-Clips. Schlusskarten
        werden lokal gerendert. Es werden hier noch keine Videos erzeugt oder
        Freigaben erteilt.
      </p>
      <div className="row">
        <button className="primary" disabled={busy || !title.trim()}>
          {busy ? "Wird angelegt …" : "Motiv anlegen"}
        </button>
        <button type="button" onClick={onCancel}>
          Abbrechen
        </button>
      </div>
    </form>
  );
}
function CampaignMetadata({ campaign, busy, onRun, onSaved }) {
  const [title, setTitle] = useState(campaign.title),
    [brief, setBrief] = useState(campaign.brief);
  return (
    <details className="campaign-metadata">
      <summary>Kampagnentitel & Briefing bearbeiten</summary>
      <label className="field">
        Titel
        <input
          value={title}
          maxLength="120"
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <label className="field">
        Kampagnenbriefing
        <textarea
          rows="3"
          value={brief}
          maxLength="8000"
          onChange={(event) => setBrief(event.target.value)}
        />
      </label>
      <p className="muted">
        Ändert die Kampagnenbeschreibung. Bereits angelegte Motivbriefings
        bleiben eigenständig.
      </p>
      <button
        disabled={busy || !title.trim()}
        onClick={() =>
          onRun(async () => {
            await request(`/campaigns/${campaign.id}`, {
              method: "PATCH",
              body: { expected_revision: campaign.revision, title, brief },
            });
            await onSaved();
          })
        }
      >
        Kampagnenbeschreibung speichern
      </button>
    </details>
  );
}
function CampaignImport({ busy, onRun, onImported }) {
  const [text, setText] = useState(""),
    [preview, setPreview] = useState(null),
    [confirmed, setConfirmed] = useState(false),
    [error, setError] = useState("");
  const setDocument = (value) => {
    setText(value);
    setPreview(null);
    setConfirmed(false);
    setError("");
  };
  return (
    <section className="panel campaign-import">
      <h2>Kampagne aus JSON importieren</h2>
      <p className="muted">
        Ein Kampagnendokument beschreibt Motive, Bildnamen und Fassungen.
        Genannte Dateinamen werden als fehlende Zuordnung angezeigt; sie laden
        keine Bilder von deinem Computer.
      </p>
      <div className="row">
        <label className="upload">
          JSON-Datei öffnen
          <input
            type="file"
            accept="application/json,.json"
            onChange={async (event) => {
              const file = event.target.files[0];
              event.target.value = "";
              if (!file) return;
              if (file.size > 2000000) {
                setError("JSON-Datei darf höchstens 2 MB groß sein.");
                return;
              }
              try {
                setDocument(await file.text());
              } catch (e) {
                setError(e.message);
              }
            }}
          />
        </label>
        <button
          className="subtle"
          onClick={() => setDocument(JSON.stringify(campaignExample, null, 2))}
        >
          Neutrales Strukturbeispiel einsetzen
        </button>
      </div>
      <label className="field">
        Kampagnendokument
        <textarea
          rows="10"
          value={text}
          onChange={(event) => setDocument(event.target.value)}
          spellCheck="false"
          placeholder="JSON hier einfügen"
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button
        disabled={busy || !text.trim()}
        onClick={() => {
          let document;
          try {
            document = JSON.parse(text);
          } catch {
            setError("Ungültiges JSON. Bitte Syntax prüfen.");
            return;
          }
          onRun(async () => {
            setPreview(
              await request("/campaigns/import/preview", {
                method: "POST",
                body: { document },
              }),
            );
            setConfirmed(false);
          });
        }}
      >
        Importvorschau prüfen
      </button>
      {preview && (
        <div className="import-preview">
          <h3>{preview.document?.title}</h3>
          <p>
            {preview.motifs_count} Motive · {preview.variants_count} Fassungen
          </p>
          {preview.warnings?.map((warning, index) => (
            <p className="notice" key={index}>
              {warning}
            </p>
          ))}
          {!!preview.missing_frames?.length && (
            <>
              <h4>Fehlende Bilddateien</h4>
              <ul className="missing-frames">
                {preview.missing_frames.map((frame, index) => (
                  <li key={index}>
                    {frame.motif_key} · {frame.scene_title} ·{" "}
                    {frame.role === "start" ? "Start" : "Ende"}:{" "}
                    {frame.name || "kein Dateiname"}
                  </li>
                ))}
              </ul>
            </>
          )}
          <label className="check">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            Als neue Kampagne mit ungeprüften Motiven und offen ausgewiesenen
            fehlenden Bildern anlegen.
          </label>
          <button
            className="primary"
            disabled={busy || !confirmed}
            onClick={() =>
              onRun(async () => {
                const campaign = await request("/campaigns/import/commit", {
                  method: "POST",
                  body: { preview_id: preview.preview_id, confirmed: true },
                });
                setPreview(null);
                setText("");
                onImported(campaign);
              })
            }
          >
            Kampagne importieren
          </button>
        </div>
      )}
    </section>
  );
}
export default function CampaignPanel({
  brands,
  assets,
  onUpload,
  activeId,
  onActiveChange,
  onOpenProject,
  onRefreshProjects,
  onError,
}) {
  const [campaigns, setCampaigns] = useState([]),
    [detail, setDetail] = useState(null),
    [busy, setBusy] = useState(false),
    [form, setForm] = useState(null),
    [createTitle, setCreateTitle] = useState(""),
    [createBrief, setCreateBrief] = useState(""),
    [createBrand, setCreateBrand] = useState(""),
    [createFormat, setCreateFormat] = useState("1080x1920"),
    [confirmMotif, setConfirmMotif] = useState(null),
    [exportWarnings, setExportWarnings] = useState([]),
    [editMotif, setEditMotif] = useState(null);
  const refresh = async () => {
    setCampaigns(await request("/campaigns"));
    if (activeId) setDetail(await request(`/campaigns/${activeId}`));
  };
  useEffect(() => {
    let active = true;
    request("/campaigns")
      .then((list) => {
        if (active) setCampaigns(list);
      })
      .catch((error) => onError(error.message));
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    setDetail(null);
    if (activeId) setForm(null);
    setConfirmMotif(null);
    setEditMotif(null);
    setExportWarnings([]);
    if (activeId)
      request(`/campaigns/${activeId}`)
        .then((value) => {
          if (active) setDetail(value);
        })
        .catch((error) => onError(error.message));
    return () => {
      active = false;
    };
  }, [activeId]);
  const act = async (fn) => {
    setBusy(true);
    try {
      await fn();
      await onRefreshProjects?.();
    } catch (error) {
      onError(error.message);
    } finally {
      setBusy(false);
    }
  };
  const imported = (campaign) => {
    setCampaigns((list) => [
      campaign,
      ...list.filter((c) => c.id !== campaign.id),
    ]);
    onActiveChange(campaign.id);
    setForm(null);
  };
  const campaign = detail;
  const baseProjects = (campaign?.motifs || [])
    .map((m) => campaign.projects?.[m.project_id])
    .filter(Boolean);
  const missingCount = (project) =>
    project?.scenes?.reduce(
      (sum, scene) =>
        sum + (!scene.start_asset_id ? 1 : 0) + (!scene.end_asset_id ? 1 : 0),
      0,
    ) || 0;
  return (
    <div className="campaign-workspace">
      <div className="campaign-toolbar">
        <button
          className={!activeId ? "selected" : ""}
          onClick={() => {
            onActiveChange(null);
            setForm(null);
          }}
        >
          Alle Kampagnen
        </button>
        <button
          className="primary"
          onClick={() => {
            onActiveChange(null);
            setForm("create");
          }}
        >
          + Kampagne
        </button>
        <button
          onClick={() => {
            onActiveChange(null);
            setForm("import");
          }}
        >
          JSON importieren
        </button>
      </div>
      {form === "create" && (
        <form
          className="panel"
          onSubmit={(event) => {
            event.preventDefault();
            act(async () => {
              const [width, height] = createFormat.split("x").map(Number);
              const created = await request("/campaigns", {
                method: "POST",
                body: {
                  title: createTitle,
                  brief: createBrief,
                  format: { width, height, fps: 30 },
                  ...(createBrand ? { brand_id: createBrand } : {}),
                  motifs: [],
                },
              });
              imported(created);
              setCreateTitle("");
              setCreateBrief("");
            });
          }}
        >
          <h2>Neue Kampagne</h2>
          <label className="field">
            Kampagnentitel
            <input
              required
              value={createTitle}
              maxLength="120"
              onChange={(event) => setCreateTitle(event.target.value)}
            />
          </label>
          <label className="field">
            Gemeinsames Briefing
            <textarea
              rows="3"
              maxLength="8000"
              value={createBrief}
              onChange={(event) => setCreateBrief(event.target.value)}
            />
          </label>
          <div className="grid">
            <label className="field">
              Format
              <select
                value={createFormat}
                onChange={(event) => setCreateFormat(event.target.value)}
              >
                <option value="1080x1920">Hochformat 9:16</option>
                <option value="1920x1080">Querformat 16:9</option>
                <option value="1080x1080">Quadratisch 1:1</option>
              </select>
            </label>
            <label className="field">
              Markenprofil
              <select
                value={createBrand}
                onChange={(event) => setCreateBrand(event.target.value)}
              >
                <option value="">— Später zuordnen —</option>
                {brands.map((brand) => (
                  <option key={brand.id} value={brand.id}>
                    {brand.name} · v{brand.version}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button className="primary" disabled={busy || !createTitle.trim()}>
            Kampagne anlegen
          </button>
        </form>
      )}
      {form === "import" && (
        <CampaignImport busy={busy} onRun={act} onImported={imported} />
      )}
      {!activeId && !form && (
        <>
          <section className="campaign-intro">
            <span className="eyebrow">KAMPAGNENSTUDIO</span>
            <h2>
              Ein Motiv. Mehrere Fassungen.
              <br />
              Eine klare Produktion.
            </h2>
            <p>
              Plane die gemeinsamen Szenen, steuere Übergänge mit Start- und
              Endbildern und erstelle daraus deine Zielgruppenfassungen.
            </p>
          </section>
          <div className="campaign-grid">
            {campaigns.map((item) => (
              <button
                className="campaign-card"
                key={item.id}
                onClick={() => onActiveChange(item.id)}
              >
                <span className="eyebrow">KAMPAGNE · REV. {item.revision}</span>
                <h3>{item.title}</h3>
                <p>{item.brief?.slice(0, 140) || "Briefing noch offen"}</p>
                <div className="row spread">
                  <small>
                    {item.motifs.length} Motive ·{" "}
                    {item.motifs.reduce((n, m) => n + m.variants.length, 0)}{" "}
                    Fassungen
                  </small>
                  <span>Öffnen →</span>
                </div>
              </button>
            ))}
          </div>
          {!campaigns.length && (
            <div className="panel muted">
              Noch keine Kampagne. Lege eine Kampagne an oder prüfe zuerst ein
              JSON-Dokument.
            </div>
          )}
        </>
      )}
      {campaign && (
        <>
          <section className="panel campaign-overview">
            <div className="row spread">
              <div>
                <span className="eyebrow">
                  KAMPAGNE · REV. {campaign.revision}
                </span>
                <h2>{campaign.title}</h2>
              </div>
              <div className="row">
                <button disabled={busy} onClick={() => act(refresh)}>
                  Stand aktualisieren
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      const result = await request(
                        `/campaigns/${campaign.id}/export`,
                      );
                      setExportWarnings(result.warnings || []);
                      const blob = new Blob(
                        [JSON.stringify(result.document, null, 2)],
                        { type: "application/json" },
                      );
                      const url = URL.createObjectURL(blob);
                      const anchor = document.createElement("a");
                      anchor.href = url;
                      anchor.download = `${campaign.title.replace(/[^a-zA-Z0-9_-]/g, "-")}.json`;
                      anchor.click();
                      setTimeout(() => URL.revokeObjectURL(url), 1000);
                    })
                  }
                >
                  JSON exportieren
                </button>
              </div>
            </div>
            <p>
              {campaign.brief || "Noch kein gemeinsames Briefing hinterlegt."}
            </p>
            <CampaignMetadata
              key={`${campaign.id}-${campaign.revision}`}
              campaign={campaign}
              busy={busy}
              onRun={act}
              onSaved={refresh}
            />
            <div className="campaign-summary">
              <span>
                {campaign.motifs.length}
                <small>Motive</small>
              </span>
              <span>
                {campaign.motifs.reduce((n, m) => n + m.variants.length, 0)}
                <small>Geplante Fassungen</small>
              </span>
              <span>
                {campaign.motifs.reduce((n, m) => n + m.outputs.length, 0)}
                <small>Erstellte Fassungen</small>
              </span>
              <span>
                {campaign.format.width}×{campaign.format.height}
                <small>Format</small>
              </span>
            </div>
            {exportWarnings.map((warning, index) => (
              <p className="notice" key={index}>
                {warning}
              </p>
            ))}
          </section>
          <div className="row spread campaign-heading">
            <h2>Motive & Fassungen</h2>
            <button
              disabled={busy}
              onClick={() => setForm(form === "motif" ? null : "motif")}
            >
              + Motiv anlegen
            </button>
          </div>
          {form === "motif" && (
            <MotifForm
              busy={busy}
              onCancel={() => setForm(null)}
              onSave={(motif) =>
                act(async () => {
                  await request(`/campaigns/${campaign.id}/motifs`, {
                    method: "POST",
                    body: { expected_revision: campaign.revision, motif },
                  });
                  setForm(null);
                  await refresh();
                })
              }
            />
          )}
          <div className="motif-grid">
            {campaign.motifs.map((motif) => {
              const project = campaign.projects?.[motif.project_id];
              const progress = motifProgress(project);
              const canAssemble =
                project &&
                ["concept", "script", "storyboard", "clips"].every(
                  (gate) => project.gates[gate] === "approved",
                );
              return (
                <article className="panel motif-card" key={motif.key}>
                  <div className="row spread">
                    <h3>{motif.title}</h3>
                    <span className="status-pill">{progress.label}</span>
                  </div>
                  <p className="muted">
                    {project?.scenes.length || 0} gemeinsame Szenen ·{" "}
                    {motif.variants.length} Fassungen
                  </p>
                  {!!missingCount(project) && (
                    <p className="notice">
                      {missingCount(project)} Start-/Endbild-Zuordnungen fehlen.
                      Dateinamen im Briefing sind noch keine geladenen Bilder.
                    </p>
                  )}
                  {!!motif.frame_labels?.some(
                    (item) => item.start_frame || item.end_frame,
                  ) && (
                    <details>
                      <summary>Geplante Bildnamen</summary>
                      <ul className="missing-frames">
                        {motif.frame_labels.map((item, index) => (
                          <li key={index}>
                            Szene {index + 1}:{" "}
                            {item.start_frame || "Start offen"} →{" "}
                            {item.end_frame || "Ende offen"}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  <div className="row">
                    <button
                      className="primary"
                      onClick={() =>
                        onOpenProject(motif.project_id, progress.phase, {
                          campaign_id: campaign.id,
                          campaign_title: campaign.title,
                          motif_key: motif.key,
                          motif_title: motif.title,
                        })
                      }
                    >
                      Motiv bearbeiten →
                    </button>
                    <button
                      onClick={() =>
                        onOpenProject(motif.project_id, "storyboard", {
                          campaign_id: campaign.id,
                          campaign_title: campaign.title,
                          motif_key: motif.key,
                          motif_title: motif.title,
                        })
                      }
                    >
                      Rahmenbilder zuordnen
                    </button>
                  </div>
                  <button
                    className="subtle"
                    disabled={busy}
                    onClick={() =>
                      setEditMotif(editMotif === motif.key ? null : motif.key)
                    }
                  >
                    Fassungen & Schlusskarten bearbeiten
                  </button>
                  {editMotif === motif.key && (
                    <VariantEditor
                      key={`${motif.key}-${campaign.revision}`}
                      campaign={campaign}
                      motif={motif}
                      assets={assets}
                      onUpload={onUpload}
                      onRun={act}
                      onSaved={async () => {
                        setEditMotif(null);
                        await refresh();
                      }}
                    />
                  )}
                  {!!motif.variants.length && (
                    <div className="variant-list">
                      <h4>Zielgruppenfassungen</h4>
                      {motif.variants.map((variant) => (
                        <div className="variant-line" key={variant.key}>
                          <strong>{variant.title}</strong>
                          <small>
                            {variant.audience || "Zielgruppe noch offen"} ·{" "}
                            {variant.duration_s}s Schlusskarte
                          </small>
                          <p>
                            {variant.cta ||
                              variant.endcard_text ||
                              "Text noch offen"}
                          </p>
                        </div>
                      ))}
                      <button
                        disabled={busy || !canAssemble}
                        onClick={() => setConfirmMotif(motif.key)}
                      >
                        Fassungen aus freigegebenen Clips anlegen …
                      </button>
                      {!canAssemble && (
                        <p className="muted">
                          Vorher Konzept, Skript, Storyboard und Clips des
                          Motivs freigeben.
                        </p>
                      )}
                      {confirmMotif === motif.key && (
                        <div className="confirm">
                          <p>
                            {motif.variants.length} neue Fassungsprojekte aus
                            den vorhandenen freigegebenen Clips erstellen.
                            Schlusskarten werden lokal ergänzt. Kein
                            Modellaufruf; jede Fassung braucht ihre eigene
                            Prüfung.
                          </p>
                          <div className="row">
                            <button
                              className="primary"
                              disabled={busy}
                              onClick={() =>
                                act(async () => {
                                  await request(
                                    `/campaigns/${campaign.id}/motifs/${encodeURIComponent(motif.key)}/variants`,
                                    {
                                      method: "POST",
                                      body: {
                                        expected_revision: project.revision,
                                        confirmed: true,
                                      },
                                    },
                                  );
                                  setConfirmMotif(null);
                                  await refresh();
                                })
                              }
                            >
                              Fassungen jetzt anlegen
                            </button>
                            <button
                              disabled={busy}
                              onClick={() => setConfirmMotif(null)}
                            >
                              Abbrechen
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                  {!!motif.outputs.length && (
                    <div className="variant-outputs">
                      <h4>Erstellte Fassungen</h4>
                      {motif.outputs.map((output, index) => {
                        const variant = motif.variants.find(
                          (v) => v.key === output.variant_key,
                        );
                        const outputProject =
                          campaign.projects?.[output.project_id];
                        return (
                          <button
                            className="variant-output"
                            key={`${output.project_id}-${index}`}
                            onClick={() =>
                              onOpenProject(output.project_id, "review", {
                                campaign_id: campaign.id,
                                campaign_title: campaign.title,
                                motif_key: null,
                                motif_title:
                                  variant?.title || output.variant_key,
                              })
                            }
                          >
                            <strong>
                              {outputProject?.title ||
                                variant?.title ||
                                output.variant_key}
                            </strong>
                            <small>
                              Motivrevision {output.source_revision}
                              {project &&
                              project.revision !== output.source_revision
                                ? " · Ausgangsmotiv inzwischen geändert"
                                : ""}
                            </small>
                            <span>Prüfen & exportieren →</span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
          {!!baseProjects.length && (
            <BatchPanel
              key={campaign.id}
              projects={baseProjects}
              onError={onError}
              onChanged={refresh}
            />
          )}
        </>
      )}
    </div>
  );
}
