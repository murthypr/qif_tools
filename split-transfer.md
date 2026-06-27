# QIFTools Enhancement: Split Transfer Suppression

## Problem Statement

### Symptom
When a split transaction in one account transfers money to another account,
both the split and the corresponding transfer are imported into GnuCash,
causing double-counting. This enhancement handles that by prescanning all the
QIF files, generating a split-transfer map as JSON, using that map during
sanitizing, and suppressing the duplicate transfers.

### Example

**Checking account split (Checking - CapitalOne - Regular.QIF):**
D7/ 4'15
U-104.59
T-104.59
CX
NEFT
PVons
LGroceries
SGroceries
$-4.59
S[Cash - Ram]
ECash With Drawal
$-100.00
^

**Cash account transfer (Cash - Ram.QIF):**
D7/ 4'15
U100.00
T100.00
PVons
MCash With Drawal
LChecking - CapitalOne - Regular
^

### Root Cause

1. Cash files are processed BEFORE Checking files (alphabetical order)
2. When processing Cash, Checking is NOT yet in `processed_accounts`
3. So `L[Checking - CapitalOne - Regular]` is NOT suppressed
4. Later, Checking is processed, and the split `S[Cash - Ram]` is imported
5. Result: Both imported ? $100 double-counted

### Current Logic Gap

`is_transfer_to_processed_account()` only suppresses transfers from
ALREADY-PROCESSED accounts. Since Cash comes before Checking alphabetically,
the transfer is never suppressed.

---

## Desired Behavior

- Suppress transfers in target account ONLY if they correspond to splits
  in the source account
- Keep existing transfer suppression logic for non-split transfers
- **Exclude salary payments** - salary splits (e.g., LSalary) should NOT be suppressed as GnuCash handles them properly

---

## Solution: Two-Phase Approach

### Phase 1: Split Transfer Scanner (New Script)

**File:** `split-transfer-scan.py`

**Purpose:** Scan all QIF files once, build a map of split transfers,
persist to `split_transfers.json`.

**Logic:**

1. Scan all `.qif`/`.QIF` files in `quicken_export_files/`
2. For each file, split into transactions (on `^` lines)
3. For each transaction:
   - Detect `S[AccountName]` lines � these are transfers within splits
   - Extract the `$` amount on the line immediately following the `S[...]`
   - Record: source_account, target_account, amount
4. Persist the map to `split_transfers.json`

**Exclusion Rules:**
- **Salary transactions** (L-line contains "Salary") are EXCLUDED from split transfer scanning
- These transfers are handled properly by GnuCash and should not be suppressed

**Extraction Rules:**

| Field | Source | Example |
|-------|--------|---------|
| source_account | QIF filename (without extension) | `Checking - CapitalOne - Regular` |
| target_account | Content inside `S[...]` | `Cash - Ram` |
| amount | `$` line immediately after `S[...]` | `-100.00` |

**Output Format (`split_transfers.json`):**

