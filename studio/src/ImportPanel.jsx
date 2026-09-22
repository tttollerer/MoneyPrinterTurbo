import React, {useState} from 'react';
import {request} from './api.js';

export default function ImportPanel({onImported, onError}) {
  const [sourceDir, setSourceDir] = useState('');
  const [projectFile, setProjectFile] = useState('project.json');
  const [brandFile, setBrandFile] = useState('');
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [imported, setImported] = useState(null);
  const selection = () => ({source_dir: sourceDir.trim(), project_file: projectFile.trim() || 'project.json', brand_file: brandFile.trim() || null});
  const change = (setter, value) => {setter(value); setPreview(null); setConfirmed(false); setImported(null);};
  const perform = async (action) => {
    setBusy(true); setError('');
    try {await action();}
    catch (failure) {setError(failure.message); onError?.(failure);}
    finally {setBusy(false);}
  };
  const inspect = (event) => {
    event.preventDefault();
    perform(async () => {setPreview(null); setConfirmed(false); setImported(null); setPreview(await request('/imports/preview', {method: 'POST', body: selection()}));});
  };
  const commit = () => perform(async () => {
    const project = await request('/imports/commit', {method: 'POST', body: {...selection(), token: preview.token, confirmed}});
    setImported(project); setConfirmed(false); setPreview(null);
    await onImported?.(project);
  });
  return <section className="panel" aria-label="Altprojekt importieren">
    <h2>Bestehendes Projekt importieren</h2>
    <p className="muted">Wähle ausdrücklich einen lokalen Quellordner. Der Import erstellt ein neues Projekt und kopiert die referenzierten Medien. Originaldateien und bisherige Exporte bleiben erhalten.</p>
    <form onSubmit={inspect}>
      <label className="field">Quellordner auf diesem Mac
        <input required disabled={busy} value={sourceDir} placeholder="/absoluter/Pfad/zum/Projekt" onChange={(event) => change(setSourceDir, event.target.value)}/>
      </label>
      <label className="field">Projektdatei relativ zum Quellordner
        <input required disabled={busy} value={projectFile} placeholder="project.json oder spot.json" onChange={(event) => change(setProjectFile, event.target.value)}/>
      </label>
      <details><summary>Markenprofil mitnehmen</summary>
        <p className="muted">Optional wird brand.json im Quellordner oder brands/&lt;Marke&gt;/brand.json verwendet. Liegen Projekt und Marke in verschiedenen Unterordnern, wähle ihren gemeinsamen Arbeitsordner als Quelle und gib die Projektdatei relativ dazu an.</p>
        <label className="field">Markendatei relativ zum Quellordner (optional)
          <input disabled={busy} value={brandFile} placeholder="brands/beispiel/brand.json" onChange={(event) => change(setBrandFile, event.target.value)}/>
        </label>
        <p className="muted">Logo-, Font- und Styleguide-Dateien werden nur über lokale logo_file-, font_file- und document_files-Verweise im Markenprofil übernommen. Eigene Schriften brauchen eine Font-Datei; ansonsten bleibt das Profil zur manuellen Übernahme im Original.</p>
      </details>
      <button className="button" disabled={busy || !sourceDir.trim()}>{busy ? 'Prüft …' : 'Importvorschau prüfen'}</button>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {preview && <div>
      <h3>Importvorschau</h3>
      {preview.summary && <p><strong>{preview.summary.title}</strong> · {preview.summary.scenes} Szenen · {preview.summary.takes} Takes · {preview.summary.files} Dateien ({(preview.summary.bytes / 1048576).toFixed(1)} MB)<br/>Markenprofil: {preview.summary.brand || 'Keines – kann später zugewiesen werden'}</p>}
      {preview.errors?.length > 0 && <ul className="error" role="alert">{preview.errors.map((value, index) => <li key={index}>{value}</li>)}</ul>}
      {preview.warnings?.length > 0 && <ul className="muted">{preview.warnings.map((value, index) => <li key={index}>{value}</li>)}</ul>}
      {preview.media?.length > 0 && <details><summary>{preview.media.length} referenzierte Dateien anzeigen</summary><ul>{preview.media.map((item) => <li key={item.path}>{item.path} · {item.kind} · {(item.size / 1048576).toFixed(2)} MB</li>)}</ul></details>}
      <p className="muted">Grenzen: 100 Szenen, 500 Dateien, 100 MB pro Datei und 500 MB insgesamt. Medien werden beim Kopieren auf Lesbarkeit geprüft. Fehlende oder unsichere Referenzen blockieren den Import. Es werden keine Anbieteraufrufe gestartet.</p>
      {preview.can_import && <><label className="check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={busy}/> Ich habe Zuordnung und Einschränkungen geprüft und möchte eine neue Kopie erstellen.</label>
        <button className="primary" disabled={busy || !confirmed} onClick={commit}>{busy ? 'Importiert …' : 'Bestätigt als neues Projekt importieren'}</button></>}
    </div>}
    {imported && <p role="status">„{imported.title}“ wurde als neues Projekt angelegt. <a href={`/api/imports/${encodeURIComponent(imported.id)}/report`} target="_blank" rel="noreferrer">Importbericht und archivierte Medienzuordnung ansehen</a></p>}
  </section>;
}
