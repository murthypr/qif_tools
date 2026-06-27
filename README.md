# qif_tools
QIF Tools for Quicken to GNU Cash Migration


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