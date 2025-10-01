#!/usr/bin/env python3
"""Script de diagnostic direct pour les programmes E30iQ"""
import asyncio
import aiohttp
import logging

logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger(__name__)

# Base URL de l'API Fluidra EMEA
FLUIDRA_EMEA_BASE = "https://poolside-api.emea.fluidra.io/v1"

async def main():
    """Diagnostic des programmes"""

    # Vous devez remplacer ces valeurs par vos identifiants
    # ou le script les demandera
    USERNAME = input("Email Fluidra: ")
    PASSWORD = input("Mot de passe: ")

    async with aiohttp.ClientSession() as session:
        try:
            # 1. Authentification
            _LOGGER.info("🔐 Connexion...")
            auth_url = f"{FLUIDRA_EMEA_BASE}/auth/token"
            auth_payload = {
                "grant_type": "password",
                "username": USERNAME,
                "password": PASSWORD
            }

            async with session.post(auth_url, json=auth_payload) as response:
                if response.status != 200:
                    _LOGGER.error(f"❌ Échec authentification: {response.status}")
                    return

                auth_data = await response.json()
                access_token = auth_data.get("access_token")
                _LOGGER.info("✅ Authentifié")

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "com.fluidra.iaqualinkplus/1741857021"
            }

            # 2. Récupérer les pools
            _LOGGER.info("📋 Récupération des piscines...")
            pools_url = f"{FLUIDRA_EMEA_BASE}/generic/pools"

            async with session.get(pools_url, headers=headers) as response:
                if response.status != 200:
                    _LOGGER.error(f"❌ Erreur récupération pools: {response.status}")
                    return

                pools = await response.json()
                _LOGGER.info(f"✅ {len(pools)} piscine(s) trouvée(s)")

            # 3. Pour chaque pool, récupérer les devices
            for pool in pools:
                pool_id = pool.get("id")
                pool_name = pool.get("name", pool_id)
                _LOGGER.info(f"\n🏊 Piscine: {pool_name} (ID: {pool_id})")

                devices_url = f"{FLUIDRA_EMEA_BASE}/generic/devices"
                params = {"poolId": pool_id, "format": "tree"}

                async with session.get(devices_url, headers=headers, params=params) as response:
                    if response.status != 200:
                        _LOGGER.error(f"❌ Erreur récupération devices: {response.status}")
                        continue

                    devices = await response.json()
                    _LOGGER.info(f"  📱 {len(devices)} appareil(s) trouvé(s)")

                    # 4. Pour chaque device, vérifier le component 20 (programmes)
                    for device in devices:
                        device_id = device.get("id")
                        device_name = device.get("name", device_id)
                        device_type = device.get("family", "")

                        _LOGGER.info(f"\n  🔧 Appareil: {device_name}")
                        _LOGGER.info(f"     Type: {device_type}")
                        _LOGGER.info(f"     ID: {device_id}")

                        # Vérifier le component 20
                        comp_url = f"{FLUIDRA_EMEA_BASE}/generic/devices/{device_id}/components/20"
                        comp_params = {"deviceType": "connected"}

                        async with session.get(comp_url, headers=headers, params=comp_params) as comp_response:
                            if comp_response.status == 200:
                                comp_data = await comp_response.json()
                                reported_value = comp_data.get("reportedValue", [])

                                _LOGGER.info(f"     ✅ Component 20 (Programmes) trouvé")
                                _LOGGER.info(f"     📊 Nombre de programmes: {len(reported_value)}")

                                if len(reported_value) == 0:
                                    _LOGGER.warning(f"     ⚠️ AUCUN PROGRAMME CONFIGURÉ!")
                                else:
                                    for i, schedule in enumerate(reported_value, 1):
                                        schedule_id = schedule.get("id")
                                        enabled = schedule.get("enabled", False)
                                        start_time = schedule.get("startTime", "N/A")
                                        end_time = schedule.get("endTime", "N/A")
                                        status = "✅ ACTIF" if enabled else "❌ INACTIF"

                                        _LOGGER.info(f"     Programme {schedule_id}: {status}")
                                        _LOGGER.info(f"       Début: {start_time}")
                                        _LOGGER.info(f"       Fin:   {end_time}")

                                        if not start_time or start_time == "N/A":
                                            _LOGGER.warning(f"       ⚠️ PROBLÈME: Heure de début manquante!")
                                        if not end_time or end_time == "N/A":
                                            _LOGGER.warning(f"       ⚠️ PROBLÈME: Heure de fin manquante!")

                            elif comp_response.status == 404:
                                _LOGGER.warning(f"     ⚠️ Component 20 non trouvé (appareil sans programmes)")
                            else:
                                _LOGGER.error(f"     ❌ Erreur component 20: {comp_response.status}")

            _LOGGER.info("\n✅ Diagnostic terminé")

        except Exception as e:
            _LOGGER.error(f"❌ Erreur: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())