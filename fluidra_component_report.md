# 🏊 Rapport d'Analyse - Composants Piscine Fluidra

## 📡 Configuration Capture
- **Proxy** : mitmproxy sur `192.168.1.180:8080`
- **API Base** : `https://api.fluidra-emea.com`
- **Authentification** : AWS Cognito
- **Format** : REST API JSON

## 🎯 Piscines Détectées
1. **Pool 1** : `99057ccd-d59c-5db8-93de-bde8fe7166cf`
2. **Pool 2** : `ff464217-2948-58a7-9177-d1a62e7a2363`

## 📋 Endpoints API Découverts

### 🔍 Lecture (GET)
```
/mobile/consumers/me                    # Infos utilisateur
/generic/users/me                       # Profil utilisateur
/generic/users/me/pools                 # Liste des piscines
/generic/pools/{pool_id}                # Configuration piscine
/generic/pools/{pool_id}/status         # État temps réel
/generic/devices?poolId={id}&format=tree # Structure complète équipements
/generic/pools/{pool_id}/telemetry      # Données historiques
```

### ⚡ Contrôle (PUT/POST)
```
PUT /generic/devices/{device_id}/components/{component_id}?deviceType=connected
```

## 🏠 Équipements Identifiés

### 🌡️ POMPE À CHALEUR
- **Device ID** : `LG24350023`
- **Component ID** : `13`
- **Commandes** :
  - ON : `PUT /generic/devices/LG24350023/components/13`
  - OFF : `PUT /generic/devices/LG24350023/components/13`
- **Type Home Assistant** : `climate` ou `water_heater`

### 💧 QUALITÉ DE L'EAU
- **pH** : Sensor + régulation automatique
- **Chlore** : total, combiné, libre
- **ORP** : Potentiel redox
- **Température eau** : Capteur temps réel
- **Phosphates** : Mesure
- **Type Home Assistant** : `sensor`

### ⚙️ POMPE DE FILTRATION
- **Vitesse** : Contrôle variable (détecté dans status)
- **Pression** : Monitoring système
- **Modes** : Divers niveaux de vitesse
- **Type Home Assistant** : `fan` avec vitesse variable

### 🔧 PROGRAMMATION
- **Schedulers** : Système de programmation intégré
- **Technologies** : Cloud + local
- **Type Home Assistant** : `schedule` / `automation`

## 📊 Structures de Données

### Pool Status Response
```json
{
  "weather": {
    "status": "ok",
    "value": {
      "hourly": [{
        "main": { "pressure": 1018 },
        "wind": { "speed": 5.09 }
      }]
    }
  }
}
```

### Device Tree Response
```json
{
  "id": "LG24350023",
  "sn": "LG24350023",
  "info": {
    "configuration": {
      "capabilities": {
        "schedulers": [{
          "id": "pump",
          "type": "minimal",
          "enabled": true
        }]
      }
    }
  }
}
```

## 🔌 Recommandations Home Assistant

### Configuration YAML
```yaml
# Pompe à chaleur
water_heater:
  - platform: fluidra
    device_id: LG24350023
    component_id: 13
    name: "Pompe à Chaleur Piscine"

# Capteurs qualité eau
sensor:
  - platform: fluidra
    sensors:
      - ph
      - orp
      - chlorine_total
      - chlorine_free
      - water_temperature
      - phosphate

# Pompe de filtration
fan:
  - platform: fluidra
    name: "Pompe Filtration"
    speed_levels: 5
```

## 🚀 Prochaines Étapes
1. ✅ Capturer plus d'équipements (éclairage, autres pompes)
2. ⏳ Analyser les payloads exacts des commandes
3. ⏳ Développer l'intégration Home Assistant
4. ⏳ Implémenter l'authentification OAuth

## 📝 Notes Techniques
- **Authentification** : Token JWT via AWS Cognito
- **Rate Limiting** : À tester
- **WebSocket** : Pas détecté, polling HTTP
- **SSL** : HTTPS obligatoire
- **Format** : JSON REST standard