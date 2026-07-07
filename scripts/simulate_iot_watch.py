import argparse
import random
import time
from datetime import datetime, timezone


SCENARIOS = {
    "red": {
        "bpm": (120, 135),
        "o2sat": (89, 94),
        "bt": (37.6, 39.3),
        "rr": (31, 36),
        "sys_bp": (82, 95),
        "dia_bp": (55, 65),
    },
    "yellow": {
        "bpm": (100, 122),
        "o2sat": (95, 96),
        "bt": (38.0, 38.8),
        "rr": (21, 30),
        "sys_bp": (96, 135),
        "dia_bp": (65, 88),
    },
    "green": {
        "bpm": (75, 95),
        "o2sat": (97, 99),
        "bt": (36.5, 37.8),
        "rr": (16, 20),
        "sys_bp": (110, 125),
        "dia_bp": (70, 82),
    },
}


def random_vitals(scenario):
    ranges = SCENARIOS[scenario]
    return {
        "bpm": random.randint(*ranges["bpm"]),
        "o2sat": random.randint(*ranges["o2sat"]),
        "bt": round(random.uniform(*ranges["bt"]), 1),
        "rr": random.randint(*ranges["rr"]),
        "sys_bp": random.randint(*ranges["sys_bp"]),
        "dia_bp": random.randint(*ranges["dia_bp"]),
    }


def build_payload(args):
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "vitals": random_vitals(args.scenario),
    }
    if args.visit_id is not None:
        payload["visit_id"] = args.visit_id
    return payload


def post_payload(args, payload):
    try:
        import requests
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: requests. Install it with `pip install requests`."
        ) from exc

    headers = {
        "Content-Type": "application/json",
        "X-DEVICE-ID": args.device_id,
        "X-API-KEY": args.api_key,
    }
    return requests.post(args.url, json=payload, headers=headers, timeout=10)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Simulate an IoT watch sending vital signs to the hospital queue API."
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000/api/iot/telemetry/",
        help="Telemetry API URL.",
    )
    parser.add_argument("--device-id", required=True, help="IoT device ID.")
    parser.add_argument("--api-key", required=True, help="IoT device API key.")
    parser.add_argument("--visit-id", type=int, help="Optional visit ID.")
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS.keys()),
        default="green",
        help="Vital-sign scenario to simulate.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5,
        help="Seconds between POST requests.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Number of requests to send. Use 0 to keep sending forever.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    sent = 0

    try:
        while args.count == 0 or sent < args.count:
            payload = build_payload(args)
            try:
                response = post_payload(args, payload)
                print(f"status_code: {response.status_code}")
                print(f"payload: {payload}")
                print(f"response: {response.text}")
                print("-" * 60)
            except Exception as exc:
                print(f"request_error: {exc}")
                print(f"payload: {payload}")
                print("-" * 60)

            sent += 1
            if args.count == 0 or sent < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped by Ctrl+C.")


if __name__ == "__main__":
    main()
