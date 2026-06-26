from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import asdict

from .client import ToucanBackendClient
from .config import ToucanClientConfig
from .dtos import LoadMeasurementRequestDto, ToucanCredentialsDto


def _parse_day(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description="OOP Toucan backend client CLI")
    parser.add_argument("--host", default="10.32.10.86")
    parser.add_argument("--port", type=int, default=17997)
    parser.add_argument("--login", default="AB")
    parser.add_argument("--password", default="AB")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--list-owners", action="store_true")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--device-search", default="")
    parser.add_argument("--owner-id", type=int, default=None)
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--date", default="")
    parser.add_argument("--measure-id", type=int, default=0)
    parser.add_argument("--csv", default="")
    parser.add_argument("--devices-csv", default="")
    parser.add_argument("--owners-csv", default="")
    args = parser.parse_args()

    config = ToucanClientConfig(host=args.host, port=args.port, timeout_seconds=args.timeout)
    creds = ToucanCredentialsDto(login=args.login, password=args.password)

    with ToucanBackendClient(config) as client:
        sid = client.login(creds)
        print(f"OK: logged in, SID={sid}")
        print(f"Directory: {len(client.list_owners())} owners, {len(client.list_devices())} devices")

        if args.owners_csv:
            client.export_owners_csv(args.owners_csv)
            print(f"Owners CSV written: {args.owners_csv}")
        if args.devices_csv:
            client.export_devices_csv(args.devices_csv)
            print(f"Devices CSV written: {args.devices_csv}")

        if args.list_owners:
            for owner in client.list_owners():
                print(asdict(owner))

        if args.list_devices or args.device_search:
            devices = client.search_devices(args.device_search, owner_id=args.owner_id, device_id=args.device_id)
            for device in devices[:200]:
                print(asdict(device))
            if not args.date and not args.measure_id:
                return 0

        if args.measure_id:
            measurement = client.load_measurement(LoadMeasurementRequestDto(measure_id=args.measure_id))
            print(f"Decoded records={measurement.records_count}, rows={len(measurement.rows)}, range={measurement.start} -> {measurement.end}")
            if args.csv:
                result = client.measurement_service.export_measurement_csv(
                    request=LoadMeasurementRequestDto(measure_id=args.measure_id),
                    csv_path=args.csv,
                )
                print(asdict(result))
            return 0

        if args.device_id and args.date:
            result = client.export_latest_measurement_for_day_csv(
                device_id=args.device_id,
                owner_id=args.owner_id,
                day=_parse_day(args.date),
                csv_path=args.csv or f"toucan_{args.device_id}_{args.date}.csv",
            )
            print(asdict(result))
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
