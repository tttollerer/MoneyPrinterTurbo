import React, { useEffect, useState } from "react";
import { request } from "./api.js";
const sources = {
  environment: "Umgebungsvariable",
  session: "Aktuelle Sitzung",
  keychain: "macOS-Schlüsselbund",
};
export default function ProviderPanel({ onChanged, onError }) {
  const [status, setStatus] = useState(null),
    [key, setKey] = useState(""),
    [persistence, setPersistence] = useState("session"),
    [busy, setBusy] = useState(false),
    [remove, setRemove] = useState(false),
    [message, setMessage] = useState("");
  const load = async () => {
    const next = await request("/providers/fal");
    setStatus(next);
    return next;
  };
  useEffect(() => {
    load()
      .then((next) =>
        setPersistence(next.persistence_available ? "keychain" : "session"),
      )
      .catch((error) => onError(error.message));
  }, []);
  const act = async (fn) => {
    setBusy(true);
    setMessage("");
    try {
      await fn();
      await load();
      await onChanged?.();
    } catch (error) {
      onError(error.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel provider-panel">
      <div className="row spread">
        <h2>fal.ai verbinden</h2>
        <span className={`status-pill ${status?.configured ? "ready" : ""}`}>
          {status?.configured ? "Schlüssel hinterlegt" : "Nicht verbunden"}
        </span>
      </div>
      <p>
        Der Videodienst wird nur nach einer bestätigten Generierung verwendet.
        Einen Schlüssel eingeben oder entfernen löst keinen Videoauftrag aus.
      </p>
      {status?.configured && (
        <p className="notice">
          Aktive Quelle: {sources[status.source] || status.source}. Der
          Schlüssel wird nicht angezeigt. „Hinterlegt“ bestätigt die
          Speicherung, nicht das Guthaben oder die Gültigkeit beim Anbieter.
        </p>
      )}
      {status?.source === "environment" && (
        <p className="muted">
          Eine gesetzte FAL_KEY-Umgebungsvariable hat Vorrang. Speichern oder
          Entfernen hier ändert diese Umgebungsvariable nicht.
        </p>
      )}
      {status?.error && <p className="error">{status.error}</p>}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const entered = key.trim();
          setKey("");
          act(async () => {
            await request("/providers/fal", {
              method: "PUT",
              body: { key: entered, persistence },
            });
            setMessage(
              "Schlüssel gespeichert. Neue Aufträge verwenden die aktive Quelle.",
            );
          });
        }}
      >
        <label className="field">
          fal.ai API-Schlüssel
          <input
            type="password"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            autoComplete="new-password"
            spellCheck="false"
            placeholder="Eigenen Schlüssel einfügen"
          />
        </label>
        <label className="field">
          Speichern
          <select
            value={persistence}
            onChange={(event) => setPersistence(event.target.value)}
          >
            <option value="session">Nur bis zum Server-Neustart</option>
            <option value="keychain" disabled={!status?.persistence_available}>
              macOS-Schlüsselbund
              {status && !status.persistence_available
                ? " · nicht verfügbar"
                : ""}
            </option>
          </select>
        </label>
        <p className="muted">
          Keine Speicherung im Browser. Das Eingabefeld wird beim Absenden
          geleert. Sitzungsmodus speichert nur im Arbeitsspeicher des lokalen
          Servers.
        </p>
        <button className="primary" disabled={busy || !key.trim()}>
          {busy ? "Wird verarbeitet …" : "Schlüssel speichern"}
        </button>
      </form>
      {message && (
        <p role="status" className="success-note">
          {message}
        </p>
      )}
      <div className="provider-remove">
        <button
          className="subtle"
          disabled={busy}
          onClick={() => setRemove(true)}
        >
          Gespeicherten Schlüssel entfernen …
        </button>
        {remove && (
          <div className="confirm">
            <p>
              Entfernt Schlüssel aus Sitzung und Schlüsselbund. Eine
              Umgebungsvariable bleibt unverändert.
            </p>
            <div className="row">
              <button
                className="danger"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    await request("/providers/fal", { method: "DELETE" });
                    setRemove(false);
                    setKey("");
                    setMessage("Gespeicherte Schlüssel entfernt.");
                  })
                }
              >
                Entfernen bestätigen
              </button>
              <button disabled={busy} onClick={() => setRemove(false)}>
                Abbrechen
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
