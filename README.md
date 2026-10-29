# Motion Tracking

Version **1.0.1**. Der STEP-Importer berücksichtigt die unterschiedlichen
Baugruppenrichtungen von Fusion und SolidWorks, einschließlich verschachtelter
Transformationen. Das Einlesen ändert keine gespeicherte Marker-Geometrie.
Details: [STEP-Import](docs/STEP_IMPORT.md).

Installationen liegen unter einem gemeinsamen
`MotionTracking_App/versions/<Versionskennung>/App`. Updates erzeugen keine
zusätzlichen Ordner mit Datum/Uhrzeit. Eine bereits vorbereitete Version wird
nach Integritäts- und Einstellungsprüfung wiederverwendet; bei Abweichungen
wird keine zweite Kopie angelegt. Nach dem erfolgreichen Wechsel werden
inaktive verwaltete Programmversionen entfernt. Messdaten, Kalibrierungen und
Begleitdateien werden vorher bytegenau geprüft in `PreservedData` erhalten.
Laufende Versionen und aktiv referenzierte Datenpfade werden nicht entfernt.

Independent project for use with Atracsys tracking hardware. Not affiliated with, endorsed by, or supported by Atracsys. Manufacturer names identify supported hardware only. Rights and licence requirements for third-party software remain separate.

Eigene Erweiterung fÃ¼r fusionTrack500, externe Messskripte (ESP32/NAU7802),
gemeinsame Aufnahmen und nachtrÃ¤gliche Auswertung. Ohne Hardware-Trigger wird
softwareseitig zeitlich zugeordnet; physikalisch gleichzeitige Messungen werden
nicht zugesichert. Rohmessungen und Kalibrierungen gehÃ¶ren nicht in dieses Repository.

## Projektstruktur

- `native/`: wiederhergestellter und gegen die aktuelle Bridge geprüfter C++-Quellcode; [Build und Prüfnachweis](native/README.md).
- `atracsys_sync/`: Aufnahmesteuerung, GerÃ¤teprozesse, Versionsadapter und OberflÃ¤che.
- `measurement_scripts/`: Messquellen; Simulation und serielle Kraftmessung.
- `analysis_scripts/`: Auswertungen, gemeinsame Bibliothek in `_lib/`.
- `tools/`: Paketbau, IntegritÃ¤tsprÃ¼fung und sichere Cache-Archivierung.
- `packaging/`: eigenstÃ¤ndiger Offline-Installer.
- `tests/`: unabhÃ¤ngige Regressionstests mit synthetischen Messdaten.

Die ursprÃ¼ngliche Tracker-EXE, das Atracsys-SDK, Qt und andere mitgelieferte
Laufzeitbibliotheken werden aus einer vorhandenen, berechtigt genutzten
Installation Ã¼bernommen. Sie werden nicht in Git gespeichert. Der Builder
prÃ¼ft die unverÃ¤nderten eingebetteten Module der Basisanwendung.

## Zwick-Steuerung und Aufnahme

Start und Stop sind getrennte, stets sichtbare Schaltflächen. Die Frequenz kann
direkt als Dezimalzahl eingegeben werden. Manuelle Dateinamen und Exportordner,
der kopierbare und änderbare Registry-Pfad sowie Updates stehen unter
**Einstellungen**. Zwick kann Name, Ordner und Frequenz direkt vorgeben; eine
`ExperimentName.txt` ist nicht erforderlich. Protokoll und Ablauf:
[Zwick / testXpert control](ZWICK_CONTROL.md).

## Auswahl der Auswertung

Für das Toggling-Setup mit 340/341/342 gibt es eine eigene punktbezogene
Auswertung mit Knochenkompensation, Zeitkurven und Zykluswerten:
[Toggling – Einrichtung und Methoden](TOGGLING.md). Der konkrete Auswertepunkt muss
vor der ersten Auswertung im lokalen Marker-Koordinatensystem definiert werden.

Die normale Liste bietet **Automatisch erkennen**, **ROM**, **Trochanter**, **Toggling** und
**Freie Marker-Auswertung**.
Die Automatik unterscheidet ROM, Trochanter, Toggling und den Drei-Marker-Monotest anhand
der Marker. Mit **Weitere Profile und eigene Skripte anzeigen** werden die
direkte Monotest-Auswertung, die drei speziellen Markerprüfungen und zusätzliche
lokale Skripte eingeblendet. Eine bereits aktive Spezialauswertung bleibt auch
bei eingeklappter Liste sichtbar und ausgewählt.

