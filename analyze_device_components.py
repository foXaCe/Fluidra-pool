#!/usr/bin/env python3
"""
Analyse les composants d'un dispositif spécifique depuis les données capturées
"""

import json
import sys
from collections import defaultdict

def analyze_device_components(device_id="LG24350023"):
    """Analyse tous les composants d'un dispositif"""

    print(f"🔍 ANALYSE DU DISPOSITIF: {device_id}")
    print("=" * 50)

    device_data = {}
    components_found = set()

    try:
        with open('pool_api_capture.json', 'r', encoding='utf-8') as f:
            content = f.read()

        # Séparer les entrées JSON
        json_entries = content.split('---\n')

        for entry in json_entries:
            entry = entry.strip()
            if not entry:
                continue

            try:
                data = json.loads(entry)
                response_body = data.get('response', {}).get('response_body', {})

                # Recherche dans la structure du device tree
                if isinstance(response_body, dict):
                    analyze_device_tree(response_body, device_id, device_data, components_found)

            except json.JSONDecodeError:
                continue

    except FileNotFoundError:
        print("❌ Fichier pool_api_capture.json non trouvé")
        return

    # Afficher les résultats
    print(f"📋 INFORMATIONS DISPOSITIF:")
    if device_data:
        for key, value in device_data.items():
            if key != 'components':
                print(f"   • {key}: {value}")

    print(f"\n🔧 COMPOSANTS DÉTECTÉS:")
    if components_found:
        for comp_id in sorted(components_found):
            print(f"   • Component {comp_id}")
    else:
        print("   ❌ Aucun composant détecté")

    # Analyser les commandes capturées
    analyze_commands(device_id)

def analyze_device_tree(data, device_id, device_data, components_found):
    """Recherche récursive dans l'arbre des dispositifs"""

    if isinstance(data, dict):
        # Si c'est le dispositif recherché
        if data.get('id') == device_id or data.get('sn') == device_id:
            # Extraire les infos du dispositif
            device_data.update({
                'serial': data.get('sn'),
                'type': data.get('type'),
                'sku': data.get('sku'),
                'version': data.get('vr')
            })

            # Rechercher les composants
            if 'components' in data:
                for comp_id, comp_data in data['components'].items():
                    components_found.add(comp_id)
                    print(f"   🔍 Component {comp_id}: {comp_data.get('type', 'unknown')}")

        # Recherche récursive
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                analyze_device_tree(value, device_id, device_data, components_found)

    elif isinstance(data, list):
        for item in data:
            analyze_device_tree(item, device_id, device_data, components_found)

def analyze_commands(device_id):
    """Analyse les commandes PUT/POST pour ce dispositif"""

    print(f"\n🎛️  COMMANDES CAPTURÉES:")
    commands = defaultdict(list)

    try:
        with open('pool_api_capture.json', 'r', encoding='utf-8') as f:
            content = f.read()

        json_entries = content.split('---\n')

        for entry in json_entries:
            entry = entry.strip()
            if not entry:
                continue

            try:
                data = json.loads(entry)
                request = data.get('request', {})

                if (request.get('method') in ['PUT', 'POST'] and
                    device_id in request.get('url', '')):

                    path = request.get('path', '')
                    timestamp = data.get('request', {}).get('timestamp', '')
                    payload = request.get('request_body', {})

                    # Extraire le component ID du path
                    component_id = extract_component_id(path)

                    commands[component_id].append({
                        'timestamp': timestamp,
                        'path': path,
                        'payload': payload
                    })

            except json.JSONDecodeError:
                continue

        # Afficher les commandes par composant
        for comp_id, cmd_list in commands.items():
            print(f"\n   🔧 Component {comp_id}:")
            for cmd in cmd_list:
                time_short = cmd['timestamp'][-12:-7] if cmd['timestamp'] else "unknown"
                print(f"      • {time_short}: {cmd['path']}")
                if cmd['payload']:
                    payload_str = str(cmd['payload'])[:100]
                    print(f"        └─ Payload: {payload_str}...")

    except FileNotFoundError:
        print("   ❌ Aucune commande trouvée")

def extract_component_id(path):
    """Extrait l'ID du composant depuis le path"""
    try:
        parts = path.split('/')
        if 'components' in parts:
            comp_idx = parts.index('components') + 1
            if comp_idx < len(parts):
                return parts[comp_idx].split('?')[0]  # Enlever les query params
    except:
        pass
    return "unknown"

if __name__ == "__main__":
    device_id = sys.argv[1] if len(sys.argv) > 1 else "LG24350023"
    analyze_device_components(device_id)