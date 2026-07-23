import asyncio
from typing import Optional

import ynab
from ynab.api import (
    accounts_api,
    categories_api,
    months_api,
    payee_locations_api,
    payees_api,
    plans_api,
    scheduled_transactions_api,
    transactions_api,
)
from ynab.models import (
    NewTransaction,
    PatchMonthCategoryWrapper,
    PatchPayeeWrapper,
    PatchTransactionsWrapper,
    PostScheduledTransactionWrapper,
    PostTransactionsWrapper,
    PutScheduledTransactionWrapper,
    SaveMonthCategory,
    SavePayee,
    SaveScheduledTransaction,
    SaveTransactionWithIdOrImportId,
)


class YNABClient:
    """Thin async wrapper over the official ``ynab`` SDK.

    Constructed from a single access token (a Personal Access Token, or an
    OAuth access token minted for one user). The token is only held for the
    life of this object, which the server builds from the caller's credentials.

    Only the endpoints backing an exposed MCP tool are wrapped here.
    """

    def __init__(self, token: str):
        configuration = ynab.Configuration(access_token=token)
        self.api_client = ynab.ApiClient(configuration)
        self._plans_api = plans_api.PlansApi(self.api_client)
        self._accounts_api = accounts_api.AccountsApi(self.api_client)
        self._categories_api = categories_api.CategoriesApi(self.api_client)
        self._transactions_api = transactions_api.TransactionsApi(self.api_client)
        self._payees_api = payees_api.PayeesApi(self.api_client)
        self._scheduled_transactions_api = scheduled_transactions_api.ScheduledTransactionsApi(
            self.api_client
        )
        self._months_api = months_api.MonthsApi(self.api_client)
        self._payee_locations_api = payee_locations_api.PayeeLocationsApi(self.api_client)

    async def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous SDK call in a worker thread."""
        return await asyncio.to_thread(func, *args, **kwargs)

    # ------------------------------------------------------------------ #
    # Plans (formerly "budgets")
    # ------------------------------------------------------------------ #
    async def get_plans(self) -> list[ynab.PlanSummary]:
        response = await self._run_sync(self._plans_api.get_plans)
        return response.data.plans

    async def get_default_plan(self) -> ynab.PlanSummary:
        """Gets the first available plan, assuming only one is used."""
        plans = await self.get_plans()
        return plans[0]

    # ------------------------------------------------------------------ #
    # Accounts
    # ------------------------------------------------------------------ #
    async def get_accounts(self, plan_id: str) -> list[ynab.Account]:
        response = await self._run_sync(self._accounts_api.get_accounts, plan_id)
        return response.data.accounts

    async def get_account_by_id(self, plan_id: str, account_id: str) -> ynab.Account:
        response = await self._run_sync(self._accounts_api.get_account_by_id, plan_id, account_id)
        return response.data.account

    # ------------------------------------------------------------------ #
    # Categories
    # ------------------------------------------------------------------ #
    async def get_categories(self, plan_id: str) -> list[ynab.CategoryGroupWithCategories]:
        response = await self._run_sync(self._categories_api.get_categories, plan_id)
        return response.data.category_groups

    async def get_category_by_id(self, plan_id: str, category_id: str) -> ynab.Category:
        response = await self._run_sync(
            self._categories_api.get_category_by_id, plan_id, category_id
        )
        return response.data.category

    async def get_month_category(self, plan_id: str, month: str, category_id: str) -> ynab.Category:
        response = await self._run_sync(
            self._categories_api.get_month_category_by_id,
            plan_id,
            month,
            category_id,
        )
        return response.data.category

    async def assign_budget_amount(self, plan_id: str, month: str, category_id: str, amount: int):
        month_category = SaveMonthCategory(budgeted=amount)
        update_wrapper = PatchMonthCategoryWrapper(category=month_category)
        return await self._run_sync(
            self._categories_api.update_month_category,
            plan_id,
            month,
            category_id,
            update_wrapper,
        )

    # ------------------------------------------------------------------ #
    # Payees
    # ------------------------------------------------------------------ #
    async def get_payees(self, plan_id: str) -> list[ynab.Payee]:
        response = await self._run_sync(self._payees_api.get_payees, plan_id)
        return response.data.payees

    async def get_payee_by_id(self, plan_id: str, payee_id: str) -> ynab.Payee:
        response = await self._run_sync(self._payees_api.get_payee_by_id, plan_id, payee_id)
        return response.data.payee

    async def update_payee(self, plan_id: str, payee_id: str, name: str) -> ynab.Payee:
        payee = SavePayee(name=name)
        update_wrapper = PatchPayeeWrapper(payee=payee)
        return await self._run_sync(
            self._payees_api.update_payee, plan_id, payee_id, update_wrapper
        )

    async def update_payees(self, plan_id: str, payee_ids: list[str], name: str):
        """Updates multiple payees to the same name (merge by rename)."""
        tasks = [self.update_payee(plan_id, payee_id, name) for payee_id in payee_ids]
        return await asyncio.gather(*tasks)

    # ------------------------------------------------------------------ #
    # Payee locations
    # ------------------------------------------------------------------ #
    async def get_payee_locations(self, plan_id: str) -> list[ynab.PayeeLocation]:
        response = await self._run_sync(self._payee_locations_api.get_payee_locations, plan_id)
        return response.data.payee_locations

    async def get_payee_location_by_id(
        self, plan_id: str, payee_location_id: str
    ) -> ynab.PayeeLocation:
        response = await self._run_sync(
            self._payee_locations_api.get_payee_location_by_id,
            plan_id,
            payee_location_id,
        )
        return response.data.payee_location

    async def get_payee_locations_by_payee(
        self, plan_id: str, payee_id: str
    ) -> list[ynab.PayeeLocation]:
        response = await self._run_sync(
            self._payee_locations_api.get_payee_locations_by_payee,
            plan_id,
            payee_id,
        )
        return response.data.payee_locations

    # ------------------------------------------------------------------ #
    # Months
    # ------------------------------------------------------------------ #
    async def get_plan_months(self, plan_id: str) -> list[ynab.MonthSummary]:
        response = await self._run_sync(self._months_api.get_plan_months, plan_id)
        return response.data.months

    async def get_plan_month(self, plan_id: str, month: str) -> ynab.MonthDetail:
        response = await self._run_sync(self._months_api.get_plan_month, plan_id, month)
        return response.data.month

    # ------------------------------------------------------------------ #
    # Transactions
    # ------------------------------------------------------------------ #
    async def get_transactions(
        self,
        plan_id: str,
        since_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[ynab.TransactionDetail]:
        """All transactions for the plan, optionally since a date."""
        response = await self._run_sync(
            self._transactions_api.get_transactions,
            plan_id,
            since_date=since_date,
        )
        transactions = response.data.transactions
        return transactions[:limit] if limit else transactions

    async def get_transactions_by_account(
        self,
        plan_id: str,
        account_id: str,
        since_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[ynab.TransactionDetail]:
        response = await self._run_sync(
            self._transactions_api.get_transactions_by_account,
            plan_id,
            account_id,
            since_date=since_date,
        )
        transactions = response.data.transactions
        return transactions[:limit] if limit else transactions

    async def get_transactions_by_category(
        self,
        plan_id: str,
        category_id: str,
        since_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[ynab.HybridTransaction]:
        response = await self._run_sync(
            self._transactions_api.get_transactions_by_category,
            plan_id,
            category_id,
            since_date=since_date,
        )
        transactions = response.data.transactions
        return transactions[:limit] if limit else transactions

    async def get_transactions_by_payee(
        self,
        plan_id: str,
        payee_id: str,
        since_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[ynab.HybridTransaction]:
        response = await self._run_sync(
            self._transactions_api.get_transactions_by_payee,
            plan_id,
            payee_id,
            since_date=since_date,
        )
        transactions = response.data.transactions
        return transactions[:limit] if limit else transactions

    async def get_monthly_transactions(
        self,
        plan_id: str,
        month: str,
        limit: Optional[int] = None,
    ) -> list[ynab.TransactionDetail]:
        response = await self._run_sync(
            self._transactions_api.get_transactions_by_month,
            plan_id,
            month,
        )
        transactions = response.data.transactions
        return transactions[:limit] if limit else transactions

    async def create_transactions(
        self, plan_id: str, transactions: list[NewTransaction]
    ) -> ynab.SaveTransactionsResponseData:
        response = await self._run_sync(
            self._transactions_api.create_transaction,
            plan_id,
            PostTransactionsWrapper(transactions=transactions),
        )
        return response.data

    async def update_transactions(
        self, plan_id: str, transactions: list[SaveTransactionWithIdOrImportId]
    ) -> ynab.SaveTransactionsResponseData:
        update_wrapper = PatchTransactionsWrapper(transactions=transactions)
        response = await self._run_sync(
            self._transactions_api.update_transactions, plan_id, update_wrapper
        )
        return response.data

    async def delete_transaction(self, plan_id: str, transaction_id: str):
        return await self._run_sync(
            self._transactions_api.delete_transaction, plan_id, transaction_id
        )

    # ------------------------------------------------------------------ #
    # Scheduled transactions
    # ------------------------------------------------------------------ #
    async def get_scheduled_transactions(
        self, plan_id: str
    ) -> list[ynab.ScheduledTransactionDetail]:
        response = await self._run_sync(
            self._scheduled_transactions_api.get_scheduled_transactions, plan_id
        )
        return response.data.scheduled_transactions

    async def create_scheduled_transaction(
        self, plan_id: str, transaction: SaveScheduledTransaction
    ) -> ynab.ScheduledTransactionDetail:
        wrapper = PostScheduledTransactionWrapper(scheduled_transaction=transaction)
        response = await self._run_sync(
            self._scheduled_transactions_api.create_scheduled_transaction,
            plan_id,
            wrapper,
        )
        return response.data.scheduled_transaction

    async def update_scheduled_transaction(
        self,
        plan_id: str,
        scheduled_transaction_id: str,
        transaction: SaveScheduledTransaction,
    ) -> ynab.ScheduledTransactionDetail:
        wrapper = PutScheduledTransactionWrapper(scheduled_transaction=transaction)
        response = await self._run_sync(
            self._scheduled_transactions_api.update_scheduled_transaction,
            plan_id,
            scheduled_transaction_id,
            wrapper,
        )
        return response.data.scheduled_transaction

    async def delete_scheduled_transaction(self, plan_id: str, scheduled_transaction_id: str):
        return await self._run_sync(
            self._scheduled_transactions_api.delete_scheduled_transaction,
            plan_id,
            scheduled_transaction_id,
        )
