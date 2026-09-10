# Installation – X3 Thermodrucker (Version 1.0.0)

Diese Anleitung führt von „nichts installiert" bis „Drucker druckt" –
**ohne root-Rechte** für den normalen Weg (sudo braucht man nur für die
optionalen Systempakete und für die alternative CUPS-Variante B).

- **Schnellweg:** `./scripts/install.sh` (prüft alles, richtet Startmenü ein, koppelt
  den Drucker, legt auf Wunsch die CUPS-Warteschlange an)
- **Nur prüfen:** `./scripts/install.sh --check` (ändert nichts)
- **Nutzung/Details:** siehe [`README.de.md`](README.de.md) (deutsch),
  [`README.md`](README.md) (englisch, Hauptfassung) sowie
  [`README.ja.md`](README.ja.md) und [`README.zh.md`](README.zh.md);
  technische Doku (englisch) unter [`docs/`](docs/README.md)

---

## 1) Voraussetzungen

| Was | Anforderung |
|---|---|
| Drucker | „X3" / ORGBRO-X3 (Snap & Tag), 864 dots @ 300 dpi, Bluetooth |
| Betriebssystem | Linux (Debian/Ubuntu getestet, Fedora analog) |
| Python | 3.9 oder neuer (getestet mit 3.12) |
| Bluetooth | BlueZ mit `bluetoothctl`, Adapter eingeschaltet |
| Sonstiges | optional `ghostscript` (nur für PDF-Druck über CUPS) |

**Systempakete** (einmalig, mit sudo):

```bash
# Debian / Ubuntu / Mint
sudo apt install python3-pil python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
                 bluez bluez-tools ghostscript

# Fedora
sudo dnf install python3-pillow python3-gobject gtk3 bluez ghostscript
```

**Python-Pakete** (ohne sudo; `Pillow` steckt in den Systempaketen):

```bash
pip3 install -r requirements.txt          # Pillow (Pflicht)
pip3 install --user --break-system-packages qrcode     # optional: QR-Codes
```

> Die GUI braucht **PyGObject im System-Python** (`python3-gi`) – eine
> pip-Installation von PyGObject reicht dafür nicht.

---

## 2) Schnellinstallation

```bash
cd /pfad/zum/ThemalDrucker      # Projektordner
./scripts/install.sh                    # führt durch alle Schritte
```

Der Installer erledigt:

1. **Prüfen** – Python, GTK3-Bindings, Pillow, BlueZ, optional qrcode/ghostscript.
   Fehlt etwas, zeigt er den passenden Installationsbefehl an.
2. **Projektdateien** – macht alle Skripte ausführbar.
3. **Selbsttest** – `tests/test_smoke.py` (Rendering, Frame-Format, ESC/POS,
   ganz ohne Drucker).
4. **Startmenü + Schreibtisch** – Eintrag „X3 Thermal Printer" + Symbol,
   dazu eine Verknüpfung auf dem Schreibtisch (beim ersten Programmstart
   passiert das automatisch, siehe 3.5).
5. **Drucker** – Koppeln (`scripts/pair.sh`) und auf Wunsch die CUPS-Warteschlange
   `X3Thermo` (`scripts/install-cups-user.sh`).

Optionen: `--check` (nur prüfen), `--yes` (keine Rückfragen), `--no-cups`,
`--no-pair`, `--no-desktop`, `--no-shortcut` (keine Desktop-Verknüpfung),
`--uninstall`, `--help`.

---

## 3) Schritt für Schritt (wenn man es einzeln machen will)

### 3.1 Projekt ablegen
Ein beliebiger Ordner genügt (z. B. `~/x3drucker` oder dieser Projektordner).
Alle Skripte finden ihre Dateien relativ zu sich selbst – **nichts wird
systemweit installiert**. Der Ordner enthält alles, was das Programm braucht
(Code, App-Symbol `assets/x3drucker.png`, Beispielbild `assets/sample.jpg`);
so sieht die Oberfläche auf jedem PC gleich aus.

