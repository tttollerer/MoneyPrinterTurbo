export const PHASES = [
  { id: "briefing", label: "Briefing" },
  { id: "storyboard", label: "Storyboard" },
  { id: "production", label: "Produktion" },
  { id: "review", label: "Abnahme" },
  { id: "export", label: "Export" },
];
export function motifProgress(project) {
  if (!project) return { label: "Projekt wird geladen", phase: "briefing" };
  if (project.render?.file && project.gates?.final === "approved")
    return { label: "Export freigegeben", phase: "export" };
  if (project.gates?.clips === "approved")
    return { label: "Clips freigegeben", phase: "export" };
  if (
    project.scenes?.length &&
    project.scenes.every((s) => s.selected_take_id && !s.stale)
  )
    return { label: "Bereit zur Abnahme", phase: "review" };
  if (project.gates?.storyboard === "approved")
    return { label: "Bereit für Produktion", phase: "production" };
  if (project.scenes?.length)
    return { label: "Storyboard in Arbeit", phase: "storyboard" };
  return { label: "Briefing in Arbeit", phase: "briefing" };
}
export function initialBoundaries(project) {
  const scenes = project?.scenes || [];
  return scenes.length
    ? [
        scenes[0].start_asset_id || "",
        ...scenes.map((s) => s.end_asset_id || ""),
      ]
    : ["", ""];
}
export function boundarySubmission({ project, assetIds, prompts, durations }) {
  if (assetIds.length < 2 || assetIds.some((id) => !id))
    throw new Error(
      "Jedes Rahmenbild muss als echte Bilddatei zugeordnet sein.",
    );
  const count = assetIds.length - 1;
  if (project.scenes.length && count !== project.scenes.length)
    throw new Error(
      "Die Anzahl der Bildübergänge muss den vorhandenen Szenen entsprechen.",
    );
  if (
    prompts.length !== count ||
    durations.length !== count ||
    durations.some((d) => !Number.isFinite(d) || d < 0.1 || d > 120)
  )
    throw new Error(
      "Jede Szene braucht einen Prompt-Eintrag und eine Dauer zwischen 0,1 und 120 Sekunden.",
    );
  return {
    expected_revision: project.revision,
    asset_ids: assetIds,
    prompts,
    durations,
  };
}
export const campaignExample = {
  title: "Beispielkampagne",
  brief: "",
  format: { width: 1080, height: 1920, fps: 30 },
  motifs: [
    {
      key: "motiv-a",
      title: "Motiv A",
      brief: "",
      script: "",
      shots: [
        {
          title: "Szene 1",
          prompt: "",
          duration_s: 5,
          start_frame: "frame-01.png",
          end_frame: "frame-02.png",
        },
      ],
      variants: [
        {
          key: "variante-a",
          title: "Variante A",
          audience: "",
          cta: "",
          endcard_text: "",
          duration_s: 3,
        },
      ],
    },
  ],
};
