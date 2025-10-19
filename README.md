# Intégration Fluidra Pool pour Home Assistant 🏊‍♂️

🇫🇷 Français | **[🇺🇸 English](README_EN.md)**

Une intégration pour Home Assistant permettant de contrôler les équipements de piscine Fluidra.

---

## 💰 Soutenir le Projet

Si cette intégration vous est utile, vous pouvez soutenir son développement avec un don en Bitcoin :

**₿ Adresse Bitcoin :** `bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh`

Vos contributions m'aident à continuer d'améliorer ce projet et à ajouter de nouvelles fonctionnalités. Merci ! 🙏

---

**🔬 État des tests :**
- ✅ **Pompe E30iQ** : Entièrement testée et fonctionnelle
- ⚠️ **Autres équipements** (éclairage, chauffages, etc.) : Code implémenté mais **nécessite des tests utilisateurs**

## ✨ Fonctionnalités

### 🔄 **Contrôle Pompe E30iQ** ✅ **TESTÉ**
- **Vitesses multiples** : Faible (45%), Moyenne (65%), Élevée (100%)
- **Mode automatique** : Gestion intelligente basée sur les programmations
- **Contrôle manuel** : Vitesse personnalisée et marche/arrêt
- **Programmations avancées** : Jusqu'à 8 créneaux horaires par jour

### 📊 **Capteurs Complets**
- **Informations pompe** ✅ : Vitesse, mode, état de fonctionnement
- **Programmations** ✅ : Affichage des créneaux actifs et planifiés
- **Informations appareil** ✅ : Firmware, signal réseau, diagnostics
- **Température** ⚠️ : Capteurs pour chauffages (actuel/cible) - **NON TESTÉ**
- **Éclairage** ⚠️ : Luminosité des équipements LED - **NON TESTÉ**

### ⚙️ **Entités Home Assistant**
- `switch` : Marche/arrêt pompe et mode automatique
- `select` : Sélection vitesse et mode de fonctionnement
- `number` : Vitesse personnalisée (0-100%)
- `time` : Configuration horaires de programmation
- `sensor` : Surveillance complète des équipements

---

## 🔌 Matériel Supporté

### ✅ **Équipements Testés et Fonctionnels**

#### **Pompes à Vitesse Variable**
- **E30iQ** - Pompe à vitesse variable
  - Contrôle 3 vitesses (Faible 45%, Moyenne 65%, Élevée 100%)
  - Mode automatique avec programmations
  - Contrôle vitesse personnalisée (0-100%)
  - Gestion de 8 créneaux horaires/jour

#### **Électrolyseurs au Sel / Chlorinateurs**
- **Chlorinateurs Fluidra** (via bridge connecté)
  - **Modèles spécifiques testés** :
    - CC24021110 ✅
    - CC25113623 ✅
    - LC24008313 (Blauswim - I.D. Electroquimica/Fluidra) ✅
    - CC24033907 ✅
  - **Fonctionnalités** :
    - Contrôle niveau de chlorination (0-100%)
    - **Contrôle pH** : Setpoint réglable (6.8-7.6)
    - **Contrôle ORP/Redox** : Setpoint réglable (650-750 mV)
    - Mode Boost (marche/arrêt)
    - Capteurs : pH, ORP, chlore libre, température eau, salinité
  - **Note** : Autres modèles de chlorinateurs Fluidra probablement compatibles

### ⚠️ **Équipements Implémentés (Tests Utilisateurs Requis)**

#### **Pompes à Chaleur**
- **LG Eco Elyo** - Pompe à chaleur réversible
  - Modes : Smart Heating, Smart Cooling, Boost, Silence
  - Contrôle température (10-40°C)
  - Capteur température eau

- **Z250iQ / Z25iQ** - Pompe à chaleur Fluidra
  - Contrôle marche/arrêt
  - Réglage température cible
  - Capteur température actuelle

#### **Chauffages**
- Support générique pour chauffages piscine
  - Capteurs température (actuelle/cible)
  - Contrôle marche/arrêt

#### **Éclairage**
- Support générique pour éclairage LED piscine
  - Contrôle marche/arrêt
  - Réglage luminosité (0-100%)

### 🆕 **Ajouter un Nouvel Équipement**

Votre équipement n'est pas listé ? Aidez-nous à l'ajouter !

1. **Activez les logs debug** :
   ```yaml
   logger:
     logs:
       custom_components.fluidra_pool: debug
   ```

2. **Créez une Issue** avec :
   - Modèle de votre équipement
   - Logs de détection (device discovery)
   - Fonctionnalités disponibles dans l'app Fluidra

3. **Testez et partagez** vos résultats

---

## 🚀 Installation

### Méthode HACS (Recommandée)

1. **Ajouter le dépôt**
   ```
   https://github.com/foXaCe/Fluidra-pool
   ```

2. **Installer l'intégration**
   - HACS → Intégrations → Explorer et télécharger → "Fluidra Pool"
   - Redémarrer Home Assistant

