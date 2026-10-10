"""Smoke test that transaction domain modules retain their public symbols."""


def test_transaction_package_imports():
    from ocpp_csms.transactions.archive import TransactionArchive
    from ocpp_csms.transactions.query import TransactionQuery
    from ocpp_csms.transactions.formatting import format_transactions
    from ocpp_csms.transactions.contracts import transactions_contract

    assert TransactionArchive is not None
    assert TransactionQuery is not None
    assert callable(format_transactions)
    assert callable(transactions_contract)
