# Chat Transfer for Threema

**Threema-Verlauf aus einer Android-Datensicherung auf Ihr iPhone übertragen – ohne das iPhone zu löschen.**
Eine kostenlose Open-Source-App für den Mac (Apple-Chip, macOS 14 oder neuer), auf Deutsch und Englisch.
**Inoffiziell: nicht von der Threema AG**, und weder Threema noch Apple bieten dafür Support.

> [!WARNING]
> **Beta (0.9.0-beta.1) – bitte diesen Kasten lesen, bevor Sie es ausprobieren.**
>
> **Auf einem echten iPhone nachgewiesen (iOS 27.0, Build 24A437):**
> - *Wie die Übertragung auf das iPhone kommt:* Eine Test-Wiederherstellung, die nichts verändert („No-op-Kanarie“),
>   hat das iPhone angenommen; danach waren Threema, Einstellungen und Fotos wie vorher.
> - *Der ganze Ablauf der App bis zur Übertragung:* Android-Sicherung lesen, iPhone sichern, Übertragung vorbereiten
>   und alle 15 Sicherheitsprüfungen bestanden. Danach hat das iPhone die Übertragung selbst abgelehnt, weil „Wo ist?“
>   noch an war – und am iPhone wurde nichts verändert.
>
> **Noch nicht nachgewiesen:** eine Übertragung, die die App selbst auf einem echten iPhone bis zum Ende ausführt.
>
> **Bitte warten Sie auf eine spätere Version, wenn** Sie jeden Tag auf dieses iPhone angewiesen sind und es keinen
> Abend entbehren können, wenn Sie keine frische Apple-Sicherung (iCloud oder Finder) haben und nicht wissen, wie man
> sie zurückspielt, oder wenn Ihnen Beta-Software nicht geheuer ist. Andere iOS-Versionen lehnt die App ohnehin ab.

English: [README.md](README.md) · Ausführliche Anleitung: [Deutsch](docs/user/de/README.md) ·
[English](docs/user/en/README.md)

## Was es macht

Sie sind von Android auf das iPhone umgestiegen und möchten Ihre alten Threema-Chats auf dem iPhone haben. Threema
Safe überträgt Ihre Threema-ID und Ihre Kontakte, aber nicht den Chatverlauf. Diese Lücke schließt Chat Transfer for Threema:

1. Sie erstellen in Threema auf dem Android-Handy eine Datensicherung und kopieren die Datei auf Ihren Mac.
2. Sie richten Threema auf dem iPhone wie gewohnt mit Threema Safe ein.
3. Chat Transfer for Threema macht eine Sicherung des iPhones, fügt Ihre alten Nachrichten, Bilder, Sprachnachrichten, Dateien und
   Umfragen in eine Kopie der Threema-Daten des iPhones ein, prüft alles doppelt und spielt das Ergebnis in einer
   einzigen Wiederherstellung auf das iPhone zurück.
4. Nach dem Neustart des iPhones macht Chat Transfer for Threema eine zweite Sicherung und vergleicht sie mit der ersten. So ist
   sicher, dass sich außer Threema nichts verändert hat.

Alles passiert auf Ihrem Mac, mit einem USB-Kabel zum iPhone. **Ihre Chats werden nicht ins Internet gesendet.** Die
App öffnet überhaupt keine Netzverbindung; das ist technisch erzwungen.

## Was es nicht macht

- Es stammt **nicht** von Threema oder Apple, und beide bieten dafür keinen Support.
- Es überträgt nicht Ihre Threema-ID oder Kontakte (das macht Threema Safe).
- Es löscht Ihr iPhone nicht und richtet es nicht neu ein.
- Es funktioniert nicht mit Threema Work oder OnPrem, nicht mit iPhones, die eine Firma oder Schule verwaltet, nicht
  auf Intel-Macs oder Windows und nicht von iPhone zu Android.
- Es überträgt nichts auf einer iOS-Version, die es nicht am Gerät geprüft hat. Dann hält es an, bevor am iPhone etwas
  verändert wird.
- Es hat keinen Schalter zum Übergehen von Prüfungen. Scheitert eine Sicherheitsprüfung, hält es an; niemand kann ihm
  sagen „trotzdem weitermachen“.
- Es ersetzt keine Apple-Sicherung. Die legen Sie unterwegs selbst an, als Sicherheitsnetz.

## Was Sie brauchen

- Einen Mac mit Apple-Chip (M1 oder neuer), macOS 14 oder neuer, am Netzteil, mit genug freiem Speicher (die App sagt
  Ihnen, wie viel).
