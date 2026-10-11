"""Command-line presentation of deterministic transaction evidence."""
from __future__ import annotations
import argparse
import json
from ocpp_csms.transactions.analysis import analyze_transaction

def run_txn_explain(args):
    if args.transaction_id < 0:
        raise ValueError("transaction ID must be zero or greater")
    if args.context < 0 or args.context > 1440:
        raise ValueError("--context must be between 0 and 1440 minutes")
    result = analyze_transaction(args.data_dir, args.transaction_id, context_minutes=args.context)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(f"Transaction {result['transaction_id']} — {result['state']}")
    print(f"CP: {result['cp']}  C: {result['connector']}  RFID: {result['rfid'] or '-'}")
    print(f"Start: {result['start_at'] or ('time unknown' if result['start_recorded'] else 'not recorded')}")
    print(f"Stop:  {result['stop_at'] or ('time unknown' if result['stop_recorded'] else 'not recorded')}")
    print(f"Latest linked event: {result['last_event_at'] or 'not recorded'}")
    print(f"Meter batches: {result['meter_batches']}")
    for finding in result["findings"]:
        print(f"{finding['severity'].upper()} [{finding['certainty']}] {finding['message']}")
    if args.verbose:
        for event in result["events"]:
            print(f"  {event['at']} {event['direction']} {event['action']} #{event['id']}")
        for event in result["context"]:
            print(f"  context {event['at']} {event['kind']} {event['action']} #{event['id']}")
    print(result["disclaimer"])
    return 0

def parse_explain(argv, *, data_dir):
    parser = argparse.ArgumentParser(prog="ocpp-csms txn explain", description="Explain stored transaction evidence without contacting the charger")
    parser.add_argument("transaction_id", type=int)
    parser.add_argument("--context", type=int, default=0, metavar="MINUTES", help="Include surrounding charge-point evidence")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("-j", "--json", action="store_true")
    args = parser.parse_args(argv)
    args.data_dir = data_dir
    return args