3. **Configuration**
   - Configuration → Intégrations → Ajouter → "Fluidra Pool"
   - Entrer vos identifiants Fluidra Connect

### Installation Manuelle

1. **Télécharger les fichiers**
   ```bash
   git clone https://github.com/foXaCe/Fluidra-pool.git
   ```

2. **Copier l'intégration**
   ```bash
   cp -r custom_components/fluidra_pool /config/custom_components/
   ```

3. **Redémarrer Home Assistant**

## ⚙️ Configuration

### Identifiants Requis
- **Email** : Votre email Fluidra Connect
- **Mot de passe** : Votre mot de passe Fluidra Connect

### Options Avancées
- **Intervalle de mise à jour** : 30 secondes (par défaut)
- **Délai d'attente** : 10 secondes (par défaut)

---

## 🎛️ Utilisation

### Contrôle de la Pompe

```yaml
# Exemple d'automatisation
automation:
  - alias: "Piscine - Mode Économie"
    trigger:
      platform: time
      at: "22:00:00"
    action:
      service: select.select_option
      target:
        entity_id: select.pool_e30iq_pump_speed
      data:
        option: "Faible"
```

### Programmations Avancées

```yaml
# Configuration de programmation via service
service: fluidra_pool.set_schedule
data:
  device_id: "LE24500883"
  schedules:
    - id: 1
      enabled: true
      startTime: "30 08 * * 1,2,3,4,5,6,7"
      endTime: "59 09 * * 1,2,3,4,5,6,7"
      startActions:
        operationName: "0"  # Faible
```

### Tableau de Bord Lovelace

```yaml
type: entities
title: Contrôle Piscine
entities:
  - entity: switch.pool_e30iq_pump
  - entity: select.pool_e30iq_pump_speed
  - entity: sensor.pool_e30iq_pump_schedules
  - entity: sensor.pool_e30iq_pump_information
```

## 🔧 Dépannage

### Problèmes de Connexion

1. **Vérifier les identifiants**
   - Email et mot de passe corrects
   - Compte actif sur Fluidra Connect

2. **Journaux de diagnostic**
   ```yaml
   logger:
     logs:
       custom_components.fluidra_pool: debug
   ```

3. **Reconnecter l'intégration**
   - Supprimer l'intégration
   - Redémarrer Home Assistant
   - Reconfigurer avec de nouveaux identifiants

### Erreurs Courantes

| Erreur | Solution |
|--------|----------|
| `Authentication failed` | Vérifier email/mot de passe |
| `No pools found` | Vérifier la configuration Fluidra Connect |
| `Device not responding` | Vérifier la connectivité réseau de l'équipement |
| `Token expired` | Redémarrer l'intégration |

## 🧪 Tests et Contribution

### État Actuel des Tests
Cette intégration a été développée par **reverse engineering** de l'API Fluidra Connect :

**✅ Équipements testés :**
- **Pompe E30iQ** : Contrôle complet (vitesses, modes, programmations)

**⚠️ Équipements non testés (aide recherchée) :**
- **Éclairage LED** : Code implémenté mais non testé
- **Chauffages** : Capteurs température implémentés mais non testés
- **Autres accessoires** : Support théorique seulement

### Besoin d'aide pour les tests
Si vous possédez d'autres équipements Fluidra, vos tests seraient précieux !
- Créer une [Issue](https://github.com/foXaCe/Fluidra-pool/issues) avec vos résultats
- Partager les logs en mode debug
- Proposer des améliorations

## 🤝 Contribution

1. **Fork** le dépôt
2. **Créer** une branche de fonctionnalité (`git checkout -b feature/NouvelleFonctionnalite`)
3. **Commit** vos changements (`git commit -m 'Ajout NouvelleFonctionnalite'`)
4. **Push** vers la branche (`git push origin feature/NouvelleFonctionnalite`)
5. **Ouvrir** une Pull Request

### Développement Local

```bash
# Cloner le dépôt
git clone https://github.com/foXaCe/Fluidra-pool.git
cd Fluidra-pool

# Configuration environnement de test
cp custom_components/fluidra_pool /config/custom_components/

# Tests
python -m pytest tests/
```


## 📄 Licence

Ce projet est sous licence MIT. Voir le fichier [LICENSE](LICENSE) pour plus de détails.

## 🙏 Remerciements

- **Fluidra** pour leurs équipements innovants
- **Home Assistant** pour la plateforme fantastique
- **La communauté** pour les tests et retours

## 📞 Support

- **Issues** : [GitHub Issues](https://github.com/foXaCe/Fluidra-pool/issues)
- **Discussions** : [GitHub Discussions](https://github.com/foXaCe/Fluidra-pool/discussions)
- **Discord** : [Home Assistant Discord](https://discord.gg/home-assistant)

---

**⭐ Si cette intégration vous est utile, n'hésitez pas à laisser une étoile !**