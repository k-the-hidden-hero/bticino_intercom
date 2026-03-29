# TODO — BTicino Intercom Integration

Roadmap delle funzionalita da implementare, ordinate per priorita e complessita.

---

## In corso / Prossima release

### Verificare ricezione eventi RTC via WebSocket
- [ ] Testare che il WS senza `filter` riceva `BNCX-rtc` e `BNCX-incoming_call` in real-time
- [ ] Verificare il formato del payload RTC e mapparlo correttamente nel coordinator
- [ ] Testare con una suonata reale del campanello

### Fix issue #17 / #9 — Snapshot e vignette diventano unavailable dopo una chiamata
- [ ] Investigare il flusso: WS event → coordinator → camera entity
- [ ] Verificare che l'URL snapshot dell'evento WS sia diverso da quello del polling
- [ ] Il campo `snapshot_url` arriva direttamente nel push (visto nel decompilato di `z71/a.java`)

---

## Funzionalita nuove — Priorita alta

### Notifica chiamata su companion app (far squillare il cellulare)
- [ ] Quando arriva un evento `incoming_call` via WS, inviare una notifica push critica tramite il servizio `notify` di HA
- [ ] Usare la [critical notification](https://companion.home-assistant.io/docs/notifications/critical-notifications/) della companion app per bypassare il "Non disturbare"
- [ ] Includere snapshot del chiamante nella notifica (se disponibile nell'evento)
- [ ] Aggiungere azioni nella notifica: "Apri porta", "Rifiuta"
- [ ] Implementare come automazione blueprint o come servizio nativo dell'integrazione

### Rispondere a una chiamata (answer/reject)
- [ ] Analizzare il payload RTC ricevuto via WS — contiene `session_id` e dati di segnalazione WebRTC
- [ ] Implementare l'invio di `answer` e `reject` via WS o API per controllare la sessione
- [ ] Esporre come servizio HA: `bticino_intercom.answer_call`, `bticino_intercom.reject_call`
- [ ] Integrare con le azioni della notifica companion app
- [ ] Verificare se il solo answer/reject funziona senza negoziazione WebRTC completa

### Video live via WebRTC
- [ ] L'app usa `wss://app.netatmo.net/appws/` per il signaling WebRTC
- [ ] HA supporta WebRTC nativo dal 2024.x — si integra con la camera entity
- [ ] Implementare il flusso di signaling: offer → answer → ICE candidates
- [ ] Decompilare il flusso WebRTC dall'app per capire il protocollo di signaling esatto
- [ ] Esporre come stream sulla camera entity esistente (`CameraEntityFeature.STREAM`)

### Audio bidirezionale
- [ ] Collegato al WebRTC — una volta implementato il video, l'audio e' parte dello stesso flusso
- [ ] Verificare se HA supporta two-way audio sulle camera entities
- [ ] Potrebbe richiedere l'uso del componente `assist_satellite` o `voip`

---

## Funzionalita nuove — Priorita media

### Flusso di reautenticazione
- [ ] Implementare `async_step_reauth` completo in `config_flow.py` (attualmente e' uno stub che abortisce)
- [ ] Quando il token scade e il refresh fallisce, mostrare un dialog per reinserire le credenziali
- [ ] Evita all'utente di dover eliminare e ricreare l'integrazione

### Pannello diagnostico
- [ ] Implementare `diagnostics.py` per il download diagnostico dalla UI HA
- [ ] Esportare: coordinator data, stato WS, versioni moduli, configurazione
- [ ] Mascherare dati sensibili (token, credenziali, MAC address)

### Sensore di qualita segnale WiFi del bridge
- [ ] Tradurre il valore RSSI in un livello qualitativo (ottimo/buono/scarso)
- [ ] Aggiungere icona dinamica basata sulla forza del segnale

### Supporto moduli senza campo `variant`
- [ ] Il test live ha mostrato che i moduli non hanno `variant` nella topologia
- [ ] Il codice attuale usa `variant.split(":")` per determinare il subtype
- [ ] Aggiungere fallback su `type` field (`BNDL` → lock, `BNEU` → binary sensor, `BNSL` → light)

### Quality scale Bronze
- [ ] Aggiungere `quality_scale.yaml` per dichiarare il livello di qualita HA
- [ ] Soddisfare i requisiti Bronze: config flow, test, entity naming, device info

---

## Funzionalita nuove — Priorita bassa

### Replay video registrati (VOD via WebRTC)
- [ ] L'app supporta VOD: invia un offer con `session_description.type: "vod"` e `video_id`
- [ ] Permette di rivedere le registrazioni degli eventi (chiamate, movimenti)
- [ ] Il Classe 100X **non** ha server HLS locale (nmap confermato: tutte le porte filtrate)
- [ ] Il video registrato passa comunque via WebRTC cloud (signaling a `wss://app.netatmo.net/appws/`)
- [ ] Comandi player supportati: `{"action":"rtc","data":{"type":"player","value":"<cmd>"}}`
- [ ] Esporre come media_player o come camera con timeline

### Refresh snapshot/vignette URL scaduti
- [ ] L'app usa l'endpoint `/api/refreshbloburl` per rinnovare URL Azure scaduti
- [ ] Implementare in pybticino e usare nel camera entity quando l'URL e' scaduto
- [ ] Evita che le camera diventino unavailable dopo la scadenza del SAS token

### Storico chiamate come calendario
- [ ] Esporre gli eventi storici come entita `calendar`
- [ ] Ogni chiamata diventa un evento nel calendario con orario, durata, tipo, snapshot

### Automazione blueprint
- [ ] Creare blueprint pronti all'uso:
  - "Apri porta automaticamente quando squilla" (con condizioni orarie)
  - "Invia notifica con foto quando qualcuno suona"
  - "Accendi luce scale quando si apre la porta"

### Pulsante "Apri porta" come entita button
- [ ] Oltre alla lock entity, esporre un `button` entity per l'apertura one-shot
- [ ] Piu intuitivo per chi vuole un semplice pulsante in dashboard

### Sensore "porta aperta/chiusa"
- [ ] Se il modulo BNDL riporta lo stato della porta (non solo della serratura), esporlo come binary_sensor
- [ ] Verificare se l'API fornisce questo dato

---

## Infrastruttura e qualita

### Test con credenziali reali (CI opzionale)
- [ ] Creare `tests/test_live.py` con `@pytest.mark.skipif(not creds)`
- [ ] Testa auth, topology, status, WS connect/subscribe
- [ ] Eseguibile manualmente con `BTICINO_USERNAME` e `BTICINO_PASSWORD` env vars

### Aggiornare actions a Node.js 24
- [ ] I CI workflow mostrano warning per Node.js 20 deprecato
- [ ] Aggiornare `actions/checkout`, `actions/setup-python`, `actions/upload-artifact` alle versioni con Node 24

### Brand assets
- [ ] HassFest segnala l'assenza di `custom_components/bticino_intercom/brand/icon.png`
- [ ] Creare icona per il brand (logo BTicino stilizzato o icona citofono)

---

## Note tecniche dal reverse engineering

Informazioni utili per l'implementazione, ricavate dalla decompilazione dell'APK Netatmo Camera v26.3.1.0.

### Push types (formato: `{DEVICE_TYPE}-{EVENT_TYPE}`)
```
BNCX-rtc                    → chiamata WebRTC in arrivo
BNCX-incoming_call          → notifica chiamata con snapshot_url
BNCX-missed_call            → chiamata persa
BNCX-new_alarm_status       → cambio stato allarme
BNCX-reachable_module_event → modulo raggiungibile/non raggiungibile
BNCX-cu_unreachable         → unita centrale non raggiungibile
BNCX-cu_default             → unita centrale stato default
BNCX-cu_tampered            → unita centrale manomessa
BNCX-sensor_default         → sensore stato default
BNC1-connection             → bridge connesso
BNC1-disconnection          → bridge disconnesso
```

### WebSocket subscribe (funzionante)
```json
{
    "action": "Subscribe",
    "access_token": "<token>",
    "app_type": "app_camera",
    "platform": "Android",
    "version": "4.1.1.3"
}
```
**Non** inviare `filter`. Senza filter si ricevono tutti gli eventi inclusi quelli RTC.

### WebSocket message format
```json
{
    "type": "Websocket",
    "push_type": "BNC1-disconnection",
    "extra_params": {
        "event_type": "disconnection",
        "device_id": "00:03:50:xx:xx:xx",
        "home_id": "...",
        "timestamp": 1774814283,
        "home_name": "...",
        "event_id": "..."
    },
    "app_type": "app_camera"
}
```

### Signaling WebRTC (da approfondire)
- URL: `wss://app.netatmo.net/appws/`
- L'app usa il namespace `app_security` per il signaling RTC
- Il flusso e': push `BNCX-rtc` → app apre WS signaling → scambio offer/answer/ICE → stream video/audio
- Classi rilevanti nel decompilato: `com.netatmo.android.webrtc.service.WebRtcCallService`, `com.netatmo.media.phonebox`

### API endpoints disponibili
```
POST /api/homesdata          → topologia (case, moduli)
POST /syncapi/v1/homestatus  → stato moduli
POST /syncapi/v1/setstate    → comandi (lock, light)
POST /api/getevents          → storico eventi
POST /api/addpushcontext     → registra token FCM
POST /api/modifyuser         → modifica utente
POST /api/gethomeusers       → lista utenti casa
POST /api/updatesession      → aggiorna sessione
```
