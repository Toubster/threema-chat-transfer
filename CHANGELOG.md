# Changelog

Format: one section per version, German first, then English. Versions: `VERSIONS.md`.

## 0.9.0-beta.1

**Deutsch**
- **Neuer Name: „Chat Transfer for Threema“** (Repository `threema-chat-transfer`, statt des Arbeitstitels). Überall
  umbenannt: App-Bundle „Chat Transfer for Threema.app“, neue Bundle-ID,
  Xcode-Projekt/Schema/Modul `ThreemaChatTransfer`, Ordner `~/Library/Application Support/Chat Transfer for Threema`,
  Schlüsselbund-Eintrag „Chat Transfer for Threema – Backup-Passwort“, DMG `threema-chat-transfer-<Version>.dmg`
  (Volume und Hintergrund mit Anzeigename), Schema-IDs `urn:threema-chat-transfer:…`, Doku, Vorlagen, Skripte.
  Ein früherer Entwicklungsordner unter dem alten Namen wird nicht mehr gelesen.
- **Inoffiziell, deutlich markiert:** neue Zeile auf dem Startbildschirm „Inoffiziell – nicht von Threema“ mit „Mehr
  dazu“ (Über-Fenster). Hinweis in App, README, Anleitung, DMG und TRADEMARKS.md: keine Verbindung zur Threema AG,
  Apple Inc. oder Google LLC; Markenhinweise für Threema, Apple/iPhone, iOS (Cisco, unter Lizenz) und Android.
- **Lizenz erklärt:** README (DE/EN) und NOTICE erklären AGPL-3.0 in Alltagssprache, die Kombination mit
  pymobiledevice3 (GPL-3.0) nach GPLv3/AGPLv3 § 13 und warum eine Lizenz „nur nicht-kommerziell“ nicht möglich ist.
- **Rückmeldungen aus dem Gerätetest** (App 0.3.1-dev, iOS 27.0 24A437, 03.10.2026):
  - Vor und während jeder Sicherung (S11, S12, S18): „Ihr iPhone fragt gleich nach seinem Code – oft zweimal. Das ist
    normal.“
  - „Bereit zur Übertragung“ (S14) neu geschrieben: Die genannten Bereiche kommen aus der Sicherung von vor wenigen
    Minuten und bleiben praktisch, wie sie sind – das iPhone wird nicht gelöscht und nicht zurückgesetzt. Neu machen
    muss man nur die Anmeldung beim Apple Account und die Apple-Pay-Karten.
  - Kommt nichts Neues dazu (0 Nachrichten, 0 Medien), steht statt „0 Nachrichten …“: „Ihr Android-Verlauf ist
    bereits auf dem iPhone – es kommt nichts Neues dazu.“
- **Freigabeliste:** 24A437 bleibt `verified`, jetzt mit `comment_code: acceptance_partial` und `min_app`
  0.9.0-beta.1. Neuer Nachweis im Geräteprotokoll (`compat/records/ios/24A437.json`, nur Strukturzählungen): ein
  App-Lauf (`app_run`), bei dem Android-Import, iPhone-Sicherung, `prepare` und alle 15 Restore-Prüfungen bestanden;
  das iPhone lehnte die Übertragung selbst ab (Wo ist? an, `MBError 211` → `E_GUARD_FINDMY`), nichts wurde verändert.
  Schema `compat.v1`: Lauf-Art `app_run`, Ergebnis `stopped_before_restore`. COMPAT-POLICY, RESTORE-MECHANISM,
  compat/README, MAINTAINER und OWNERSHIP angepasst.
- **S10b als Entscheidung** (Rückmeldung aus dem Laientest: „Welches Passwort – oder ist es beliebig?“): neuer Titel
  „Ihr iPhone hat bereits ein Sicherungs-Passwort“; das iPhone verschlüsselt jede Sicherung mit seinem gespeicherten
  Passwort, **ein neues oder beliebiges funktioniert nicht**. Zwei Antworten, nichts vorausgewählt: „Ich kenne das
  Passwort“ (Feld, Prüfung nach der ersten Sicherung) oder „Ich weiß es nicht / habe nie eins festgelegt“ (warum es an
  sein kann; 1. frühere Passwörter, 2. Schlüsselbund dieses Mac, 3. Apples „Alle Einstellungen zurücksetzen“, dann
  „Erneut prüfen“ → `device-status` → S10a). F-PW-WRONG öffnet dieselbe Hilfe („Passwort unbekannt?“). S10a beginnt mit
  „Ihr iPhone hat noch kein Sicherungs-Passwort.“ Engine: `encryption-enable` verwirft eine PRE-Sicherung, die für einen
  weiteren Passwort-Versuch aufbewahrt war (unter dem alten Passwort erstellt); die nächste PRE-Sicherung ist neu.
  Anleitung DE/EN, Häufige Fragen, ENGINE-PROTOCOL; Demo-Bilder S10a, S10b, S10b-unknown (neu), F-PW-WRONG.
