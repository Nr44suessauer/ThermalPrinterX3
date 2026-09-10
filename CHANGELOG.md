# Changelog

Alle wichtigen Änderungen an diesem Projekt. Format lose nach
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [Semantic Versioning](https://semver.org/lang/de/).

## [1.0.0] – 2026-09-10

Erste veröffentlichte Version.

**Sprache im Projekt:** Quellcode, Kommentare, Docstrings, CLI-Hilfen,
CLI-Ausgaben und die technische Doku (`docs/`) sind **englisch**; die
Bedienungs-Doku gibt es in vier Sprachen (`README.md` englisch als
Hauptfassung, dazu `README.de.md`, `README.ja.md`, `README.zh.md`;
`INSTALL.md` und `CHANGELOG.md` deutsch), und die Oberfläche ist
mehrsprachig (English/Deutsch/日本語/中文, Standard folgt der Systemsprache).

### Enthalten
- **Enthalten** – das Projekt ist selbstständig: Code, App-Icon
  (`assets/x3drucker.png`) und Beispielbild (`assets/sample.jpg`, wird beim
  ersten Start geladen, damit die Oberfläche überall gleich aussieht).
- **CLI `x3print.py`** – Text, Textdatei, Bild (PNG/JPG/SVG), EAN-13,
  QR-Code (Webseite, WLAN, vCard, Kontakt, E-Mail, SMS, Standort),
  Markdown, Meme, Vorschub, Demo-Seite, Kalibrierung, Status.
- **Protokoll** – YK/YZW-Frames für die ORGBRO-X3-Familie (864 dots @ 300 dpi)
  über Bluetooth-RFCOMM, ohne root; die Daten gehen **ohne künstliche Pausen**
  an den Drucker (der Druckerpuffer gibt das Tempo vor, der Motor läuft durch);
  Bildaufbereitung mit Schwellwert, Dithering, Helligkeit/Dichte, Drehen,
  Spiegeln, Strecken, Zuschneiden.
- **CUPS** – Warteschlange `X3Thermo` in jedem Druckdialog, wahlweise über
  die Brücke `cups/x3bridge.py` (ohne sudo) oder den nativen Filter
  `cups/x3raster.py` + Backend `cups/x3bt.py`.
- **Desktop-Programm `x3gui.py`** (GTK3) – Live-Vorschau mit Ziehen/Skalieren,
  Inhalts-Tabs (Text/Markdown, Layout mit Hintergrundbild und mehreren
  Textblöcken, Bild, Barcode & QR, Einstellungen), Druckerdatenbank
  (X3 **und** Standard-ESC/POS), drei Designs (dark/light/deadlinedriven),
  Sprachumschaltung Deutsch/English/日本語/中文, einstellbare UI-Größe,
  **Desktop-Verknüpfung** (wird beim ersten Start automatisch angelegt und
  ist in *Einstellungen → Design* jederzeit erstell- und entfernbar),
  kompaktes Layout mit automatischer Spaltenbreite.
- **Verbindung** – Der X3 nimmt nur **einen** Client an: Bleibt eine alte
  Bluetooth-Sitzung hängen (PC meldet `Connected: yes`, RFCOMM läuft aber in
  einen Timeout), trennt der Treiber die Verbindung automatisch
  (`drop_bluetooth_link()`) und verbindet sich neu; bei „device busy" wartet er
  nur und versucht erneut. Vorher musste man `bluetoothctl disconnect` von Hand
  ausführen.
- **Schriften** – Text mit japanischen/chinesischen Schriftzeichen wird
  automatisch mit einer CJK-Schrift (Noto Sans CJK, Droid Sans Fallback …)
  gesetzt, auch die Maßangaben der Vorschau; ohne diese Erkennung wären die
  Zeichen als Kästchen gedruckt worden.
- **Dokumentation** – `README.md` (englische Hauptfassung) mit Übersetzungen in
  `README.de.md`, `README.ja.md` und `README.zh.md` (je mit Screenshot der
  Oberfläche, Schlagwörtern für Suchmaschinen und Verweis auf
  <https://www.deadlinedriven.dev>), `INSTALL.md` (Installation, Deinstallation,
  Fehlersuche), `docs/README.md` (technische Doku, englisch) und
  `docs/diagrams/` mit 13 PlantUML-Diagrammen
  (Anwendungsfälle, Architektur, Verteilung, Abläufe, Klassen,
  Zustände, Wireframe, Hardware-Fakten) samt Quellen und `build.sh`.
- **Hilfen** – `scripts/pair.sh` (Bluetooth-Kopplung), `scripts/install-cups-user.sh`
  (ohne sudo), `scripts/install-cups.sh` (nativ), `scripts/install-desktop.sh`
  (Menüeintrag + Symbol + Desktop-Verknüpfung), `scripts/install.sh` (Komplett-Installer mit
  Abhängigkeitsprüfung, Selbsttest, Koppeln und optionaler CUPS-Einrichtung),
  `--selftest` (Selbsttest ohne Drucker), Anleitung in `INSTALL.md`.
- **Release** – Versionsnummer `1.0.0` (`--version`), `requirements.txt`,
  MIT-Lizenz, Changelog, Selbsttests (`tests/test_smoke.py`),
  Startmenü-Eintrag mit Vorlage, Git-Repository; keine persönlichen
  Pfade oder MAC-Adressen als Vorgabe (Drucker wird automatisch gesucht).
- **Projektstruktur** – übersichtlich getrennt: Programm im Wurzelordner
  (`x3print.py`, `x3gui.py`, `i18n.py`, `x3gui.sh`), Hilfsskripte in
  `scripts/`, CUPS-Anbindung in `cups/`, Bilder in `assets/`, technische
  Doku in `docs/`, Selbsttests in `tests/`.
