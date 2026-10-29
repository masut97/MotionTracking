# Messskripte in Motion Tracking

Die erweiterte Anwendung heißt `AtracsysLiveTracker_Messskripte.exe`. Die bisherige
`AtracsysLiveTracker.exe` bleibt unverändert und kann weiterhin verwendet werden.

## Bedienung

Die Aufnahmeleiste bleibt in allen Ansichten sichtbar. Darunter gliedern drei Tabs die
Anwendung; zusätzlich gibt es **Einstellungen** für manuelle Speicherziele,
Zwick-Steuerung und Software-Updates:

- **Live** zeigt beide Kamerabilder und erkannte Geometrien. Selten benötigte Markerfunktionen
  befinden sich im Dropdown **Werkzeuge**.
- **Messgeräte** enthält Auswahl, Einstellungen, Verbindungstest und laufenden Status aller
  Messskripte. Das Dropdown **Messgeräte** in der Aufnahmeleiste erlaubt eine schnelle Auswahl.
- **Auswertung** enthält die optionale automatische Auswertung nach dem Speichern.

Die erkannte Marker- und Geometrieanzeige bleibt unter den Tabs in jeder Ansicht
eingeblendet und wird auch beim Wechsel zwischen den Tabs weiter aktualisiert.
Während der kurzen Gerätevorbereitung steht oben „Messgeräte werden vorbereitet“;
dieser Wartezustand ist kein Fehler.

1. Im Tab **Messgeräte** ein oder mehrere Skripte auswählen.
2. Eine Zeile anklicken und über **Einstellungen** den Anschluss und weitere Werte festlegen.
3. Mit **Verbindung testen** fünf Sekunden lang prüfen, ob gültige Werte eintreffen.
4. **Aufnahme starten** startet Tracker und ausgewählte Skripte gemeinsam und wechselt zur
   Live-Ansicht. **Aufnahme stoppen**
   beendet und speichert alle Quellen.
5. **Details** zeigt Datenrate, gespeicherte Zeilen, Datenlücken, Verluste und Fehlermeldungen.

Start und Stop sind getrennte Schaltflächen. Die Frequenz lässt sich direkt als
ganze Dezimalzahl in Hz eingeben. Für die direkte Steuerung aus Zwick ohne
ExperimentName.txt siehe [Zwick-Steuerung](ZWICK_CONTROL.md).

Für die aktuell angeschlossene Hardware wird `zugkraft.py` verwendet. Der Treiber
liest den laufenden Zahlenstrom des Nano ESP32 direkt über USB und entspricht der
MATLAB-Umrechnung:

```text
Kraft [N] = -Rohwert + 87.5
COM6, 230400 Baud, DTR/RTS aktiv, LF-Zeilenabschluss
```

Port, Baudrate, DTR/RTS, Nullpunkt und Zeitlimits können in der Oberfläche geändert
werden. Der bisherige HC-06-Pfad kann bei Bedarf weiterhin über COM4 gewählt werden.

## Ausgabedateien

Für eine Aufnahme `Versuch.csv` entstehen beispielsweise:

```text
Versuch.csv                              Atracsys-Markerwerte
Versuch.json                             Atracsys-Metadaten
Versuch.script_01_zugkraft.csv           Kraftwerte
Versuch.script_02_andere_quelle.csv      weitere Messwerte
Versuch.scripts.json                     gemeinsamer Status und Vollständigkeitsprüfung
```

Die Dateien enthalten `session_id`, `sample_index`, `time_s`,
`computer_monotonic_s`, `frame_counter` und `tracker_device_timestamp_us`. Damit lassen
sich zusammengehörige Datensätze eindeutig verbinden. Ein Lauf wird nur dann als
`complete` markiert, wenn Tracker und alle Messskripte jeden Referenz-Sample ohne Verlust
gespeichert haben.

Beide JSON-Dateien werden direkt beim Stoppen finalisiert. Ein späterer Test oder
Aufnahmestart verändert eine bereits abgeschlossene `.scripts.json` nicht mehr.

## Software-Synchronisierung ohne Trigger

