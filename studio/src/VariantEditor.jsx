import React, { useState } from "react";
import { request } from "./api.js";
export default function VariantEditor({
  campaign,
  motif,
  assets,
  onUpload,
  onRun,
  onSaved,
}) {
  const [variants, setVariants] = useState(motif.variants),
    [busy, setBusy] = useState(false);
  const setField = (index, key, value) =>
    setVariants((list) =>
      list.map((item, i) => (i === index ? { ...item, [key]: value } : item)),
    );
  return (
    <div className="variant-editor">
      <h4>Fassungen bearbeiten</h4>
      {variants.map((variant, index) => (
        <div className="variant-form" key={variant.key}>
          <div className="row spread">
            <strong>{variant.title || `Fassung ${index + 1}`}</strong>
            <button
              className="subtle"
              disabled={busy}
              onClick={() =>
                setVariants((list) => list.filter((_, i) => i !== index))
              }
            >
              Entfernen
            </button>
          </div>
          <label className="field">
            Titel
            <input
              maxLength="100"
              value={variant.title}
              onChange={(event) => setField(index, "title", event.target.value)}
            />
          </label>
          <label className="field">
            Zielgruppe
            <input
              maxLength="500"
              value={variant.audience}
              onChange={(event) =>
                setField(index, "audience", event.target.value)
              }
            />
          </label>
          <label className="field">
            CTA
            <input
              maxLength="300"
              value={variant.cta}
              onChange={(event) => setField(index, "cta", event.target.value)}
            />
          </label>
          <label className="field">
            Text der Schlusskarte
            <textarea
              rows="2"
              maxLength="500"
              value={variant.endcard_text}
              onChange={(event) =>
                setField(index, "endcard_text", event.target.value)
              }
            />
          </label>
          <label className="field">
            Vorhandene fertige Schlusskarte
            <select
              value={variant.endcard_asset_id || ""}
              onChange={(event) =>
                setField(index, "endcard_asset_id", event.target.value || null)
              }
            >
              <option value="">Markenhintergrund mit eingegebenem Text</option>
              {assets
                .filter((asset) => asset.kind === "image")
                .map((asset) => (
                  <option value={asset.id} key={asset.id}>
                    {asset.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="upload">
            Schlusskarte hochladen
            <input
              type="file"
              accept="image/*"
              disabled={busy}
              onChange={async (event) => {
                const file = event.target.files[0];
                event.target.value = "";
                if (!file) return;
                setBusy(true);
                try {
                  const asset = await onUpload(file);
                  if (asset) setField(index, "endcard_asset_id", asset.id);
                } finally {
                  setBusy(false);
                }
              }}
            />
          </label>
          <p className="muted">
            Bei fertig gestalteten Karten CTA und Schlusskartentext leer lassen,
            wenn der Text bereits im Bild enthalten ist.
          </p>
          <label className="field">
            Dauer der Schlusskarte
            <input
              type="number"
              min="1"
              max="10"
              step="0.5"
              value={variant.duration_s}
              onChange={(event) =>
                setField(index, "duration_s", Number(event.target.value))
              }
            />
          </label>
        </div>
      ))}
      <div className="row">
        <button
          disabled={busy || variants.length >= 20}
          onClick={() =>
            setVariants((list) => [
              ...list,
              {
                key: `fassung-${crypto.randomUUID().slice(0, 8)}`,
                title: `Fassung ${list.length + 1}`,
                audience: "",
                cta: "",
                endcard_text: "",
                endcard_asset_id: null,
                duration_s: 3,
              },
            ])
          }
        >
          + Fassung hinzufügen
        </button>
        <button
          className="primary"
          disabled={busy || variants.some((v) => !v.title.trim())}
          onClick={async () => {
            setBusy(true);
            try {
              await onRun(async () => {
                await request(
                  `/campaigns/${campaign.id}/motifs/${encodeURIComponent(motif.key)}`,
                  {
                    method: "PATCH",
                    body: { expected_revision: campaign.revision, variants },
                  },
                );
                await onSaved();
              });
            } finally {
              setBusy(false);
            }
          }}
        >
          Fassungen speichern
        </button>
      </div>
      <p className="muted">
        Vorhandene Fassungsprojekte bleiben erhalten. Geänderte Texte erzeugen
        beim nächsten Anlegen neue Fassungen mit eigenen Freigaben.
      </p>
    </div>
  );
}