- README/Anleitung: Beta-Kasten (was nachgewiesen ist, was nicht, wer warten sollte).
- Datenschutz der Bildschirmfotos: Die Demo-Bilder trugen das ICC-Farbprofil des Bildschirms, auf dem sie entstanden
  (es nennt das Bildschirmmodell). `mark_png.py` rechnet Bilder mit eingebettetem Profil jetzt nach sRGB um und
  ersetzt das Profil durch einen Standard-`sRGB`-Eintrag; `scrub_check.py` meldet eingebettete Profile
  (`image_icc_profile`). Alle Demo-Bilder neu erzeugt.
- Datenschutz der Veröffentlichung: neutrale Bundle-ID `org.threema-chat-transfer.app`; das DMG trägt keine erweiterten
  Dateiattribute (`com.apple.provenance` hätte den Build-Mac wiedererkennbar gemacht), Volume-Daten in UTC, neutrale
  PDF-Metadaten (Zeit = Commit-Zeit); `threema-import` ohne Suchpfad in die Xcode-Werkzeuge des Build-Macs.
  `verify-bundle.sh` und `scrub_check.py` prüfen das jetzt (Konto-Name nur in Repository-Links, keine festen
  Arbeitsordner-Pfade, Commits mit No-Reply-Identität und UTC-Zeit).
- **Verhalten:** App-Texte und Anordnung (S00, S10a, S10b, S11, S12, S14, S18, F-PW-WRONG), Name/Pfade/Bundle-ID;
  S10b/F-PW-WRONG „Erneut prüfen“ (nur lesend) und der Weg S10b → S10a nach dem Zurücksetzen; `encryption-enable`
  verwirft eine abgelehnte PRE-Sicherung. Restore-Set, Guards, Verdikte unverändert; die Freigabeliste ändert nur
  Metadaten (Status unverändert `verified`).

**English**
- **New name: "Chat Transfer for Threema"** (repository `threema-chat-transfer`, replacing the working title).
  Renamed everywhere: app bundle "Chat Transfer for Threema.app", new bundle id,
  Xcode project/scheme/module `ThreemaChatTransfer`, folder `~/Library/Application Support/Chat Transfer for Threema`,
  keychain item "Chat Transfer for Threema – Backup-Passwort", DMG `threema-chat-transfer-<version>.dmg` (volume and
  background with the display name), schema ids `urn:threema-chat-transfer:…`, docs, templates, scripts. A
  development folder under the old name is no longer read.
- **Clearly unofficial:** new line on the start screen "Unofficial – not by Threema" with "More" (About window).
  Notice in the app, README, guide, DMG and TRADEMARKS.md: not affiliated with Threema AG, Apple Inc. or Google LLC;
  trademark notes for Threema, Apple/iPhone, iOS (Cisco, under licence) and Android.
- **Licence explained:** README (DE/EN) and NOTICE explain AGPL-3.0 in plain words, the combination with
  pymobiledevice3 (GPL-3.0) under GPLv3/AGPLv3 § 13 and why a "non-commercial only" licence is not possible.
- **Feedback from the device test** (app 0.3.1-dev, iOS 27.0 24A437, 2026-10-03):
  - Before and during every backup (S11, S12, S18): "Your iPhone will ask for its passcode in a moment – often twice.
    That is normal."
  - "Ready to transfer" (S14) rewritten: the listed areas come from the backup made a few minutes ago and stay
    practically as they are – the iPhone is not erased and not reset. Only the Apple Account sign-in and the Apple Pay
    cards have to be redone.
  - When nothing new is added (0 messages, 0 media), the screen says "Your Android history is already on the iPhone –
    nothing new will be added." instead of "0 messages …".
- **Allow-list:** 24A437 stays `verified`, now with `comment_code: acceptance_partial` and `min_app` 0.9.0-beta.1. New
  evidence in the device record (`compat/records/ios/24A437.json`, structural counts only): an app run (`app_run`) in
  which the Android import, the iPhone backup, `prepare` and all 15 restore guards passed; the iPhone refused the
  transfer itself (Find My on, `MBError 211` → `E_GUARD_FINDMY`), nothing changed. Schema `compat.v1`: run kind
  `app_run`, verdict `stopped_before_restore`. COMPAT-POLICY, RESTORE-MECHANISM, compat/README, MAINTAINER and
  OWNERSHIP updated.