```json
{
  "Checking - CapitalOne - Regular": [
    {"target": "Cash - Ram", "amount": -100.00},
    {"target": "Cash - Ram", "amount": -50.00},
    {"target": "Savings - ING Ram", "amount": -500.00}
  ],
  "Rentals - Checking": [
    {"target": "Home - Alma Loan", "amount": -390.83},
    {"target": "Home - Maitland Loan", "amount": -1109.17}
  ]
}
Script Structure:
#!/usr/bin/env python3
"""
Split Transfer Scanner

Scans QIF files to identify split transactions that transfer money
between accounts. Outputs a JSON map for use by qif_sanitizer.py.
"""

import os
import json
from pathlib import Path

def get_qif_files(input_dir):
    """Return sorted list of QIF files in input_dir."""
    # Reuse logic from qif_sanitizer.py

def split_qif_transactions(qif_content):
    """Split QIF content into individual transactions on '^' lines."""
    # Reuse logic from qif_sanitizer.py

def load_qif_file(qif_file):
    """Load QIF file with CP1252 encoding."""
    # Reuse logic from qif_sanitizer.py

def extract_account_name(file_path):
    """Extract account name from QIF filename."""
    return Path(file_path).stem

def is_salary_transaction(transaction_lines):
    """Check if transaction is a salary payment (L-line contains Salary)."""
    for line in transaction_lines:
        stripped = line.strip()
        if stripped.startswith('L') and 'Salary' in stripped:
            return True
    return False

def scan_file_for_split_transfers(file_path):
    """
    Scan a single QIF file for split transfers.
    
    Returns list of dicts: [{"target": "Account", "amount": -100.00}, ...]
    """
    qif_content = load_qif_file(file_path)
    transactions = split_qif_transactions(qif_content)
    split_transfers = []
    
    for txn in transactions:
        # Skip salary transactions - GnuCash handles these properly
        if is_salary_transaction(txn):
            continue
            
        for i, line in enumerate(txn):
            stripped = line.strip()
            if stripped.startswith('S[') and ']' in stripped:
                # Extract target account from S[TargetAccount]
                target = stripped[2:stripped.index(']')].strip()
                
                # Extract amount from next line (should start with $)
                if i + 1 < len(txn):
                    next_line = txn[i + 1].strip()
                    if next_line.startswith('$'):
                        try:
                            amount_str = next_line[1:].replace(',', '')
                            amount = float(amount_str)
                            split_transfers.append({
                                "target": target,
                                "amount": amount
                            })
                        except ValueError:
                            pass
    
    return split_transfers

def build_split_transfer_map(input_dir):
    """
    Scan all QIF files and build the split transfer map.
    
    Returns dict: {source_account: [{"target": ..., "amount": ...}, ...]}
    """
    qif_files = get_qif_files(input_dir)
    split_map = {}
    
    for qif_file in qif_files:
        account_name = extract_account_name(qif_file)
        transfers = scan_file_for_split_transfers(qif_file)
        if transfers:
            split_map[account_name] = transfers
    
    return split_map

def save_split_map(split_map, output_file="split_transfers.json"):
    """Persist split transfer map to JSON file."""
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(split_map, f, indent=2, ensure_ascii=False)

def main():
    import sys
    
    # Load config to get input directory
    from qif_sanitizer import load_config
    config = load_config()
    input_dir = config.get('INPUT_DIR', '').strip()
    
    if not input_dir:
        print("ERROR: INPUT_DIR not configured in qif_sanitizer.config")
        sys.exit(1)
    
    print(f"Scanning QIF files in: {input_dir}")
    split_map = build_split_transfer_map(input_dir)
    
    # Count entries
    total_files = len(split_map)
    total_transfers = sum(len(v) for v in split_map.values())
    
    save_split_map(split_map)
    
    print(f"\nScan complete:")
    print(f"  Files with split transfers: {total_files}")
    print(f"  Total split transfers found: {total_transfers}")
    print(f"  Output: split_transfers.json")

if __name__ == "__main__":
    main()
CLI Usage:
python split-transfer-scan.py
Phase 2: Modify qif_sanitizer.py
Changes Required:
1. New Function: is_split_transfer_transaction()
def is_split_transfer_transaction(transaction_lines, source_account,
                                   current_account, split_transfer_map):
    """
    Check if a transfer corresponds to a split transfer from source_account
    to current_account with a matching amount.
    
    Args:
        transaction_lines: List of lines in the transaction
        source_account: Account extracted from L[SourceAccount]
        current_account: Account being processed (from filename)
        split_transfer_map: Dict loaded from split_transfers.json
        
    Returns:
        True if this is a split transfer that should be suppressed
    """
    if not split_transfer_map or not source_account:
        return False
    
    # Look up source_account in the split transfer map
    transfers = split_transfer_map.get(source_account, [])
    if not transfers:
        return False
    
    # Extract the transfer amount from the transaction
    txn_amount = None
    for line in transaction_lines:
        stripped = line.strip()
        if stripped.startswith('$'):
            try:
                amount_str = stripped[1:].replace(',', '')
                txn_amount = float(amount_str)
                break
            except ValueError:
                continue
    
    if txn_amount is None:
        return False
    
    # Check for matching split transfer
    for transfer in transfers:
        if (transfer["target"] == current_account and
            transfer["amount"] == txn_amount):
            return True
    
    return False
2. Modify apply_mappings_to_qif()
Add new parameter and suppression check:
def apply_mappings_to_qif(qif_content, mappings, security_suffixes=None,
                           processed_accounts=None, split_transfer_map=None,
                           current_account=None):
    # ... existing code ...
    
    for txn in transactions:
        # Phase 1: NSellX normalization
        processed_lines, memo_updated = apply_mappings_to_transaction(
            txn, mappings, replacement_counts, security_suffixes, suffix_counts
        )
        
        # Phase 2a: NEW - Check for split transfer suppression
        if split_transfer_map and current_account:
            source_account = extract_transfer_target_from_lines(processed_lines)
            if source_account and is_split_transfer_transaction(
                processed_lines, source_account, current_account, split_transfer_map
            ):
                skipped_transfers += 1
                continue
        
        # Phase 2b: existing - Check for processed account transfer
        if is_transfer_to_processed_account(processed_lines, processed_accounts):
            skipped_transfers += 1
            continue
        
        # ... rest of existing code ...
3. Add Helper: extract_transfer_target_from_lines()
def extract_transfer_target_from_lines(transaction_lines):
    """Extract transfer target from L[...] line in transaction."""
    for line in transaction_lines:
        if line and line.startswith('L'):
            return extract_transfer_target(line)
    return None
4. Modify process_file()
Add new parameters:
def process_file(input_path, output_path, mappings, security_suffixes=None,
                 processed_accounts=None, split_transfer_map=None):
    # ... existing code ...
    
    current_account = get_account_name_from_filename(input_path)
    
    sanitized_content, replacement_counts, tag_insert_count, \
        transactions_processed, suffix_counts, skipped_transfers = \
        apply_mappings_to_qif(
            qif_content, mappings, security_suffixes, processed_accounts,
            split_transfer_map=split_transfer_map,
            current_account=current_account
        )
    
    # ... rest of existing code ...
5. Modify main()
Load split transfer map and pass through:
def main():
    # ... existing config loading ...
    
    # NEW: Load split transfer map
    split_transfer_map = {}
    split_map_file = "split_transfers.json"
    if os.path.exists(split_map_file):
        with open(split_map_file, 'r', encoding='utf-8') as f:
            split_transfer_map = json.load(f)
        logger.info(f"Loaded split transfer map: {len(split_transfer_map)} accounts")
    else:
        logger.info("No split_transfers.json found; split transfer suppression disabled")
    
    # ... in the processing loop ...
    stats = process_file(
        input_path, output_path, mappings, security_suffixes, accounts_processed,
        split_transfer_map=split_transfer_map
    )
    
    # ... rest of existing code ...
Phase 3: Update Tests
File: tests/test_nsellx_transfer.py
Add new test class:
import unittest
import json
import tempfile
import os
from qif_sanitizer import (
    apply_mappings_to_qif,
    is_split_transfer_transaction
)

class TestSplitTransferSuppression(unittest.TestCase):
    
    def test_split_transfer_detected(self):
        """Transfer in Cash should be suppressed when split exists in Checking."""
        split_map = {
            "Checking - CapitalOne - Regular": [
                {"target": "Cash - Ram", "amount": -100.00}
            ]
        }
        
        # Transfer in Cash account
        qif_content = '\n'.join([
            'D7/ 4\'15',
            'U100.00',
            'T100.00',
            'PVons',
            'MCash With Drawal',
            'L[Checking - CapitalOne - Regular]'
        ]) + '\n^'
        
        result, _, _, transactions_processed, _, skipped = apply_mappings_to_qif(
            qif_content, {}, None, [], 
            split_transfer_map=split_map,
            current_account="Cash - Ram"
        )
        
        # Should be suppressed
        self.assertEqual(skipped, 1)
        self.assertEqual(transactions_processed, 0)
    
    def test_non_split_transfer_not_suppressed(self):
        """Regular transfer (not from a split) should NOT be suppressed."""
        split_map = {
            "Checking - CapitalOne - Regular": [
                {"target": "Cash - Ram", "amount": -100.00}
            ]
        }
        
        # Transfer with different amount
        qif_content = '\n'.join([
            'D7/ 4\'15',
            'U200.00',
            'T200.00',
            'PVons',
            'MCash With Drawal',
            'L[Checking - CapitalOne - Regular]'
        ]) + '\n^'
        
        result, _, _, transactions_processed, _, skipped = apply_mappings_to_qif(
            qif_content, {}, None, [],
            split_transfer_map=split_map,
            current_account="Cash - Ram"
        )
        
        # Should NOT be suppressed (amount mismatch)
        self.assertEqual(skipped, 0)
        self.assertEqual(transactions_processed, 1)
    
    def test_no_split_map_no_suppression(self):
        """Without split map, no split transfer suppression."""
        qif_content = '\n'.join([
            'D7/ 4\'15',
            'U100.00',
            'T100.00',
            'PVons',
            'MCash With Drawal',
            'L[Checking - CapitalOne - Regular]'
        ]) + '\n^'
        
        result, _, _, transactions_processed, _, skipped = apply_mappings_to_qif(
            qif_content, {}, None, [],
            split_transfer_map=None,
            current_account="Cash - Ram"
        )
        
        # Should NOT be suppressed (no map)
        self.assertEqual(skipped, 0)
        self.assertEqual(transactions_processed, 1)

    def test_salary_transfer_not_suppressed(self):
        """Salary splits should NOT be suppressed (GnuCash handles them properly)."""
        split_map = {
            "Checking - CapitalOne - Regular": [
                {"target": "401(K) - eBay - Fidelity", "amount": -2157.69}
            ]
        }
        
        # Salary transaction with LSalary
        qif_content = '\n'.join([
            'D8/16\'19',
            'U2,106.22',
            'T2,106.22',
            'CX',
            'NDEP',
            'PeBay',
            'MPTO: -11.08',
            'LSalary',
            'SSalary',
            'ESalary',
            '$7,192.31',
            'S[401(K) - eBay - Fidelity]',
            'EEmployee Contribution Transfer',
            '$-2,157.69'
        ]) + '\n^'
        
        result, _, _, transactions_processed, _, skipped = apply_mappings_to_qif(
            qif_content, {}, None, [],
            split_transfer_map=split_map,
            current_account="Checking - CapitalOne - Regular"
        )
        
        # Should NOT be suppressed (salary transaction)
        self.assertEqual(skipped, 0)
        self.assertEqual(transactions_processed, 1)

if __name__ == '__main__':
    unittest.main()
```

