# API reverse-engineering — `iaqualink-plus` APK v2.23.1

> Source : `com.fluidra.iaqualinkplus_2.23.1.apks` (Flutter, AOT dans `libapp.so`,
> 33,4 Mo). Pas de code Java/Kotlin lisible — toute l'analyse vient des chaînes
> littérales extraites de `libapp.so`. Les modèles DTO Dart (`fluidra_iot_generic_api_client`,
> `generic_api`) sont visibles via leurs `package:` ; les valeurs min/max/units/factor
> des registres viennent des blocs JSON `readId`/`writeId` du `configFile` de l'UI.

## 1. Stack et architecture (vue client)

| Couche | Détail |
|---|---|
| Framework mobile | Flutter (AOT), 0 code Java/Kotlin utile |
| HTTP | `Dio` (intercepteurs `BearerAuthInterceptor`, `ApiKeyAuthInterceptor`, `OAuthInterceptor`) |
| AuthN | AWS Cognito `eu-west-1` (InitiateAuth, RespondToAuthChallenge, etc.) + `Auth0` OAuth pour l'app mobile |
| Sérialisation | `freezed` + `json_serializable` (modèles Dart) |
| SDKs tiers | AppsFlyer, Firebase, Google Play Services, Helpshift (`fluidraemea.helpshift.com`) |
| WebSocket | `wss://ws.fluidra-emea.com` (prod) / `wss://dev.ws.fluidra-emea.com` etc. |
| Stockage local | SharedPreferences, Drift, Isar (DeviceLocalDatabaseClient) |

## 2. Endpoints HTTP

### 2.1 Base et auth (constants)

```
prod   : https://api.fluidra-emea.com/generic
stage  : https://stage.api.fluidra-emea.com/generic
dev    : https://dev.api.emea-iot.aws.fluidra.com/generic
test   : https://test.api.emea-iot.aws.fluidra.com/generic
```

