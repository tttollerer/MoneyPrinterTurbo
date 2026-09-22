/** Additional cross-reference checks alongside the authoritative Python schema. */
export function validateManifest(m) {
  const fail = (message) => {
    throw new Error(`Invalid render manifest: ${message}`);
  };
  if (m.schema_version !== 1) fail("unsupported schema version");
  if (
    !m.format ||
    !["width", "height", "fps"].every(
      (k) => Number.isInteger(m.format[k]) && m.format[k] > 0,
    )
  )
    fail("format");
  if (!Number.isInteger(m.duration_frames) || m.duration_frames < 1)
    fail("duration");
  if (!Array.isArray(m.scenes) || !m.scenes.length) fail("no scenes");
  const references = [];
  const need = (id, kinds) => {
    const a = m.assets?.[id];
    if (!a || !kinds.includes(a.kind) || typeof a.url !== "string")
      fail(`missing or invalid asset ${id}`);
    if (!/^https?:\/\//.test(a.url)) fail(`asset URL must be HTTP(S): ${id}`);
    references.push(a);
  };
  let end = 0;
  for (const s of m.scenes) {
    need(s.asset_id, ["image", "video"]);
    if (
      !Number.isInteger(s.from_frame) ||
      s.from_frame !== end ||
      !Number.isInteger(s.duration_frames) ||
      s.duration_frames < 1
    )
      fail("scenes must form a contiguous timeline");
    end += s.duration_frames;
  }
  if (end !== m.duration_frames) fail("scene duration differs from timeline");
  for (const key of ["narration_asset_id", "music_asset_id"])
    if (m.audio?.[key]) need(m.audio[key], ["audio"]);
  if (m.brand?.font_asset_id) need(m.brand.font_asset_id, ["font"]);
  if (m.brand?.logo_asset_id) need(m.brand.logo_asset_id, ["image"]);
  for (const cue of m.captions || []) {
    const valid = (x) =>
      Number.isInteger(x.start_ms) &&
      x.start_ms >= 0 &&
      Number.isInteger(x.end_ms) &&
      x.end_ms > x.start_ms &&
      x.end_ms <= Math.ceil((m.duration_frames / m.format.fps) * 1000);
    if (!valid(cue)) fail("caption outside timeline");
    for (const word of cue.words || [])
      if (
        !valid(word) ||
        word.start_ms < cue.start_ms ||
        word.end_ms > cue.end_ms
      )
        fail("word outside cue");
    if (m.brand?.caption_style === "karaoke" && !cue.words?.length)
      fail("karaoke requires real word timings");
  }
  return [...new Set(references)];
}
