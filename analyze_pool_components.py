#!/usr/bin/env python3
"""
Analyse des composants de piscine capturés via mitmproxy
"""

import json
import sys
from collections import defaultdict

def analyze_captured_data():
    """Analyse les données capturées et génère un rapport des composants"""

    components = defaultdict(list)
    endpoints = set()

    # Lire les composants trouvés
    try:
        with open('pool_components_found.json', 'r', encoding='utf-8') as f:
            content = f.read()

        # Séparer les entrées JSON
        json_entries = content.split('---\n')

        for entry in json_entries:
            entry = entry.strip()
            if not entry:
                continue

            try:
                data = json.loads(entry)
                endpoint = data.get('endpoint', '')
                endpoints.add(endpoint)

                for comp in data.get('components', []):
                    keyword = comp.get('keyword_found', '')
                    key = comp.get('key', '')
                    value = comp.get('value', '')
                    context = comp.get('context', '')

                    components[keyword].append({
                        'endpoint': endpoint,
                        'key': key,
                        'value': value,
                        'context': context
                    })

            except json.JSONDecodeError:
                continue

    except FileNotFoundError:
        print("❌ Fichier pool_components_found.json non trouvé")
        return

    # Générer le rapport
    print("🏊 ANALYSE DES COMPOSANTS DE PISCINE FLUIDRA")
    print("=" * 50)

    print(f"\n📡 ENDPOINTS DÉCOUVERTS ({len(endpoints)}):")
    for endpoint in sorted(endpoints):
        print(f"   • {endpoint}")

    print(f"\n🔧 COMPOSANTS PAR CATÉGORIE:")

    # Organiser par catégories
    categories = {
        'POMPES & FILTRATION': ['pump', 'filtration', 'pressure', 'flow', 'speed'],
        'QUALITÉ DE L\'EAU': ['ph', 'chlorine', 'orp', 'sensor'],
        'TEMPÉRATURE': ['temperature', 'heater', 'chauffage'],
        'ÉCLAIRAGE': ['light', 'lumière', 'éclairage'],
        'DISPOSITIFS': ['device', 'appareil'],
        'PROGRAMMATION': ['schedule', 'timer', 'minuteur'],
        'MODES & ÉTATS': ['mode', 'status', 'état'],
        'PISCINE': ['pool', 'piscine', 'spa']
    }

    for category, keywords in categories.items():
        found_in_category = []
        for keyword in keywords:
            if keyword in components:
                found_in_category.extend(components[keyword])

        if found_in_category:
            print(f"\n🎯 {category}:")
            # Dédupliquer par clé
            unique_keys = {}
            for item in found_in_category:
                key = item['key']
                if key not in unique_keys:
                    unique_keys[key] = item

            for key, item in unique_keys.items():
                context_info = f" ({item['context']})" if item['context'] else ""
                print(f"   • {key}{context_info}")
                if isinstance(item['value'], (dict, list)):
                    print(f"     └─ Type: {type(item['value']).__name__}")
                else:
                    value_str = str(item['value'])[:100]
                    if len(str(item['value'])) > 100:
                        value_str += "..."
                    print(f"     └─ Valeur: {value_str}")

    # Recommandations pour Home Assistant
    print(f"\n🏠 RECOMMANDATIONS HOME ASSISTANT:")
    print("   • sensor: waterTemperature, pH, orp, chlorine, pressure")
    print("   • switch: pump modes, éclairage")
    print("   • number: speed levels, température consigne")
    print("   • select: modes de fonctionnement, programmes")

    return components, endpoints

if __name__ == "__main__":
    analyze_captured_data()