- Ihr iPhone mit einer iOS-Version, die Chat Transfer for Threema geprüft hat, und der normalen Threema-App.
- Ihr Android-Handy mit Threema und ein USB-Kabel für das iPhone.
- Ihr Threema-Safe-Passwort, den Code Ihres iPhones, das Passwort Ihres Apple Accounts und das Passwort der
  Android-Sicherung. Falls schon eingeschaltet, auch das Passwort für iPhone-Sicherungen auf dem Mac (siehe
  [Häufige Fragen](#häufige-fragen)).
- Etwa 1½ bis 3 Stunden. Davon ist Ihr iPhone 30 bis 60 Minuten offline (Flugmodus) und darf nicht benutzt werden.
- Grenzen von Version 1: höchstens 20 GB lokale Fotos auf dem iPhone; „Wo ist?“ muss während der Übertragung aus sein.

## Risiken – bitte lesen

Am Ende spielt Chat Transfer for Threema Daten auf Ihr iPhone zurück. Dabei setzt das iPhone einige Bereiche auf den Stand von wenigen
Minuten vorher zurück: **Einstellungen, Mitteilungen, lokale Fotos, SMS/iMessage, Anrufliste, Wallet-Pässe und
Tastatur**. Weil diese Sicherung erst Minuten alt ist, sehen diese Bereiche danach praktisch genauso aus wie vorher;
das iPhone wird weder gelöscht noch zurückgesetzt. Was Sie in dieser Zeit am iPhone tun, geht verloren – deshalb
bleibt es im Flugmodus. Danach melden Sie
sich mit Ihrem Apple Account neu an und fügen Karten in Apple Pay neu hinzu. Mails, die nur „Auf meinem iPhone“ liegen
(POP, lokale Ordner), können verloren gehen. Ihre anderen Apps und gespeicherten Passwörter bleiben unverändert.

Chat Transfer for Threema prüft vorher und nachher sehr genau und hält bei jeder Unklarheit an. Ein Restrisiko bleibt: iOS kann die
Wiederherstellung ändern, und Software kann Fehler haben. Deshalb legen Sie vor der Übertragung zusätzlich eine
Sicherung bei Apple an (iCloud oder Finder), und die Anleitung erklärt, wie Sie dorthin zurückkommen. Wie die
Wiederherstellung funktioniert und warum sie so gebaut ist (auf Englisch):
[docs/RESTORE-MECHANISM.md](docs/RESTORE-MECHANISM.md).

## Download

Versionen erscheinen auf der [Release-Seite](../../releases) als `threema-chat-transfer-X.Y.Z.dmg`, zusammen mit
`SHA256SUMS` (Prüfsummen). Alle Release-Dateien werden von GitHub Actions aus diesem Repository gebaut und tragen
eine Herkunftsbestätigung ([Attestations](../../attestations)). Versionen 0.9.x sind Betas (siehe Kasten oben); eine
minisign-Signatur ist ab Version 1.0 geplant.

## Die App zum ersten Mal öffnen („Trotzdem öffnen“)

Chat Transfer for Threema ist kostenlos und nicht bei Apple registriert (das kostet 99 USD pro Jahr). macOS kann den Hersteller
deshalb nicht bestätigen. Sie lassen die App einmal zu:

1. Öffnen Sie `threema-chat-transfer-X.Y.Z.dmg` und ziehen Sie Chat Transfer for Threema in den Ordner „Programme“. Starten Sie Chat Transfer for Threema nicht direkt
   aus dem DMG.
2. Öffnen Sie Chat Transfer for Threema in „Programme“. macOS meldet *„Chat Transfer for Threema“ wurde nicht geöffnet*. Klicken Sie auf **Fertig**
   (nicht „In den Papierkorb“).
3. Öffnen Sie Systemeinstellungen → Datenschutz & Sicherheit und scrollen Sie nach unten zu „Sicherheit“. Bei
   *„Chat Transfer for Threema“ wurde blockiert …* klicken Sie auf **Trotzdem öffnen**.
4. Bestätigen Sie mit **Trotzdem öffnen** und Ihrem Mac-Passwort oder Touch ID. Das ist nur beim ersten Start nötig.

Unter macOS 14 geht zusätzlich der alte Weg: Rechtsklick auf die App → **Öffnen** → **Öffnen**.

Meldet macOS, die App **„ist beschädigt“**, ist der Download kaputt: bitte erneut laden und die Prüfsumme vergleichen.
Kopieren Sie nie Befehle aus dem Internet in das Terminal, um eine App zu „reparieren“. Bilder zu jedem Schritt:
[Anleitung, Kapitel 3](docs/user/de/README.md#3-installieren).

## Häufige Fragen

**Welches „Passwort für iPhone-Sicherungen“ meint die App?** Ein eigenes Passwort nur für Sicherungen Ihres iPhones
auf einem Computer. Es ist **nicht** der iPhone-Code, **nicht** das Passwort Ihres Apple Accounts und hat **nichts**
mit iCloud-Backups zu tun: iCloud-Backups fragen nie danach. Chat Transfer for Threema braucht verschlüsselte Sicherungen auf dem Mac,
weil nur sie alle Daten enthalten.

**Ich habe nie ein solches Passwort festgelegt.** Dann ist es meist aus, und Chat Transfer for Threema legt eines für Sie an. Sie
schreiben es auf, das iPhone fragt zur Bestätigung nach seinem Code, und auf dem iPhone wird nichts gelöscht. Die
Einstellung bleibt danach an; künftige Sicherungen auf einem Computer verwenden dieses Passwort.

**Die App sagt, es ist schon eingeschaltet, aber ich kenne es nicht.** Meist wurde es früher in iTunes, im Finder oder
mit einem anderen iPhone-Programm festgelegt, oft für ein früheres iPhone: Die Einstellung kann beim Wechsel auf ein
neues iPhone mitkommen. Vielleicht steht es im Schlüsselbund Ihres Mac (Schlüsselbundverwaltung, Suche nach
„Backup“). Schritt für Schritt:
[Anleitung, „Das Passwort für iPhone-Sicherungen“](docs/user/de/README.md#das-passwort-für-iphone-sicherungen).

## Datenschutz in Kürze

Offline, keine Nutzungsdaten, kein Konto. Während des Umzugs liegen lesbare Kopien Ihrer Chats in einem privaten
Ordner auf Ihrem Mac; die App bietet am Ende an, sie zu löschen. Ein Diagnosebericht, falls Sie einen speichern,
enthält nur Codes und Zählungen. Details: [docs/PRIVACY.md](docs/PRIVACY.md).

## Für Entwicklerinnen und Entwickler

- Wiederherstellungs-Mechanismus: [docs/RESTORE-MECHANISM.md](docs/RESTORE-MECHANISM.md) · Sicherheitsmodell:
  [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md) · unterstützte Versionen:
  [docs/COMPAT-POLICY.md](docs/COMPAT-POLICY.md)
- Engine-Vertrag: [docs/ENGINE-PROTOCOL.md](docs/ENGINE-PROTOCOL.md) · Maintainer-Handbuch:
  [docs/MAINTAINER.md](docs/MAINTAINER.md)
- Mitmachen: [CONTRIBUTING.md](CONTRIBUTING.md) (niemals echte Daten) · Sicherheitsmeldungen: [SECURITY.md](SECURITY.md)
- Aus dem Quellcode bauen: `git clone https://github.com/Toubster/threema-chat-transfer`, dann `make app` (Xcode,
  XcodeGen; siehe [packaging/README.md](packaging/README.md)).

## Lizenz

Chat Transfer for Threema ist freie Software unter der **GNU Affero General Public License, Version 3** (AGPL-3.0,
[LICENSE](LICENSE), [NOTICE](NOTICE)). Kurz gesagt:

- Sie dürfen die App kostenlos nutzen, den Quellcode lesen, ihn verändern und weitergeben.
- Wer die App oder eine veränderte Fassung weitergibt – oder eine veränderte Fassung anderen über ein Netzwerk
  anbietet –, muss das unter derselben Lizenz tun und den vollständigen Quellcode veröffentlichen. Geschlossene
  Kopien sind nicht erlaubt.
- Es gibt keine Gewährleistung (AGPL-3.0 §§ 15–16). Nutzung auf eigene Verantwortung.

Eigene Quelldateien stehen unter `AGPL-3.0-or-later`, das mitgelieferte Threema-Datenmodell (`model/`) unter
`AGPL-3.0-only`. Warum keine Lizenz „nur nicht-kommerziell“: Die App enthält dieses Threema-Datenmodell (AGPL-3.0) und
pymobiledevice3 (GPL-3.0), und deren Lizenzen verbieten zusätzliche Einschränkungen wie „nur nicht-kommerziell“
(GPL-3.0/AGPL-3.0 §§ 7 und 10). Die AGPL ist die Standardlizenz, die jede Kopie und jede veränderte Fassung offen hält.

## Hinweis

Chat Transfer for Threema ist ein **inoffizielles**, unabhängiges Open-Source-Projekt. Es steht in keiner Verbindung
zur Threema AG, zur Apple Inc. oder zur Google LLC und wird von keiner von ihnen unterstützt oder geprüft. Threema ist
eine Marke der Threema AG. Apple, iPhone, Finder, iCloud und Apple Pay sind Marken der Apple Inc., eingetragen in den
USA und anderen Ländern; iOS ist eine Marke von Cisco und wird unter Lizenz verwendet. Android ist eine Marke der
Google LLC. Die Namen werden nur verwendet, um zu sagen, womit die App zusammenarbeitet. Siehe
[TRADEMARKS.md](TRADEMARKS.md).
