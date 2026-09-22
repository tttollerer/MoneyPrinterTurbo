# SpotForge – lokales Video-Studio für den Mac

Dieser Fork ergänzt MoneyPrinterTurbo um ein lokales React-Studio, eine Python-Projektverwaltung und Remotion für Vorschau und MP4-Export. Die ursprüngliche Anwendung bleibt über ihre bisherigen Startbefehle erreichbar. SpotForge verbindet manuell verfasste Briefings/Skripte, lokale Mac-Sprachausgabe mit Untertiteln, Stockmaterial, bildgesteuerte KI-Clips und versionierte Markenprofile. Die automatische KI-Skripterstellung und die übrigen Upstream-TTS-Anbieter bleiben in der ursprünglichen Anwendung.

## Starten

Voraussetzungen: macOS, Node.js 22 oder neuer, `npm`, `uv`, `ffmpeg` und `ffprobe` im PATH. Mit vorhandenen Homebrew lassen sich fehlende Werkzeuge über `brew install node uv ffmpeg` installieren. `uv` richtet Python 3.11 anhand des Lockfiles ein.

Im Finder `start-spotforge.command` doppelklicken oder im Repository ausführen:

```sh
./start-spotforge.command
```

Danach **http://127.0.0.1:4831** öffnen. Das Terminal muss geöffnet bleiben. Der erste Start installiert die festgeschriebenen Abhängigkeiten; beim ersten Render kann Remotion seinen lokalen Browser herunterladen. Nach Einrichtung funktionieren lokale Medien und Exporte ohne KI-Anbieter. Beenden mit Ctrl+C.

Standardordner:

- Projekte, Marken, Medien und Aufträge: `~/Documents/Video Generator/spotforge-data`
- Fertige Videos: `~/Documents/Video Generator/Ausgaben/SpotForge/<Auftrags-ID>/video.mp4`

`SPOTFORGE_DATA_DIR` und `SPOTFORGE_OUTPUT_DIR` überschreiben diese Ordner. Die Daten liegen getrennt vom Code; vorhandene Projekte anderer Anwendungen werden nicht verändert. Für eine Sicherung den Server beenden und beide Ordner kopieren. Pro Datenordner darf genau ein Server laufen. Der Server bindet nur an `127.0.0.1` und ist nicht für öffentliche Bereitstellung oder mehrere Benutzer vorgesehen.

## Erstes Video

1. Projekt anlegen, Hoch- oder Querformat wählen. „Frei“ eignet sich zum Ausprobieren; „Spot“ verlangt nacheinander Konzept-, Skript-, Storyboard-, Clip- und finale Freigaben.
2. Unter „Marken“ ein Profil mit Farben, Logo, Schriftdatei, Textpflichten und Gestaltungsregeln speichern. Eine konkrete Profilversion auf das Projekt anwenden.
3. Szenen hinzufügen. Für einen lokalen Test ein Bild oder Video hochladen und als Quelle wählen. Szenendauer und eingeblendeten Text speichern. Videoclips müssen mindestens so lang wie ihre Szene sein.
4. Im Ablauf-Panel Briefing und Skript speichern. Eine installierte Mac-Stimme wählen und lokal erzeugen; danach das fertige Ergebnis bewusst laden. Die Sprachausgabe enthält aus der tatsächlichen Audiolänge berechnete Satzuntertitel. Beim Rezept „Spot“ vorher Konzept und Skript freigeben. Alternativ Voiceover/Musik hochladen und zuweisen; eigene Untertitel als JSON-Liste eintragen, beispielsweise `[{"text":"Hallo Welt","start_ms":0,"end_ms":2000}]`. Karaoke verlangt zusätzlich Wortzeitpunkte; es erzeugt diese nicht selbst.
5. Vorschau prüfen, gegebenenfalls Freigaben erteilen und „Rendern“ wählen. Der Render erzeugt keine KI-Kosten. Beim Rezept „Spot“ ist die Vorschau vor der finalen Freigabe verfügbar; der Download wird danach freigegeben.

Die Demo lässt sich nach Einrichtung mit `.venv/bin/python -m spotforge.demo --data-dir "$HOME/Documents/Video Generator/spotforge-data"` anlegen. Sie erzeugt synthetische Medien und lokale deutsche Sprachausgabe mit macOS `say`, zwei Projekte und ein Beispiel-Markenprofil. Wiederholtes Ausführen erzeugt weitere Demos. Es werden keine kostenpflichtigen Anbieter aufgerufen.

Szenen lassen sich über Pfeile umordnen. Eine verknüpfte Szene bleibt hinter ihrem Vorgänger. Zeitgebundene Untertitel müssen vor einer neuen Reihenfolge entfernt und anschließend neu abgestimmt werden. Voiceover darf nicht länger als die gesamte Szeneabfolge sein; Videoclips dürfen nicht kürzer als die vorgesehene Szenendauer sein. Das Studio meldet solche Konflikte vor dem Export.

