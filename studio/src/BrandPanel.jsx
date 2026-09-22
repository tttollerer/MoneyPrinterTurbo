import React, {useState} from 'react';

const defaults = () => ({
  name: '', colors: {primary: '#ea765b', background: '#10131a', text: '#ffffff'},
  font_family: 'Arial', font_asset_id: null, logo_asset_id: null,
  logo_position: 'top-right', safe_margin: 0.07, caption_style: 'sentence',
  language: 'de', tone: '', visual_style: '', rules: [], forbidden_claims: [],
  required_text: '', document_asset_ids: [],
  locked_fields: ['logo_asset_id', 'font_asset_id', 'colors', 'required_text'],
});
const lockLabels = {
  name: 'Name', colors: 'Farben', font_family: 'Schriftfamilie', font_asset_id: 'Schriftdatei',
  logo_asset_id: 'Logo', logo_position: 'Logoposition', safe_margin: 'Sicherheitsabstand',
  caption_style: 'Untertitelstil', language: 'Sprache', tone: 'Tonalität',
  visual_style: 'Bildwelt', rules: 'Regeln', forbidden_claims: 'Verbotene Aussagen',
  required_text: 'Pflichttext', document_asset_ids: 'Referenzdokumente',
};

async function request(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail || body));
  }
  return body;
}