- **S10b as a decision** (feedback from a layperson test: "which password – or is any one fine?"): new title "Your
  iPhone already has a backup password"; the iPhone encrypts every backup with its stored password, **a new or made-up
  one does not work**. Two answers, nothing preselected: "I know the password" (field, checked after the first backup)
  or "I don't know it / never set one" (why it can be on; 1. earlier passwords, 2. this Mac's keychain, 3. Apple's
  "Reset All Settings", then "Check again" → `device-status` → S10a). F-PW-WRONG opens the same help ("Don't know the
  password?"). S10a starts with "Your iPhone has no backup password yet." Engine: `encryption-enable` drops a PRE backup
  kept for another password attempt (made under the old password); the next PRE backup is a new one. Guide DE/EN, FAQ,
  ENGINE-PROTOCOL; demo pictures S10a, S10b, S10b-unknown (new), F-PW-WRONG.
- README/guide: beta box (what is proven, what is not, who should wait).
- Screenshot privacy: the demo pictures carried the ICC colour profile of the display they were taken on (it names
  the display model). `mark_png.py` now converts pictures with an embedded profile to sRGB and replaces the profile
  with a standard `sRGB` chunk; `scrub_check.py` reports embedded profiles (`image_icc_profile`). All demo pictures
  regenerated.
- Release privacy: neutral bundle id `org.threema-chat-transfer.app`; the DMG carries no extended attributes
  (`com.apple.provenance` would have made the build Mac recognisable), UTC volume dates, neutral PDF metadata (time =
  commit time); `threema-import` without a search path into the build Mac's Xcode toolchain. `verify-bundle.sh` and
  `scrub_check.py` now check this (account name only in repository links, no hard-coded workspace paths, commits with
  a no-reply identity and UTC dates).
- **Behaviour:** app texts and layout (S00, S10a, S10b, S11, S12, S14, S18, F-PW-WRONG), name/paths/bundle id;
  S10b/F-PW-WRONG "Check again" (read-only) and the path S10b → S10a after the reset; `encryption-enable` drops a
  rejected PRE backup. Restore set, guards, verdicts unchanged; the allow-list changes metadata only (status still
  `verified`).

## 0.3.2-dev

**Deutsch**
- Texte zum Passwort für iPhone-Sicherungen (Rückmeldung aus dem ersten Gerätetest: Bei S10b war unklar, welches
  Passwort gemeint ist; gedacht wurde an das iCloud-Backup). Ein einheitlicher Erklärtext „Welches Passwort ist das?“
  auf S10a, S10b, F-PW-WRONG und im Passwort-Dialog: eigenes Passwort nur für Sicherungen auf einem Computer, nicht
  der iPhone-Code, nicht das Apple-Account-Passwort, nichts mit iCloud-Backups. S10b: neuer Titel, „Wann haben Sie
  das festgelegt?“ (iTunes, Finder, anderes Programm, früheres iPhone) und aufklappbar „Wo finde ich es vielleicht?“
  (Schlüsselbundverwaltung, Einträge „iOS Backup“/„iPhone Backup“, nicht auf jeder macOS-Version geprüft); „Passwort
  vergessen?“ unverändert. S10a: warum nötig und „Was dann passiert“ (Code am iPhone, nichts gelöscht, Einstellung
  bleibt an). S09: iCloud ohne extra Passwort; Finder-Weg nennt das neue bzw. bisherige Passwort. F-PW-WRONG: Titel
  und Text (`codes.v1.json`, nur Texte) plus dieselben Hinweise wie S10b; Demo-Bildschirmfoto F-PW-WRONG neu.
- Anleitung DE/EN: neuer Abschnitt „Das Passwort für iPhone-Sicherungen“ mit beiden Fällen; Häufige Fragen in Anleitung
  und README. Korrektur: der Schlüsselbund-Eintrag heißt in jeder Sprache „Chat Transfer for Threema – Backup-Passwort“.
- **Verhalten:** nur Texte und Anordnung der App (S09, S10a, S10b, F-PW-WRONG, Passwort-Dialog); Engine, Abläufe,
  Restore-Set, Guards, Verdikte und Freigabeliste unverändert.

**English**
- Copy for the iPhone backup password (feedback from the first device test: on S10b it was unclear which password was
  meant; the tester thought of the iCloud backup). One shared explanation "Which password is this?" on S10a, S10b,
  F-PW-WRONG and in the password sheet: a separate password only for backups on a computer, not the iPhone passcode,
  not the Apple Account password, nothing to do with iCloud backups. S10b: new title, "When did you set it?" (iTunes,
  Finder, another program, an earlier iPhone) and a "Where might I find it?" disclosure (Keychain Access, items
  "iOS Backup"/"iPhone Backup", not verified on every macOS version); "Forgot the password?" unchanged. S10a: why it
  is needed and "What happens next" (passcode on the iPhone, nothing deleted, setting stays on). S09: iCloud needs no
  extra password; the Finder path names the new or existing password. F-PW-WRONG: title and body (`codes.v1.json`,
  texts only) plus the same hints as S10b; new demo screenshot F-PW-WRONG.
- User guide DE/EN: new section "The iPhone backup password" with both cases; FAQ entries in the guide and README.
  Fix: the keychain item is called "Chat Transfer for Threema – Backup-Passwort" in every language.
- **Behaviour:** app texts and layout only (S09, S10a, S10b, F-PW-WRONG, password sheet); engine, flows, restore set,
  guards, verdicts and allow-list unchanged.

## 0.3.1-dev

**Deutsch**
- Fix: Netzwerksperre wertete den IPv6-Test von urllib3 beim Laden als Netzwerkzugriff; dadurch scheiterte jeder
  Geräte-Befehl am echten iPhone (`E_NETWORK_BLOCKED`). Ein verweigertes *Anlegen* eines Sockets zählt nicht mehr als
  Verstoß (es erreicht nichts); Verbinden, `bind`, Senden an Adressen und Namensauflösung bleiben verweigert und
  gezählt, auch wenn eine Bibliothek den Fehler schluckt. Regressionstests: Einheit (Zählung je Fall) und Vertrag
  (Gerätepfad mit pymobiledevice3 unter der Sperre ohne Verstoß). **Verhalten (Engine):** Gerätebefehle laufen am
  echten iPhone wieder; Restore-Set, Guards, Verdikte und Freigabeliste unverändert.

**English**
- Fix: the network guard counted urllib3's IPv6 probe at import time as network access, so every device command
  failed on a real iPhone (`E_NETWORK_BLOCKED`). A refused socket *creation* no longer counts as a violation (it
  reaches nothing); connect, `bind`, sending to an address and name lookups stay refused and counted, also when a
  library swallows the error. Regression tests: unit (count per case) and contract (device path with
  pymobiledevice3 under the guard, no violation). **Behaviour (engine):** device commands work on a real iPhone
  again; restore set, guards, verdicts and allow-list unchanged.

## 0.3.0-dev

**Deutsch**
- Repository-Grundgerüst, Lizenz (AGPL-3.0), NOTICE, Marken-Hinweis, Sicherheits- und Beitragsregeln.
- Datenschutz-Scan (`scripts/scrub_check.py`, Schicht 1 und 2) mit Git-Hooks.
- Engine-Vertrag Protokoll v1 eingefroren: `events.v1`, `codes.v1` (alle Codes mit deutschen und englischen
  Texten), `session.v1`, `compat.v1`, `report.v1`; Swift-Codes und String-Katalog daraus erzeugt.
- `tmcore`-Gerüst (Protokoll, Sitzung, Geheimnisse über stdin, Netzsperre, Redaktion, Hashing, alle Befehle).
- Entwürfe der Mock-Szenarien für die App.
- Dokumentation: README (DE/EN) für Laien mit Anleitung „Trotzdem öffnen“; vollständige Nutzeranleitung DE/EN mit
  Bild-Platzhaltern aus `docs/images/screenshots.json`; `RESTORE-MECHANISM`, `SECURITY-MODEL`, `PRIVACY`,
  `COMPAT-POLICY`, `MAINTAINER`; Prüfskript `docs/tools/check_docs.py`.
- GitHub-Workflows `ci` (scrub, lint, core-tests, contract, importer, e2e-fixture, app, package-smoke), `release`
  (nur Entwurf, manuell), `upstream-watch`, `demo-screenshots`; Issue- und PR-Vorlagen.
- Importer als Swift-Paket (`importer/Package.swift`, `swift build -c release --arch arm64`, macOS 14+) mit
  Prüfsuite gegen synthetische Daten (`importer/Tests/run_checks.py`); `threema-import --version`; Report-Feld
  `error_code`. **Verhalten:** der Override `--allow-duplicate-1to1` ist entfernt, doppelte 1:1-Chats brechen immer ab.
- Modell V56 mit Herkunft (`model/V56/SOURCE`, Threema iOS 7.4) und `model/build-momd.sh --verify`: baut das Modell
  aus dem gepinnten Threema-iOS-Commit und vergleicht es mit der eingecheckten Fassung und `compat/threema-ios.json`.
- Paketbau (`packaging/`, `make app|dmg|smoke`): gepinnte Python-Laufzeit (python-build-standalone 3.13.15, sha256),
  Kern mit hash-gepinnten Wheels, Importer, App-Zusammenbau, Ad-hoc-Signatur von innen nach außen, Bundle-Prüfung
  inkl. `tmcore selftest`, DMG mit den vier Gatekeeper-Schritten und „Zuerst lesen.pdf“/„Read me first.pdf“,
  SBOM (CycloneDX) und Quellpaket; Lizenz-Allowlist und `THIRD_PARTY_LICENSES`.
- `tmcore` Teil A (Ziel 0.3.0; Schnittstelle neu, Entscheidungen gleich wie im bewiesenen Werkzeug): Prozessrahmen (genau ein `result`, Exit-Codes aus dem Katalog, SIGTERM nur an sicheren Punkten, stdout nur Events), Geheimnisse nur über stdin, Netzsperre (nur der usbmuxd-Socket; Fake-Gerät: gar kein Socket), Redaktion des Debug-Logs auch per Wert (Geheimnisse, Threema-IDs der Sitzung); Befehle `host-check`, `android-inspect`, `android-normalize`, `prepare`, `session-status`, `diag-report`, `cleanup`. Portierte Bibliotheken ohne `trim`, Waiver, `--deep-paths`, `--show-app-names`, `--allow-*` und Passwortdateien. Tests: portierte Suiten, Vertragstests aller Befehle, Fixture-E2E Android → `prepare` unter Netzsperre mit Kanarien-Scan.
- `tmcore` Teil B (Ziel 0.3.0; Schnittstelle neu, Entscheidungen gleich wie im bewiesenen Werkzeug): Gerätebefehle `device-watch`, `device-status`, `encryption-enable`, `backup --role pre|post` (Wiederholung bei abgebrochener erster Sitzung, Code-Hinweis am iPhone, Keybag-Passwortprüfung ohne zweites Backup), `restore` (alle Guards aus DESIGN §6.1 in fester Reihenfolge, genau ein Restore mit `system reboot no-copy no-settings no-remove skip-apps`, MBError 211 → „Wo ist?“), `postcheck` (Gate v2: `verify_import --post-launch`, Payload- und strenge Sicht, harmlose Klassen nur mit Gegenbeleg und nur für den eigenen iOS-Build, Verdikte ohne Waiver), `rollback-threema` (R1, nur nach `threema_only`, ≤ 6 h). Virtuelles iPhone für `--fake-device` mit iOS-27-Annotationssemantik und 27 Szenarien; die Mock-Szenarien der App sind jetzt Aufnahmen der echten Engine. Tests: reine Guard- und Verdikt-Tests, Fixture-E2E je Szenario mit Vertrags-, Schema- und Kanarienprüfung, Guard-E2E an echten Artefakten. Eingebaute iOS-Liste unverändert (24A437 `unknown`): echte Restores bleiben bis zum Gerätebeweis gesperrt.
- SwiftUI-App (Ziel 0.4.0; neues UI-Verhalten, keine Prüflogik in der App): Bildschirme S00–S23 und alle
  F-Bildschirme mit den Texten aus DESIGN §8 (Deutsch mit „Sie“, Englisch), Zustandsautomat mit Zurück-Sperre ab S11,
  Abbrechen bis S14, Beenden-Sperre während `critical`, Frist-Countdown mit automatisch neuer Sicherung,
  Wiederaufnahme nach §8.1 (nach gesendeter Übertragung immer S16), Ruhezustand-Sperre, Schlüsselbund nur für das
  erzeugte Backup-Passwort, `LiveEngine` (startet das gebündelte `tmcore`, Geheimnisse nur über stdin, Version aus
  `TMEngineVersion`) und `MockEngine` (spielt die Szenarien ab), DEMO-Wasserzeichen, Diagnosebericht mit Vorschau.
  Tests: Unit (Zustandsautomat, Wiederaufnahme, Fehlerkatalog, Countdown, Engine-Anbindung, jedes Szenario DE + EN),
  Größenprüfung aller Bildschirme im kleinsten Fenster (Hell/Dunkel), XCUITests je Szenario DE + EN.
- Integration (Ziel 0.5.0; Paket und Demo, Prüfentscheidungen unverändert): die signierte App startet das gebündelte
  `tmcore` (python-build-standalone 3.13) mit Importer und Modell V56; DMG 44 MiB. Demo-Autopilot der App
  (`TM_AUTOPILOT=1`, nur mit `fake:`/`mock:`-Engine, nie mit dem echten iPhone) und `devtools/demo_screenshots.py`
  fahren alle 27 Szenarien mit der echten Engine auf dem virtuellen iPhone und erzeugen die Bildschirmfotos der
  Anleitung (DE/EN, DEMO-Wasserzeichen) ohne UI-Automation. **Datenschutz:** Paketbau schreibt keine Pfade des
  Bau-Rechners mehr in `.pyc` und `threema-import`; `verify-bundle.sh` prüft das. `host-check --fake-device` meldet
  einen festen virtuellen Mac; das Debug-Log lässt ISO-Zeitstempel stehen (Telefonnummern bleiben geschwärzt).
  Texte: Zählungen in S06/S07 und den Hinweisen `W_OWN_UNSENT_AS_SENT`/`W_MISSING_KEY_SENDERS` stimmen auch bei 1;
  Größen unter 0,1 GB erscheinen als „< 0,1 GB“; S19-Hinweise als eine kompakte Liste; Seitenleiste zeigt S21/S22
  unter „Kontrolle“ und S23 beim Schritt der Sitzung. `x-area-tokens` im Code-Katalog liefert die Wörter für
  `{Bereiche}`. `lib/restore_engine.py` entfernt (ersetzt durch Schritte und Guards). **Verhalten (App):** nach
  F-FRESHNESS startet die neue Sicherung wieder von selbst (der automatische Start lief in einer abgebrochenen
  Task und endete immer auf F-INTERNAL; nur der Klick funktionierte); ein verspäteter Klick auf „Neue Sicherung“
  startet nie eine zweite. **Verhalten (Bedienungshilfen):** S10a stürzte ab, sobald VoiceOver den Bildschirm
  las (Endlosschleife in SwiftUI); behoben. Dialoge behalten die Kennungen ihrer Bedienelemente, die
  Aufräum-Knöpfe auf S20 sind für VoiceOver einzeln erreichbar.
- Korrekturen aus dem Review nach der Integration (Ziel 0.5.0, `docs/REVIEW.md`; **Verhalten** von App und Engine,
  Restore-Set, Guards und Restore-Flags unverändert): **B1** Finder-Sicherung als Sicherheitsnetz bei
  ausgeschalteter Verschlüsselung: die App liest die Verschlüsselung nach S09 neu und fragt auf S10b nach dem
  Finder-Passwort; `encryption-enable` meldet `changed:false`, wenn sie schon an war, und die App behält dann kein
  angebotenes Passwort und speichert keines im Schlüsselbund; F-PW-WRONG fragt immer neu (kein Kreisel über den
  Schlüsselbund; nach der Kontroll-Sicherung prüft es die Kontroll-Sicherung, nicht eine neue PRE-Sicherung). **M1**
  `postcheck --threema-answer ok|problem`: „Problem“ auf S17 bei grünen Systemprüfungen ist `threema_only`, also R1,
  das die Engine auch annimmt; S17/S19 nach dem Zurücksetzen sprechen von „Threema wie vorher“; lehnt die Engine das
  Zurücksetzen ab, bietet S21 „So lassen“ und die Apple-Anleitung. Neues Szenario `threema_problem_reported`. **M2**
  S16 „Auch Sprache, Land …“ führt ohne S17 und ohne Kontroll-Sicherung direkt zu R4 (`postcheck --buddy-answer
  full_setup` braucht weder Sicherung noch Passwort); S22 hat für R4 eine Anleitung ab dem Setup-Assistenten. **M3**
  S22 iCloud: zuerst prüfen, dass das iCloud-Backup von heute vor der Übertragung existiert, sonst nichts löschen;
  erklärt, warum nur hier gelöscht wird und dass WLAN nötig ist. **M4** Jeder Ausstieg vor dem Senden (Abbrechen,
  Beenden, Verwerfen, F-Bildschirme ohne Weiter, S23) und S22 nennen, was am iPhone wieder einzuschalten ist. **M5**
  `E_THREEMA_ID_UNREADABLE` hat einen eigenen Text auf F-THREEMA-ID (nichts verändert, Notizgruppe anlegen, „Neue
  Sicherung“) statt „Interner Prüffehler“. **M6** S16 „Gar keine Fragen“ zählt wie „Nur Apple Account …“: die
  routinemäßige Setup-Assistent-Wiederholung nach jedem Restore ist mit Gegenbeleg ein Hinweis, kein Daten-Alarm.
  **m1** `prepare` verweigert eine PRE-Sicherung, deren Prüfungen nicht bestanden sind. S09 sagt, dass der Finder
  nach einem Passwort fragt.
- Noch keine Funktion für Endnutzer; nichts veröffentlicht.

**English**
- Repository skeleton, licence (AGPL-3.0), NOTICE, trademark notice, security and contribution rules.
- Privacy scan (`scripts/scrub_check.py`, layers 1 and 2) with git hooks.
- Engine contract protocol v1 frozen: `events.v1`, `codes.v1` (every code with German and English texts),
  `session.v1`, `compat.v1`, `report.v1`; Swift codes and string catalog generated from it.
- `tmcore` skeleton (protocol, session, secrets via stdin, network guard, redaction, hashing, all commands).
- Draft mock scenarios for the app.
- Documentation: README (DE/EN) for non-experts with the "Open Anyway" walkthrough; full user guide DE/EN with image
  placeholders from `docs/images/screenshots.json`; `RESTORE-MECHANISM`, `SECURITY-MODEL`, `PRIVACY`,
  `COMPAT-POLICY`, `MAINTAINER`; checker `docs/tools/check_docs.py`.
- GitHub workflows `ci` (scrub, lint, core-tests, contract, importer, e2e-fixture, app, package-smoke), `release`
  (draft only, manual), `upstream-watch`, `demo-screenshots`; issue and pull request templates.
- Importer as a Swift package (`importer/Package.swift`, `swift build -c release --arch arm64`, macOS 14+) with a
  check suite on synthetic data (`importer/Tests/run_checks.py`); `threema-import --version`; report field
  `error_code`. **Behaviour:** the `--allow-duplicate-1to1` override is gone, duplicate 1:1 chats always abort.
- Model V56 with provenance (`model/V56/SOURCE`, Threema iOS 7.4) and `model/build-momd.sh --verify`: compiles the
  model from the pinned Threema iOS commit and compares it with the committed copy and `compat/threema-ios.json`.
- Packaging (`packaging/`, `make app|dmg|smoke`): pinned Python runtime (python-build-standalone 3.13.15, sha256),
  core with hash-pinned wheels, importer, app assembly, inside-out ad-hoc signing, bundle verification incl.
  `tmcore selftest`, DMG with the four Gatekeeper steps and "Zuerst lesen.pdf"/"Read me first.pdf", SBOM
  (CycloneDX) and source bundle; licence allow-list and `THIRD_PARTY_LICENSES`.
- `tmcore` part A (target 0.3.0; new interface, same decisions as the proven tools): process frame (exactly one `result`, exit codes from the catalog, SIGTERM only at safe points, stdout carries events only), secrets via stdin only, network guard (usbmuxd socket only; fake device: no socket at all), debug-log redaction also by value (secrets, the session's Threema IDs); commands `host-check`, `android-inspect`, `android-normalize`, `prepare`, `session-status`, `diag-report`, `cleanup`. Ported libraries without `trim`, waivers, `--deep-paths`, `--show-app-names`, `--allow-*` and password files. Tests: ported suites, contract tests of every command, fixture E2E Android → `prepare` under the network block with the canary scan.
- `tmcore` part B (target 0.3.0; new interface, same decisions as the proven tools): device commands `device-watch`, `device-status`, `encryption-enable`, `backup --role pre|post` (retry when the device drops the first session, passcode prompt, keybag password check without a second backup), `restore` (every guard of DESIGN §6.1 in a fixed order, exactly one restore with `system reboot no-copy no-settings no-remove skip-apps`, MBError 211 → Find My), `postcheck` (gate v2: `verify_import --post-launch`, payload and strict view, benign classes only with evidence and only for the device's iOS build, verdicts without waivers), `rollback-threema` (R1, only after `threema_only`, ≤ 6 h). Virtual iPhone for `--fake-device` with iOS 27 annotation semantics and 27 scenarios; the app's mock scenarios are now recordings of the real engine. Tests: pure guard and verdict tests, fixture E2E per scenario with contract, schema and canary checks, guard E2E on real artifacts. Shipped iOS list unchanged (24A437 `unknown`): real restores stay refused until the device proof.
- SwiftUI app (target 0.4.0; new UI behaviour, no checks in the app): screens S00–S23 and every F-screen with the
  texts of DESIGN §8 (German with "Sie", English), state machine with the back-lock from S11, cancel until S14,
  quit refused during `critical`, freshness countdown with an automatic new backup, resume per §8.1 (always S16 once
  the transfer was sent), sleep prevention, keychain only for the generated backup password, `LiveEngine` (starts
  the bundled `tmcore`, secrets via stdin only, version from `TMEngineVersion`) and `MockEngine` (replays the
  scenarios), DEMO watermark, diagnostic report with preview. Tests: unit (state machine, resume, error catalog,
  countdown, engine plumbing, every scenario DE + EN), size check of every screen at the smallest window
  (light/dark), XCUITests per scenario DE + EN.
- Integration (target 0.5.0; package and demo, check decisions unchanged): the signed app starts the bundled
  `tmcore` (python-build-standalone 3.13) with the importer and model V56; DMG 44 MiB. The app's demo autopilot
  (`TM_AUTOPILOT=1`, only with a `fake:`/`mock:` engine, never with a real iPhone) and `devtools/demo_screenshots.py`
  run all 27 scenarios with the real engine on the virtual iPhone and produce the guide's screenshots (DE/EN, DEMO
  watermark) without UI automation. **Privacy:** packaging no longer writes build-machine paths into `.pyc` files and
  `threema-import`; `verify-bundle.sh` checks it. `host-check --fake-device` reports a fixed virtual Mac; the debug
  log keeps ISO timestamps (phone numbers stay redacted). Texts: counts in S06/S07 and the notes
  `W_OWN_UNSENT_AS_SENT`/`W_MISSING_KEY_SENDERS` read correctly for 1; sizes below 0.1 GB show as "< 0.1 GB"; S19
  notes as one compact list; the sidebar shows S21/S22 under "Check" and S23 at the session's step. `x-area-tokens` in
  the code catalog provides the words for `{areas}`. `lib/restore_engine.py` removed (replaced by steps and guards).
  **Behaviour (app):** after F-FRESHNESS the new backup starts by itself again (the automatic start ran inside a
  cancelled task and always ended on F-INTERNAL; only the click worked); a late click on "New backup" never
  starts a second one. **Behaviour (accessibility):** S10a crashed as soon as VoiceOver read the screen
  (endless recursion in SwiftUI); fixed. Sheets keep the identifiers of their controls, the S20 cleanup buttons are
  reachable one by one for VoiceOver.
- Fixes from the review after integration (target 0.5.0, `docs/REVIEW.md`; **behaviour** of app and engine; restore
  set, guards and restore flags unchanged): **B1** Finder backup as the safety net while encryption was off: the app
  reads encryption again after S09 and asks for the Finder password on S10b; `encryption-enable` reports
  `changed:false` when it was already on, and the app then keeps no offered password and stores none in the keychain;
  F-PW-WRONG always asks again (no loop over the keychain; after the control backup it re-checks the control backup,
  not a new PRE backup). **M1** `postcheck --threema-answer ok|problem`: "Problem" on S17 with green system checks is
  `threema_only`, i.e. R1, which the engine also accepts; S17/S19 after the reset speak of "Threema as before"; if the
  engine refuses the reset, S21 offers "Leave as is" and the Apple guide. New scenario `threema_problem_reported`.
  **M2** S16 "Also language, country …" goes straight to R4 without S17 and without a control backup (`postcheck
  --buddy-answer full_setup` needs neither a backup nor a password); S22 has an R4 guide that starts in Setup
  Assistant. **M3** S22 iCloud: first check that today's iCloud backup from before the transfer exists, otherwise
  erase nothing; explains why erasing is right only here and that Wi-Fi is needed. **M4** Every way out before the
  send (cancel, quit, discard, F-screens without a way on, S23) and S22 say what to turn back on on the iPhone. **M5**
  `E_THREEMA_ID_UNREADABLE` has its own text on F-THREEMA-ID (nothing changed, create a note group, "New backup")
  instead of "Internal check failed". **M6** S16 "No questions at all" counts like "Only Apple Account …": the
  routine Setup Assistant re-run after every restore is a note with evidence, not a data alarm. **m1** `prepare`
  refuses a PRE backup whose checks did not pass. S09 says that Finder asks for a password.
- No end-user functionality yet; nothing published.
