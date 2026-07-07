from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path

from .client import ToucanBackendClient
from .config import ToucanClientConfig
from .dtos import LoadMeasurementRequestDto, ToucanCredentialsDto


def _parse_day(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%Y-%m-%d").date()


def _write_json(path: str, payload: object) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="OOP Toucan backend client CLI")
    parser.add_argument("--host", default="10.32.10.86")
    parser.add_argument("--port", type=int, default=17997)
    parser.add_argument("--login", default="AB")
    parser.add_argument("--password", default="AB")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--list-owners", action="store_true")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--list-work-types", action="store_true")
    parser.add_argument("--device-search", default="")
    parser.add_argument("--owner-id", type=int, default=None)
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--date", default="")
    parser.add_argument("--measure-id", type=int, default=0)
    parser.add_argument("--csv", default="")
    parser.add_argument("--devices-csv", default="")
    parser.add_argument("--owners-csv", default="")
    parser.add_argument("--directory-json", default="")
    parser.add_argument("--details-json", default="")
    parser.add_argument("--full-json", default="")
    parser.add_argument("--raw-bin", default="")
    args = parser.parse_args()

    config = ToucanClientConfig(host=args.host, port=args.port, timeout_seconds=args.timeout)
    creds = ToucanCredentialsDto(login=args.login, password=args.password)

    with ToucanBackendClient(config) as client:
        sid = client.login(creds)
        print(f"OK: logged in, SID={sid}")
        print(
            "Directory: "
            f"{len(client.list_owners())} owners, "
            f"{len(client.list_devices())} devices, "
            f"{len(client.list_work_types())} work types",
        )

        if args.directory_json:
            _write_json(args.directory_json, asdict(client.directory))
            print(f"Directory JSON written: {args.directory_json}")

        if args.owners_csv:
            client.export_owners_csv(args.owners_csv)
            print(f"Owners CSV written: {args.owners_csv}")
        if args.devices_csv:
            client.export_devices_csv(args.devices_csv)
            print(f"Devices CSV written: {args.devices_csv}")

        if args.list_owners:
            for owner in client.list_owners():
                print(asdict(owner))

        if args.list_work_types:
            for work_type in client.list_work_types():
                print(asdict(work_type))

        if args.list_devices or args.device_search:
            devices = client.search_devices(args.device_search, owner_id=args.owner_id, device_id=args.device_id)
            for device in devices[:200]:
                print(asdict(device))
            if not args.date and not args.measure_id:
                return 0

        if args.measure_id:
            if args.raw_bin:
                raw = client.measurement_service.load_raw_measurement(
                    LoadMeasurementRequestDto(measure_id=args.measure_id),
                )
                Path(args.raw_bin).write_bytes(raw)
                print(f"Raw measurement written: {args.raw_bin}")
            if args.details_json:
                details = client.load_measurement_details(LoadMeasurementRequestDto(measure_id=args.measure_id))
                _write_json(args.details_json, asdict(details))
                print(f"Details JSON written: {args.details_json}")
            if args.full_json:
                full = client.load_full_measurement(LoadMeasurementRequestDto(measure_id=args.measure_id))
                _write_json(args.full_json, asdict(full))
                print(f"Full measurement JSON written: {args.full_json}")
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
