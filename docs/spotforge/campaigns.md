# Kampagnen und Batch-Produktion

Der Arbeitsablauf folgt der üblichen Werbeproduktion: **Briefing → Storyboard → Produktion → Abnahme → Export**. Eine Kampagne bündelt mehrere Motive. Ein Motiv hat ein gemeinsames Projekt mit Szenen und Bildfolgen. Zielgruppenfassungen ergänzen eigene Schlusskarten und CTAs; ihre Handlung kann dieselben Clips verwenden. Unterschiedliche Handlungen gehören in getrennte Motive.

## Bedienung

1. Kampagne anlegen oder ein Kampagnen-JSON zur Vorschau importieren. Marke und Bildformat festlegen. Fehlende Medien sind sichtbar; Dateinamen allein laden keine Dateien.
2. Pro Motiv Briefing, Skript und Szenen im Editor bearbeiten. Für N Szenen N+1 Grenzbilder zuordnen: Bild 1 → Bild 2, Bild 2 → Bild 3 usw. Alternativ Start-/Endbilder einzeln im Szeneneditor setzen. Ein vorbereitetes Endbild ist eine Modellvorgabe, keine Garantie für einen pixelgenauen KI-Übergang.
3. Konzept, Skript und Storyboard prüfen und freigeben. fal lokal konfigurieren. Motive für den Batch auswählen, Vorschau auf Blockaden und Auftragsanzahl prüfen und die kostenpflichtige Produktion ausdrücklich bestätigen. Neue Motive verwenden Seedance 2.5 US: Startbild bzw. Start+Endbild, feste ganze Laufzeiten von 4 bis 30 Sekunden, 480p/720p und optionaler Ton. Kling 2.5 Turbo Pro bleibt mit 5 oder 10 Sekunden verfügbar. Die Modellwahl gilt pro Szene; bestehende Projekte werden nicht automatisch umgestellt.
4. Ergebnisse und Takes sichten. Erst nach Clipfreigabe Zielgruppenfassungen erstellen. Handlung und Markensnapshot werden kopiert; lokale Mediendateien werden geteilt, keine Clips erneut generiert. Eine neue CTA oder eine geänderte Motivversion erzeugt neue Fassungsprojekte. Frühere bleiben erhalten.
5. Fassungen im Editor prüfen und freigeben, mit Remotion rendern, Vorschau ansehen und Endabnahme erteilen. Downloads bleiben an die finale Freigabe gebunden. Markenlogo, Schriften, Farben und Pflichttext kommen aus dem übernommenen Markenprofil. Freigaben werden nie automatisch erteilt.

Der Batch führt Video-KI-Aufträge aus; die finale Freigabe und der Remotion-Export bleiben pro Fassung bewusst kontrollierbar. Sounddesign, manuelle Display-Einsätze und feinere Comedy-Pausen bleiben redaktionelle Arbeit. Ein Chat-Verweis allein enthält keine herunterladbaren JSON-/Bildpakete: Medien müssen separat importiert oder neu erzeugt werden. Diese Version erzeugt aus einer freien Werbeidee nicht automatisch ein vollständiges Drehbuch oder Grenzbilder.

## Zeitlich geplante Grenzbilder

Die Laufzeiten der Szenen sind die maßgebliche Zeitplanung. Bei sechs Clips à fünf Sekunden liegen die sieben Grenzbilder bei 0, 5, 10, 15, 20, 25 und 30 Sekunden. Das Storyboard zeigt diese Zeitpunkte und warnt bei geänderten Laufzeiten: Die Bildinhalte müssen dann erneut auf plausible Bewegung und fortschreitende Zustände geprüft werden. Gemeint ist die Zeit innerhalb der Handlung, nicht eine Wartezeit zwischen Bildgenerierungsaufrufen.

Bildpläne sollten je Grenze Figuren, Kamera, Requisiten und bereits eingetretene Schäden beschreiben. Je Übergang wird die Handlung in der konkreten Clipdauer beschrieben. Das gemeinsame Grenzbild ist Ende des vorherigen und Start des nächsten Clips. Ein Schnitt oder Ortswechsel benötigt eine ausdrücklich geplante Einstellung; ein Bildpaar allein garantiert keine physikalisch korrekte KI-Bewegung.

