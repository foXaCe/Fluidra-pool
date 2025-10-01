#!/usr/bin/env python3
"""Script de diagnostic pour vérifier les programmes de l'E30iQ"""
import asyncio
import sys
import logging
from custom_components.fluidra_pool.fluidra_api import FluidraAPI

logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger(__name__)

async def main():
    """Test de récupération des programmes"""
    # Utiliser les identifiants stockés
    api = FluidraAPI()

    try:
        # Authentification
        _LOGGER.info("🔐 Connexion à l'API Fluidra...")
        success = await api.authenticate()
        if not success:
            _LOGGER.error("❌ Échec de l'authentification")
            return

        _LOGGER.info("✅ Authentification réussie")

        # Récupérer les pools
        pools = await api.get_pools()
        _LOGGER.info(f"📋 {len(pools)} piscine(s) trouvée(s)")

        # Pour chaque appareil E30iQ
        for pool in pools:
            pool_id = pool.get("id")
            _LOGGER.info(f"\n🏊 Piscine: {pool.get('name', pool_id)}")

            for device in pool.get("devices", []):
                device_id = device.get("device_id")
                device_name = device.get("name", device_id)
                device_type = device.get("type", "")

                # Filtrer uniquement les pompes E30iQ
                if "pump" in device_type.lower() and device_type != "heat pump":
                    _LOGGER.info(f"\n  📱 Appareil: {device_name} ({device_id})")

                    # Vérifier si les données de programme sont présentes
                    schedule_data = device.get("schedule_data")
                    if schedule_data is None:
                        _LOGGER.warning(f"    ⚠️ PROBLÈME: Aucune donnée 'schedule_data' trouvée")
                        _LOGGER.info(f"    🔍 Tentative de récupération directe du component 20...")

                        # Essayer de récupérer directement le component 20
                        comp_state = await api.get_device_component_state(device_id, 20)
                        if comp_state:
                            _LOGGER.info(f"    ✅ Component 20 récupéré: {comp_state}")
                            reported_value = comp_state.get("reportedValue", [])
                            _LOGGER.info(f"    📊 Nombre de programmes: {len(reported_value)}")

                            for i, schedule in enumerate(reported_value, 1):
                                enabled = schedule.get("enabled", False)
                                start_time = schedule.get("startTime", "")
                                end_time = schedule.get("endTime", "")
                                status = "✅ Actif" if enabled else "❌ Inactif"
                                _LOGGER.info(f"      Programme {i} {status}: {start_time} → {end_time}")
                        else:
                            _LOGGER.error(f"    ❌ Impossible de récupérer le component 20")
                    else:
                        _LOGGER.info(f"    ✅ {len(schedule_data)} programme(s) trouvé(s)")

                        # Afficher les détails de chaque programme
                        for schedule in schedule_data:
                            schedule_id = schedule.get("id")
                            enabled = schedule.get("enabled", False)
                            start_time = schedule.get("startTime", "")
                            end_time = schedule.get("endTime", "")
                            status = "✅ Actif" if enabled else "❌ Inactif"
                            _LOGGER.info(f"      Programme {schedule_id} {status}: {start_time} → {end_time}")

        _LOGGER.info("\n✅ Diagnostic terminé")

    except Exception as e:
        _LOGGER.error(f"❌ Erreur: {e}", exc_info=True)
    finally:
        await api.close()

if __name__ == "__main__":
    asyncio.run(main())