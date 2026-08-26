# Plan — Fluidra `uiconfig` / WebSocket / new registers / automations / telemetry

> Source: `API-DISCOVERY.md` (APK `com.fluidra.iaqualinkplus_2.23.1.apks`, Flutter
> AOT). Existing integration: v2.84.0, Platinum, 1312 tests, ~97.5 % coverage.
> This plan does **not** implement anything; it is the sequence of self-contained
> work sessions the operator will run later.

## 0. Reading-the-doc audit (what's already covered, do not re-plan)

The `API-DISCOVERY.md` doc lists a lot of surfaces. Several are already wired and
must not be re-planned:

| Surface | Existing module | Status |
|---|---|---|
| `GET /generic/devices` | `fluidra_api/_devices.py:60-226` | covered (`async_update_data`, `_discover_devices_for_pool`) |
| `GET /generic/devices/{id}/components` (bulk + per-component) | `fluidra_api/_components.py:40-114` (`get_all_components`, `_parse_bulk_components`) | covered since v2.66.0 |
| `GET /generic/pools/{id}` / `/status` | `fluidra_api/_devices.py:344-371` (`get_pool_details`) | covered |
| `GET /generic/pools/{id}/schedulers` | `fluidra_api/_schedules.py:219-249` | covered (Issue #144/#67) — **not** the same as `/automations` |
| `PUT /generic/devices/{id}/components/{cid}` (control write) | `fluidra_api/_components.py:131-184` + `control_device_component` + `write_verifier` | covered (Issues #133/#195) |
| Per-component write formats (c9, c10, c11, c13, c15, c17, c20, c45, c258) | `fluidra_api/_commands.py` + per-family profiles in `device_registry/configs/` | covered (Pr #205, #210, #212, #195) |
| Read-only (viewer) write guard | `entity.py:109-123`, `__init__.py:337-354` | covered (Issue #133) |

Also **explicitly excluded** by the brief and not in scope here: Auth0 OAuth,
Alexa/Google voice, subscription/paywall, pool sharing (`/pools/{id}/link`),
`/configFiles`, `/v1/help`, `/v1/account/preferences/*`,
`/v1/popup/pools/...`, `POST /generic/pools/link`, `/telemetry` is in scope
(see Pass 4), `/pools/{id}/settings` is in scope for Pass 3 (capability
detection), every other `/v1/...` page is a UI artefact, not a data source.

This plan is therefore **only** the 6 KEEP items in the brief, in ROI order.

---

## Priority order and ROI

| # | Pass | Risk | Value | Depends on | Confidence |
|---|------|------|-------|-----------|-----------|
| 1 | Boost chlorination + UV register entities | low (sensors) | medium (existing users) | — | high (doc + decodable) |
| 2 | Pump 3-speed via c135/c137/c244 (binary_sensor + better select) | low | medium | — | high (doc + state machine known) |
| 3 | Dynamic UI config loader (`/uiconfig`) — opt-in | medium (parser) | high (future-proof) | real account to capture | medium (response shape TBD) |
| 4 | Telemetry endpoint `/telemetry` — rolling sensors | low | medium (analytics) | real account | low (shape TBD) |
| 5 | WebSocket `wss://ws.fluidra-emea.com` (Issue #210 root cause) | medium (lifecycle) | high (UX) | Pass 1+2 stable | medium (handshake TBD) |
| 6 | Automations endpoint (CRUD bridge) | medium | low-medium | real account, Pass 5 | low (shape TBD) |
| 7 | Recommendations endpoint (diagnostic sensor) | low | low | real account | low (shape TBD) |

Rationale: **5 is the highest-value but highest-uncertainty** — it solves
Issue #210 (cloud notifies HA instead of waiting for the next poll) and is
explicitly called out in the doc as "probably the most profitable
optimisation". It needs a real-account capture to confirm the auth handshake,
so it sits in the middle of the queue once the cheaper Pass 1/2 land. **3
is the second-highest** because it removes the need to ever hand-code
register ids again — but it is a refactor that touches every platform and
must be opt-in. **6/7** are listed for completeness; the doc itself says
"GET it first to see the shape", and the integration rule is to expose
nothing we haven't decoded.

All passes that need a real account MUST be carried out by the operator on
their own pool: the integration has no access to a real Fluidra account
from CI, and the doc itself flags every new endpoint as "not verified on
hardware".

---

## Pass 1 — New registers: UV + Boost countdown + 3-speed state

> **Statut au 2026-08-26 — IN PROGRESS (branche `feat/pass1-new-registers`).**
> Le code et les tests sont écrits et verts (1959 tests, ruff + mypy propres) ;
> il reste la seule étape qui exige le matériel : déclarer les nouvelles
> fonctionnalités dans les profils, une fois les registres confirmés sur une
> vraie installation.
>
> Livré :
> - `binary_sensor.FluidraUvPresentBinarySensor` (masque UV, c252, diagnostic)
> - `sensor.FluidraUvRunningHoursSensor` (heures lampe UV, c253)
> - `sensor.FluidraBoostRemainingHoursSensor` (compte à rebours de boost réparti
>   sur c118 heures + c111 minutes, restitué en heures)
> - `binary_sensor.FluidraFiltrationStateBinarySensor` (état du bloc filtration,
>   c135 avec repli c244)
> - traductions en/fr/es/pt, icônes, `tests/test_new_registers.py` (26 tests)
>
> Écarts assumés par rapport au plan initial, et pourquoi :
> - **Pas de `FluidraBoostActiveBinarySensor`.** L'état du boost est déjà exposé
>   par `switch.FluidraChlorinatorBoostSwitch`, qui lit exactement le registre
>   `boost_mode` (c245 avec repli c103 selon le profil). Un binary_sensor de plus
>   aurait dupliqué l'état d'une entité existante.
> - **Aucune constante `COMPONENT_*` ajoutée dans `const.py`.** La règle du
>   fichier est explicite : « Only IDs with a single stable meaning get a name ».
>   c135 en est le contre-exemple exact — il porte l'emplacement de quick-function
>   actif sur une pompe Victoria VS (`configs/pumps.py`, profil vérifié) et l'état
>   du bloc filtration sur le contrôleur du dump `configFile`. Les ids restent donc
>   déclarés par profil, comme `cell_production_state` ou `speed_input_components`.
> - **Pas de capteur d'état filtration sur les profils de pompes.** Même raison :
>   sur la Victoria VS, c135 est déjà décodé autrement. La fonctionnalité vise le
>   contrôleur (chlorinateur) qui pilote le bloc filtration.
> - **Registres `boost_remaining` (c51, eXO) et `boost_remaining_hours`
>   (c118/c111) séparés.** Familles disjointes ; aucune entité existante n'est
>   touchée, donc aucun `unique_id` ne change.
>
> Reste à faire (bloqué sur relevé matériel) : ajouter `uv_lamp`,
> `boost_remaining_hours` et `filtration_state` aux `features` des profils
> concernés, ainsi que 111/118/135/244/252/253 à leurs `specific_components`.
> Le relevé se fait sans capture réseau : en logs DEBUG, le coordinateur émet
> déjà une ligne « reports N component(s) no profile maps » avec la valeur de
> chaque registre qu'aucun profil ne mappe (`coordinator._log_unmapped_components`).

> **Highest-value, lowest-risk pass.** All three additions are read-only
> sensors derived from registers the doc identifies. None requires a new
> write path. None depends on a real account at runtime — only the operator
> must verify the units / factor on their own pool.

### 1.1 Rationale

- **UV (`c252`/`c253`)**: the doc shows a complete UI block ("UV lamp
  running hours") that the integration has zero entity for. Adding a
  binary_sensor (`uv_present`, c252) + a `sensor` (hours, c253) closes a
  class of equipment the integration is currently silent on.
- **Boost chlorination countdown (`c103`/`c245`/`c111`/`c118`)**:
  `FluidraBoostRemainingSensor` already exists
  (`sensor/chlorinator.py`) but is a textual countdown. Splitting it into
  a proper `binary_sensor` (boost active) + a `sensor` (hours remaining,
  state_class `measurement`) matches what the app does and what
  automations need.
- **Pump 3-speed state (`c135`/`c137`/`c244`)**: c11 is read for the
  current speed percentage; c135/c244 carry the on/off state of the
  filtration block. Adding a `binary_sensor` (filtration running) gives
  automations a target the speed sensor alone cannot (a pump running at
  speed=0 is not running).

### 1.2 Endpoints + response shape

All three live in the existing component bulk fetch. **No new endpoint.**
The operator must:

1. Open the Fluidra app on their pool.
2. Note the current value of `c103`, `c111`, `c118`, `c245`, `c252`,
   `c253`, `c135`, `c137`, `c244` from diagnostics export
   (Settings → Devices → ⋯ → Download diagnostics — the integration
   already redacts ids in its own diagnostics).
3. Cross-check against `API-DISCOVERY.md` table §4.1.

### 1.3 Code changes

#### Constants (`custom_components/fluidra_pool/const.py`)

- Add to the `COMPONENT_*` block:
  - `COMPONENT_BOOST_ACTIVE = 103` (already implicitly referenced by
    some profiles — verify by `grep` before naming)
  - `COMPONENT_BOOST_STATE = 245` (alias for 103 in newer firmware)
  - `COMPONENT_BOOST_MINUTES_REMAINING = 111`
  - `COMPONENT_BOOST_HOURS_REMAINING = 118`
  - `COMPONENT_UV_PRESENT = 252`
  - `COMPONENT_UV_RUNNING_HOURS = 253`
  - `COMPONENT_PUMP_FILTRATION_STATE = 135`
  - `COMPONENT_PUMP_SPEED_STATE = 137`
  - `COMPONENT_PUMP_MODE = 244`
- Add `UV_RUNNING_HOURS_UNIT = "h"` (sensor unit).
- Add `BOOST_REMAINING_UNIT = "h"` (state_class: measurement).
- Add a 3-speed state map: `PUMP_3SPEED_STATE_TO_LABEL = {0: "off",
  1: "low", 2: "medium", 3: "high"}` and reverse.

#### Profile-aware scan set (`custom_components/fluidra_pool/device_registry/configs/`)

- Identify which profiles gain the new registers. A grep of
  `specific_components` across the configs (3-4 chlorinator files +
  standard-tecnoLC2) will reveal whether c103/c111/c118/c252/c253 are
  already scanned — likely c103 is, c111/c118 are not.
- Add the new ids to the `specific_components` list of every chlorinator
  profile (UV is opt-in per-profile since not all chlorinators have a UV
  lamp). For c135/c137/c244, add to the pump profiles.
- New device-registry feature gates: `"uv_lamp": True`,
  `"boost_countdown": True`, `"filtration_state_binary": True`. A
  profile that has the register but is unknown can still be polled —
  the entity stays `unavailable` if the value is missing.

#### New entities

| Platform | Class | Key | Device class | Unit | State class | Source reg |
|----------|-------|-----|--------------|------|-------------|-----------|
| `binary_sensor` | `FluidraUvPresentBinarySensor` | `uv_present` | `run` (or `safety`?) | — | — | c252 |
| `sensor` | `FluidraUvRunningHoursSensor` | `uv_running_hours` | `duration` | `h` | `total_increasing` | c253 |
| `binary_sensor` | `FluidraBoostActiveBinarySensor` | `boost_active` | `running` | — | — | c103/c245 |
| `sensor` | `FluidraBoostRemainingHoursSensor` | `boost_remaining_hours` | `duration` | `h` | `measurement` | c118 |
| `binary_sensor` | `FluidraFiltrationStateBinarySensor` | `filtration_state` | `running` | — | — | c135 (c244 fallback) |

- Implement all five under the existing `binary_sensor.py` /
  `sensor/device.py` patterns, inheriting `FluidraPoolEntity` (per
  plan 006). The `binary_sensor` ones follow
  `FluidraChlorinatorAlarmSensor` (existing); the `sensor` ones follow
  `FluidraRunningHoursSensor` (existing) for the total_increasing one
  and a new pattern for the `measurement` boost_remaining.
- Coalesce c103 and c245 (the doc notes both exist; on tecnoLC2 c103
  is the boost write, c245 is the boost state on some firmware).
  Precedence rule: read c245 first, fall back to c103.

#### Coordinator integration

- These sensors are read-only — they do not change coordinator flow.
- Confirm the components_to_scan fan-out already includes the new ids
  (no coordinator logic change expected).

### 1.4 Tests

**Unit** (new file `tests/test_new_registers.py`):
- Mock `coordinator.data` with hand-crafted `device["components"]`
  carrying the new registers. Construct the entity, assert:
  - `native_value` is the expected number/text.
  - `available` is `True` only when the register is present and the
    parent is online.
  - For c252: returns `True`/`False` based on the integer value (≥1).
- Test the c103↔c245 fallback (both, only c103, only c245, neither →
  `None` / unavailable).
- Test the boost_remaining sensor when only c111 is present (compute
  `hours + minutes/60`); when only c118 is present; when both are.

**Integration** (extend `tests/test_binary_sensor.py` and
`tests/test_sensor_full.py`):
- Mock the full poll cycle (`mock_pool_data` extended) and assert
  the entity is created with the right `unique_id` and
  `_attr_device_class`.

**Operator-side verification (no test)**:
- One real pool with UV (any GenSalt with UV option).
- One real pool without UV → boost countdown still works, uv sensor
  stays `unavailable`.
- One real pump with 3-speed state registers → c135 reads 1/0
  correctly when toggling filtration.

### 1.5 Risk + rollback

- Risk: low. Sensors going `unavailable` is the only failure mode — no
  control surface, no entity id collision (new sensors get new
  `unique_id` keys).
- Rollback: revert the commit. No migration needed (no `unique_id`
  change to existing entities).
- Feature flag: not required; profile-gated, so a profile that does
  not declare the feature simply does not create the entity.

### 1.6 Real-account dependency

**Yes** for the unit / factor verification. The doc says c253 is
integer hours with factor 1; the operator must confirm on their UV
unit. The chlorinator boost behaviour (which of c103/c245 is read,
whether `c118` reads hours since cycle start or hours remaining) must
be confirmed.

---

## Pass 2 — Pump 3-speed control via c135 / c137 / c244

> **Statut au 2026-08-26 — IN PROGRESS (branche `feat/pass1-new-registers`).**
> Code et tests écrits et verts (1979 tests, ruff + mypy propres). Comme pour la
> passe 1, il reste la déclaration en profil, qui exige du matériel.
>
> Livré : `select.FluidraThreeSpeedPumpSelect`, entièrement paramétré par le
> profil (`pump_3speed`), + traductions en/fr/es/pt + `tests/test_pump_three_speed.py`
> (16 tests).
>
> **Deux erreurs du plan initial, corrigées à l'écriture :**
>
> 1. **« Ajouter un `select` au lieu du `number` de pourcentage » — le `select`
>    3 vitesses existe déjà.** `select.FluidraPumpSpeedSelect` (`select/pump.py`)
>    offre `stopped/low/medium/high` depuis longtemps ; il n'y a jamais eu de
>    `number` de vitesse. Ce qui manquait n'était pas l'entité mais la famille :
>    l'existant écrit en dur sur c9/c11 et lit `speed_level_reported`, décodé par
>    le coordinateur pour l'E30iQ. La nouvelle entité vise le cas où un
>    contrôleur porte la vitesse sur un seul registre qu'il accepte aussi en
>    écriture. Les deux s'excluent sur un même appareil : déclarer `pump_3speed`
>    remplace le select E30iQ.
> 2. **« Poser la fonctionnalité sur les profils eXO et Victoria » — impossible
>    pour la Victoria.** Sur `victoria_smart_connect_pump` (profil **vérifié**,
>    Issue #144), c135 est déjà décodé comme l'emplacement de quick-function
>    actif et c136 son expiration ; c137 n'est même pas scanné. Y déclarer
>    `pump_3speed` ferait écrire sur un registre dont la famille n'a jamais
>    confirmé le sens. Le eXO NS25 est un chlorinateur avec sa propre carte de
>    registres (boost c46, chlorination c38) : les c135/c137/c244 du dump
>    viennent du `configFile` tecnoLC2, donc du contrôleur, pas d'un profil de
>    pompe existant.
>
> **Aucune fonctionnalité `pump_3speed` n'est donc déclarée nulle part**, et
> c'est délibéré : contrairement à la passe 1, il s'agit d'une **écriture**.
> Le doc lui-même donne la table d'états comme « à confirmer », et poser à
> l'aveugle un select qui écrit 1/2/3 sur c137 chez des utilisateurs reviendrait
> à commander du matériel réel sur une hypothèse. Les deux tables — lecture et
> écriture — vivent dans le profil (`write_map`, `read_map`), donc le jour où un
> possesseur de pompe 3 vitesses confirme le mapping, c'est une édition de
> profil, sans toucher au code.

> **Refines existing control.** c11 is currently read for speed
> percentage; the app uses c137 to map state 0/1/2 → write 1/2/3.
> Adds a proper `select` instead of the existing `number` percentage
> and surfaces the filtration state as a separate switch.

### 2.1 Rationale

- The current `number` speed entity works for variable-speed pumps
  but the eXO/Victora 3-speed pumps benefit from a discrete
  `select` (off/low/medium/high) matching what the app offers.
- A `binary_sensor` for "filtration block active" (c135/c244)
  independent from the pump on/off (c9/c13) lets automations target
  the actual filter cycle, which the c9 on/off does not represent on
  schedule-driven pumps (Victoria, eXO).
- Distinct from Pass 1: Pass 1 only adds read sensors; this pass
  re-shapes the control entity and resolves the state mapping.

### 2.2 Endpoints + response shape

- No new endpoint. Existing PUT `/components/{cid}` (c137) with
  integer 1/2/3 — already used in the bulk fetch. The write payload
  is `{"desiredValue": <int>}` exactly as the rest of
  `control_device_component`.
- **TBD on operator's pump**: does c137 read 0 when the pump is off
  (and writing 1 starts it on low), or does c135 need to be 1 first?
  The doc's table hints at `0→3, 1→1, 2→2` but the doc itself flags
  this as "to be confirmed". This is the only real-account blocker.

### 2.3 Code changes

#### Constants (`const.py`)

- Already have `COMPONENT_PUMP_SPEED = 11` (legacy c11). Add
  `COMPONENT_PUMP_SPEED_3SPEED = 137`,
  `COMPONENT_PUMP_FILTRATION_ON = 135`,
  `COMPONENT_PUMP_MODE = 244`.
- New map `PUMP_3SPEED_VALUE_TO_OPTION = {0: "off", 1: "low", 2:
  "medium", 3: "high"}` (and inverse).

#### Profile (`device_registry/configs/`)

- Add a feature `pump_3speed: True` on the eXO and Victoria profiles.
- Add c135, c137, c244 to `specific_components` for those families.
- Gate the existing `FluidraPumpSpeedSensor` (number %) on
  `not device.features.get("pump_3speed")` so the two do not collide.

#### Entities

- New `select` entity `FluidraPumpSpeedSelect` for 3-speed pumps
  (option list: `off`, `low`, `medium`, `high`). On `select_option`,
  translate the option back to 1/2/3 and call
  `control_device_component(c137, value)` (NOT c11).
- Refactor (or coexist with) the existing `binary_sensor` for pump
  running to also use c135. The current implementation uses
  `device["is_running"]` (set by the c9 PUT path) — for schedule-
  driven pumps, that flag stays False even while the pump is running.
  Resolution: prefer c135 over `is_running` when the feature is
  declared; fall back otherwise.

### 2.4 Tests

**Unit** (extend `tests/test_select.py`):
- Mock a 3-speed pump with c137 = 2; assert select shows "medium".
- Mock option pick "high"; assert PUT on c137 with value 3.
- Mock write returning 200 but cloud drop → assert
  `HomeAssistantError("...")` is raised and optimistic state is
  cleared (project rule from `entity.py` comment + `ARCHITECTURE.md`).

**Integration** (extend `tests/test_switch.py`):
- Verify the binary_sensor entity uses c135 when the feature is
  declared; uses c9/is_running otherwise.

**Real-account**:
- One eXO or Victoria pump. Confirm the 0/1/2 ↔ option mapping.

### 2.5 Risk + rollback

- Risk: medium. Adding a new entity while the old one stays is the
  safest path — the integration rule forbids changing an existing
  `unique_id` (`ARCHITECTURE.md` §Hard rules). The old `number`
  speed entity stays available for non-3-speed pumps.
- Rollback: delete the new select/binary_sensor files, drop the
  `pump_3speed` feature flag. Existing entities untouched.
- Feature flag: yes — `pump_3speed` per profile. No config-flow
  option needed.

### 2.6 Real-account dependency

**Yes**, must be verified on a 3-speed pump. The doc itself flags
the 0↔off mapping as unconfirmed.

---

## Pass 3 — Dynamic UI config loader (`/uiconfig`)

> **Statut au 2026-08-26 — PARTIEL, bloqué sur un paramètre inconnu.**
> Lecteur et parseur livrés et verts (2002 tests, ruff + mypy propres,
> `_uiconfig.py` à 94 % de couverture). Le câblage dans le coordinateur et
> l'option de config flow ne sont **pas** livrés, et ne peuvent pas l'être.
>
> **Ce que la sonde a établi** (appels réels sur le compte de l'opérateur,
> lecture seule, via le client de l'intégration) :
>
> | Requête | Réponse |
> |---|---|
> | `GET /uiconfig` sans paramètre | `400 Missing required request parameters: [deviceType, appId]` |
> | `+ deviceType=connected` | `400 Missing required request parameters: [appId]` |
> | `+ appId=iaqualink_plus` (et 4 variantes) | `500 Internal server error` |
> | `+ appVr=` (8 formats : `2.23.1`, `2.23`, `223`, `2`, `v2.23.1`, `2.23.1.0`, `2230100`, +lang) | `400 Invalid appVr parameter` |
>
> L'endpoint **existe** — il ne rend jamais 404 — et il valide ses paramètres
> côté serveur. `appId=iaqualink_plus` franchit la validation (le 400 devient
> 500), ce qui en fait le meilleur candidat connu ; `appVr` reste introuvable.
> Aucune valeur littérale d'`appId`/`appVr` n'apparaît dans les chaînes de
> `libapp.so` — seulement les messages d'erreur qui les mentionnent
> (« A non-empty appId is required for automations queries »,
> « fetching config files without identification appId= », « appIdMinVr »).
> **Un `curl` ne suffira donc pas : il faut une capture MITM de l'app
> officielle** (ou un dump du `Dio` interceptor), ce qui corrige le §3.2 du
> plan, qui pensait la capture faisable à la main.
>
> Conséquence pour la **passe 6** (automations) : même verrou, la chaîne de
> l'APK le dit explicitement pour cet endpoint-là.
>
> **Livré** :
> - `fluidra_api/_uiconfig.py` — `UiRegister` (dataclass gelée : `read_id`,
>   `write_id`, `type`, `factor`, `decimals`, `min`/`max`/`steps`, `units`,
>   plus `decode()`/`encode()` qui appliquent le facteur dans les deux sens),
>   `UiConfigMixin.get_device_uiconfig()`, `parse_uiconfig()`,
>   `build_dynamic_scan_set()`
> - mixin assemblé dans `FluidraPoolAPI`, symboles exportés
> - `tests/test_uiconfig.py` (23 tests)
>
> Le parseur accepte les quatre enveloppes plausibles (liste nue, wrapper
> `configFile`/`components`/`registers`/`uiConfig`, mapping indexé par id) et
> rend `None` — jamais un dict vide — sur tout le reste, comme
> `_parse_bulk_components`. La forme d'un **bloc** de registre, elle, est
> documentée par l'APK ; seule l'enveloppe est supposée.
>
> **Rien n'appelle `get_device_uiconfig()`**, et un test le verrouille
> (`test_nothing_calls_the_endpoint_yet`) : le brancher aujourd'hui coûterait
> une requête par appareil et par poll, toutes vouées à l'échec. Ce test est
> ce qu'il faudra supprimer le jour où les paramètres seront connus.
>
> **Suite de la mesure (même jour) — une source qui répond, elle.**
> `GET /generic/devices/{id}` renvoie `info.configuration.capabilities`, où le
> cloud **déclare le registre des plannings de l'appareil**
> (`schedulers: [{id: "pump", componentRead: 20, componentWrite: 20, type:
> "minimal", enabled: true}]`, mesuré sur l'E30iQ de l'opérateur). C'est la
> réponse d'autorité à l'Issue #174, que les profils résolvent aujourd'hui
> depuis les drapeaux de type de pompe. Livré : `get_device_capabilities()` +
> `parse_scheduler_capabilities()`, exposés dans les **diagnostics** sous
> `cloud_schedulers`, avec le registre résolu par le profil et un booléen
> `matches_profile`. Une requête par appareil au téléchargement des
> diagnostics, **jamais sur le chemin de poll** — un test le verrouille.
> `GET /generic/configFiles` et `/configFiles/identification/{thingType}`
> répondent aussi (124 configFiles, 392 Ko) mais ne contiennent **aucun**
> `readId`/`writeId`/`factor` : l'identification officielle y est (noms
> commerciaux, 82 `prCode`, expression JSONata de détection des sondes), les
> registres UI non. Détail complet dans `API-DISCOVERY.md` §9.
>
> **Écart avec le §3.3** : pas de `uiconfig_runtime.py` séparé ni de
> `VisibilityRule`. Les helpers purs tiennent dans le même module que le
> parseur (le projet ne sépare pas ailleurs), et les règles `hide`/`hideValue`
> n'ont pas été implémentées — sans réponse réelle, leur syntaxe exacte est
> une supposition de plus, et elles ne servent qu'à masquer des entités que
> personne ne crée encore.

> **Refactor — opt-in.** Replace the hard-coded `COMPONENT_*` lookups
> for the *scan set* with a runtime loader that reads the cloud's own
> `configFile` JSON. The hardcoded constants stay as the default; the
> loader runs only when an option flag is on.

### 3.1 Rationale

- The doc shows that the Fluidra cloud serves a complete
  `configFile` JSON describing every register, its readId/writeId,
  factor, decimals, min/max, units, and visibility rules
  (`hide`/`hideValue`). Loading it removes the need to hand-code
  every new model and makes "device X has a register the integration
  doesn't know" a non-issue.
- Existing per-family profile specificity is preserved: a profile
  that declares a `specific_components` list wins over the dynamic
  set. This keeps the well-tested paths (tecnoLC2, Z250iQ, …) intact
  while letting new families be discovered.
- **Cost estimate matches the doc**: ~100 LoC for the parser + a
  hook into `_fetch_components` / `_discover_devices_for_pool`.

### 3.2 Endpoint + response shape

- `GET /generic/devices/{id}/uiconfig` (Bearer, same auth as the
  other generic endpoints).
- **TBD — must be measured at runtime**: the response envelope is
  not in the doc, only the inner `configFile` JSON (with `readId`/
  `writeId` blocks). The operator MUST capture one raw response
  (via `curl -H "Authorization: Bearer $TOKEN" ...` on their own
  pool) and attach the first 200 lines to the implementation PR.
- The 5-language translations files (en/fr/de/es/it) must each gain
  a short description for the new options-flow toggle (see 3.5).

### 3.3 Code changes

#### New mixin (`custom_components/fluidra_pool/fluidra_api/_uiconfig.py`)

- `class UiConfigMixin(FluidraAPIBase):`:
  - `async def get_device_uiconfig(self, device_id: str) -> dict[str, Any] | None`
    — `GET /generic/devices/{id}/uiconfig`, retry through the
    standard `_request` funnel. Returns the raw payload or `None`
    on failure (transient error → cached value used).
  - `def parse_uiconfig(self, payload: Any) -> dict[int, UiRegister]` —
    pure parser, returns a mapping `{readId: UiRegister}` (or
    `None` for unparseable). `UiRegister` is a small dataclass:
    `read_id: int`, `write_id: int | None`, `type: Literal[…]`,
    `factor: float`, `decimals: int`, `min: float | None`,
    `max: float | None`, `units: str | None`,
    `visibility: VisibilityRule | None`. The `VisibilityRule`
    captures the `hide` / `hideValue` expressions as a callable
    `def visible(component_states: dict[int, Any]) -> bool` for
    simple cases, or `None` (always visible) for the rest — the
    doc lists simple `cXX==N` and `cXX==N || cYY==M` patterns
    which are tractable, the more complex ones get a default
    visible.
- Wire the mixin into `FluidraPoolAPI` in `fluidra_api/client.py`.

#### New module (`custom_components/fluidra_pool/uiconfig_runtime.py`)

- Pure helpers, no `hass`, no I/O.
- `def build_dynamic_scan_set(profile_specific: list[int],
  uiconfig: dict[int, UiRegister] | None) -> list[int]`
  — returns the union. Profile-specific ids always win.
- `def apply_factor(raw_value: Any, reg: UiRegister) -> Any` —
  applies `factor` and rounds to `decimals`. Inverse for writes.
- `def option_list_for(reg: UiRegister) -> list[str] | None` —
  for `selector` registers (`options: [...]` field if present in
  the uiconfig; the doc hints at it but does not confirm).

#### Coordinator hook (`coordinator/coordinator.py`)

- In `_fetch_components`, when the option flag is on:
  - On the first poll for a device, call
    `api.get_device_uiconfig(device_id)` once and cache the result
    on `device["uiconfig"]`.
  - Merge the uiconfig register set with the profile
    `specific_components` for the scan request.
- One `uiconfig` request per device per session is the budget —
  the doc already shows the cloud has it cached (no per-poll cost).
- The existing `get_all_components` (bulk) is the actual fetch path
  — the scan set change just makes sure the bulk response is
  decoded against the *right* factor/units.

#### Entity adapters

- For Pass 1 / Pass 2 sensors that are already mapped (c103, c111,
  c118, c252, c253, c135, c137, c244): the dynamic loader
  re-affirms their unit/factor from the cloud. Existing entities
  consume `device["components"][N]["reportedValue"]` as before —
  no change to entity code, just the decoder.
- **New** entities unlocked by the dynamic loader (chlorinator
  pH/ORP probes on families that don't have a profile yet, secondary
  salinity `c174`/`c185` that already exist in the doc table) get
  auto-created ONLY if the user opts in. The opt-in path is the
  safe one — we do not want a flood of new entities on a
  user's existing pool without their consent.

### 3.4 Config-flow integration

- Add an options-flow toggle (in `config_flow.py`
  `FluidraPoolOptionsFlowHandler.async_step_init`):
  - `CONF_ENABLE_DYNAMIC_UICONFIG: bool = False` (default off).
- Behind the flag:
  - First-poll `uiconfig` fetch for every device.
  - Dynamic entity creation gated on a per-register profile
    feature (e.g. `auto_create` derived from the doc table).
  - The flag is stored in `entry.options`, honoured by the
    coordinator via `runtime_data.options_snapshot` (already
    wired per `ARCHITECTURE.md`).
- Bump `manifest.json` version (SemVer minor — 2.85.0) and
  `hacs.json` minimum.

### 3.5 Tests

**Unit** (new `tests/test_uiconfig.py`):
- Synthetic `configFile` JSON fixture (build from the doc's
  table — at least 10 registers including a `hide` rule, a
  `selector`, a `factor`).
- Assert the parser returns the right `UiRegister` shape, the
  factor applies correctly, the visibility rule hides the right
  registers.
- Assert `build_dynamic_scan_set` unions profile + dynamic.
- Assert the config-flow toggle is round-tripped via
  `entry.options`.

**Integration** (extend `tests/test_init_setup.py`):
- With the option off → no uiconfig request, no dynamic
  entities. Existing behaviour preserved.
- With the option on → first poll triggers exactly one
  uiconfig request per device; subsequent polls do not.

**Operator-side verification**:
- Capture the real `/uiconfig` response from the operator's
  account. Commit it under
  `tests/fixtures/uiconfig_sample.json` (with redactions for
  device id and pool id — the project already has a mask
  helper, `utils.mask_device_id`).
- Without that capture the parser is built on a guess and
  may fail on the real envelope.

### 3.6 Risk + rollback

- Risk: medium. The parser is the riskiest part (response
  shape TBD). Mitigated by opt-in flag, by `None` on parse
  failure (falls through to hardcoded), and by a one-line
  kill switch in the coordinator.
- Rollback: turn the option off. No entity id change for
  users who never enabled it.
- Feature flag: yes — `CONF_ENABLE_DYNAMIC_UICONFIG` in
  options flow.

### 3.7 Real-account dependency

**Yes — critical.** The parser cannot be validated without
a real `/uiconfig` response. The operator must run a manual
`curl` against the prod endpoint and paste the JSON into a
fixture before the implementation work begins.

---

## Pass 4 — Telemetry endpoint `/telemetry`

> **Statut au 2026-08-26 — BLOQUÉ (mesuré, pas supposé).** `GET /generic/telemetry` rend
> **403**, et le corps n'est pas un refus de droits mais le message d'**AWS
> SigV4** : « Invalid key=value pair (missing equal-sign) in Authorization
> header ». Cette route attend une requête **signée IAM**, pas le Bearer
> Cognito qui sert partout ailleurs dans l'intégration. Aucun niveau de compte
> ne débloquera ça — il faut le schéma de signature, donc une capture de
> l'app. Voir `API-DISCOVERY.md` §9.3.

> **Read-only rolling sensors.** A single fetch per poll cycle
> (per pool) yielding water-quality / device-state aggregates
> over 24h/7d windows. Exposed as `state_class: measurement`
> sensors so HA can compute `statistics` (mean, min, max) and
> feed the Energy dashboard.

### 4.1 Rationale

- The doc lists `telemetry/{telemetry, telemetry_device,
  telemetry_measure, telemetry_pool, telemetry_records,
  telemetry_user}` DTOs and the `/telemetry` endpoint. The
  official app uses these for water-quality trends and
  device-state histories — the integration has zero telemetry
  sensors today.
- Maps cleanly to HA's `SensorStateClass.MEASUREMENT` for
  non-cumulative quantities (pH, ORP, water temperature
  history) and `TOTAL_INCREASING` for running hours
  telemetry.

### 4.2 Endpoint + response shape

- `GET /telemetry` (Bearer). The doc does not show the
  envelope. **TBD — must be measured.**
- Probable shape (inferred from DTO names, **not** measured):
  `{"devices": [...], "pools": [...], "user": [...],
  "waterQuality": [...]}` with per-measurement records
  carrying `value`, `unit`, `timestamp`, `registerId`.
- The operator must capture one raw response and confirm.

### 4.3 Code changes

#### New mixin (`fluidra_api/_telemetry.py`)

- `class TelemetryMixin(FluidraAPIBase):`:
  - `async def get_telemetry(self, pool_id: str | None = None)
    -> dict[str, Any] | None` — `GET /telemetry`, optional
    `poolId` param. Returns raw payload or `None`.
- Wire into `FluidraPoolAPI`.

#### New module (`coordinator/telemetry.py`)

- `def normalise_telemetry(payload: Any) -> list[TelemetryPoint]`:
  pure parser. Each `TelemetryPoint` is a `dataclass`:
  `register_id: int`, `value: float`, `unit: str | None`,
  `timestamp: datetime`, `scope: Literal["device", "pool",
  "water_quality", "user"]`. Pure function, no I/O.
- `def latest_per_register(points: list[TelemetryPoint]) ->
  dict[int, TelemetryPoint]`: collapse to most-recent per
  register.

#### Coordinator hook

- New `_async_update_telemetry()` task, called once per
  poll cycle (or at a coarser interval — telemetry records
  may be coarse, e.g. hourly). Stash the result on
  `coordinator.data[pool_id]["telemetry"]`.
- Failure: keep the previous value, log at DEBUG. A single
  failed fetch must not fail the whole poll (per the
  `coordinator` comment on `EMPTY_COMPONENT_FETCH_THRESHOLD`).

#### New sensors (`sensor/pool.py`)

- `FluidraPoolTelemetrySensor` — pool-attached, picks
  one of:
  - latest pH (rolling)
  - latest ORP (rolling)
  - latest water temperature (rolling)
- Each: `state_class = SensorStateClass.MEASUREMENT`,
  `device_class` matches the metric (PH, VOLTAGE for ORP mV,
  TEMPERATURE for °C).
- **No aggregation across pools** — one set per pool.

### 4.4 Tests

**Unit** (`tests/test_telemetry.py`):
- Synthetic telemetry payload fixture (build from the
  inferred shape).
- `normalise_telemetry` extracts the right number of points.
- `latest_per_register` keeps the newest.
- Sensor reads the latest value of its target register.

**Integration** (extend `tests/test_sensor.py`):
- A pool with telemetry enabled gets the new entities.
- A pool where the endpoint returns 404 has no new entities
  and no warning spam.

**Real-account**:
- Capture raw `/telemetry` for one pool. Without it the
  parser is built on a guess.

### 4.5 Risk + rollback

- Risk: low. Read-only, opt-in via the dynamic-uiconfig
  flag (Pass 3) or its own `CONF_ENABLE_TELEMETRY` toggle.
- Rollback: turn the toggle off. No `unique_id` change.
- Feature flag: yes.

### 4.6 Real-account dependency

**Yes**. The endpoint shape is not in the doc.

---

## Pass 5 — WebSocket `wss://ws.fluidra-emea.com`

> **Statut au 2026-08-26 — IMPLÉMENTÉ (opt-in), déployé et connecté sur le HA dev.**
>
> Ancien statut : DÉBLOQUÉ, mesuré de bout en bout.
> Le canal a été ouvert, abonné, et vérifié en poussant un vrai changement
> d'état (accord explicite de l'opérateur pour commander sa pompe ; valeur
> d'origine restaurée et vérifiée stable sur 60 s).
>
> **Ce que le plan supposait et qui est faux** : l'authentification ne se fait
> pas par en-tête `Authorization`. Bearer et jeton brut donnent tous deux un
> handshake **401**. Ce qui marche :
> `wss://ws.fluidra-emea.com?token=<access_token>` — le jeton Cognito que
> l'intégration détient déjà, en **paramètre de requête**.
>
> **Ce que le canal livre** : 2,5 s après une écriture,
> `{"action":"componentChange","body":"{deviceId, deviceType, componentId,
> reportedValue, ts}"}` — exactement le triplet que le coordinateur applique.
> `subsDevice` et `subsPool` (non documenté) répondent `200 OK` ; une action
> inconnue rend `Forbidden` sans fermer la connexion.
>
> À noter, mesuré au passage : après un démarrage manuel, l'E30iQ tourne à
> 100 % pendant ~1 min avant de redescendre au niveau demandé (comportement
> confirmé par l'opérateur). La valeur rapportée diverge donc de la consigne
> pendant cette minute ; la grâce de vérification d'écriture (180 s minimum)
> la couvre, et ne doit pas être raccourcie sans refaire ce calcul.
>
> **Robustesse mesurée** (`API-DISCOVERY.md` §10.5), qui fixe le cycle de vie :
> une connexion silencieuse est coupée **à 600 s pile** avec `close_code=1006`
> (délai d'inactivité d'API Gateway, fermeture abrupte), alors qu'un ping
> toutes les 60 s la maintient **au moins 23 min** sans incident. Surtout, le
> jeton n'est validé **qu'au handshake** : à +420 s, jeton expiré depuis
> 2,5 min, `subsDevice` répond encore `200 OK`. Il ne faut donc **pas** rouvrir
> la connexion à chaque rafraîchissement de jeton — ouvrir une fois, garder
> vivant par ping, rouvrir seulement sur fermeture. `unsubsPool` rend `500` :
> pour tout arrêter, fermer la connexion. Les réponses sont asynchrones et
> décalées — boucle de lecture indépendante, corrélation par `action`.
>
> Détail complet et traces dans `API-DISCOVERY.md` §10.

> **Livré** :
> - `fluidra_api/_websocket.py` — `parse_frame()` (double décodage : le `body`
>   est une chaîne JSON dans l'enveloppe), `ComponentChange`, et
>   `FluidraWebsocketClient` (connexion, abonnements, ping toutes les 240 s,
>   reconnexion 5 s → 300 s en backoff, arrêt propre)
> - coordinateur : `async_start_realtime()` / `async_stop_realtime()`,
>   `_handle_realtime_change()` qui passe le changement poussé par
>   **`_process_component_state`, le décodeur du poll** — pas de second chemin
>   qui pourrait diverger — puis `async_set_updated_data()` pour réveiller les
>   entités sans aller sur le réseau
> - option `enable_realtime` (défaut **off**) dans le flux d'options, avec
>   traductions en/fr/es/pt
> - `tests/test_realtime.py` (34 tests, dont la trame mesurée telle quelle)
>
> **Principes tenus** : le poll reste la source de vérité et tourne à
> l'identique ; le canal ne peut pas casser l'intégration (toute exception est
> rattrapée et déclenche une reconnexion, un callback qui échoue ne ferme pas
> la connexion) ; la découverte d'appareils reste au poll (un changement sur un
> appareil inconnu est ignoré) ; l'arrêt est attendu dans `async_unload_entry`
> plutôt que confié à un `async_on_unload` qui aurait laissé une tâche
> résiduelle.
>
> **Vérifié en réel** : activé sur le HA dev, `Realtime channel connected,
> subscribing to 1 device(s)`, sans erreur.

> **The structural fix for Issue #210.** The cloud pushes
> state-change notifications over a WebSocket. Subscribing
> after login lets the coordinator refresh its state
> immediately after a PUT (or any external change) instead of
> waiting for the next 30 s poll. The REST polling remains
> the source of truth; the WS is a *fast trigger*.

### 5.1 Rationale

- Issue #210 is fundamentally a latency problem: after a
  control write, the cloud often has the new value within
  1-2 s, but the integration only re-polls every 30 s, so
  the user sees their toggle stuck for up to 30 s after the
  write. The WebSocket is the cloud's own solution.
- The doc says: subscribe with
  `{"action": "subsDevice", "deviceType": "connected",
  "deviceId": "<id>"}`, server pushes
  `{"action": "command", "commandId": "..."}` which
  correlates to a previous write. There is no `value` in
  the push — the WS just says "something changed, go read".
- The integration rule (per `ARCHITECTURE.md` §Optimistic
  state + Issue #210) is to keep the optimistic value
  visible until the REST confirms. The WS simply makes the
  confirmation arrive faster.

### 5.2 Endpoint + handshake

- `wss://ws.fluidra-emea.com` (Bearer auth — exact header
  shape TBD).
- Doc says "header d'auth supposé Bearer après login, à
  confirmer par capture". **Critical real-account blocker.**
- The operator MUST capture a handshake (e.g. via
  `websocat wss://ws.fluidra-emea.com -H "Authorization:
  Bearer $TOKEN"`) and attach the first 30 s of frames
  before the implementation work begins. Without it the
  auth header / ping interval / reconnection logic are
  guesses.

### 5.3 Code changes

#### New mixin (`fluidra_api/_websocket.py`)

- `class WebSocketMixin(FluidraAPIBase):`:
  - `async def connect(self) -> None` — open the WS,
    authenticate, store the connection on `self._ws`.
  - `async def subscribe_device(self, device_id: str) ->
    None` — send the `subsDevice` frame. Idempotent.
  - `async def _reader_loop(self) -> None` — long-running
    task: read frames, parse, push to the listener queue.
  - `async def _ping_loop(self) -> None` — periodic ping
    (interval TBD, start with 25 s).
  - `async def _reconnect(self) -> None` — exponential
    backoff reusing the existing
    `INITIAL_BACKOFF/MAX_BACKOFF/BACKOFF_MULTIPLIER` from
    `api_resilience.py`.
  - `def add_listener(self, callback) -> None` — register
    a coroutine to fire on every pushed frame.
- Reuses the auth primitives from `_auth.py` and the
  circuit-breaker-aware reconnect pattern from
  `_session.py`. The WS is **outside** the REST
  circuit breaker (per `ARCHITECTURE.md` rule about
  Cognito — different hosts) but the per-pool refresh
  failure isolation (in the coordinator) still applies.
- Wire into `FluidraPoolAPI` in `client.py`. The WS is
  started lazily on the first `subscribe_device` call and
  owned by the API instance for its lifetime.

#### Coordinator hook (`coordinator/coordinator.py`)

- On entry setup, after the first successful poll, call
  `api.subscribe_device(device_id)` for every
  `connected`-type device.
- Add a listener via `api.add_listener(...)`: on every
  pushed `command` frame, call
  `coordinator.async_request_refresh()` (the existing
  debounced refresh — already does the right thing per
  `ARCHITECTURE.md`).
- **No state mutation in the listener** — the WS only
  triggers a re-read, the actual state comes from the
  REST response. This avoids the
  poll-vs-ws-conflict trap (see Cross-cutting concerns).

#### Lifecycle

- Start WS in `__init__.py:async_setup_entry` AFTER the
  first successful poll, so a failed login does not block
  setup.
- Stop WS in `async_unload_entry` BEFORE closing the
  REST session, so a clean shutdown order is preserved.
- If the WS is disabled (option flag off, see 5.5) the
  coordinator behaves exactly as today.

### 5.4 Tests

**Unit** (`tests/test_fluidra_api_websocket.py`):
- Synthetic WS server (`aiohttp.web` test server) that
  accepts the connect + subs, then pushes a frame.
- Assert the listener fires within X ms of the push.
- Reconnect test: kill the server, assert exponential
  backoff is honoured, restart, assert reconnect.
- Auth-failure test: server returns 401 → WS closes, no
  reconnect loop spam (the integration rule from
  `ARCHITECTURE.md` §Resilience).

**Integration** (extend `tests/test_coordinator.py`):
- With WS enabled, push a frame, assert
  `async_request_refresh` was called.
- With WS disabled (option off), no WS connect attempt.

**Real-account**:
- Capture handshake + first 30 s. **Required** for the
  implementation to be mergeable.

### 5.5 Config flow

- `CONF_ENABLE_WEBSOCKET: bool = False` (default off,
  behind the same options-flow page as Pass 3 and Pass 4).
- First release with WS: default OFF. Second minor
  release after field reports: flip to default ON.

### 5.6 Risk + rollback

- Risk: medium. The lifecycle (start, stop, reconnect) is
  the riskiest part. The REST path is untouched so a WS
  bug is fully recoverable.
- Rollback: option off. The integration continues to
  poll-only, same as v2.84.0.
- Feature flag: yes — `CONF_ENABLE_WEBSOCKET`.
- Observability: log every connect/disconnect/reconnect
  at INFO (single line, no payload), every frame at
  DEBUG. A WS that flaps every 30 s should be loud.

### 5.7 Real-account dependency

**Yes — critical.** Auth header, ping interval, and the
exact "command" payload shape are all unconfirmed. The
doc itself flags this.

---

## Pass 6 — Automations endpoint (`/pools/{id}/automations`)

> **Statut au 2026-08-26 — BLOQUÉ (mesuré, pas supposé).** `GET /generic/pools/{id}/automations` rend
> **403**, et le corps n'est pas un refus de droits mais le message d'**AWS
> SigV4** : « Invalid key=value pair (missing equal-sign) in Authorization
> header ». Cette route attend une requête **signée IAM**, pas le Bearer
> Cognito qui sert partout ailleurs dans l'intégration. Aucun niveau de compte
> ne débloquera ça — il faut le schéma de signature, donc une capture de
> l'app. Voir `API-DISCOVERY.md` §9.3.

> **Bridge CRUD for the app's "automations" feature**
> (distinct from `/schedulers`). The doc says it is
> "plus haut niveau" (higher level) — likely the
> conditional wizards the app offers, separate from
> per-schedule times. Whether this is worth a full HA
> service surface depends on the actual shape, which
> is not in the doc.

### 6.1 Rationale

- If "automations" is a thin CRUD over a small fixed
  schema (e.g. "if mode==X then run schedule Y"), it
  maps cleanly to a service. If it is a freeform
  conditional graph, an HA service would be too
  opinionated.
- Per the doc's own recommendation: "GET it first to
  see the shape". This pass is intentionally
  exploratory and degrades gracefully.

### 6.2 Endpoint + response shape

- `GET/POST /generic/pools/{id}/automations` (and
  likely `PUT/DELETE /generic/pools/{id}/automations/
  {aid}`). Bearer auth.
- **TBD — must be measured.** Capture a real GET
  response first. If the body is empty or the
  endpoint 404s, defer the whole pass.

### 6.3 Code paths (sketch only — implement after capture)

- If GET returns a non-empty list with a stable schema:
  - `AutomationMixin.get_pool_automations(pool_id) ->
    list[Automation] | None` in a new mixin.
  - `sensor/pool.py:FluidraPoolAutomationCountSensor` —
    pool-attached, `state_class = MEASUREMENT`, value is
    the count.
  - If the schema permits a stable write shape: a
    `fluidra_pool.set_automation` service, registered
    in `__init__.py:_async_register_services` following
    the existing schedule-service pattern.
- If GET returns empty / 404: log once at INFO, no
  entity, defer to a future pass.

### 6.4 Tests

- Same capture-then-test pattern as Pass 4. No test
  without a real payload fixture.

### 6.5 Risk + rollback

- Risk: low to medium depending on shape.
- Rollback: drop the entity / service. No existing
  `unique_id` is touched.

### 6.6 Real-account dependency

**Yes**. Cannot proceed without a capture.

---

## Pass 7 — Recommendations endpoint (`/pools/{id}/recommendations`)

> **Statut au 2026-08-26 — BLOQUÉ (mesuré, pas supposé).** `GET /generic/pools/{id}/recommendations` rend
> **403**, et le corps n'est pas un refus de droits mais le message d'**AWS
> SigV4** : « Invalid key=value pair (missing equal-sign) in Authorization
> header ». Cette route attend une requête **signée IAM**, pas le Bearer
> Cognito qui sert partout ailleurs dans l'intégration. Aucun niveau de compte
> ne débloquera ça — il faut le schéma de signature, donc une capture de
> l'app. Voir `API-DISCOVERY.md` §9.3.

> **Lowest-risk read.** A single diagnostic sensor per
> pool carrying the latest recommendation text from the
> cloud (e.g. "shock treatment recommended", "filter
> backwash due"). One sensor per pool, no control.

### 7.1 Rationale

- The doc lists this as "non exposé par l'intégration".
  The information is useful (it is what the app shows
  on the pool home screen) and it is a single GET, so
  the cost is one HTTP call per pool per poll cycle.
- Maps to a `sensor` with `state_class = None` (text
  recommendation, not a measurement) and
  `device_class = ENUM` if the recommendations are from
  a fixed list, else no `device_class`.

### 7.2 Endpoint + response shape

- `GET /generic/pools/{id}/recommendations` (Bearer).
- **TBD — must be measured.** The doc says
  "vides / filtrage" which suggests the payload may
  filter by context (e.g. only return recommendations
  relevant to current water-quality readings).
- One real capture needed.

### 7.3 Code changes

- `RecommendationsMixin.get_pool_recommendations(pool_id)
  -> list[Recommendation] | None` in a new mixin.
- `sensor/pool.py:FluidraPoolRecommendationSensor` —
  pool-attached. `native_value` is the most recent
  recommendation's short text; attributes carry the
  full list and the timestamp.
- Coordinator hook: one fetch per pool per poll cycle.
  Failure: keep previous value, log at DEBUG.
- No new config-flow option — this is read-only and
  cheap, safe to enable by default. (If a poll-cycle
  cost shows up in profiling, gate it then.)

### 7.4 Tests

- Synthetic payload (built from the real capture once
  it exists). Test the most-recent pick and the
  attribute shape.

### 7.5 Risk + rollback

- Risk: low.
- Rollback: drop the entity file. No `unique_id` change.

### 7.6 Real-account dependency

**Yes** for the response shape.

---

## Cross-cutting concerns

### Config flow

- New options-flow toggles added in `config_flow.py`
  `FluidraPoolOptionsFlowHandler.async_step_init`:
  - `CONF_ENABLE_WEBSOCKET` (Pass 5) — default off.
  - `CONF_ENABLE_DYNAMIC_UICONFIG` (Pass 3) — default
    off.
  - `CONF_ENABLE_TELEMETRY` (Pass 4) — default off.
  - `CONF_ENABLE_RECOMMENDATIONS` (Pass 7) — default
    on (cheap, read-only).
- Each option MUST be honoured by the coordinator
  via the existing `runtime_data.options_snapshot`
  reload-on-change machinery (`ARCHITECTURE.md`
  §Hard rules: update listener reloads only on
  *options* changes).
- Each option requires a translation key in
  `strings.json` and all 5 `translations/*.json`
  files (en, fr, de, es, it — count files in
  `translations/`). Existing pattern in
  `__init__.py:_async_register_services`.

### Capability detection

- The `_discover_devices_for_pool` already returns
  per-device metadata including `thing_type` (Issue
  #195). Add to that metadata:
  - `device["capabilities"]["websocket"]` — set
    `True` for `connection_type == "connected"` (the
    doc's WS is for connected devices only).
  - `device["capabilities"]["uiconfig"]` — set
    `True` after the first successful uiconfig GET
    (Pass 3).
  - `device["capabilities"]["telemetry"]` — set
    `True` after the first successful `/telemetry`
    response (Pass 4).
- All capabilities are read on the first successful
  poll and cached on the device dict (consistent
  with the existing `thing_type` caching pattern in
  `_devices.py:197,222`).

### Polling vs WebSocket conflict resolution

- **Single source of truth**: the REST bulk fetch
  (`get_all_components`, `_components.py:40`). The WS
  frame is a *trigger* to re-read, never a *writer*
  of state.
- The listener (Pass 5) calls
  `coordinator.async_request_refresh()` only — it
  does NOT mutate `device["components"]` directly.
- The existing debouncer (1.5 s cooldown, per
  `coordinator.py:104-109`) coalesces multiple WS
  triggers into a single poll, which is exactly the
  right shape for "20 devices all pushed a frame
  in 200 ms".
- Optimistic state (`ARCHITECTURE.md` §Optimistic
  state) is unchanged: optimistic value is shown
  until either the next REST poll confirms it (now
  triggered by the WS instead of by the 30 s timer)
  or the optimistic timeout elapses.
- A WS frame for a device whose REST read is
  currently in-flight: the in-flight read wins, the
  WS just triggers the *next* read. No race.

### Error handling

- Reuse the existing `api_resilience.py` exception
  hierarchy: `FluidraConnectionError`,
  `FluidraAuthError`, `FluidraCircuitBreakerError`.
  The WS introduces no new error class.
- WS auth failures: do NOT trigger a reauth flow on
  the first failure (the REST path is still
  healthy). Only escalate to reauth if the REST
  path also returns 401 — same primitive as today.
- Per-pool refresh failure isolation (existing
  `coordinator.py:_refresh_pool`) extends naturally
  to telemetry: a failed telemetry fetch is logged
  at DEBUG, not promoted to a per-pool failure.
- The `connection_error` repair issue (existing,
  `repairs.py:async_create_connection_issue`,
  raised at `CONNECTION_ISSUE_THRESHOLD` consecutive
  poll failures) MUST NOT count WS disconnects —
  a WS that drops every 30 s on a flaky network
  must not surface a "pool offline" repair issue
  while the REST polls keep succeeding. Guard with
  an explicit `connection_issue_counts_ws =
  False` flag in the listener.

### Documentation

- `CHANGELOG.md` (SemVer, Keep a Changelog — already
  followed, `CHANGELOG.md:1-7`) for every release
  that ships a pass. Minor bumps for new entities /
  options (2.85.0, 2.86.0, …).
- `manifest.json` and `hacs.json` version bump
  per release (per `CHANGELOG.md:7`).
- `README.md` — when a pass lands, add a one-line
  description of the new entity in the supported
  features list. Do not re-document the API
  surface (the doc and the changelog already do).
- `ARCHITECTURE.md` — when Pass 3 (uiconfig) lands,
  add a one-paragraph note in §Key mechanisms
  about the dynamic loader and its opt-in flag.
  When Pass 5 (WS) lands, add a one-paragraph
  note in §Data flow about the WS as a refresh
  trigger.
- `CLAUDE.md` — leave untouched. The project
  guidelines are stable; the new options
  conform to them (Ruff, type hints, no
  `Co-Authored-By` in commits, `helpers.py`
  for pure functions, mixin pattern in
  `fluidra_api/`).

### Release cadence

- Pass 1 and Pass 2 ship together as **v2.85.0** —
  read-only and control-only additions, low risk,
  SemVer minor.
- Pass 3 ships as **v2.86.0** — opt-in refactor,
  SemVer minor.
- Pass 4 ships as **v2.87.0** — opt-in read,
  SemVer minor.
- Pass 5 ships as **v2.88.0** — opt-in WS,
  SemVer minor. **First release stays default-off**
  per 5.5. After one minor cycle of field
  reports, default flips to on at **v2.89.0** —
  a SemVer patch since behaviour is unchanged
  for opted-in users.
- Pass 6 and Pass 7 ship as **v2.90.0** once
  the real-account capture exists. If the
  capture does not exist at the time of the
  release, drop them and re-evaluate after the
  next pass.

### Test gates

- Every pass MUST land with:
  - All existing 1312 tests green.
  - Per-pass coverage ≥ 90 % on the new module(s).
  - mypy `--strict` clean (existing CI gate per
    `ARCHITECTURE.md` §Tests).
  - Ruff clean.
  - A live deploy on the operator's HA dev
    instance via `scp -r custom_components/
    fluidra_pool/ ha-dev:/config/custom_components/
    && ssh ha-dev 'ha core restart'` and a
    zero-error log read (`ARCHITECTURE.md` §Tests
    + `ha-debugging-09` skill).
- For Pass 3/4/5/6/7, the operator MUST commit
  the real-account capture (redacted of device
  ids via the existing `utils.mask_device_id`)
  under `tests/fixtures/<endpoint>_sample.json`
  before the implementation PR can be reviewed.