GPT Image 2 kann außerhalb des Studios über die eingebaute Codex-Bildgenerierung verwendet werden. Diese nutzt die Codex-Kontingente und ist nicht unbegrenzt kostenlos. Die separate OpenAI CLI ruft die kostenpflichtige API auf. Neue Bilder werden anschließend als lokale Assets zugeordnet. Quellen: [Codex Bildgenerierung](https://learn.chatgpt.com/docs/image-generation), [OpenAI Images CLI](https://developers.openai.com/api/reference/cli/resources/images/methods/generate).

## Kampagnenformat v1

```json
{
  "schema_version": 1,
  "title": "Produktkampagne",
  "brief": "Ein gemeinsamer Produktfilm mit zwei Schlussfassungen.",
  "format": {"width": 1080, "height": 1920, "fps": 30},
  "motifs": [{
    "key": "pilot",
    "title": "Pilotmotiv",
    "script": "Ausgangssituation, Veränderung, Produktauflösung.",
    "shots": [
      {"title": "Ausgangssituation", "prompt": "Ruhige Ausgangssituation", "duration_s": 5, "start_frame": "01.png", "end_frame": "02.png"},
      {"title": "Auflösung", "prompt": "Die Veränderung wird sichtbar", "duration_s": 5, "start_frame": "02.png", "end_frame": "03.png"}
    ],
    "variants": [
      {"key": "private", "title": "Privatkunden", "audience": "Privatkunden", "endcard_text": "Produkt entdecken", "cta": "Mehr erfahren", "duration_s": 3},
      {"key": "business", "title": "Geschäftskunden", "audience": "Geschäftskunden", "endcard_text": "Produkt im Unternehmen", "cta": "Kontakt aufnehmen", "duration_s": 3}
    ]
  }]
}
```

Pro Shot werden `model`, `resolution`, `generate_audio` und `bitrate_mode` gespeichert und exportiert. Seedance verwendet den Endbildparameter `end_image_url`, Kling `tail_image_url`; Adapter, Modell und Optionen bleiben pro Auftrag festgehalten. Seedance erhält `aspect_ratio: "auto"` anhand des Eingangsbilds und immer eine explizite Laufzeit. `auto` als Laufzeit ist für zeitlich geplante Bildfolgen nicht zulässig. [Seedance 2.5 API](https://fal.ai/models/bytedance/seedance-2.5/us/image-to-video/api).

Optionale Kampagnenfelder `brand_id` und `brand_version` referenzieren ein lokales Markenprofil. Der Import pinnt dessen Version. `start_asset_id`/`end_asset_id` und `endcard_asset_id` referenzieren bereits hochgeladene lokale Bilder. Dateinamen `start_frame`/`end_frame` sind nur redaktionelle Hinweise; sie werden nie als Dateipfade gelesen. Eine vorhandene gestaltete Schlusskarte kann per `endcard_asset_id` eingesetzt werden; Text und CTA dürfen leer bleiben, wenn sie bereits im Bild enthalten sind. Ohne Bild erstellt die App einen neutralen Markenfarb-Hintergrund und Remotion setzt den Text.

Der JSON-Export ist ein **Produktionsplan, kein Projektbackup**. Er enthält keine Mediendateien, Takes, Tonspuren oder Freigaben. Die vollständigen Projekte bleiben in `SPOTFORGE_DATA_DIR`. Auf einem anderen Rechner müssen Marken und Medien neu zugeordnet werden. Bestehende Motive behalten ihr eigenes Briefing; Änderungen am Kampagnenbriefing überschreiben sie nicht.

## API und Persistenz

- `GET/POST /api/campaigns`; `GET/PATCH /api/campaigns/{id}`. Detail ergänzt `projects`, indiziert nach Projekt-ID.
- `POST /api/campaigns/import/preview` mit `{document}` prüft alle bekannten Asset-Referenzen. Antwort: `preview_id`, `document`, `motifs_count`, `variants_count`, `warnings`, `missing_frames`.
- `POST /api/campaigns/import/commit` mit `{preview_id, confirmed:true}` ist wiederholbar: dieselbe Vorschau liefert dieselbe Kampagne. Es entstehen keine Anbieteraufträge.
- `POST /api/campaigns/{id}/motifs` mit `{expected_revision, motif}`. Revision bezieht sich auf die Kampagne.
- `PATCH /api/campaigns/{id}/motifs/{key}` mit `{expected_revision, variants:[...]}` bearbeitet die Fassungen, erhält alle bisherigen Ausgaben.
- `POST /api/campaigns/{id}/motifs/{key}/frames` mit `{expected_revision, asset_ids:[...], prompts?:[...], durations?:[...]}`. Revision bezieht sich auf das Motivprojekt. Exakt N+1 Bilder für N Szenen, keine stillen Löschungen; geänderte Takes werden als veraltet markiert.
- `POST /api/campaigns/{id}/motifs/{key}/variants` mit `{expected_revision, confirmed:true}` prüft Motivprojekt und Freigaben. Gleiche Quelle/Fassungsdefinition liefert dieselben Projekte, Änderungen erhalten eine neue Version. Antwort: `{campaign, projects}`.
- `GET /api/campaigns/{id}/export` liefert `{document,warnings,timelines}` mit abgeleiteten Zeitpunkten je Motiv; für einen späteren Import nur `document` verwenden.

`campaigns/` und `campaign_previews/` enthalten atomar geschriebene JSON-Dokumente. Projekte sind weiterhin die maßgebliche Quelle für Storyboard, Marken, Takes und Freigaben. Es gibt keinen zweiten Szenenstatus in der Kampagne. Grenzen: 30 Motive, je 100 Szenen und 20 Fassungen. Import und Fassungsbau validieren vor Schreibzugriffen; wiederholte Fassungsanforderungen überschreiben keine bearbeiteten Ausgaben.
