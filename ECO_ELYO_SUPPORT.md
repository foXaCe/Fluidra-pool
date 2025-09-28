# Support Astralpool Eco Elyo

## 🌡️ Détection automatique améliorée

L'intégration a été mise à jour pour détecter automatiquement les pompes à chaleur Astralpool Eco Elyo et créer les contrôles appropriés.

### ✅ Ce qui a été ajouté

1. **Détection intelligente** : L'Eco Elyo est maintenant détecté automatiquement basé sur :
   - ID du dispositif commençant par "LG" (ex: LG24350023)
   - Nom contenant "eco" et "elyo"
   - Modèle contenant "astralpool"
   - Composant 7 contenant "BXWAA*" (signature Eco Elyo)

2. **Switch dédié** : Un switch "Eco Elyo" remplace les 8 switches de scheduler inutiles

3. **Gestion d'état améliorée** : Utilise plusieurs sources de données pour l'état ON/OFF :
   - `heat_pump_reported` (priorité)
   - `pump_reported` (fallback)
   - `is_running` (base)
   - `is_heating` (compatibilité)

4. **Mode Auto** : Switch Auto disponible si supporté par l'équipement

### 🔄 Comportement

**AVANT (problématique) :**
- L'Eco Elyo était détecté comme "pump"
- Création de 8 switches de scheduler inutiles (1-8)
- Switch principal ne restait pas ON
- Mode Auto ne fonctionnait pas

**APRÈS (corrigé) :**
- L'Eco Elyo est détecté comme "heat_pump"
- Un seul switch "Eco Elyo" ON/OFF
- Switch Auto séparé si supporté
- Pas de switches de scheduler
- État ON/OFF persistant

### 🎯 Entités créées pour l'Eco Elyo

1. **`switch.piscine_eco_elyo`** - Contrôle ON/OFF principal
2. **`switch.piscine_eco_elyo_auto`** - Mode automatique (si disponible)

### 🛠️ Compatibilité

- ✅ **Équipements existants** : Aucun impact sur les pompes classiques
- ✅ **E30iQ Pumps** : Continuent à fonctionner normalement
- ✅ **Autres pompes à chaleur** : Détection élargie
- ✅ **Rétrocompatibilité** : Anciens systèmes non affectés

### 🔍 Tests effectués

- Détection basée sur l'ID `LG24350023`
- Détection par nom "eco elyo"
- Détection par modèle "astralpool"
- Détection par composant 7 "BXWAA*"
- Non-détection des pompes classiques

### 🚀 Installation

1. Remplacez le fichier `switch.py` dans votre installation
2. Redémarrez Home Assistant
3. L'Eco Elyo sera automatiquement détecté au prochain polling
4. Les anciens switches inutiles peuvent être supprimés manuellement

### 📋 Logs améliorés

Recherchez dans les logs :
```
🌡️ Detected Eco Elyo heat pump: LG24350023
```

### 💡 Note pour l'utilisateur

Votre Eco Elyo `LG24350023` sera maintenant correctement détecté et vous devriez voir :
- Un switch principal "Eco Elyo" qui reste ON quand activé
- Possiblement un switch "Auto" pour le mode automatique
- Plus de switches 1-8 qui ne fonctionnaient pas

Les composants détectés dans vos logs :
- Component 6: 77254 (données système)
- Component 7: BXWAA1402713924007 (signature Eco Elyo)
- Component 11: État running (utilisé pour le contrôle)