export default function BrandPanel({brands = [], assets = [], onRefresh, onError}) {
  const [selected, setSelected] = useState('');
  const [version, setVersion] = useState(null);
  const [form, setForm] = useState(defaults);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [uploaded, setUploaded] = useState([]);
  const files = [...assets, ...uploaded.filter((item) => !assets.some((a) => a.id === item.id))];
  const set = (key, value) => setForm((old) => ({...old, [key]: value}));
  const report = (failure) => {
    setError(failure.message);
    onError?.(failure.message);
  };
  const choose = (id) => {
    const profile = brands.find((brand) => brand.id === id);
    setSelected(id);
    setVersion(profile?.version || null);
    setForm(profile ? {...defaults(), ...profile, colors: {...profile.colors}} : defaults());
    setMessage('');
    setError('');
  };
  const save = async (event) => {
    event.preventDefault();
    setBusy(true); setError(''); setMessage('');
    try {
      const profile = await request(selected ? `/api/brands/${encodeURIComponent(selected)}/versions` : '/api/brands', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
          ...form, rules: form.rules.map((line) => line.trim()).filter(Boolean),
          forbidden_claims: form.forbidden_claims.map((line) => line.trim()).filter(Boolean),
        }),
      });
      setSelected(profile.id); setVersion(profile.version); setForm(profile);
      setMessage(`Version ${profile.version} gespeichert. Bestehende Projekte behalten ihre bisherige Version.`);
      await onRefresh?.();
    } catch (failure) { report(failure); }
    finally { setBusy(false); }
  };
  const upload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    event.target.value = '';
    setBusy(true); setError('');
    try {
      const payload = new FormData(); payload.append('file', file);
      const asset = await request('/api/assets', {method: 'POST', body: payload});
      setUploaded((old) => [...old, asset]);
      if (asset.kind === 'image') set('logo_asset_id', asset.id);
      else if (asset.kind === 'font') set('font_asset_id', asset.id);
      else if (asset.kind === 'document') setForm((old) => ({...old, document_asset_ids: [...old.document_asset_ids, asset.id]}));
      await onRefresh?.();
    } catch (failure) { report(failure); }
    finally { setBusy(false); }
  };
  const assetSelect = (label, key, kind) => <label className="field">{label}
    <select value={form[key] || ''} onChange={(event) => set(key, event.target.value || null)}>
      <option value="">Keine Datei</option>
      {files.filter((asset) => asset.kind === kind).map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
    </select>
  </label>;

  return <section className="panel" aria-label="Markenprofile">
    <h2>Marken & Styleguides</h2>
    <p className="muted">Farben, Logos, Schriften und Regeln werden als feste Version gespeichert. Wähle im Projekt bewusst die Version aus, die angewendet werden soll.</p>
    <label className="field">Marke bearbeiten
      <select value={selected} onChange={(event) => choose(event.target.value)} disabled={busy}>
        <option value="">Neue Marke anlegen</option>
        {brands.map((brand) => <option key={brand.id} value={brand.id}>{brand.name} · v{brand.version}</option>)}
      </select>
    </label>
    {selected && <p className="muted">Bearbeitungsgrundlage: Version {version}. Speichern erstellt eine neue Version.</p>}
    <form onSubmit={save}>
      <fieldset disabled={busy} style={{border: 0, padding: 0, margin: 0, minWidth: 0}}>
        <label className="field">Markenname<input required maxLength={120} value={form.name} onChange={(event) => set('name', event.target.value)}/></label>
        <div className="grid" style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12}}>
          {[['primary', 'Primärfarbe'], ['background', 'Hintergrund'], ['text', 'Textfarbe']].map(([key, label]) => <label className="field" key={key}>{label}
            <input type="color" value={form.colors[key]} onChange={(event) => set('colors', {...form.colors, [key]: event.target.value})}/>
            <span className="muted">{form.colors[key]}</span>
          </label>)}
        </div>
        <div className="grid">
          {assetSelect('Logo', 'logo_asset_id', 'image')}
          {assetSelect('Schriftdatei', 'font_asset_id', 'font')}
          <label className="field">Schriftfamilie<input required value={form.font_family} onChange={(event) => set('font_family', event.target.value)}/></label>
        </div>
        <p className="muted">Eigene Schriften brauchen eine hochgeladene TTF-, OTF-, WOFF- oder WOFF2-Datei. Ohne Datei steht Arial zur Verfügung.</p>
        {form.logo_asset_id && <img alt="Ausgewähltes Markenlogo" src={`/api/assets/${encodeURIComponent(form.logo_asset_id)}/file`} style={{maxWidth: 180, maxHeight: 90, objectFit: 'contain'}}/>}
        <div className="grid">
          <label className="field">Logo-Position<select value={form.logo_position} onChange={(event) => set('logo_position', event.target.value)}>
            {[['top-left', 'Oben links'], ['top-right', 'Oben rechts'], ['bottom-left', 'Unten links'], ['bottom-right', 'Unten rechts']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <label className="field">Sicherheitsabstand (%)<input type="number" min="0" max="25" step="1" value={Math.round(form.safe_margin * 100)} onChange={(event) => set('safe_margin', Number(event.target.value) / 100)}/></label>
          <label className="field">Untertitel<select value={form.caption_style} onChange={(event) => set('caption_style', event.target.value)}>
            <option value="sentence">Satzweise</option><option value="karaoke">Wortweise hervorheben</option>
          </select></label>
        </div>
        <label className="field">Sprache<input value={form.language} onChange={(event) => set('language', event.target.value)}/></label>
        <label className="field">Tonalität<textarea value={form.tone} onChange={(event) => set('tone', event.target.value)} placeholder="Direkt, warm, Du-Ansprache"/></label>
        <label className="field">Bildwelt<textarea value={form.visual_style} onChange={(event) => set('visual_style', event.target.value)} placeholder="Natürliches Licht, ruhige Kamera, klare Komposition"/></label>
        <p className="muted">Tonalität und Bildwelt steuern generative Vorgaben und brauchen Sichtprüfung. Logos, Schriften und Farben setzt der Renderer um.</p>
        <label className="field">Regeln (eine pro Zeile)<textarea value={form.rules.join('\n')} onChange={(event) => set('rules', event.target.value.split('\n'))} onBlur={() => set('rules', form.rules.filter((line) => line.trim()))}/></label>
        <label className="field">Verbotene Aussagen (eine pro Zeile)<textarea value={form.forbidden_claims.join('\n')} onChange={(event) => set('forbidden_claims', event.target.value.split('\n'))} onBlur={() => set('forbidden_claims', form.forbidden_claims.filter((line) => line.trim()))}/></label>
        <label className="field">Pflichttext<textarea value={form.required_text} onChange={(event) => set('required_text', event.target.value)}/></label>
        <label className="field">Styleguide-Dokumente
          <select multiple size={Math.max(2, Math.min(5, files.filter((asset) => asset.kind === 'document').length))} value={form.document_asset_ids}
            onChange={(event) => set('document_asset_ids', [...event.target.selectedOptions].map((option) => option.value))}>
            {files.filter((asset) => asset.kind === 'document').map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
          </select>
        </label>
        <p className="muted">Dokumente dienen als Referenz. Ihre Inhalte werden nicht automatisch in Regeln umgewandelt.</p>
        {form.document_asset_ids.map((id) => <p key={id}><a href={`/api/assets/${encodeURIComponent(id)}/file`} target="_blank" rel="noreferrer">{files.find((asset) => asset.id === id)?.name || 'Referenz öffnen'}</a></p>)}
        <label className="field">Logo, Schrift oder Dokument hochladen<input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml,.ttf,.otf,.woff,.woff2,.pdf,.txt,.md" onChange={upload}/></label>
        <details>
          <summary>Verbindliche Markenfelder</summary>
          <p className="muted">Diese Auswahl dokumentiert die Markenrichtlinie. Im aktuellen Umfang verwenden Projekte immer die vollständige gespeicherte Version; individuelle Feld-Überschreibungen sind noch nicht verfügbar.</p>
          <div className="grid">{Object.entries(lockLabels).map(([key, label]) => <label key={key} className="row">
            <input type="checkbox" checked={form.locked_fields.includes(key)} onChange={(event) => set('locked_fields', event.target.checked ? [...form.locked_fields, key] : form.locked_fields.filter((value) => value !== key))}/>{label}
          </label>)}</div>
        </details>
        <button className="button" type="submit" disabled={busy || !form.name.trim()}>{busy ? 'Speichert …' : selected ? 'Neue Version speichern' : 'Marke anlegen'}</button>
      </fieldset>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
  </section>;
}