Hilfsbibliotheken werden nicht als Auswertung angeboten. Die historische
ROM-Nachauswertung bleibt im eigenen CSV-Bereich verfügbar. Die Bereinigung
ändert nur die Auswahl; Bibliotheken, Messdaten und Kalibrierungen bleiben erhalten.

Mit **Freie Zuordnung …** werden Marker frei benannt, ein Referenzmarker und
beliebige Vergleichspaare gewählt. Das gespeicherte Profil kann nach Aufnahmen
automatisch laufen oder mit **CSV frei auswerten …** auf vorhandene aktuelle
Aufnahmen angewendet werden. Details: [Freie Marker-Auswertung](FREE_ANALYSIS.md).

## Entwicklung und Tests

Windows x64, Python **3.11** und Git werden fÃ¼r die Entwicklung benÃ¶tigt.
Die fertige Offline-Anwendung benÃ¶tigt kein zusÃ¤tzlich installiertes Python.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

Den GUI-Selbsttest ausschlieÃŸlich in einer neu gebauten Testinstallation starten:

```powershell
.\.venv\Scripts\python.exe tools/build_extension.py --source "C:\Pfad\BestehendeApp" --target "C:\Pfad\NeueVersion\App" --config "C:\Pfad\BestehendeApp\config.json"
.\.venv\Scripts\python.exe tools/verify_release.py "C:\Pfad\NeueVersion\App"
& "C:\Pfad\NeueVersion\App\AtracsysLiveTracker_Messskripte.exe" --sync-self-test "C:\Pfad\Testausgabe"
```

Der Builder akzeptiert nur einen neuen Zielordner. Er Ã¤ndert bestehende
Installationen, Messdateien oder Kalibrierungen nicht. Ein Test ist erst
erfolgreich, wenn auch `Testausgabe/result.json` den Wert `passed: true` enthÃ¤lt.
Echte GerÃ¤teaufnahme und Dauertest sind zusÃ¤tzliche PrÃ¼fungen am Messplatz.

## GitHub und mehrere Computer

Normale Programmstarts verwenden eine Windows-Startsperre über alle neuen
Installationsordner hinweg. Ein weiterer Start öffnet keine zweite Kamera-
Verbindung, sondern aktiviert das vorhandene Programmfenster. Die Sperre wird
vom Betriebssystem auch nach einem Absturz freigegeben. Interne Messskript-
und Auswertungsprozesse sowie isolierte Selbsttests laufen weiterhin separat.
Ältere Versionen ohne diese Sperre sollten nicht mehr direkt gestartet werden;
sichtbare ältere Hauptfenster werden beim Start der neuen Version ebenfalls erkannt.

Öffentliches Repository: https://github.com/masut97/MotionTracking (Branch `main`).

Dieses Repository enthÃ¤lt den bearbeitbaren eigenen Code, Tests und Build-Weg.
Eine neue Version wird erst nach Review und grÃ¼nen Tests verÃ¶ffentlicht.
Nach einmaliger Installation des neuen vollstÃ¤ndigen ZIPs stehen Updates direkt
im Tab **Analysis / Auswertung** Ã¼ber **Nach Updates suchen â€¦** bereit.
Die Update-Prüfung und Downloads erfolgen über HTTPS ohne Anmeldung, Token oder GitHub CLI. Auf dem Mess-PC sind weder Python noch Git erforderlich.

Der Button lÃ¤dt ausschlieÃŸlich nach bestandenen GitHub-Tests verÃ¶ffentlichte
Code-Pakete. Die neue Version wird getrennt installiert und mit zwei simulierten
Aufnahmen geprÃ¼ft. Erst danach wird die Desktop-VerknÃ¼pfung aktualisiert;
anschlieÃŸend kann neu gestartet werden. WÃ¤hrend Aufnahme, Vorbereitung oder
GerÃ¤teprÃ¼fung sind Updates gesperrt. Die vorherige Programmversion bleibt bis zum erfolgreichen Wechsel erhalten; danach werden ihre Programmdateien bereinigt. Rohdaten bleiben erhalten. Messpfad, GerÃ¤teeinstellungen und Geometrien/Kalibrierungen werden vom
jeweiligen PC Ã¼bernommen; diese Dateien werden nicht aus GitHub ersetzt.

