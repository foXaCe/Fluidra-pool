"""
Get full device info for CC24033907
"""
import json
from mitmproxy import http

def response(flow: http.HTTPFlow) -> None:
    """Capture GET on device to see all components."""

    if "CC24033907" in flow.request.pretty_url and flow.request.method == "GET":
        if "/devices/CC24033907" in flow.request.path and "components" not in flow.request.path:
            try:
                body = json.loads(flow.response.content)

                print(f"\n{'='*80}")
                print(f"DEVICE FULL INFO")
                print(f"{'='*80}")
                print(f"ID: {body.get('id')}")
                print(f"Name: {body.get('name')}")
                print(f"Family: {body.get('family')}")
                print(f"Model: {body.get('model')}")
                print(f"Type: {body.get('type')}")
                print(f"Manufacturer: {body.get('manufacturer')}")

                components = body.get('components', {})
                print(f"\nCOMPONENTS ({len(components)} total):")
                for comp_id in sorted(components.keys(), key=lambda x: int(x) if x.isdigit() else 999):
                    comp = components[comp_id]
                    reported = comp.get('reportedValue')
                    desired = comp.get('desiredValue')
                    print(f"  [{comp_id:3s}] reported={str(reported):10s} desired={str(desired):10s}")

                # Save to file
                with open("/tmp/CC24033907_full.json", "w") as f:
                    json.dump(body, f, indent=2)
                print(f"\n💾 Saved to /tmp/CC24033907_full.json")
                print(f"{'='*80}\n")

            except Exception as e:
                print(f"Error: {e}")