Cognito user pool : `fluidra-emea-fluidra-pool-prod` / `-staging` / `-dev` / `-test`
(région `eu-west-1`, le même que l'intégration actuelle).

### 2.2 Endpoints découverts (Tier 1)

| Méthode | Path | Source | Auth | Note |
|---|---|---|---|---|
| GET | `/generic/devices` | `_devices.py` | Bearer | déjà utilisé |
| GET | `/generic/devices/{id}` | APK | Bearer | détail d'un appareil |
| GET | `/generic/devices/{id}/components` | `_components.py:57` | Bearer | liste des `desiredValue`/`reportedValue` |
| GET | `/generic/devices/{id}/components?deviceType=connected&details=true` | `_components.py:44` | Bearer | variante avec détails |
| GET | `/generic/devices/{id}/components/{cid}` | `_components.py:144` | Bearer | composant unique |
| PUT | `/generic/devices/{id}/components/{cid}` | `_components.py` + APK | Bearer | `desiredValue` + `connected=true&tz=...` |
| GET | `/generic/devices/{id}/uiconfig` | APK | Bearer | **NOUVEAU** — schéma UI avec `readId`/`writeId` |
| GET | `/generic/pools/{poolId}` | `_devices.py:329` | Bearer | déjà utilisé (status) |
| GET | `/generic/pools/{poolId}/status` | `_devices.py:363` | Bearer | agrégat |
| GET | `/generic/pools/{poolId}/schedulers` | `_schedules.py:234` | Bearer | déjà utilisé (Issue #210/218/219) |
| PUT/POST | `/generic/pools/{poolId}/schedulers/{sid}` | APK | Bearer | scheduler CRUD |
| POST/GET | `/generic/pools/{poolId}/automations` | APK | Bearer | **NOUVEAU** — non exposé par l'intégration |
| GET | `/generic/pools/{poolId}/recommendations` | APK | Bearer | **NOUVEAU** — recommandations (vides / filtrage) |
| GET | `/generic/pools/{poolId}/settings` | APK | Bearer | **NOUVEAU** — settings pool |
| GET | `/generic/pools/current/devices` | APK | Bearer | devices du pool courant |
| POST | `/generic/pools/link` | APK | Bearer | invitation / partage |
| GET | `/v1/pools/{poolId}/devices` | APK | Bearer | variante v1 (devices) |
| GET | `/v1/pools/{poolId}/devices/{id}/main` | APK | Bearer | page « main » d'un appareil |
| GET | `/v1/pools/{poolId}/devices/{id}/pages/{+pageId}` | APK | Bearer | page UI dynamique |
| GET | `/v1/pools/{poolId}/devices/{id}/pages/wifi-setup` | APK | Bearer | wizard Wi-Fi BLE |
| GET | `/v1/pools/{poolId}/algorithms/{algoId}/jobs/{jobId}/main` | APK | Bearer | assistant (e.g. recommandations) |
| GET | `/v1/pools/{poolId}/automations` | APK | Bearer | alias v1 de l'endpoint v0 |
| GET | `/v1/pools/{poolId}/recommendations` | APK | Bearer | **NOUVEAU** (v1) |
| GET | `/v1/user/settings` | APK | Bearer | settings utilisateur |
| GET | `/v1/help` | APK | Bearer | contenus d'aide |
| GET | `/v1/account/preferences/notifications` | APK | Bearer | notifications opt-in |
| GET | `/v1/account/preferences/assistance` | APK | Bearer | préférences d'assistant |
| GET | `/v1/popup/pools/{poolId}/devices/{id}/pages/{+pageId}` | APK | Bearer | page en mode popup |
| POST | `/generic/devices/{id}/components/{cid}/command` | APK (ws + http) | Bearer | **NOUVEAU** — `action: "command", commandId: "..."` |
| GET | `/configFiles` | APK | Bearer | **NOUVEAU** — liste de fichiers de config |
| GET | `/configFiles/thingTypes/` | APK | Bearer | **NOUVEAU** — types d'objets (thingTypes) |
| GET | `/configFiles/identification/{configFileId}` | APK | Bearer | **NOUVEAU** — identification (AWS, BLE) |
| GET | `/telemetry` | APK | Bearer | **NOUVEAU** — télémétrie agrégée (devices, pool, user, water quality) |

### 2.3 Endpoints auth (Cognito + Auth0)

| Méthode | Path | Note |
|---|---|---|
| `AWSCognitoIdentityProviderService.InitiateAuth` | Cognito eu-west-1 | login par mot de passe (déjà utilisé) |
| `AWSCognitoIdentityProviderService.RespondToAuthChallenge` | Cognito | MFA / SMS |
| `AWSCognitoIdentityProviderService.GetUser` | Cognito | profil |
| `AWSCognitoIdentityProviderService.SetUserMFAPreference` | Cognito | activation MFA |
| `AWSCognitoIdentityProviderService.ConfirmSignUp` / `ResendConfirmationCode` | Cognito | signup |
| `Auth0` OAuth password grant | `fluidra-emea-fluidra-pro-auth0-*` | **distinct de Cognito** — pour l'app mobile |

## 3. WebSocket temps réel

```
URL prod : wss://ws.fluidra-emea.com
URL stage: wss://stage.ws.fluidra-emea.com
URL dev  : wss://dev.ws.fluidra-emea.com
```

Message d'abonnement :
```json
{"action": "subsDevice", "deviceType": "connected", "deviceId": "<id>"}
```

Réponse aux commandes push (depuis le cloud) :
```json
{"action": "command", "commandId": "<uuid>"}
```

Désabonnement : `unsubsDevice` (et `disableSchedulerWebsockets` pour couper le
canal schedulers, `disableWebsockets` pour tout couper).

L'intégration actuelle n'utilise **pas** ces websockets — un poll REST reste
la seule source d'état. C'est probablement l'optimisation la plus rentable
pour ne plus attendre que le cloud publie un changement après une écriture.

## 4. Registres (composants) découverts

L'UI Flutter charge un `configFile` (JSON) avec un bloc par registre.
Chaque bloc a la forme :

```json
"readId":  <int>,
"writeId": <int>,            (facultatif)
"type": "number" | "boolean" | "string",
"factor": <float>,           (multiplicateur à l'affichage)
"decimals": <int>,
"min": <float>, "max": <float>, "steps": <float>,
"units": "°C" | "mV" | "mg/l" | "g/L" | "%" | "h" | ""
```

Pour chaque id on a croisé label, fonction (gauge / slider / selector / tile / label), bornes, et les conditions d'affichage (`hide`, `hideValue`).

### 4.1 Sondes / qualité d'eau (déjà partiellement mappés dans l'intégration)

| `readId` | `writeId` | Libellé UI | Unité | Plage mesure | Plage consigne | Rôle / rem |
|---|---|---|---|---|---|---|
| 8 | 8 | pH | — | 0–14 | 6.5–8.5 (steps 0.1) | consigne pH (facteur écriture = 100) |
| 11 | 11 | ORP | mV | 0–1000 | 300–850 (steps 10) | consigne redox |
| 12 | 12 | Free Chlorine | mg/l | 0–5 (readId 178) | 0.3–3.5 (steps 0.1) | chlore libre ; read=178, setpoint=12 |
| 16 | 16 | Pool Temperature (HPC) | °C | 6–50 | 6–50 (steps 1) | consigne PAC — facteur 0.1 (gauge) **ou** facteur 100 (writeId 16 sur pompe pH) |
| 20 | 20 | ORP | mV | 0–1000 | 300–850 | consigne ORP alternative (2e slider ORP, min 500/950 visu) |
| 76 | — | (mask pH) | — | — | — | `hide` OR-condition : `c76==0` *ou* `c80==1` → masque le slider pH |
| 77 | — | (mask ORP) | — | — | — | `hide` : `c77==0` → masque ORP |
| 80 | — | (mask pH) | — | — | — | voir c76 |
| 135 | 137 | Filtration mode + Speed | — | state 0/1/2 | state→write 1/2/3 | **pompe à vitesse** (writeId 137, value 1/2/3) — masque si `c135==0 \|\| (c20==2)` |
| 137 | 137 | Speed | — | state 0/1/2 (3 vitesses) | — | Pompe à 3 vitesses (Victron/Victoria) |
| 154 | 10 | Chlorination (D2R) | % | 0–100 | 0–100 (steps 10) | % chlorination D2R (read=154, setpoint read=228, write=4) |
| 164 | 4 | Chlorination (D2R) | % | 0–100 | 0–100 (steps 10) | idem 154, autre bloc UI |
| 165 | — | pH (label) | — | 0–14 | — | 2e sonde pH (multi-pool) |
| 170 | 20 | ORP (label) | mV | 0–1000 | — | 2e sonde ORP |
| 172 | 8 | pH (slider) | — | 0–14 | 6.5–8.5 (steps 0.1) | slider pH principal (read=172, setpoint 8) |
| 174 | — | Salinity (label) | g/L | 0–?? | — | 1re sonde salinité (read=174) — facteur 0.01 |
| 178 | 12 | Free Chlorine (slider) | mg/l | 0–5 | 0.3–3.5 | 2e sonde chlore libre (read=178, write=12) |
| 183 | — | Pool Temperature (label) | °C | 0–50 | — | 2e sonde température (read=183) — facteur 0.1 |
| 185 | — | Salinity (label) | g/L | 0–?? | — | 2e sonde salinité (read=185) — facteur 0.01 |
| 200 | — | Cover mode (label) | — | 0/1 | — | icône `devices.cover`, état « mode hivernage » |
| 214 | — | (mask pH/ORP/Cl) | — | 0/1 | — | flag équipement de traitement présent (sinon masque les sliders) |
| 228 | 10 | Chlorination (setpoint) | % | 0–100 | 0–100 | consigne chlorination (write=10) |
| 244 | 2 | Filtration state | — | 0/1/2 | — | `hide` (OR avec c135==0) — état du bloc filtration |
| 245 | 245 | Boost (selector) | — | true/false | 0/1 | mode boost chlorination (24h) |
| 246 | — | Cover mode / Boost | — | 0/1 | — | `hide` slider pH/ORP : si `c246==0` → visible |
| 247 | — | (mask pH) | — | 0 | — | `hide` (OR avec c248==0) — voir pH |
| 248 | — | (mask ORP) | — | 0 | — | `hide` (OR avec c249==0) — voir ORP |
| 249 | — | (mask Free Cl) | — | 0 | — | `hide` (OR avec c250==0) — voir chlore libre |
| 250 | — | (mask Pool T°) | — | 0 | — | `hide` UV lamp block |
| 251 | — | (mask Salinity) | — | 0 | — | `hide` Salinity block |
| 252 | — | (mask UV) | — | 0 | — | `hide` UV block |
| 253 | — | UV lamp running hours | h | 0 | — | heures de fonctionnement UV (entier) |
| 254 | — | (mask pH/ORP) | — | 0 | — | `hide` (OR avec c214==1) — bloque la consigne |
| 263 | — | Chlorination (D2R setpoint read) | % | 0–100 | — | 2e consigne chlorination D2R (read=263, write=4) |
| 264 | — | (mask pH/ORP) | — | 0/1 | — | `hide` (OR avec c254==0) — bloque la valeur |
| 269 | — | HPC state (gauge) | — | 0/1/2 | — | états PAC : `0`=Heating, `1`=No Flow, `2`=No Flow (popup) |
| 275 | — | Filtration present | bool | true/false | — | `hide` (AND `c275==false`) — la tuile Filtration |
| 103 | 103 | Boost mode | — | true/false | 0/1 | idem 245, code couleur + label |
| 111 | — | Boost minutes remaining | — | 0 | — | `attributeId: "minutes"` du label « Will set prod to max for 24h » |
| 118 | — | Boost hours remaining | — | 0 | — | `attributeId: "hours"` du même label |
| 137 | 137 | Speed | — | state 0/1/2 | write 1/2/3 | (déjà connu : Pompe 3 vitesses) |
| 4 | 4 | Chlorination write | % | — | 0–100 | 2e bloc D2R |
| 48 | — | HPC cover/icon | — | 0/1 | — | icône « cover mode » PAC |
| 0,1,2,3,5,6,7,9,10,13,14,15,17,18,21,… | — | non observés dans ce dump | — | — | — | Registres non décodés ici |

### 4.2 Ce qui était manquant et qu'on a maintenant

- **Facteur d'écriture** : `c8` (pH write) utilise un **facteur 100** dans l'APK (la valeur envoyée est 700 pour pH 7,00), l'intégration HA a un commentaire identique. Cohérent.
- **Sonde pH 2e mesure (`c165`)** : confirmé comme un second registre pH (label = « pH », `factor 0.01`). Le code l'ignore ; il pourrait être utile pour les doubles bassins.
- **Salinité (`c174`, `c185`)** : déjà utilisé (`c185` est l'unité `c16` du `probes.py`). `c174` est une **seconde** salinité, ignorée.
- **UV (`c252`, `c253`)** : le bloc UI affiche un compteur « UV lamp running hours » en heures, `factor 1`, `decimals 0`. L'intégration HA n'a aucune entité UV. Candidat à ajouter (système UV GenSalt / similaire).
- **Boost chlorination (`c103`, `c245`, `c111`, `c118`)** : status + min/h restantes. L'intégration a probablement un « boost » mais pas de minuterie visible. `c111` = minutes, `c118` = heures (jauge 24 h).
- **`hide` / `hideValue` logique** : on sait maintenant que la présence du bloc pH/ORP/Cl dépend de `c214==0` ET `c246==0` ET (`c76==0 \|\| c80==1`). La simple lecture d'un registre peut donc masquer un autre — important pour ne pas masquer par erreur en production.
- **Pompe 3 vitesses (`c137`)** : la table d'états UI montre `0→3`, `1→1`, `2→2`. Le « 0 » est probablement « off ». L'intégration lit la vitesse via `COMPONENT_PUMP_SPEED=11` mais ignore `c135`/`c244` qui portent l'état « mode filtration on/off ». Un nouveau `binary_sensor` est possible.

## 5. Modèles (`fluidra_iot_generic_api_client` + `generic_api`)

DTOs Dart présents dans `libapp.so` (visibles via `package:fluidra_iot_generic_api_client/src/models/...`) :

```
actions_op, alarm, alarm_default, alarm_i,
auto_date_and_time, aws_identification, ble, ble_nodon,
ble_pre_discovery_screen, ble_protocols, ble_provisioning_info,
bridge, bridged_info, bridge_manual_children_adding,
button_ui_component, calendar, capabilities, capability,
clock, command_and_control, command_and_control_properties,
command_and_control_ui_component, component_or_number,
config, config_file, config_file_configuration,
config_file_identification, config_file_information, config_file_response,
configuration_info, configuration_settings,
connection_technology, connectivity, data, device, device_component,
device_family, device_item, device_name, device_query, device_request,
device_update, divider_ui_component, document, dynamic_image, dynamic_value,
federated_identification, form_list_ui_properties,
gauge_container_ui_component, gauge_mode_ui_properties,
iaq_full_screen, icon_ui_component, info_ui_component, internal_label,
job_ui_configuration, job_ui_response,
label_ui_component, link_info, list_layout_ui_component,
manual, manual_date_and_time, manual_identification, migration, mobile,
nn_identification, on_tap_ui_component, ota, ota_update_status,
paginated_list, pool, pool_data, pool_owner, pool_sharing_code,
provisioning, schedulers, selector_ui_component, serial_number,
service_type, shadow, slave_linking,
slider_setpoint_ui_component, slider_ui_component,
step, tabs_screen_item, timezone, ui_component_i, ui_config, ui_response,
user, user_name, user_pool, user_preferences
+   telemetry/{telemetry, telemetry_device, telemetry_measure,
                telemetry_pool, telemetry_records, telemetry_user}
+   pool_data/pool_status, pool_temperature, pool_water_quality,
                pool_weather/{weather, daily_weather}
+   generic_api/src/model/{subscription_plan, user_integrations, voice_assistants}
+   ~80 modèles `generic_api/src/model/...` (REST OpenAPI auto-généré)
```

Le `PoolAccessLevel` est un des modèles importants :

```
accessLevel: string
  valeurs probables : "owner" | "viewer" | "shared" | "blocked"   (à confirmer)
```

L'intégration actuelle n'utilise **que** `pool.owner` (cf. commentaire
`info.py` sur les droits viewer — Issue #133). Le reste de la matrice
d'accès est invisible côté client.

## 6. Couverture fonctionnelle côté app

| Capacité | Visible dans l'APK | Couvert par l'intégration |
|---|---|---|
| Lecture des composants (poll REST) | oui | oui |
| PUT composant | oui | oui (c9, c10, c11, c13, c15, c17, c20, c45, c258…) |
| Schedules (`/schedulers`) | oui | oui (Issue #210/218/219) |
| **WebSocket subsDevice** | oui | **non** |
| **Automations (`/pools/{id}/automations`)** | oui | non |
| **Recommendations (`/pools/{id}/recommendations`)** | oui | non |
| **Telemetry (water quality trends)** | oui | non |
| **UV lamp running hours (`c253`)** | oui | non |
| **Boost chlorination minuterie (`c111/c118`)** | oui | partiel |
| **Pompe 3 vitesses via `c135/c137/c244`** | oui | partiel (c11 vitesse lue, pas l'état) |
| **Piston backwash (`d2r_piston_backwash_label`)** | oui | non |
| **CellGuard operation (`cellGuardTecnologyIsEnabledComponentId`)** | oui | non |
| **Ota update status (`otaUpdateStatus`)** | oui | non (capability detection only) |
| **Pool sharing code (`/pools/{id}/link`)** | oui | non |
| **Pool climate (`/v1/.../devices/.../main`)** | oui | non |
| **Alexa/Google voice integration** | oui (`user_integrations_alexa`, `device_properties_voice_assistants`) | non |
| **Subscription plan / paywall** | oui | non |
| **Connected-device access code (`accessCodeComponentId`)** | oui | non (c10) |
| **Offline access code (`offlineDeviceAccessCodeComponentId`)** | oui | non (c11) |

## 7. Pistes concrètes pour l'intégration

1. **WebSocket** : passer à `wss://ws.fluidra-emea.com` avec
   `{"action":"subsDevice","deviceType":"connected","deviceId":<id>}`
   après login. Réduit la latence sur les changements émis par le cloud
   (notamment après un PUT — la cible d'Issue #210).
2. **`/pools/{id}/automations`** : si l'APK expose ce qu'elle appelle
   « automations » (≠ schedulers, plus haut niveau : déclenchement
   conditionnel par mode/heure/état), c'est probablement le « wizards »
   qu'on voit dans l'UI, distinct du scheduler. Première chose à GET
   pour voir la forme.
3. **`/telemetry`** : agrégats water quality / pump speed / device state
   sur 24 h/7 j. Permettrait des sensors `state_class: measurement` (rolling).
4. **`/generic/devices/{id}/uiconfig`** : le bloc JSON `configFile` est la
   source canonique des `readId`/`writeId`/factor/units. Le charger
   en option permet de **ne plus coder en dur** les c11/c13/c15/c20 :
   les nouvelles sondes s'ajoutent toutes seules. Coût d'implémentation
   ≈ 100 lignes.
5. **Capteurs UV** : `c252` (présence UV) + `c253` (heures) → un
   `binary_sensor` + un `sensor` « hours ».
6. **Boost chlorination** : `c245` (state) + `c111` (min) + `c118` (h)
   → un `binary_sensor` « boost » + un `sensor` « boost_remaining »
   formaté « X h Y ».
7. **Pompe à 3 vitesses** : `c135` (état filtration) + `c137` (vitesse)
   + `c244` (mode). Expose le `select` au lieu d'un nombre brut.
8. **Piston backwash / CellGuard** : visiblement des actions ponctuelles
   (`PistonBackwashOperation`, `CellGuardOperation`) avec leur propre
   `componentId`. À confirmer côté `set_command` (Endpoint §2.2).
9. **Pool sharing code** : `POST /generic/pools/link` permet
   d'inviter ; `PoolSharingCode` est l'objet retour. Voir ce qu'il
   faudrait pour supporter le partage de HA ↔ autres utilisateurs.
10. **OTA** : `device_properties_ota_update_status` est dans le payload
    components. Le parser pourrait remonter un `update` HA quand le
    statut change. Domaine d'utilité discutable mais gratuit à extraire.

## 8. Limites de cette passe

- **Dart AOT** : on n'a accès qu'aux chaînes littérales ; les structures
  internes (`freezed` copyWith, `Map<int, dynamic>` non sérialisés) sont
  perdues. La forme exacte des payloads PUT n'est pas confirmée par
  le code (seulement par ce que l'APK reçoit déjà).
- **Pas de confirmation `accessLevel`** : le modèle `PoolAccessLevel`
  existe mais ses valeurs enum ne sont pas dans le dump. À mesurer sur
  un compte non-owner (cf. Issue #133).
- **Pas de trace des autres backends** (Fluida Connect, AstralPool NA,
  APAC) — confirmé par l'absence de chaînes les référençant dans
  `libapp.so`. La recherche croisée avec le dump de DenisLacroix (#201)
  reste à faire.
- **Tests dynamiques** : aucun des nouveaux endpoints n'a été
  vérifié sur matériel. Le format JSON `uiconfig` est
  hypothétiquement bon mais le parsing DTO reste à écrire.
- **WebSocket auth** : handshake exact non vu (header d'auth supposé
  Bearer après login, à confirmer par capture).


---

## 9. Mesures sur le cloud réel (2026-08-26)

Tout ce qui précède vient des chaînes de `libapp.so`. Cette section vient
d'appels **réellement effectués** sur un compte propriétaire (une pompe E30iQ,
`thingType: eppvs`), en lecture seule, via le client de l'intégration. Elle
corrige plusieurs suppositions des sections 2 et 7.

### 9.1 Ce qui répond, ce qui refuse

| Endpoint | Code | Ce que ça dit |
|---|---|---|
| `GET /generic/devices/{id}` | **200** | payload riche : `info.configuration.capabilities`, `thingType`, `sku`, `vr`, `alarms` |
| `GET /generic/configFiles` | **200** | index de **124** configFiles (`{id, vr, cf}`) |
| `GET /generic/configFiles/identification/{thingType}` | **200** | les mêmes 124 records, complets — **392 Ko** |
| `GET /generic/configFiles/thingTypes/` | **200** | **67** types (`AC3`, `BC3`, `eppvs`, `tecnoLC2`, `domS2`, …) |
| `GET /generic/devices/{id}/uiconfig` | **400** | `Missing required request parameters: [deviceType, appId]` |
| `GET /generic/telemetry` | **403** | *signature AWS attendue* — voir 9.3 |
| `GET /generic/pools/{id}/automations` | **403** | idem |
| `GET /generic/pools/{id}/recommendations` | **403** | idem |
| `GET /generic/pools/{id}/settings` | **403** | idem |
| `GET /v1/pools/{id}/automations` | **403** | `Forbidden` (sec) |
| `GET /v1/user/settings` | **403** | `Forbidden` (sec) |

### 9.2 `/uiconfig` : deux paramètres non devinables

`appId` et `appVr` sont validés côté serveur. `appId=iaqualink_plus` franchit la
validation (le 400 devient 500) ; huit formats d'`appVr` ont été refusés
(`2.23.1`, `2.23`, `223`, `2`, `v2.23.1`, `2.23.1.0`, `2230100`, + `lang`).
Aucune valeur littérale de ces deux paramètres n'existe dans `libapp.so` —
seulement les messages qui les nomment. **Un `curl` ne suffit pas : il faut une
capture MITM de l'app.**

### 9.3 Les 403 ne sont pas des refus de droits

Le corps est explicite :

```
Invalid key=value pair (missing equal-sign) in Authorization header
(hashed with SHA-256 and encoded with Base64): '…'
```

C'est le message d'**AWS SigV4**. Ces routes attendent une requête signée IAM,
pas le Bearer Cognito qui sert partout ailleurs. Ce n'est donc pas une question
de niveau d'accès du compte : `/telemetry`, `/automations`, `/recommendations`
et `/settings` sont derrière un **autre schéma d'authentification**. Les passes
4, 6 et 7 du plan 013 sont bloquées par là, pas par les droits.

### 9.4 `capabilities` : le cloud déclare le registre des plannings

`GET /generic/devices/{id}` renvoie, pour l'E30iQ mesurée :

```json
"capabilities": {
  "schedulers": [{"id": "pump", "type": "minimal", "enabled": true,
                  "componentRead": 20, "componentWrite": 20}],
  "ota": {"technologies": ["cloud"], "type": "default", "enabled": true},
  "dateAndTime": {"technologies": ["ble"], "manual": {"timezone": {"enabled": true}}},
  "postLinkingSteps": ["poolDetails"], "provisioning": {"ble": {…}}, "ble": {}
}
```

C'est **la réponse d'autorité à l'Issue #174** : quel registre porte les
plannings d'un appareil donné, que les profils résolvent aujourd'hui à la main
depuis les drapeaux de type de pompe (c82/c83). L'intégration l'expose désormais
dans ses diagnostics, à côté du registre que le profil a résolu, avec un booléen
`matches_profile` — une seule requête par appareil, au téléchargement des
diagnostics, jamais sur le chemin de poll.

### 9.5 `configFiles` : l'identification officielle, sans les registres

Un record `CF#tecnoLC2` (9 Ko) contient :

- `alternativeCommercialNames`: `["Clear Connect", "Ei2 iQ", "GenSalt OE iQ", "Tecno LC2 KA"]`
  — exactement la gamme que le dépôt identifie à la main par numéro de série ;
- `nn.manId` (2 valeurs), `nn.prCode` (**82** codes produit), `nn.swVr` (133 versions)
  — la table d'identification officielle des appareils bridgés « nn » ;
- `configuration.op.jsonata` — une expression **JSONata** qui décode
  `payload.identification.modbus.holding.rD` en groupes de capacités :
  `electrolysis`, `ph`, `cl-orp`, `cl-ppm`, … C'est la détection officielle de
  « quelles sondes cette unité possède », que les profils devinent aujourd'hui.

En revanche **aucun `readId`/`writeId`/`factor`/`units`** : zéro occurrence dans
les 392 Ko. Les blocs UI de registres ne sont pas là — ils restent derrière
`/uiconfig` et son `appId`.

### 9.6 Pistes ouvertes par cette mesure

1. **Identification par `prCode`/`manId`** plutôt que par motif de numéro de
   série, si le payload d'un appareil bridgé les expose (à vérifier : le compte
   mesuré n'a pas d'appareil bridgé).
2. **Capacités par JSONata** : le cloud sait dire quelles sondes existent ; la
   piste vaut pour les profils « non vérifiés » de l'Issue #73 et consorts.
3. **`configFileVersion`** est présent par appareil (`0.0.8` sur l'E30iQ) — de
   quoi savoir quand un profil codé en dur a pris du retard.


---

## 10. WebSocket : mesuré de bout en bout (2026-08-26)

La section 3 décrivait le canal d'après les chaînes de l'APK. Il a été
**ouvert, abonné et vérifié** sur le compte de l'opérateur, avec son accord
explicite pour commander sa pompe.

### 10.1 Handshake

L'authentification ne passe **pas** par un en-tête :

| Tentative | Résultat |
|---|---|
| `Authorization: Bearer <access_token>` | handshake **401** |
| `Authorization: <access_token>` (brut) | handshake **401** |
| **`wss://ws.fluidra-emea.com?token=<access_token>`** | **connecté** |

C'est le jeton d'accès Cognito, celui que l'intégration détient déjà, passé en
**paramètre de requête**. Le §3 du présent document ne le disait pas et le plan
013 supposait un Bearer — c'est corrigé.

### 10.2 Actions acceptées

```json
{"action": "subsDevice", "deviceType": "connected", "deviceId": "<id>"}
→ {"statusCode":200,"action":"subsDevice","body":"{…,\"message\":\"OK\"}"}

{"action": "subsPool", "poolId": "<uuid>"}
→ {"statusCode":200,"action":"subsPool","body":"{…,\"message\":\"OK\"}"}
```

`subsPool` n'était pas documenté — il est accepté. Une action inconnue
(`subsScheduler`) reçoit `{"message": "Forbidden", "connectionId": …}` sans
fermer la connexion.

### 10.3 Le canal pousse bien les changements — 2,5 s

Écriture de `c11` (vitesse) via le chemin PUT habituel, en écoutant :

```
[  0.0s] subsDevice → 200 OK
[  2.5s] {"statusCode":200,"action":"componentChange",
          "body":"{\"deviceId\":\"…\",\"deviceType\":\"connected\",
                   \"componentId\":11,\"reportedValue\":1,\"ts\":1787767038}"}
[ 28.2s] componentChange … componentId:11, reportedValue:0     (la restauration)
[ 39.5s] componentChange … componentId:11, reportedValue:1     (voir 10.4)
```

**`componentChange` porte tout ce qu'il faut** : `deviceId`, `componentId`,
`reportedValue`, `ts`. **2,5 secondes** après l'écriture, contre un intervalle
de poll complet aujourd'hui. C'est la cible de l'Issue #210, et elle est
atteignable : le canal existe, s'authentifie avec un jeton déjà en main, et
livre exactement le triplet que le coordinateur applique.

### 10.4 La troisième frame, c'est l'amorçage de la pompe

La frame à 39,5 s renvoie `1` **après** la restauration à `0` de 28,2 s, puis la
valeur se stabilise à `0` (vérifié sur 60 s). Explication donnée par
l'opérateur, qui connaît son matériel : **en mode manuel, l'E30iQ démarre à
100 % pendant environ une minute, puis redescend au niveau demandé.** La
remontée observée est cette phase d'amorçage, pas un croisement d'écritures ni
un rejeu de consigne par le cloud.

Ce qu'il faut en retenir pour l'intégration : après un démarrage manuel, la
valeur *rapportée* peut diverger de la consigne pendant ~1 min sans que rien
n'aille mal. La vérification d'écriture y résiste déjà —
`WRITE_VERIFY_MIN_GRACE_SECONDS` vaut **180 s**, et la grâce effective est
`max(180 s, 3 × intervalle de poll)` — donc l'amorçage ne peut pas être compté
comme une écriture perdue. À ne pas raccourcir sans refaire ce calcul.

### 10.5 Robustesse : mesuré (connexions en écoute pure, aucune commande émise)

| Question | Mesure |
|---|---|
| Jeton invalide dans l'URL | handshake **403** |
| Jeton vide, ou paramètre absent | handshake **401** |
| Le jeton est-il validé à l'ouverture ? | **oui** — pas de connexion anonyme |
| Ping WebSocket standard | **PONG reçu** |
| Connexion silencieuse (aucun trafic) | **fermée à +600 s pile**, `close_code=1006` |
| Connexion avec ping toutes les 60 s | **vivante à +23 min**, sans incident |
| Le canal survit-il à l'expiration du jeton ? | **oui** — à +420 s, jeton mort depuis 2,5 min, `subsDevice` répond `200 OK` |
| `unsubsDevice` | `200 OK` |
| `unsubsPool` | **`500` « Unsubscription Error »**, avec rollback annoncé |
| `disableSchedulerWebsockets` / `disableWebsockets` | `Forbidden` — verbes non reconnus sur cette route |

Trois conséquences directes pour un futur client :

1. **Un keepalive de moins de 10 minutes est obligatoire.** Les 600 s pile et
   le `1006` sont la signature du délai d'inactivité d'AWS API Gateway
   WebSocket. La fermeture est *abrupte* — pas de frame de close propre — donc
   le client doit traiter `1006` comme un événement normal et se reconnecter
   avec un backoff, pas comme une erreur.
2. **Inutile de rouvrir la connexion à chaque rafraîchissement de jeton.** Le
   jeton n'est vérifié qu'au handshake ; une fois la connexion établie, elle
   reste utilisable, abonnements compris. Cela simplifie beaucoup le cycle de
   vie : ouvrir une fois, garder vivant, rouvrir seulement sur fermeture.
   (API Gateway impose par ailleurs une durée maximale de connexion de 2 h —
   non atteinte lors de cette mesure, à traiter comme une reconnexion de plus.)
3. **Ne pas compter sur `unsubsPool`.** Il rend 500. Pour se désabonner de
   tout, fermer la connexion.

Les réponses arrivent de façon **asynchrone** : celle d'`unsubsDevice` n'est
apparue qu'après l'envoi suivant. Un client qui attend une réponse synchrone
après chaque envoi se bloquerait — il faut une boucle de lecture indépendante
et une corrélation par le champ `action`.

### 10.6 Ce qui reste inconnu

- ce que `subsPool` pousse réellement (l'abonnement est accepté, aucune frame
  ne l'a exercé) ;
- le comportement au-delà de 2 h ;
- `appVr` pour `/uiconfig` : le versionCode de l'User-Agent
  (`1741857021`) a été essayé avec quatre `appId` différents, toujours
  `400 Invalid appVr parameter`. La piste est fermée.


---

## 11. Table `thingType` → gamme (extraite des 124 configFiles)

`GET /generic/devices` — l'appel que l'intégration fait déjà — renvoie pour
chaque appareil un `thingType` : **l'identifiant de famille officiel de
Fluidra**, disponible dès la découverte, avant tout scan de registre. Croisé
avec les configFiles (§9.5), il donne la correspondance suivante. Les lignes
marquées ✅ sont désormais déclarées en `thing_type_patterns` sur le profil
correspondant.

| `thingType` | Famille cloud | Gamme commerciale | Profil du dépôt |
|---|---|---|---|
| `eppvs` | Filtration Pumps | E30iQ, Inari VS, Verdon VS | ✅ `e30iq_pump` |
| `mppvs` | Filtration Pumps | Victoria Smart Connect VS | ✅ `victoria_smart_connect_pump` |
| `exr` | Chlorinators | eXO iQ, GenSalt OT iQ, Hydroxinator | ✅ `ns25_exo_chlorinator` |
| `zs500` | Heat Pumps | Z550iQ, Z550iQ+ (Z550iQR32) | ✅ `z550iq_heat_pump` |
| `SRC` | Cabinets | Command Connect | ✅ `command_connect_cabinet` |
| `tecnoLC2` / `lc2` | Chlorinators / Bridges | Energy Connect, Clear Connect, Ei2 iQ, GenSalt OE iQ | déroutage dédié existant (inchangé) |
| `domoticS2` / `dm2` | Chlorinators / Bridges | Elite Connect, Control Connect, Neolysis | catch-all `chlorinator` |
| `amt` | Heat Pumps | Z250iQ, Z260iQ, PX25, PX26, Eco Elyo | **non déclaré** — zone gelée, ces profils se départagent au composant 7 |
| `nhpp` | Heat Pumps | Z450, Z650iQ, Verti/Silent/Eco Elyo R290 | **non déclaré** — famille trop large pour un seul profil |
| `hpc` | Heat Pumps | Z350iQ | aucun profil |
| `proelyo` | Heat Pumps | PX50, HPO, Pro Elyo Touch | aucun profil |
| `evoline` | Heat Pumps | PM40, Evoline | aucun profil |
| `z950iq` | Heat Pumps | z950iQ Powerforce Inverter | aucun profil |
| `tecno2` | Chlorinators | eXPERT, Smart Next (± pH/Rx) | aucun profil |
| `tecnoS3` / `ts3` | Chlorinators | Smart Connect LS, eXPERT iQ LS | aucun profil |
| `dosingpump` | Chlorinators | DoseNext Pro, eDose Pro iQ, Smart Dosing | aucun profil |
| `LWM` | Light Controllers | Lumiplus Connect | `generic_light` |
| `N30L`, `cycr`, `vrr`, `EBOX`, `cbr`, `T51L` | Robot Cleaners | Freedom, Cyclonext, Alpha… | aucun profil |
| `svrac` | Valves | Multiport valve | aucun profil |
| `isp`, `gpio` | GPIOs | Smart Plug, GPIO | aucun profil |
| `BC3`, `AC3` | Data collectors | Blue Connect, Aqua Connect | aucun profil |
| `teststrip` | Test Strips | Test strip | virtuel, connu pour ne pas répondre aux polls |

Deux familles sont volontairement laissées de côté. `amt` mélange Z250iQ et
Z260iQ, que l'intégration sépare par la signature du composant 7 : y accrocher
un profil brouillerait exactement ce que cette logique distingue. `nhpp` couvre
le Z450 et les Elyo R290 en plus du Z650iQ ; router tout le monde vers le profil
Z650iQ donnerait des lectures fausses avec l'apparence d'un profil vérifié.

La colonne « aucun profil » est la liste de ce que l'intégration ne sait pas
encore lire — robots, vannes, prises, doseurs, et six familles de pompes à
chaleur. C'est aussi la feuille de route la plus factuelle dont dispose le
projet.