Der bestehende Aufbau und die Nano-Firmware bleiben unverändert. Ein Hintergrundthread
liest jede vollständige LF-terminierte Zahl sofort ein und versieht sie mit
`sensor_received_monotonic_s`. Für jeden vom Tracker akzeptierten Aufnahmeframe wird
der zeitlich nächste bereits empfangene Sensorwert gewählt. Dafür verwendet die
Erweiterung den systemweiten Windows `QueryPerformanceCounter` mit hoher Auflösung.
Beide Dateien erhalten
dieselbe `session_id`, `sample_index`, Tracker-Referenzzeit und Frame-ID.

Die Kraftdatei enthält zusätzlich:

- `sensor_sequence`: fortlaufender Zähler der tatsächlich empfangenen Sensorwerte,
- `sensor_to_tracker_ms`: Empfangszeitpunkt minus Trackerzeit in Millisekunden,
- `sensor_reused`: 1, wenn derselbe Sensorwert für einen weiteren Frame verwendet wurde,
- `sensor_rate_Hz`: gemessene Rate des seriellen Datenstroms.

Damit besitzen die getrennten Dateien dieselbe Zeilenzahl und Referenzfrequenz. Die
Spalten zeigen zugleich, wo der Kraftsensor langsamer war oder ein Wert zeitlich weiter
vom Kameraframe entfernt lag. USB-, Bluetooth- und Betriebssystemlatenzen bleiben in
diesem Verfahren enthalten; es ist daher ausdrücklich als `best_effort_host_time`
gekennzeichnet.

Im Mock-Kameramodus darf `demo.py` den vollständigen Softwareablauf testen. Alle dabei
erzeugten Dateien tragen ausdrücklich `synchronization=simulation` und
`hardware_synchronized=false`.

## Schnittstelle für weitere Python-Skripte

Ein importierbares Skript deklariert zuerst seine Metadaten. Beim Import werden diese
ohne Ausführung des Skripts gelesen. Ausgeführt wird das Skript erst beim Verbindungstest
oder Aufnahmebeginn, jeweils in einem eigenen Prozess.

```python
MEASUREMENT_SCRIPT = {
    "api_version": 1,
    "name": "Temperatur",
    "columns": ["temperature_C"],
    "simulated": False,
    "sync_mode": "best_effort_host_time_v1",
    "defaults": {"port": "COM5", "baudrate": 115200},
}


class Source:
    def __init__(self, config):
        # Gerät öffnen und Konfiguration prüfen
        pass

    def sample(self, tick):
        # Ein Dictionary mit genau den deklarierten Spalten zurückgeben.
        # None bedeutet: Für diesen Sample liegt kein Wert vor.
        return {"temperature_C": 21.5}

    def close(self):
        # Gerät immer geordnet schließen
        pass


def open_source(config, context):
    return Source(config)
```

`tick` enthält die gemeinsamen Referenzfelder. Bei `best_effort_host_time_v1` ordnet
das Skript seinen Messwert dem Feld `computer_monotonic_s` zu und sollte seinen eigenen
Empfangszeitpunkt sowie die Differenz als Messwertspalten zurückgeben. Alle Messwerte müssen endliche Zahlen
sein. Abweichende Spalten, `NaN`, unendliche Werte, ungültiges JSON, blockierende
Treiber oder ein unerwartet beendeter Prozess werden als Fehler angezeigt. Maximal acht
Messskripte können gleichzeitig laufen.

Ein fremdes Messskript ist ausführbarer Python-Code und sollte nur importiert werden,
wenn Herkunft und Inhalt vertrauenswürdig sind.

## Fehlerbehandlung

- `Läuft`: gültige Werte treffen ein.
- `Keine Daten`: Verbindung steht, aber es kam noch kein gültiger Wert an.
- `Datenlücke`: mindestens ein gemeinsamer Sample besitzt keinen Messwert.
- `Fehler`: Initialisierung, Kommunikation oder Validierung ist fehlgeschlagen.
- `Gespeichert`: die Skriptdatei wurde geschlossen und finalisiert.

Bei einem Prozess- oder Kommunikationsfehler stoppt die gemeinsame Aufnahme. Eine
unvollständige Datei bleibt mit `.part` erhalten; die zugehörige `.log`-Datei enthält
die Fehlermeldung. Vorhandene Messdateien werden nicht überschrieben.