```bash
cd /pfad/zum/ThemalDrucker
chmod +x *.sh scripts/*.sh    # einmalig ausführbar machen
```

Aufbau des Ordners: `x3print.py` / `x3gui.py` / `i18n.py` (Programm),
`scripts/` (Installer, Koppeln, Menüeintrag), `cups/` (Systemdrucker),
`assets/` (Bilder), `docs/` (technische Doku auf Englisch inkl. Diagramme),
`tests/` (Selbsttests).

### 3.2 Drucker koppeln

```bash
./scripts/pair.sh                       # MAC wird automatisch gesucht
./scripts/pair.sh AA:BB:CC:DD:EE:FF     # oder MAC direkt angeben
```

- Beim Pairing fragt der Drucker nach einer PIN – meist **`0000`**.
- Ohne koppelbaren Drucker hilft `bluetoothctl scan on` (MAC des Geräts „X3").
- Die MAC wird **nirgends fest vorgegeben**; die Software sucht den Drucker
  selbst (`bluetoothctl`), sonst gibt man sie in der GUI/CLI an.

### 3.3 Erster Test (Kommandozeile)

```bash
python3 x3print.py status                       # Verbindung + Firmware
python3 x3print.py text 'Hallo vom PC'          # erste Seite drucken
python3 x3print.py text 'Test' --out test.png   # nur Vorschau, kein Druck
```

### 3.4 Desktop-Programm starten

```bash
./x3gui.sh
```

- **Ohne Drucker** nutzbar: die Vorschau rechts zeigt immer das Druckbild.
- Oben links: „Ansicht" (UI-Größe 70–140 %), darunter die Tabs
  *Text / Image / Barcode & QR / Settings*, dann **🖨 Print**.
- Designs: *Settings → Theme* (Dark / Light / DeadlineDriven),
  Sprache: *Settings → Language* (English / Deutsch / 日本語 / 中文).
- Alle Einstellungen liegen in `~/.config/x3drucker.json`.

### 3.5 Eintrag ins Startmenü + Desktop-Verknüpfung

```bash
./scripts/install-desktop.sh                        # Menüeintrag + Verknüpfung auf dem Schreibtisch
./scripts/install-desktop.sh --no-desktop-shortcut  # nur Menüeintrag
./scripts/install-desktop.sh --uninstall            # beides entfernen
```

Das Programm macht das **beim ersten Start selbst**: `x3gui.py` legt das
Start-Symbol auf dem Schreibtisch an (einmalig, danach nie wieder automatisch)
und schreibt den Menüeintrag mit. Von Hand lässt sich das jederzeit im
Programm wiederholen oder rückgängig machen: *Einstellungen → Design →
„Desktop-Verknüpfung: Erstellen / Entfernen“*. Die Verknüpfung ist eine
`.desktop`-Datei und startet `x3gui.sh`.

### 3.6 Als Systemdrucker (CUPS) – optional

**Variante A (empfohlen, ohne sudo):** Warteschlange über eine kleine Brücke.

```bash
./scripts/install-cups-user.sh            # legt Queue "X3Thermo" + User-Service an
./scripts/install-cups-user.sh --uninstall
```

Danach ist **`X3Thermo` in jedem Druckdialog** wählbar (LibreOffice, Browser,
PDF-Viewer …). Der Dienst läuft als systemd-**User**-Service:

```bash
systemctl --user status x3bridge      # Status
systemctl --user restart x3bridge     # neu starten
journalctl --user -u x3bridge -f      # Log mitlesen
```

**Variante B (nativ, braucht sudo):** eigener CUPS-Filter + Backend + PPD.
Wird nur gebraucht, wenn der Druck ohne Hilfsdienst laufen soll.

```bash
sudo bash scripts/install-cups.sh                       # MAC wird gesucht
sudo bash scripts/install-cups.sh AA:BB:CC:DD:EE:FF     # oder angeben
sudo bash scripts/install-cups.sh --uninstall
```

### 3.7 Selbsttest

```bash
python3 tests/test_smoke.py     # Rendering, YK-Frames, ESC/POS – ohne Drucker
./x3gui.sh --selftest           # baut die Oberfläche auf und rendert die Vorschau
```

---

## 4) Was liegt wo? (Installationsorte)

| Pfad | Inhalt |
|---|---|
| Projektordner | Programm selbst (nichts wird kopiert) |
| `~/.config/x3drucker.json` | alle Einstellungen der GUI (wird automatisch gespeichert) |
| `~/.local/share/applications/x3drucker.desktop` | Startmenü-Eintrag |
| `~/Schreibtisch/x3drucker.desktop` | Verknüpfung auf dem Desktop (optional) |
| `~/.local/share/icons/hicolor/256x256/apps/x3drucker.png` | App-Symbol |
| `~/.config/systemd/user/x3bridge.service` | Brückendienst für CUPS (Variante A) |
| CUPS-Warteschlange `X3Thermo` | Systemdrucker (Variante A **oder** B) |

Technische Doku (englisch) mit Aufbau, Protokoll und Diagrammen liegt im
Projektordner unter [`docs/`](docs/README.md).

---

## 5) Aktualisieren

```bash
# Variante mit Git:
git pull

# ohne Git: neue Dateien über den Projektordner kopieren
./scripts/install.sh --yes            # Menüeintrag/Prüfungen erneut ausführen
systemctl --user restart x3bridge     # falls CUPS-Brücke im Einsatz
```

Die eigenen Einstellungen (`~/.config/x3drucker.json`) bleiben dabei erhalten.

---

## 6) Deinstallieren

```bash
./scripts/install.sh --uninstall      # Startmenü + CUPS-Warteschlange + Dienst
```

Danach optional noch aufräumen:

```bash
rm -rf ~/.config/x3drucker.json                         # Einstellungen löschen
sudo bash scripts/install-cups.sh --uninstall                   # nur Variante B
```

Der Projektordner selbst kann einfach gelöscht werden – es wurde nichts
außerhalb davon installiert (bis auf Startmenü-Eintrag und Dienst).

---

## 7) Fehlersuche

| Symptom | Ursache / Lösung |
|---|---|
| `No printer found …` in der GUI/CLI | Drucker nicht gekoppelt → `./scripts/pair.sh`; MAC im Tab *Settings* eintragen |
| Drucker druckt nicht, Status „no response" | Drucker an? Papier eingelegt? `bluetoothctl info <MAC>` zeigt `Connected: yes`? |
| Kein Bluetooth-Gerät im Scan | `bluetoothctl power on`, `scan on`; Adapter per `rfkill list` prüfen |
| Startmenü-Eintrag fehlt | `./scripts/install-desktop.sh` erneut ausführen (und ab/neu anmelden) |
| Druckdialog zeigt `X3Thermo` nicht | `./scripts/install-cups-user.sh` prüfen, `lpstat -p X3Thermo` |
| PDF-Aufträge kommen nicht an | `ghostscript` fehlt (`gs --version`) |
| `x3bridge: no printer found` im Log | MAC setzen: `systemctl --user edit x3bridge` → `Environment=X3_MAC=…` |
| Ausdruck zu hell/dunkel | GUI → *Print image* → Helligkeit; oder CLI `--brightness 70…160` |
| Druck nicht mittig | GUI: Inhalt in der Vorschau ziehen oder *Seitl. Versatz (mm)* |
| GUI startet nicht | Snap-Umgebungsvariablen kommen über `x3gui.sh` – immer über diesen Wrapper starten |

Weitere Themen (Kalibrierung, Papierbreite, Barcodes, QR-Vorlagen,
protokollierte Rohbefehle) stehen im `README.md`.
