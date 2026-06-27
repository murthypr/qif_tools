#!/usr/bin/env python3
"""
Split Transfer Scanner

Scans QIF files to identify split transactions that transfer money
between accounts. Outputs a JSON map for use by qif_sanitizer.py.

Salary transactions are excluded as GnuCash handles them properly.
"""

import os
import json
from pathlib import Path


def load_qif_file(qif_file):
    """Load QIF file with CP1252 encoding."""
    with open(qif_file, "rb") as f:
        lines = []
        for line in f:
            lines.append(line.decode("cp1252"))
    return "".join(lines)


def split_qif_transactions(qif_content):
    """Split QIF content into individual transactions on '^' lines."""
    lines = qif_content.splitlines()
    transactions = []
    current_transaction = []

    for line in lines:
        stripped = line.strip()
        if stripped == "^":
            if current_transaction:
                transactions.append(current_transaction)
            current_transaction = []
        else:
            current_transaction.append(line)

    if current_transaction:
        transactions.append(current_transaction)

    return transactions


def get_qif_files(input_dir):
    """Return sorted list of QIF files in input_dir."""
    input_path = Path(input_dir)
    qif_files = []
    for file_path in input_path.iterdir():
        if file_path.is_file() and file_path.suffix.lower() == ".qif":
            qif_files.append(str(file_path.absolute()))
    return sorted(qif_files)


def is_salary_transaction(transaction_lines):
    """Check if transaction is a salary payment (L-line contains Salary).

    Note: This function is duplicated from qif_sanitizer.py to keep the
    scanner script standalone. Any changes should be made in both places.
    """
    for line in transaction_lines:
        stripped = line.strip()
        if stripped.startswith("L") and "Salary" in stripped:
            return True
    return False


def scan_file_for_split_transfers(file_path):
    """
    Scan a single QIF file for split transfers.

    Returns list of dicts: [{"target": "Account", "amount": -100.00, "date": "7/ 1'15"}, ...]
    """
    qif_content = load_qif_file(file_path)
    transactions = split_qif_transactions(qif_content)
    split_transfers = []

    for txn in transactions:
        # Skip salary transactions - GnuCash handles these properly
        if is_salary_transaction(txn):
            continue

        # Extract date from transaction (D line)
        date = ""
        for line in txn:
            stripped = line.strip()
            if stripped.startswith("D"):
                date = stripped[1:].strip()
                break

        for i, line in enumerate(txn):
            stripped = line.strip()
            if stripped.startswith("S[") and "]" in stripped:
                # Extract target account from S[TargetAccount]
                target = stripped[2 : stripped.index("]")].strip()

                # Extract amount from next lines (look for $ line after S[...])
                for j in range(i + 1, len(txn)):
                    next_line = txn[j].strip()
                    if next_line.startswith("$"):
                        try:
                            amount_str = next_line[1:].replace(",", "")
                            amount = float(amount_str)
                            split_transfers.append(
                                {"target": target, "amount": amount, "date": date}
                            )
                        except ValueError:
                            pass
                        break
                    # Stop if we hit another S[...] or end of split section
                    if next_line.startswith("S[") or next_line == "":
                        break

    return split_transfers


def build_split_transfer_map(input_dir):
    """
    Scan all QIF files and build the split transfer map.

    Returns dict: {source_account: [{"target": ..., "amount": ...}, ...]}
    """
    qif_files = get_qif_files(input_dir)
    split_map = {}

    for qif_file in qif_files:
        account_name = Path(qif_file).stem
        transfers = scan_file_for_split_transfers(qif_file)
        if transfers:
            split_map[account_name] = transfers

    return split_map


def save_split_map(split_map, output_file):
    """Persist split transfer map to JSON file."""
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(split_map, f, indent=2, ensure_ascii=False)


def main():
    import sys

    # Load config to get input directory
    sys.path.insert(0, os.path.dirname(__file__))
    from qif_sanitizer import load_config

    config = load_config()
    input_dir = config.get("INPUT_DIR", "").strip()
    split_map_file = config.get("SPLIT_TRANSFER_MAP_FILE", "split_transfers.json")

    if not input_dir:
        print("ERROR: INPUT_DIR not configured in qif_sanitizer.config")
        sys.exit(1)

    print(f"Scanning QIF files in: {input_dir}")
    split_map = build_split_transfer_map(input_dir)

    # Count entries
    total_files = len(split_map)
    total_transfers = sum(len(v) for v in split_map.values())

    save_split_map(split_map, split_map_file)

    print(f"\nScan complete:")
    print(f"  Files with split transfers: {total_files}")
    print(f"  Total split transfers found: {total_transfers}")

    # Per-account breakdown
    print(f"\nPer-account breakdown:")
    for account, transfers in sorted(split_map.items()):
        # Count unique target accounts
        targets = {}
        for t in transfers:
            target = t["target"]
            if target not in targets:
                targets[target] = 0
            targets[target] += 1
        print(f"  {account}: {len(transfers)} split transfers")
        for target, count in sorted(targets.items()):
            print(f"    -> {target}: {count}")

    print(f"\n  Output: {split_map_file}")


if __name__ == "__main__":
    main()
