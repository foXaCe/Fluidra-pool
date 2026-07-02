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
- **Diagnostics** — downloadable diagnostics (with credentials redacted) for bug reports.

### 🧩 Entity platforms

| Platform | What it controls |
|----------|------------------|
| `switch` | Pump on/off, auto mode, heater, heat pump, chlorinator, boost, schedule slots |
| `select` | Pump speed / mode, chlorinator mode, light effect/scene, per-slot schedule speed |
| `number` | Custom pump speed (0–100%), chlorination level, pH & ORP setpoints, light effect speed |
| `climate` | Heat-pump control (HVAC mode/action, target temperature, preset modes) |
| `light`  | LumiPlus Connect RGBW (on/off, brightness, colour) |
| `time`   | Schedule start/end time editing |
| `sensor` | pH, ORP, free chlorine, salinity, temperatures, pump speed/mode, firmware, signal, status |

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
- Generic variable-speed pump fallback

### 🔥 Heat Pumps
- **LG Eco Elyo** — reversible: Smart Heating / Cooling, Boost, Silence presets; target temp; water-temp sensor
- **Z250iQ / Z25iQ** — on/off, target temperature, current temperature
- **Z260iQ** — HVAC modes (heat / cool / heat-cool), presets, no-flow alarm, water/air temperatures
- **Z550iQ+** — HVAC modes (heat / cool / auto), presets, HVAC action (heating/cooling/idle/no-flow), water/air temperatures
- **Gre HPGIC** — on/off, target temperature, water temperature
- Generic heat-pump fallback

### 🧂 Salt Chlorinators / Electrolysers
- **tecnoLC2 family (30+ models)** — AstralPool Clear Connect / Clear Connect Evo / Scalable,
  Blauswim, IrriPool iSalt, KLINWASS Mark Salt, Zodiac OE iQ, Gre, Energy Connect, and more
  (`CC*` / `LC*` serials, including bridged `*.nn_*` devices)
- **Zodiac EXO iQ** (e.g. iQ35 / NS25) — 0–100% chlorination in 5% steps, output schedules
- **DM24049704** (Domotic S2) — program/slot schedule format
- Typical capabilities (model-dependent): chlorination level (0–100%), **pH setpoint**,
  **ORP/Redox setpoint**, boost mode, schedules, and sensors (pH, ORP, free chlorine,
  salinity, water temperature)

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
2. **Open an [issue](https://github.com/foXaCe/Fluidra-pool/issues)** with:
   - Your equipment model and serial prefix
   - The device-discovery debug logs
   - The features/values shown in the official Fluidra Pool app
3. **Test and share** your results — most new models are added this way.

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

### Options
- **Update interval** — polling interval in seconds, configurable from **30 to 1800**
  (default **30 s**). Change it via the integration's **Configure** button.

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
| `Authentication failed` | Wrong credentials or expired token → re-authenticate |
| `No pools found` | Account has no equipment, or it's offline in the Fluidra app |
| Device shows *unavailable* | The device reports itself offline to the Fluidra cloud |
| Commands seem ignored | Check debug logs; transient cloud rejections now surface as errors |

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