**Additional Test for Scanner:**
```python
class TestSplitTransferScanner(unittest.TestCase):
    
    def test_salary_transactions_excluded(self):
        """Salary splits should be excluded from split transfer map."""
        from split_transfer_scan import is_salary_transaction
        
        salary_txn = [
            'D8/16\'19',
            'U2,106.22',
            'T2,106.22',
            'LSalary',
            'SSalary',
            'ESalary',
            '$7,192.31',
            'S[401(K) - eBay - Fidelity]',
            '$-2,157.69'
        ]
        
        self.assertTrue(is_salary_transaction(salary_txn))
        
        non_salary_txn = [
            'D7/ 4\'15',
            'U-104.59',
            'T-104.59',
            'LGroceries',
            'SGroceries',
            '$-4.59',
            'S[Cash - Ram]',
            '$-100.00'
        ]
        
        self.assertFalse(is_salary_transaction(non_salary_txn))
```
File Changes Summary
File	Action	Lines Changed	Description
split-transfer-scan.py	Create	~100	New scanner script
split_transfers.json	Generated	N/A	Output of scanner
qif_sanitizer.py	Modify	~50	Add split transfer logic
tests/test_nsellx_transfer.py	Modify	~80	Add test cases
Usage Flow
# Step 1: Build split transfer map (run once, or when QIF files change)
cd "QIFTools"
python split-transfer-scan.py

# Step 2: Run sanitizer (uses persisted map)
python qif_sanitizer.py
Notes
- Amount matching is exact (to the penny) as requested
- Scanner is a standalone script, not imported by sanitizer
- Split transfer suppression runs BEFORE existing processed-account check
- Existing behavior is preserved for non-split transfers
- The split_transfers.json file can be regenerated anytime
- Data is stable (old transactions), so scanner rarely needs re-running
