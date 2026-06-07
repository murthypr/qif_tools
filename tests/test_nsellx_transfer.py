import unittest
from qif_sanitizer import apply_mappings_to_transaction, apply_mappings_to_qif

class TestNSellXTransferSuppression(unittest.TestCase):
    def test_nsellx_normalization_removes_L_and_amount(self):
        txn = [
            'D01/01/2020',
            'NSellX',
            'YAcme Corp',
            'L[Checking - Bank]',
            '$1,234.56',
            'T100.00'
        ]
        processed, memo_updated = apply_mappings_to_transaction(txn, {}, {})
        # NSellX should be converted to NSell
        self.assertIn('NSell', processed)
        # L[...] and the following $ amount should be removed
        self.assertFalse(any(line.startswith('L[') for line in processed))
        self.assertFalse(any(line.startswith('$') for line in processed))

    def test_nsellx_not_suppressed_when_target_in_processed_accounts(self):
        # Single NSellX transaction content
        qif_content = '\n'.join([
            'D01/01/2020',
            'NSellX',
            'YAcme Corp',
            'L[Checking - Bank]',
            '$1,234.56',
            'T100.00'
        ]) + '\n^'

        result, replacement_counts, tag_insert_count, transactions_processed, suffix_counts, skipped = apply_mappings_to_qif(
            qif_content, {}, None, ['Checking - Bank']
        )

        # NSellX should be preserved (not suppressed) because its L[...] was removed
        self.assertEqual(skipped, 0)
        self.assertEqual(transactions_processed, 1)

    def test_pure_transfer_suppressed_when_target_in_processed_accounts(self):
        qif_content = '\n'.join([
            'D01/02/2020',
            'NTransfer',
            'L[Checking - Bank]',
            'T-100.00'
        ]) + '\n^'

        result, replacement_counts, tag_insert_count, transactions_processed, suffix_counts, skipped = apply_mappings_to_qif(
            qif_content, {}, None, ['Checking - Bank']
        )

        # Pure transfer should be suppressed
        self.assertEqual(skipped, 1)
        self.assertEqual(transactions_processed, 0)

if __name__ == '__main__':
    unittest.main()
