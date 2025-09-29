# 🌡️ POMPE À CHALEUR FLUIDRA - MAPPING COMPLET

## 📋 Informations Générales
- **Modèle** : LG24350023
- **API Endpoint** : `https://api.fluidra-emea.com`
- **Type** : Pompe à chaleur connectée
- **Format commandes** : `PUT /generic/devices/{device_id}/components/{component_id}?deviceType=connected`

## 🔧 Components Identifiés

### Component 13 - Contrôle Principal (ON/OFF)
```http
PUT /generic/devices/LG24350023/components/13?deviceType=connected
Content-Type: application/json

{"desiredValue": 1}  # Allumer la pompe à chaleur
{"desiredValue": 0}  # Éteindre la pompe à chaleur
```

### Component 14 - Modes de Chauffage/Refroidissement
```http
PUT /generic/devices/LG24350023/components/14?deviceType=connected
Content-Type: application/json

{"desiredValue": 0}  # Smart Heating
{"desiredValue": 1}  # Smart Cooling
{"desiredValue": 2}  # Smart Heating Cooling
{"desiredValue": 3}  # Boost Heating
{"desiredValue": 4}  # Silence Heating
{"desiredValue": 5}  # Boost Cooling
```

## 🏠 Configuration Home Assistant

### Option 1 - Climate Entity
```yaml
climate:
  - platform: fluidra
    name: "Pompe à Chaleur Piscine"
    device_id: "LG24350023"
    power_component: 13
    mode_component: 14
    modes:
      "smart": 0           # Smart Heating
      "smart_cooling": 2   # Smart Heating Cooling
      "boost": 3          # Boost Heating
      "silence": 4        # Silence Heating
```

### Option 2 - Switch + Select
```yaml
switch:
  - platform: fluidra
    name: "Pompe à Chaleur"
    device_id: "LG24350023"
    component_id: 13

select:
  - platform: fluidra
    name: "Mode Chauffage"
    device_id: "LG24350023"
    component_id: 14
    options:
      - name: "Smart Heating"
        value: 0
      - name: "Smart Heating Cooling"
        value: 2
      - name: "Boost Heating"
        value: 3
      - name: "Silence Heating"
        value: 4
```

## 🚀 Commandes Exemple

### Activer en mode Boost
```bash
# 1. Allumer la pompe
curl -X PUT "https://api.fluidra-emea.com/generic/devices/LG24350023/components/13?deviceType=connected" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"desiredValue": 1}'

# 2. Sélectionner mode Boost
curl -X PUT "https://api.fluidra-emea.com/generic/devices/LG24350023/components/14?deviceType=connected" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"desiredValue": 3}'
```

### Passer en mode Silence
```bash
curl -X PUT "https://api.fluidra-emea.com/generic/devices/LG24350023/components/14?deviceType=connected" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"desiredValue": 4}'
```

## 📊 Séquences Capturées

### Test 1: Activation Smart Heating Cooling
- **Timestamp**: 2025-09-29 19:35:48
- **Commande**: `PUT .../components/14`
- **Payload**: `{"desiredValue": 2}`
- **Mode**: Smart Heating Cooling

### Test 2: Activation Boost Heating
- **Timestamp**: 2025-09-29 19:36:09
- **Commande**: `PUT .../components/14`
- **Payload**: `{"desiredValue": 3}`
- **Mode**: Boost Heating

### Test 3: Activation Smart Heating
- **Timestamp**: 2025-09-29 19:36:37
- **Commande**: `PUT .../components/14`
- **Payload**: `{"desiredValue": 0}`
- **Mode**: Smart Heating

### Test 4: Activation Silence Heating
- **Timestamp**: 2025-09-29 19:36:55
- **Commande**: `PUT .../components/14`
- **Payload**: `{"desiredValue": 4}`
- **Mode**: Silence Heating

## 🔐 Authentification
- **Type**: AWS Cognito JWT Token
- **Header**: `Authorization: Bearer {jwt_token}`
- **Endpoint Auth**: `https://cognito-idp.eu-west-1.amazonaws.com`

## ✅ Status Vérifié
- [x] Commandes ON/OFF fonctionnelles
- [x] 4 modes de chauffage identifiés
- [x] Mapping values → modes complet
- [x] API endpoints validés
- [x] Payloads JSON confirmés

---
*Mapping généré par analyse de trafic mitmproxy - 2025-09-29*