## Stockmaterial und lokale Sprache

Stock-Suche ist mit `PEXELS_API_KEY` beziehungsweise `PIXABAY_API_KEY` in der Serverumgebung möglich. Die Suche wird nur auf ausdrücklichen Klick gestartet, übermittelt den Suchbegriff an den gewählten Anbieter und zeigt dessen Quellenlinks. Erst die bewusste Auswahl eines Treffers lädt einen Clip in das lokale Projekt. Die ursprüngliche Upstream-Konfiguration wird nicht automatisch gelesen oder verändert. Die Adapter folgen den [Pexels-](https://www.pexels.com/api/documentation/) und [Pixabay-Schnittstellen](https://pixabay.com/api/docs/); Lizenz und Herkunft des ausgewählten Materials bleiben relevant. Ohne Schlüssel sind die entsprechenden Aktionen deaktiviert.

Mac-Sprachausgabe benötigt keinen API-Schlüssel. Sie verwendet ausschließlich lokal installierte `say`-Stimmen und FFmpeg. Bei einem Projektwechsel während der Verarbeitung bleibt das Ergebnis als Asset und Auftragsresultat erhalten, wird aber nicht über neuere Eingaben geschrieben. Fehlgeschlagene oder unterbrochene Medienaufträge starten nach einem Neustart nicht automatisch erneut.

## Bestehende Projekte übernehmen

Im Reiter „Projekt importieren“ den lokalen Quellordner und die relativ dazu liegende `project.json` (Version 3) oder alte `spot.json` auswählen. Liegen Marke und Projekt in unterschiedlichen Unterordnern, ihren gemeinsamen Arbeitsordner als Quelle und beispielsweise `projects/mein-projekt/project.json` als Projektdatei verwenden. Erst die Importvorschau prüfen, dann die neue Kopie ausdrücklich bestätigen.

Der Import erhält vorhandene Video-Takes samt Auswahl, Referenzbilder, Audio, Untertitel und passende Markendaten. Bild-Takes, frühere Exporte und nicht direkt darstellbare Zuordnungen bleiben über den Importbericht nachvollziehbar. Fehlende Medien oder Pfade außerhalb der ausgewählten Quelle blockieren den Import. Fremde Verzeichnisse werden nicht durchsucht, entfernte Medien nicht automatisch heruntergeladen und Zugangsdaten nicht übernommen. Unbekannte Felder und nicht übertragbare Markenangaben werden als Einschränkungen angezeigt. Eigene Markenfonts benötigen eine lokale Font-Datei.

Die Originale bleiben unverändert; jeder Import erstellt ein neues Projekt. Frühere Freigaben werden zurückgesetzt. Grenzen: 100 Szenen, 500 Dateien, 100 MB pro Datei und 500 MB insgesamt. Es wurden ausschließlich synthetische Importbeispiele geprüft; echte Nutzerprojekte sollten zunächst anhand der Vorschau kontrolliert werden.

## Start- und Endbilder

Der erste Adapter unterstützt **Kling 2.5 Turbo Pro via fal** mit 5 oder 10 Sekunden. Unterstützt werden ein Startbild sowie Start- und Endbild gemeinsam. Ein Endbild allein benötigt zusätzlich ein ausdrücklich ausgewähltes Startbild; dafür gibt es einen Hinweis im Editor. Eine automatische Erzeugung dieses zusätzlichen Startbilds und reine Text-zu-Video-Modelle folgen später.

Referenzen müssen PNG, JPEG oder WebP sein und zum Seitenverhältnis des Projekts passen. Alternativ lässt sich der letzte Frame eines ausgewählten Takes der vorherigen Szene übernehmen. Änderungen am Vorgänger kennzeichnen abhängige Szenen als veraltet; vor dem Export müssen sie geprüft oder neu erzeugt werden. Frühere Takes bleiben erhalten. Die Bilder steuern das KI-Modell, garantieren jedoch keine pixelgenaue Übereinstimmung. Ein Endbild-Take darf im Schnitt nicht vor seinem Ende gekürzt werden.

Unter **fal einrichten** den API-Schlüssel lokal eingeben: wahlweise im macOS-Schlüsselbund oder nur für die laufende Sitzung. Alternativ wird `FAL_KEY` aus der Serverumgebung unterstützt und hat Vorrang. Die App zeigt den gespeicherten Schlüssel nie wieder an und prüft die Einrichtung ohne bezahlten Modellaufruf. Ohne Schlüssel bleibt der lokale Medien-/Render-Workflow nutzbar. Details: [fal-Gateway](docs/spotforge/fal-gateway.md).

Vor jedem Anbieterauftrag verlangt die Oberfläche eine Kostenbestätigung. Die aktuelle Version zeigt **keinen verlässlichen Preis** an; den Preis beim Anbieter prüfen. Referenzbilder und Szenenprompt werden für diese Funktion an fal übermittelt. Es gibt keine automatischen kostenpflichtigen Wiederholungen. Bei unterbrochenen Aufträgen setzt „Status fortsetzen“ die Abfrage anhand der gespeicherten Anbieter-ID fort. Ist die Übermittlung unklar, erst im Anbieter-Dashboard prüfen und die bekannte Request-ID eintragen; niemals blind neu absenden.

Der Adapter ist mit simulierten Anbieterantworten getestet. Ein bezahlter Live-Auftrag gehört noch zur ausstehenden Abnahme.

## Kampagnen und Batch-Produktion

Der Einstieg **Kampagnen** bündelt Werbemotive und Zielgruppenfassungen. Der geführte Ablauf lautet **Briefing → Storyboard → Produktion → Abnahme → Export**. Motive können gemeinsam als Batch vorbereitet werden. Die Vorschau prüft Aufträge und Freigaben; erst die ausdrückliche Bestätigung startet die kostenpflichtige Clipproduktion. Vorhandene ausgewählte, aktuelle Takes werden übersprungen. Pausieren und Abbrechen verhindern weitere Übermittlungen, stornieren aber keine bereits gestarteten Anbieterleistungen.

Für N Szenen N+1 Bilder zuordnen: Bild 1 ist Start von Clip 1, Bild 2 zugleich dessen Ende und Start von Clip 2 usw. Die Zielgruppenfassungen eines Motivs verwenden dieselben freigegebenen Clips und ergänzen jeweils Schlusskarte und CTA. Geänderte Fassungen behalten frühere Ausgaben als eigene Projekte. Unterschiedliche Handlung oder Besetzung braucht ein separates Motiv.

Ein Kampagnen-JSON kann als Vorschau importiert werden. Dateinamen ohne zugeordnete Assets bleiben sichtbar fehlend. Ein Chat-Link allein lädt keine Bilder oder Anhänge. Der JSON-Export sichert den Produktionsplan, kein vollständiges Medien-/Projektbackup. Format und konkrete Markenversion dürfen pro Motiv variieren und bleiben im Plan erhalten. Beispiel und API: [Kampagnen](docs/spotforge/campaigns.md).

## Corporate Identity und Styleguides

Profile enthalten Logo/Position, Farben, eigene Schriftdatei, Schutzabstand, Untertitelstil, Pflichttext, Sprache, Tonalität, visuellen Stil, Regeln und verbotene Aussagen. Änderungen erzeugen neue unveränderliche Versionen. Bestehende Projekte behalten ihren bisherigen Stand, bis eine neue Version ausdrücklich angewendet wird.

Remotion verwendet denselben Profilstand in Vorschau und Export. Dokumente (PDF, TXT, Markdown) lassen sich als ergänzende Referenzen hinterlegen. Sie werden derzeit nicht automatisch ausgelesen. Visueller Stil und Regeln fließen in den Generierungsprompt ein; Freitextregeln sind keine Garantie für das Verhalten des KI-Modells. Die menschlichen Freigaben bleiben erforderlich. Projektbezogene Einzel-Overrides und eine semantische automatische Markenprüfung sind noch nicht enthalten.

## Entwicklung und Prüfung

```sh
uv sync --frozen --python 3.11
uv run --no-sync ruff check app cli.py main.py webui test docs/skill spotforge
uv run --no-sync python -X utf8 -m coverage run -m pytest -q test
uv run --no-sync python -m coverage report
npm --prefix renderer ci
npm --prefix renderer test
npm --prefix renderer run build
npm --prefix studio ci
npm --prefix studio test
npm --prefix studio run build
```

Für UI-Entwicklung zusätzlich `npm --prefix studio run dev` auf Port 4832 starten; der Proxy erwartet Python auf Port 4831. API: `http://127.0.0.1:4831/docs`. Verträge: [contracts.md](docs/spotforge/contracts.md). Die ursprüngliche Python-Coverage-Grenze gilt für den Upstream-Code; neue SpotForge-Tests laufen in derselben Suite. Node-Tests und beide Builds werden zusätzlich in GitHub Actions geprüft.

Reale lokale Abnahme: MP4 in 1080×1920 und 1920×1080, 30 fps, H.264/AAC, sechs Sekunden, deutsche Sprachausgabe, synchronisierte Satzuntertitel, Logo und eingebettete Schrift. Die Dateien bleiben außerhalb von Git.

MoneyPrinterTurbo steht unter der Repository-Lizenz. Remotion hat eigene [Lizenzbedingungen](https://www.remotion.dev/license); diese vor einer kommerziellen oder teamweiten Nutzung passend zum Einsatz prüfen. Medien- und Schriftlizenzen müssen ebenfalls zur vorgesehenen Ausgabe passen.