Die automatische VerÃ¶ffentlichung liegt in `.github/workflows/tests.yml`.
`tools/create_update.py` erzeugt das Update aus dem exakten Git-Commit, mit
SHA256-Dateimanifest und kompatiblem Basisprogramm-Hash. Lizenzierte Bibliotheken
werden ausschlieÃŸlich lokal Ã¼bernommen und nicht auf GitHub hochgeladen.

FÃ¼r die Entwicklung auf weiteren PCs klonen und die Umgebung wie oben einrichten:

```powershell
git clone https://github.com/masut97/MotionTracking.git
cd MotionTracking
```

Danach erfolgt die Ã¼bliche Arbeit Ã¼ber Branch, Pull Request und Tests. Den
freigegebenen Branch auf den anderen PCs klonen. `git pull --ff-only` Ã¼bernimmt
neue Ã„nderungen ohne automatisches Vermischen lokaler Anpassungen. Der Helfer
`tools/Update-FromGit.ps1` aktualisiert einen sauberen Checkout, testet und baut
eine neue Version. Er startet keine Hardwareaufnahme und wechselt keine laufende App.

Lokale GerÃ¤teports, Speicherpfade, Kalibrierungen und Messdaten verbleiben auf
dem jeweiligen PC. Keine Zugangsdaten, persÃ¶nlichen Messdaten oder lizenzierten
Herstellerdateien hochladen. Ein Lizenzrecht an Fremdkomponenten wird durch
dieses Repository nicht erteilt.

## Trochanter

Akzeptierte Namen: `<Probe>_<8er|double|screw>[Wiederholung]_<150N|300N|450N|RTF>.csv`.
Auch vorhandene Namen ohne Unterstrich vor dem Protokoll werden erkannt.
`double2` und `double3` bleiben getrennte Aufnahmen. Ergebnisse werden unter
`<Probe>_analysis/recordings/<Dateiname>/` gespeichert; erneute Auswertungen
erhalten einen zusÃ¤tzlichen Zeitstempel. Originale werden nicht umbenannt.
Keine erkannten aktiven Zyklen fÃ¼hrt zu einer klaren Fehlermeldung statt
einer erfundenen aktiven Phase. Marker-AusfÃ¤lle mÃ¼ssen bei der Interpretation
der Ergebnisse berÃ¼cksichtigt werden.

## Sicherheit beim AufrÃ¤umen

`tools/inventory_cleanup.py --help` beschreibt Inventar und Cache-QuarantÃ¤ne.
Das Werkzeug lÃ¶scht nicht dauerhaft und akzeptiert nur explizite eigene
Cache-Verzeichnisse mit geprÃ¼ftem Inhalt. Rohmessungen, Geometrien und
Kalibrierungen bleiben geschÃ¼tzt. RÃ¼ckfallstand und PrÃ¼fprotokoll liegen
auÃŸerhalb des Repositories.


## Lokale Einstellungen und Datenschutz

Messdaten, Herstellerbibliotheken und Kalibrierungen werden nicht veröffentlicht.
Rechnerspezifische Analysepfade und Gerätekonfigurationen bleiben lokal in
`*_config.local.json`, Markertransformationen in `marker_transforms.local.json`
und Versuchsanmerkungen in `analysis_vendor/failure_annotations.local.json`.
Ein Update übernimmt vorhandene lokale Werte; fehlende Marker-Kalibrierungen
werden bei angeforderter Transformation als Fehler gemeldet.

Die öffentliche Historie beginnt mit einer bereinigten Version. Frühere private
Historie, Diagnoseberichte und Rohdaten gehören nicht zu diesem Repository.
Ein bestehender Entwicklungs-Checkout mit alter Historie muss neu geklont werden.
Programm-Updates über den Update-Button benötigen keinen Git-Checkout.

## Automatic ROM assessment

English plots now display **Approved**, **Not approved — REPEAT TEST**, or
**Check data and if needed repeat**, with a problem description and corrective
action for each finding. Camera-only results explicitly exclude load approval.
See [ROM quality rules and limitations](ROM_QUALITY.md) for the mathematical
checks and their interpretation. Measurements and validation examples stay local.
