/** Keep every frame dependency before its consumer when moving a scene. */
export function reorderedSceneIds(scenes, id, direction) {
  if (direction !== -1 && direction !== 1) return null;
  const index = scenes.findIndex((scene) => scene.id === id);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= scenes.length) return null;
  const ordered = [...scenes];
  [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
  const positions = new Map(
    ordered.map((scene, position) => [scene.id, position]),
  );
  if (
    ordered.some(
      (scene, position) =>
        scene.predecessor_scene_id &&
        (!positions.has(scene.predecessor_scene_id) ||
          positions.get(scene.predecessor_scene_id) >= position),
    )
  )
    return null;
  return ordered.map((scene) => scene.id);
}
export const jobLabel = (kind) =>
  ({
    render: "Export",
    generation: "Generierung",
    speech: "Lokale Sprache",
    stock_import: "Stock-Import",
  })[kind] || kind;
export const jobStateLabel = (state) =>
  ({
    queued: "Wartet",
    running: "Läuft",
    submitting: "Wird gestartet",
    complete: "Fertig",
    failed: "Fehlgeschlagen",
    interrupted: "Unterbrochen",
    unknown: "Status unklar",
  })[state] || state;
