[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Community Forum](https://img.shields.io/badge/Home_Assistant-Community-blue?logo=home-assistant)](https://community.home-assistant.io/t/custom-component-ajax-systems/948939/2)
[![Donate](https://img.shields.io/badge/Donate-PayPal-blue.svg)](https://www.paypal.com/paypalme/foXaCe66)

# Fluidra Pool Integration for Home Assistant 🏊‍♂️

A Home Assistant integration for Fluidra pool equipment control.

---

## 💰 Support the Project

If this integration is useful to you, you can support its development with a Bitcoin donation:

**🪙 Bitcoin Address:** `bc1qhe4ge22x0anuyeg0fmts6rdmz3t735dnqwt3p7`

Your contributions help me continue improving this project and adding new features. Thank you! 🙏

---

**🔬 Testing Status:**
- ✅ **E30iQ Pump**: Fully tested and functional
- ✅ **LumiPlus Connect**: RGBW lighting control tested
- ⚠️ **Other equipment** (heaters, etc.): Code implemented but **requires user testing**

## ✨ Features

### 🔄 **E30iQ Pump Control** ✅ **TESTED**
- **Multiple speeds**: Low (45%), Medium (65%), High (100%)
- **Automatic mode**: Smart management based on schedules
- **Manual control**: Custom speed and on/off control
- **Advanced scheduling**: Up to 8 time slots per day

### 📊 **Complete Sensors**
- **Pump information** ✅: Speed, mode, operating status
- **Schedules** ✅: Display of active and planned time slots
- **Device information** ✅: Firmware, network signal, diagnostics
- **Temperature** ⚠️: Sensors for heaters (current/target) - **NOT TESTED**
- **Lighting** ⚠️: Brightness of LED equipment - **NOT TESTED**

### ⚙️ **Home Assistant Entities**
- `switch`: Pump on/off and automatic mode
- `select`: Speed and operating mode selection
- `number`: Custom speed (0-100%)
- `time`: Schedule time configuration
- `sensor`: Complete equipment monitoring
- `light`: RGBW lighting control (LumiPlus Connect)

---

## 🔌 Supported Hardware

### ✅ **Tested and Functional Equipment**

#### **Variable Speed Pumps**
- **E30iQ** - Variable speed pump
  - 3-speed control (Low 45%, Medium 65%, High 100%)
  - Automatic mode with schedules
  - Custom speed control (0-100%)
  - Management of 8 time slots/day

#### **Salt Chlorinators / Electrolyzers**
- **Fluidra Chlorinators** (via connected bridge)
  - **Specific tested models**:
    - CC24021110 ✅
    - CC25113623 ✅
    - LC24008313 (Blauswim - I.D. Electroquimica/Fluidra) ✅
    - CC24033907 ✅
  - **Features**:
    - Chlorination level control (0-100%)
    - **pH Control**: Adjustable setpoint (6.8-7.6)
    - **ORP/Redox Control**: Adjustable setpoint (650-750 mV)
    - Boost mode (on/off)
    - Sensors: pH, ORP, free chlorine, water temperature, salinity
  - **Note**: Other Fluidra chlorinator models likely compatible

#### **Pool Lighting**
- **LumiPlus Connect** (76290_RGBW) ✅
  - On/off control
  - Brightness adjustment (0-100%)
  - RGBW color control
  - White channel support

### ⚠️ **Implemented Equipment (User Testing Required)**

#### **Heat Pumps**
- **LG Eco Elyo** - Reversible heat pump
  - Modes: Smart Heating, Smart Cooling, Boost, Silence
  - Temperature control (10-40°C)
  - Water temperature sensor

- **Z250iQ / Z25iQ** - Fluidra heat pump
  - On/off control
  - Target temperature adjustment
  - Current temperature sensor

#### **Heaters**
- Generic support for pool heaters
  - Temperature sensors (current/target)
  - On/off control

#### **Lighting**
- Generic support for LED pool lighting
  - On/off control
  - Brightness adjustment (0-100%)

### 🆕 **Adding New Equipment**

Your equipment is not listed? Help us add it!

1. **Enable debug logs**:
   ```yaml
   logger:
     logs:
       custom_components.fluidra_pool: debug
   ```

2. **Create an Issue** with:
   - Your equipment model
   - Detection logs (device discovery)
   - Features available in the Fluidra app

3. **Test and share** your results

---

## 🚀 Installation

### HACS Method (Recommended)

1. **Add the repository**
   ```
   https://github.com/foXaCe/Fluidra-pool
   ```

2. **Install the integration**
   - HACS → Integrations → Explore & Download → "Fluidra Pool"
   - Restart Home Assistant

3. **Configuration**
   - Configuration → Integrations → Add → "Fluidra Pool"
   - Enter your Fluidra Connect credentials

### Manual Installation

1. **Download files**
   ```bash
   git clone https://github.com/foXaCe/Fluidra-pool.git
   ```

2. **Copy the integration**
   ```bash
   cp -r custom_components/fluidra_pool /config/custom_components/
   ```

3. **Restart Home Assistant**

## ⚙️ Configuration

### Required Credentials
- **Email**: Your Fluidra Connect email
- **Password**: Your Fluidra Connect password

### Advanced Options
- **Update interval**: 30 seconds (default)
- **Timeout**: 10 seconds (default)

---

## 🎛️ Usage

### Pump Control

```yaml
# Automation example
automation:
  - alias: "Pool - Economy Mode"
    trigger:
      platform: time
      at: "22:00:00"
    action:
      service: select.select_option
      target:
        entity_id: select.pool_e30iq_pump_speed
      data:
        option: "Low"
```

### Advanced Scheduling

```yaml
# Schedule configuration via service
service: fluidra_pool.set_schedule
data:
  device_id: "LE24500883"
  schedules:
    - id: 1
      enabled: true
      startTime: "30 08 * * 1,2,3,4,5,6,7"
      endTime: "59 09 * * 1,2,3,4,5,6,7"
      startActions:
        operationName: "0"  # Low
```

### Lovelace Dashboard

```yaml
type: entities
title: Pool Control
entities:
  - entity: switch.pool_e30iq_pump
  - entity: select.pool_e30iq_pump_speed
  - entity: sensor.pool_e30iq_pump_schedules
  - entity: sensor.pool_e30iq_pump_information
```

## 🔧 Troubleshooting

### Connection Issues

1. **Check credentials**
   - Correct email and password
   - Active account on Fluidra Connect

2. **Diagnostic logs**
   ```yaml
   logger:
     logs:
       custom_components.fluidra_pool: debug
   ```

3. **Reconnect integration**
   - Remove integration
   - Restart Home Assistant
   - Reconfigure with new credentials

### Common Errors

| Error | Solution |
|-------|----------|
| `Authentication failed` | Check email/password |
| `No pools found` | Check Fluidra Connect configuration |
| `Device not responding` | Check equipment network connectivity |
| `Token expired` | Restart integration |

## 🧪 Testing and Contributing

### Current Testing Status
This integration was developed through **reverse engineering** of the Fluidra Connect API:

**✅ Tested equipment:**
- **E30iQ Pump**: Complete control (speeds, modes, scheduling)
- **LumiPlus Connect**: RGBW lighting control (on/off, brightness, color)

**⚠️ Untested equipment (help needed):**
- **Heaters**: Temperature sensors implemented but not tested
- **Other accessories**: Theoretical support only

### Help Needed for Testing
If you own other Fluidra equipment, your testing would be valuable!
- Create an [Issue](https://github.com/foXaCe/Fluidra-pool/issues) with your results
- Share debug logs
- Suggest improvements

## 🤝 Contributing

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/AmazingFeature`)
3. **Commit** your changes (`git commit -m 'Add AmazingFeature'`)
4. **Push** to the branch (`git push origin feature/AmazingFeature`)
5. **Open** a Pull Request

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Fluidra** for their innovative equipment
- **Home Assistant** for the amazing platform
- **The community** for testing and feedback

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/foXaCe/Fluidra-pool/issues)
- **Discussions**: [GitHub Discussions](https://github.com/foXaCe/Fluidra-pool/discussions)
- **Discord**: [Home Assistant Discord](https://discord.gg/home-assistant)

---

**⭐ If this integration is useful to you, feel free to leave a star!**
