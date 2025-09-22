# Fluidra Pool Integration for Home Assistant 🏊‍♂️

**[🇫🇷 Français](README.md)** | 🇺🇸 English

A Home Assistant integration for Fluidra pool equipment control.

**🔬 Testing Status:**
- ✅ **E30iQ Pump**: Fully tested and functional
- ⚠️ **Other equipment** (lighting, heaters, etc.): Code implemented but **requires user testing**

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

**⚠️ Untested equipment (help needed):**
- **LED Lighting**: Code implemented but not tested
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

### Local Development

```bash
# Clone the repository
git clone https://github.com/foXaCe/Fluidra-pool.git
cd Fluidra-pool

# Test environment setup
cp custom_components/fluidra_pool /config/custom_components/

# Tests
python -m pytest tests/
```


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