[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Quality Scale](https://img.shields.io/badge/Quality_Scale-Platinum-27ae60.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
[![GitHub release](https://img.shields.io/github/v/release/foXaCe/Fluidra-pool?sort=semver)](https://github.com/foXaCe/Fluidra-pool/releases)
[![Donate](https://img.shields.io/badge/Donate-PayPal-blue.svg)](https://www.paypal.com/paypalme/foXaCe66)

# Fluidra Pool Integration for Home Assistant 🏊‍♂️

A Home Assistant integration for **Fluidra Connect** pool equipment — variable-speed pumps,
heat pumps, salt chlorinators / electrolysers, water analysers and connected lighting.
It talks to the Fluidra cloud (AWS Cognito auth) and exposes each device as native
Home Assistant entities.

> The integration was built by reverse-engineering the Fluidra Connect API. Most device
> mappings were confirmed by the community against the official Fluidra Pool app — if your
> model isn't recognised yet, [open an issue](#-adding-new-equipment) and help us add it.

---

## 💰 Support the Project

If this integration is useful to you, you can support its development:

- **PayPal:** [paypal.me/foXaCe66](https://www.paypal.com/paypalme/foXaCe66)
- **🪙 Bitcoin:** `bc1qhe4ge22x0anuyeg0fmts6rdmz3t735dnqwt3p7`

Your contributions help me keep improving this project and adding new equipment. Thank you! 🙏

---

## ✨ Features

- **Cloud login with MFA** — email/password sign-in, multi-factor (OTP) challenge support,
  automatic token refresh, plus **re-authentication** and **reconfigure** flows when your
  credentials change or expire.
- **Automatic device discovery** — pools and their equipment are discovered from your
  account; each device is created with proper Home Assistant *device* grouping.
- **Robust cloud client** — bounded timeouts, exponential-backoff retries on 429/5xx,
  a circuit breaker for sustained outages, and a rate limiter.
- **Localized UI** — English, French, Spanish and Portuguese translations; failed commands
  surface a clear, translated error instead of silently doing nothing.
- **Realtime updates (opt-in)** — the Fluidra cloud can push changes as they happen
  instead of being polled for them: a state change reaches Home Assistant in about
  2 seconds. Polling keeps running underneath, so the channel only ever shortens the
  wait. Off by default; enable it under **Configure**.
- **Family-aware recognition** — devices are matched on Fluidra's own family identifier
  as well as on their serial, so a model nobody has reported yet still lands on the right
  register map instead of a generic fallback.
- **Diagnostics** — downloadable diagnostics (with credentials redacted) for bug reports,
  including what the cloud itself declares about each device's schedule register.

### 🧩 Entity platforms

| Platform | What it controls |
|----------|------------------|
| `switch` | Pump on/off, auto mode, heater, heat pump, chlorinator, boost, schedule slots |
| `select` | Pump speed / mode, chlorinator mode, light effect/scene, per-slot schedule speed |
| `number` | Custom pump speed (0–100%), chlorination level, pH & ORP setpoints, light effect speed |
| `climate` | Heat-pump control (HVAC mode/action, target temperature, preset modes) |
| `light`  | LumiPlus Connect RGBW (on/off, brightness, colour) |
| `time`   | Schedule start/end time editing |
| `button` | Victoria VS pump Stop (halt without disarming the schedule) |
| `sensor` | pH, ORP, free chlorine, salinity, temperatures, pump speed/mode, power & head & flow (VS pumps), UV lamp hours, boost countdown, firmware, signal, status |
| `binary_sensor` | Cell production, alarms, speed-preset inputs, UV lamp presence, filtration running |

---

## 🔌 Supported Hardware

Device recognition is data-driven and community-confirmed. Many models below were added and
verified through GitHub issues. Anything not matched falls back to a sensible **generic**
profile, so unknown equipment is usually still usable.

### 💧 Variable-Speed Pumps
- **E30iQ** (also matches `LE*` / `PUMP*` serials)
  - 3 speeds: Low (45%), Medium (65%), High (100%)
  - Automatic / scheduled mode
  - Custom speed control (0–100%)
  - Up to 8 daily schedule slots (per-slot speed + start/end time)
- **Victoria Smart Connect VS** (AstralPool, `mppvs`) — running state, live output %,
  AUTO / QUICK FUNCTION mode, speed or flow-rate setpoint, plus **power (W)**, **head (m)**
  and **flow rate (m³/h)** sensors, and an **activity** sensor that reports the transient
  priming / calibration phases separately from the speed. Control mirrors the app: an
  **Auto-schedule toggle** and a dedicated **Stop button** (halts the motor without
  disarming the schedule); speed-preset dry-contact inputs are exposed as diagnostic
  binary sensors. Direct speed/quick-function control (via `/schedulers`) is still being
  added — see [#144](https://github.com/foXaCe/Fluidra-pool/issues/144).
- Generic variable-speed pump fallback

### 🔥 Heat Pumps
- **LG Eco Elyo** — reversible: Smart Heating / Cooling, Boost, Silence presets; target temp; water-temp sensor
- **Z250iQ / Z25iQ** — same firmware family as the Z260iQ, so the same feature set:
  HVAC modes (heat / cool / heat-cool), presets, no-flow alarm, water/air temperatures,
  running hours, WiFi signal
- **Z260iQ** — HVAC modes (heat / cool / heat-cool), presets, no-flow alarm, water/air temperatures
- **Z550iQ+** — HVAC modes (heat / cool / auto), presets, HVAC action (heating/cooling/idle/no-flow), water/air temperatures
- **Z650iQ** — HVAC modes (heat / cool / heat-cool), Smart+/Smart/Ecosilence/Boost presets,
  on/off switch, water/air temperatures, running hours, compressor running hours, WiFi
  signal, instantaneous power (Watts) and compressor modulation (percent). Reverse-
  engineered from live captures; some registers remain undecoded and show up in
  the unmapped-register
  debug log.
- **Gre HPGIC** — on/off, target temperature, water temperature
- Generic heat-pump fallback

### 🧂 Salt Chlorinators / Electrolysers
- **tecnoLC2 family (30+ models)** — AstralPool Clear Connect / Clear Connect Evo / Scalable,
  Blauswim, IrriPool / Irrijardin iSalt, KLINWASS Mark Salt, Zodiac GenSalt OE iQ, Zodiac
  Ei2 iQ / Ei2 pH Evo, Gre, Energy Connect, and more (`CC*` / `LC*` serials, including
  bridged `*.nn_*` devices)
- **Automatic tecnoLC2 detection** — a chlorinator whose serial isn't on file yet is
  auto-recognised from its component signature and reads pH, ORP, water temperature and
  salinity on the right registers, so unknown units work correctly without waiting for
  their serial to be added by hand
- **Zodiac EXO iQ** (e.g. iQ35 / NS25) — 0–100% chlorination in 5% steps, output schedules, Boost (with remaining-time countdown), Low and freeze protection, Aux 1 / Aux 2 outputs (Off/On/Auto), heating setpoint
- **DM24049704** (Domotic S2) — program/slot schedule format
- Typical capabilities (model-dependent): chlorination level (0–100%), **pH setpoint**,
  **ORP/Redox setpoint**, boost mode, schedules, and sensors (pH, ORP, free chlorine,
  salinity, water temperature)

> **tecnoLC2: there is no free-chlorine probe.** These units expose 11 components and carry exactly
> **two** probes — pH and ORP/Redox. The *Free Chlorine* sensor stays `unavailable` for their whole
> lifetime (verified against a recorder database: not a single numeric state, ever). That is the
> hardware, not a bug in this integration, so please don't open an issue for it. Use **ORP as the
> disinfection proxy** — 650 mV is the usual floor — and measure free chlorine with a test kit.

### 🧪 Water Analysers
- **Zodiac Blue Connect Silver / Gold** (`WA*`, BC3) — pH, ORP and water-temperature sensors (read-only)

### 💡 Pool Lighting
- **LumiPlus Connect** (RGBW) — on/off, brightness (0–100%), RGBW colour + white channel,
  effect/scene selection and effect speed, light schedules
- Generic LED light fallback

### ♨️ Heaters
- Generic on/off heater (component-9) with optional temperature attributes

### 🆕 Adding New Equipment

Your equipment isn't listed or is only partially recognised? Help us add it:

1. **Enable debug logs**
   ```yaml
   logger:
     logs:
       custom_components.fluidra_pool: debug
   ```
2. **Switch the missing feature on and off in the official app** while the logs run. The
   first debug dump lists every register no profile maps; each following change is logged
   on its own line, so the register that moved with the feature is the one to report.
3. **Download diagnostics** from the integration's device page (credentials are redacted).
   When a device sits on a guessed profile — the one that raises the *unverified profile*
   repair warning — the dump carries an `unverified_devices` block with **all** of its
   registers, not only the handful that profile reads.
4. **Open an [issue](https://github.com/foXaCe/Fluidra-pool/issues)** with:
   - Your equipment model and serial prefix
   - The diagnostics file and the debug logs — both carry the device's `thing_type`
     (Fluidra's own family id, e.g. `eppvs`, `tecnoLC2`), which is what places an
     unreported model on the right register map
   - The features/values shown in the official Fluidra Pool app
5. **Test and share** your results — most new models are added this way.

---

## 🚀 Installation

### HACS (recommended)

1. Add this repository as a custom repository (category *Integration*):
   ```
   https://github.com/foXaCe/Fluidra-pool
   ```
2. HACS → search **"Fluidra Pool"** → Download
3. Restart Home Assistant
4. Settings → Devices & Services → **Add Integration** → "Fluidra Pool"

### Manual

```bash
git clone https://github.com/foXaCe/Fluidra-pool.git
cp -r Fluidra-pool/custom_components/fluidra_pool /config/custom_components/
```
Then restart Home Assistant and add the integration from the UI.

---

## ⚙️ Configuration

The integration is configured entirely from the UI (config flow):

- **Email** — your Fluidra Connect account email
- **Password** — your Fluidra Connect password
- **MFA** — if your account uses multi-factor authentication, you'll be prompted for the code
- **Re-auth / Reconfigure** — Home Assistant prompts you to re-authenticate if the token is
  rejected; you can also reconfigure (e.g. change the account email) from the integration menu

> [!IMPORTANT]
> **Region — EMEA (Europe) only.** This integration talks to Fluidra's **EMEA** backend
> (`api.fluidra-emea.com`). Only myFluidra / Fluidra Connect accounts registered in the
> EMEA region can log in. Accounts created in other regions — e.g. **North America**
> (iAquaLink US) or **Australia / APAC** — live on a different Fluidra backend and will be
> rejected with an "invalid credentials" error even though the same credentials work in the
> official app. Multi-region support isn't available yet (it needs the regional endpoints and
> a test account to implement safely).

### Options
- **Update interval** — polling interval in seconds, configurable from **30 to 1800**
  (default **30 s**). Change it via the integration's **Configure** button.
- **Realtime updates (experimental)** — subscribe to the Fluidra cloud's push channel so
  state changes arrive in about 2 seconds instead of waiting for the next poll. Off by
  default. Polling continues either way, so turning it off simply restores the previous
  behaviour.

---

## 🎛️ Usage

### Pump speed automation

```yaml
automation:
  - alias: "Pool — economy mode at night"
    triggers:
      - trigger: time
        at: "22:00:00"
    actions:
      - action: select.select_option
        target:
          entity_id: select.pool_pump_speed
        data:
          option: "low"
```

### ⏱️ Measuring real filtration hours (no extra hardware)

If your pump is driven by a mechanical timer or an external contactor, the integration still tells you
when it actually ran — no wiring, no extra sensor, no touching the electrical panel. When the unit
loses power its entities go `unknown`, so the chlorinator's **alarm binary sensor doubles as a run-time
log**:

```sql
-- Home Assistant recorder database (/config/home-assistant_v2.db)
-- Works with the container stopped: docker cp homeassistant:/config/home-assistant_v2.db ./ha.db
SELECT s.state,
       datetime(s.last_updated_ts, 'unixepoch') AS utc
FROM states s
JOIN states_meta m ON m.metadata_id = s.metadata_id
WHERE m.entity_id = 'binary_sensor.<your_chlorinator>_alarm'   -- slug follows your HA language
ORDER BY s.last_updated_ts;
```

`unknown` → `off` is a **start**; `off` → `unknown` is a **stop**. Two caveats measured on a real
installation:

- Polling is roughly **35 s**, so every transition carries that much uncertainty. Fine for hours/day,
  useless for anything that needs the second.
- **Discard the first sample after each start** (see [Troubleshooting](#-troubleshooting)) — it is stale,
  and it will skew any average you compute over the block.

The same trick verified a mechanical timer disc that closed its contact ~6.7 min *before* the mark and
opened it *on* the mark — about 20 extra minutes of filtration a day that no one had accounted for.

### 🔁 The first value after a reconnect is not real

Every time the unit comes back online, the integration emits **one wrong value per entity** before the
real one arrives on the next poll (~35 s later). It affects **sensors and `number` entities alike**, and
on the `number` entities it is the more dangerous of the two, because those are the control surfaces.

Measured on a tecnoLC2. The `number` entities repeat this exact two-step sequence on **every**
reconnect in the recorder; the sensor figures are from one clean power cycle:

| Entity | 1st value after reconnect | 2nd value (real) |
|---|---|---|
| `sensor.*_orp` | 694 mV | 659 mV |
| `sensor.*_ph` | 7.50 | 7.70 |
| `number.*` pH setpoint | 7.2 | 7.7 |
| `number.*` ORP setpoint | 700 | 750 |
| `number.*` chlorination level | 0 | 60 |

For the sensors it is a stale reading; for the `number` entities the first value is a placeholder that
never corresponded to anything on the device. Either way:

> **Discard the first value after every reconnection.** An automation that reads a chlorination level of
> `0`, or a pH setpoint of `7.2`, and acts on it, is acting on a value the equipment never held. The same
> goes for any average or statistic computed across a power cycle.

A reconnect is easy to spot: entities pass through `unavailable`/`unknown` on the way back, so a `for:`
delay of about a minute on that transition — or simply ignoring the first update after it — is enough.

### Services

The integration registers three services for schedule management. The `device_id` is the
Fluidra equipment serial (visible in the device's *Diagnostics* / *Device info*).

**`fluidra_pool.set_schedule`** — replace the schedule of a device:

```yaml
action: fluidra_pool.set_schedule
data:
  device_id: "LE24500883"
  schedules:
    - enabled: true
      start_time: "08:00"
      end_time: "12:00"
      mode: "1"            # 0 = Low, 1 = Medium, 2 = High
      days: [1, 2, 3, 4, 5]  # 1 = Monday … 7 = Sunday
    - enabled: true
      start_time: "18:00"
      end_time: "20:00"
      mode: "2"
      days: [6, 7]
```

**`fluidra_pool.clear_schedule`** — remove all schedules of a device:

```yaml
action: fluidra_pool.clear_schedule
data:
  device_id: "LE24500883"
```

**`fluidra_pool.set_preset_schedule`** — apply a ready-made schedule preset:

```yaml
action: fluidra_pool.set_preset_schedule
data:
  device_id: "LE24500883"
  preset: "standard"   # standard | intensive | eco | summer | winter
```

| Preset | Schedule |
|--------|----------|
| `standard` | 08:00–12:00 + 18:00–20:00 (Medium) |
| `intensive` | 08:00–18:00 (High) |
| `eco` | 10:00–14:00 (Low) |
| `summer` | 06:00–10:00 + 16:00–22:00 (High) |
| `winter` | 12:00–16:00 (Low) |

### Lovelace dashboard

```yaml
type: entities
title: Pool Control
entities:
  - entity: switch.pool_pump
  - entity: select.pool_pump_speed
  - entity: number.pool_chlorination_level
  - entity: climate.pool_heat_pump
  - entity: light.pool_light
```

> Entity IDs depend on your device names (entities use *has_entity_name*); the names above are
> illustrative.

---

## 🔧 Troubleshooting

1. **Authentication fails** — check the email/password, and complete the MFA prompt if shown.
   If the token was rejected, Home Assistant starts a re-authentication flow automatically.
2. **No pools found** — confirm your equipment appears in the official Fluidra Pool app.
3. **Enable debug logs** (see [Adding New Equipment](#-adding-new-equipment)) and attach them
   to any issue.
4. **Download diagnostics** — from the integration's device page (credentials are redacted).

| Symptom | Likely cause / fix |
|---------|--------------------|
| `Invalid credentials` but the app works | Account registered **outside EMEA** (North America, Australia, …) — not supported (see [Configuration](#-configuration)) |
| `Authentication failed` | Wrong credentials or expired token → re-authenticate |
| `No pools found` | Account has no equipment, or it's offline in the Fluidra app |
| Wrong readings after a HACS update (e.g. a chlorinator's pH shows a temperature) | A custom integration's code only reloads on a **full Home Assistant restart** (Settings → System → Restart) — a "Reload" is not enough. Restart, then re-check |
| Device shows *unavailable* | The device reports itself offline to the Fluidra cloud |
| Commands seem ignored | Check debug logs; transient cloud rejections now surface as errors |
| Setpoints/switches never change (no error) | Account has **viewer** (read-only) access to the pool — the cloud accepts writes but doesn't apply them. Check the `access_level` attribute on the pool status sensor; owner access is required to control equipment |
| `Free chlorine` is permanently `unavailable` (tecnoLC2) | Expected — the unit has no free-chlorine probe, only pH and ORP (see [Salt Chlorinators](#-salt-chlorinators--electrolysers)). Use ORP as the disinfection proxy |
| The first value after the unit powers back on is wrong | Affects **sensors and `number` entities alike** — every reconnection emits one bad value before the real one. See [The first value after a reconnect is not real](#-the-first-value-after-a-reconnect-is-not-real) |

---

## 🤝 Contributing

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/AmazingFeature`)
3. **Run the checks** — `ruff check`, `ruff format`, `mypy`, and `pytest` (see `requirements_test.txt`)
4. **Commit** your changes (Conventional Commits)
5. **Open** a Pull Request

CI runs Ruff, HACS validation, Hassfest, the pytest suite (with a coverage gate) and mypy.

## 📄 License

MIT — see [LICENSE](LICENSE).

## 🙏 Acknowledgments

- **Fluidra** for their equipment
- **Home Assistant** for the platform
- **The community** for testing, device captures and feedback

## 📞 Support

- **Issues:** [GitHub Issues](https://github.com/foXaCe/Fluidra-pool/issues)
- **Discussions:** [GitHub Discussions](https://github.com/foXaCe/Fluidra-pool/discussions)

---

**⭐ If this integration is useful to you, feel free to leave a star!**
