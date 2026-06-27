import unittest
from qif_sanitizer import apply_mappings_to_transaction, apply_mappings_to_qif, is_split_transfer_transaction

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


class TestSplitTransferSuppression(unittest.TestCase):

    def test_split_transfer_detected(self):
        """Transfer in Cash should be suppressed when split exists in Checking."""
        split_map = {
            "Checking - CapitalOne - Regular": [
                {"target": "Cash - Ram", "amount": -100.00, "date": "7/ 4'15"}
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
                {"target": "Cash - Ram", "amount": -100.00, "date": "7/ 4'15"}
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
                {"target": "401(K) - eBay - Fidelity", "amount": -2157.69, "date": "8/16'19"}
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

    def test_is_split_transfer_transaction_match(self):
        """is_split_transfer_transaction returns True for matching split transfer."""
        split_map = {
            "Checking - CapitalOne - Regular": [
                {"target": "Cash - Ram", "amount": -100.00, "date": "7/ 4'15"}
            ]
        }
        txn_lines = [
            'D7/ 4\'15',
            'U100.00',
            'T100.00',
            'L[Checking - CapitalOne - Regular]',
            '$100.00'
        ]
        result = is_split_transfer_transaction(
            txn_lines, "Checking - CapitalOne - Regular", "Cash - Ram", split_map
        )
        self.assertTrue(result)

    def test_is_split_transfer_transaction_no_match(self):
        """is_split_transfer_transaction returns False when amount doesn't match."""
        split_map = {
            "Checking - CapitalOne - Regular": [
                {"target": "Cash - Ram", "amount": -100.00, "date": "7/ 4'15"}
            ]
        }
        txn_lines = [
            'D7/ 4\'15',
            'U200.00',
            'T200.00',
            'L[Checking - CapitalOne - Regular]',
            '$200.00'
        ]
        result = is_split_transfer_transaction(
            txn_lines, "Checking - CapitalOne - Regular", "Cash - Ram", split_map
        )
        self.assertFalse(result)

    def test_is_split_transfer_transaction_date_mismatch(self):
        """is_split_transfer_transaction returns False when date doesn't match."""
        split_map = {
            "Checking - CapitalOne - Regular": [
                {"target": "Cash - Ram", "amount": -100.00, "date": "7/ 4'15"}
            ]
        }
        # Same amount but different date
        txn_lines = [
            'D7/ 5\'15',
            'U100.00',
            'T100.00',
            'L[Checking - CapitalOne - Regular]',
            '$100.00'
        ]
        result = is_split_transfer_transaction(
            txn_lines, "Checking - CapitalOne - Regular", "Cash - Ram", split_map
        )
        self.assertFalse(result)

    def test_same_amount_different_dates_not_suppressed(self):
        """Multiple same-amount transfers on different dates: only exact date matches."""
        split_map = {
            "Checking - CapitalOne - Regular": [
                {"target": "Cash - Ram", "amount": -40.00, "date": "7/ 1'15"},
                {"target": "Cash - Ram", "amount": -40.00, "date": "7/15'15"}
            ]
        }

        # Transfer on 7/1 matches first entry
        qif_content1 = '\n'.join([
            'D7/ 1\'15',
            'U40.00',
            'T40.00',
            'MCash With Drawal',
            'L[Checking - CapitalOne - Regular]'
        ]) + '\n^'

        result1, _, _, _, _, skipped1 = apply_mappings_to_qif(
            qif_content1, {}, None, [],
            split_transfer_map=split_map,
            current_account="Cash - Ram"
        )
        self.assertEqual(skipped1, 1)

        # Transfer on 7/20 (no match) should NOT be suppressed
        qif_content2 = '\n'.join([
            'D7/ 20\'15',
            'U40.00',
            'T40.00',
            'MCash With Drawal',
            'L[Checking - CapitalOne - Regular]'
        ]) + '\n^'

        result2, _, _, _, _, skipped2 = apply_mappings_to_qif(
            qif_content2, {}, None, [],
            split_transfer_map=split_map,
            current_account="Cash - Ram"
        )
        self.assertEqual(skipped2, 0)


class TestSplitTransferScanner(unittest.TestCase):

    def test_salary_transactions_excluded(self):
        """Salary splits should be excluded from split transfer map."""
        from qif_sanitizer import is_salary_transaction

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


class TestSplitTransactionNotSuppressed(unittest.TestCase):
    """Split transactions with non-transfer S-lines should not be suppressed
    even when the L-line targets a processed account."""

    def test_split_with_expense_not_suppressed(self):
        """L[Cash - Ram] + S Groceries + S[Cash - Ram] should NOT be suppressed."""
        qif_content = '\n'.join([
            'D10/26\'15',
            'U-50.31',
            'T-50.31',
            'CX',
            'PSafeway',
            'L[Cash - Ram]',
            'S[Cash - Ram]',
            'EWithdrawal',
            '$-40.00',
            'SGroceries',
            'EFruits',
            '$-10.31'
        ]) + '\n^'

        result, _, _, transactions_processed, _, skipped = apply_mappings_to_qif(
            qif_content, {}, None, ['Cash - Ram']
        )

        self.assertEqual(transactions_processed, 1)
        self.assertEqual(skipped, 0)

    def test_pure_transfer_still_suppressed(self):
        """L[Cash - Ram] with only S[Cash - Ram] splits should still be suppressed."""
        qif_content = '\n'.join([
            'D10/26\'15',
            'U-40.00',
            'T-40.00',
            'CX',
            'PSafeway',
            'L[Cash - Ram]',
            'S[Cash - Ram]',
            'EWithdrawal',
            '$-40.00'
        ]) + '\n^'

        result, _, _, transactions_processed, _, skipped = apply_mappings_to_qif(
            qif_content, {}, None, ['Cash - Ram']
        )

        self.assertEqual(transactions_processed, 0)
        self.assertEqual(skipped, 1)

    def test_split_with_different_transfer_targets_not_suppressed(self):
        """L[Cash - Ram] + S[Cash - Ram] + S[Savings] should NOT be suppressed."""
        qif_content = '\n'.join([
            'D10/26\'15',
            'U-80.00',
            'T-80.00',
            'L[Cash - Ram]',
            'S[Cash - Ram]',
            'EWithdrawal',
            '$-40.00',
            'S[Savings - Ram]',
            'EDeposit',
            '$-40.00'
        ]) + '\n^'

        result, _, _, transactions_processed, _, skipped = apply_mappings_to_qif(
            qif_content, {}, None, ['Cash - Ram']
        )

        self.assertEqual(transactions_processed, 1)
        self.assertEqual(skipped, 0)

    def test_non_transfer_l_line_not_suppressed(self):
        """L Groceries (non-transfer) should never be suppressed by this logic."""
        qif_content = '\n'.join([
            'D7/ 1\'15',
            'U-70.36',
            'T-70.36',
            'CX',
            'LGroceries',
            'SGroceries',
            '$-50.36',
            'S[Cash - Ram]',
            'ECash Withdrawal',
            '$-20.00'
        ]) + '\n^'

        result, _, _, transactions_processed, _, skipped = apply_mappings_to_qif(
            qif_content, {}, None, ['Cash - Ram']
        )

        self.assertEqual(transactions_processed, 1)
        self.assertEqual(skipped, 0)


if __name__ == '__main__':
    unittest.main()
