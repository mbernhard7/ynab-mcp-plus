import asyncio
import json

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.models import InitializationOptions
from ynab.models import NewTransaction, SaveScheduledTransaction, SaveTransactionWithIdOrImportId

from .settings import settings
from .tool_models import (
    BulkManageTransactionsInput,
    GetMonthInfoInput,
    ListAccountsInput,
    ListCategoriesInput,
    ListPayeesInput,
    ListTransactionsInput,
    LookupEntityByIdInput,
    LookupPayeeLocationsInput,
    ManageBudgetedAmountInput,
    ManagePayeesInput,
    ManageScheduledTransactionInput,
)
from .ynab_client import YNABClient

server = Server("ynab-mcp")

# Single-user client for PAT/stdio mode, built lazily from the configured token.
_default_client: YNABClient | None = None

# Whether the configured PAT's user has been checked against YNAB_ALLOWED_USER_IDS.
_pat_user_checked = False


def _client() -> YNABClient:
    """Return the YNAB client for the current request.

    In OAuth mode a per-request access token is present in the auth context, so
    each caller acts as themselves. In PAT/stdio mode there is no request token
    and we fall back to the single configured Personal Access Token.
    """
    access = get_access_token()
    if access is not None and access.claims:
        ynab_token = access.claims.get("ynab_access_token")
        if ynab_token:
            return YNABClient(token=ynab_token)

    global _default_client
    if _default_client is None:
        if not settings.ynab_api_token:
            raise RuntimeError("No YNAB credentials available: set YNAB_PAT, or configure OAuth.")
        _default_client = YNABClient(token=settings.ynab_api_token)
    return _default_client


async def _check_allowed_user() -> None:
    """Enforce YNAB_ALLOWED_USER_IDS for the PAT fallback client.

    OAuth callers are validated in the auth layer (the user id is checked when
    tokens are issued and on every request). The PAT path has no auth context,
    so the PAT's own user is checked once here and cached.
    """
    allowed = settings.allowed_user_ids
    if allowed is None:
        return

    access = get_access_token()
    if access is not None and access.claims and access.claims.get("ynab_access_token"):
        return  # OAuth request; already validated by the provider.

    global _pat_user_checked
    if _pat_user_checked:
        return
    user_id = await _client().get_user_id()
    if user_id not in allowed:
        raise ValueError(
            "The configured YNAB_PAT belongs to a user not listed in YNAB_ALLOWED_USER_IDS."
        )
    _pat_user_checked = True


READ_ONLY_TOOLS = {
    "whoami",
    "list-plans",
    "list-accounts",
    "list-transactions",
    "list-categories",
    "list-payees",
    "list-scheduled-transactions",
    "get-month-info",
    "lookup-payee-locations",
}


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """
    List available tools.
    """
    all_tools = [
        types.Tool(
            name="whoami",
            description=(
                "Return the YNAB user ID of the authenticated user. "
                "Useful for populating YNAB_ALLOWED_USER_IDS."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list-plans",
            description="List all available YNAB plans",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list-accounts",
            description="List all accounts for a given plan. Useful for getting account IDs for other tools.",
            inputSchema=ListAccountsInput.model_json_schema(),
        ),
        types.Tool(
            name="list-transactions",
            description=(
                "List transactions, optionally filtered by account, category, payee, or month. "
                "Provide at most one of account_id / category_id / payee_id / month; with none, "
                "returns all plan transactions (use since_date to bound the range). Use this to "
                "answer questions like 'what did I spend on groceries' (category_id) or 'how much "
                "at Amazon' (payee_id)."
            ),
            inputSchema=ListTransactionsInput.model_json_schema(),
        ),
        types.Tool(
            name="list-categories",
            description=(
                "List all categories, groups, and their budgeting details for a given plan. "
                "Call this before managing budgeted amounts to see what's available and what's already been allocated."
            ),
            inputSchema=ListCategoriesInput.model_json_schema(),
        ),
        types.Tool(
            name="list-payees",
            description="List all payees for a given plan. Good for finding payee IDs or identifying messy payee data that needs to be merged.",
            inputSchema=ListPayeesInput.model_json_schema(),
        ),
        types.Tool(
            name="manage-payees",
            description="Merge multiple payee names into a single name. Use this to clean up payee data, for example, by renaming 'STARBUCKS #123' and 'Starbucks Coffee' to just 'Starbucks'.",
            inputSchema=ManagePayeesInput.model_json_schema(),
        ),
        types.Tool(
            name="manage-budgeted-amount",
            description="Assign a budgeted amount to a category or move money between categories for a specific month. This is the primary tool for allocating funds.",
            inputSchema=ManageBudgetedAmountInput.model_json_schema(),
        ),
        types.Tool(
            name="bulk-manage-transactions",
            description="Create, update, or delete multiple transactions at once. More efficient than making single changes.",
            inputSchema=BulkManageTransactionsInput.model_json_schema(),
        ),
        types.Tool(
            name="list-scheduled-transactions",
            description="List all upcoming scheduled transactions for a given plan. Useful for forecasting upcoming bills.",
            inputSchema={
                "type": "object",
                "properties": {
                    "plan_id": {
                        "type": "string",
                        "description": "The ID of the plan. If not provided, the default plan will be used.",
                    }
                },
            },
        ),
        types.Tool(
            name="manage-scheduled-transaction",
            description="Create, update, or delete a single scheduled (recurring) transaction. Use this to manage recurring bills or savings transfers.",
            inputSchema=ManageScheduledTransactionInput.model_json_schema(),
        ),
        types.Tool(
            name="lookup-entity-by-id",
            description="Look up the name and details of a specific account, category, or payee by its ID. A utility for when you have an ID but need the full context.",
            inputSchema=LookupEntityByIdInput.model_json_schema(),
        ),
        types.Tool(
            name="get-month-info",
            description="Get detailed plan information for a single month, including age of money and total amounts budgeted, spent, and available. Call this to check the monthly plan's status before making changes.",
            inputSchema=GetMonthInfoInput.model_json_schema(),
        ),
        types.Tool(
            name="lookup-payee-locations",
            description="Look up geographic locations associated with a payee.",
            inputSchema=LookupPayeeLocationsInput.model_json_schema(),
        ),
    ]

    tools = all_tools
    if settings.ynab_read_only:
        tools = [tool for tool in tools if tool.name in READ_ONLY_TOOLS]

    if settings.ynab_default_plan_id:
        tools = [tool for tool in tools if tool.name != "list-plans"]

    return tools


async def _get_plan_id(arguments: dict | None) -> str:
    """Gets the plan_id from arguments, settings, or falls back to the default plan."""
    if settings.ynab_default_plan_id:
        return settings.ynab_default_plan_id

    if arguments and "plan_id" in arguments and arguments["plan_id"]:
        return arguments["plan_id"]

    plan = await _client().get_default_plan()
    # The SDK models ids as UUID objects; downstream SDK calls require strings.
    return str(plan.id)


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """
    Handle tool execution requests.
    """
    if settings.ynab_read_only and name not in READ_ONLY_TOOLS:
        raise ValueError("The server is in read-only mode. Write operations are disabled.")

    await _check_allowed_user()

    if name == "whoami":
        user_id = await _client().get_user_id()
        return [types.TextContent(type="text", text=f"YNAB user ID: {user_id}")]
    elif name == "list-plans":
        plans = await _client().get_plans()

        if not plans:
            return [types.TextContent(type="text", text="No plans found.")]

        plan_list = "\n".join(f"- {p.name} (ID: {p.id})" for p in plans)

        return [
            types.TextContent(
                type="text",
                text=f"Here are your available plans:\n{plan_list}",
            )
        ]
    elif name == "list-accounts":
        args = ListAccountsInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())
        accounts = await _client().get_accounts(plan_id=plan_id)

        if not accounts:
            return [types.TextContent(type="text", text="No accounts found for this plan.")]

        account_list = "\n".join(
            f"- {acc.name} (ID: {acc.id}): {acc.balance / 1000:.2f} (Type: {acc.type.value})"
            for acc in accounts
        )
        return [
            types.TextContent(
                type="text",
                text=f"Here are the accounts for plan {plan_id}:\n{account_list}",
            )
        ]
    elif name == "list-transactions":
        args = ListTransactionsInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())

        limit = int(args.limit) if args.limit is not None else None

        if args.account_id:
            transactions = await _client().get_transactions_by_account(
                plan_id=plan_id,
                account_id=args.account_id,
                since_date=args.since_date,
                limit=limit,
            )
            header = f"Transactions for account {args.account_id}:"
        elif args.category_id:
            transactions = await _client().get_transactions_by_category(
                plan_id=plan_id,
                category_id=args.category_id,
                since_date=args.since_date,
                limit=limit,
            )
            header = f"Transactions for category {args.category_id}:"
        elif args.payee_id:
            transactions = await _client().get_transactions_by_payee(
                plan_id=plan_id,
                payee_id=args.payee_id,
                since_date=args.since_date,
                limit=limit,
            )
            header = f"Transactions for payee {args.payee_id}:"
        elif args.month:
            transactions = await _client().get_monthly_transactions(
                plan_id=plan_id,
                month=args.month,
                limit=limit,
            )
            header = f"Transactions for {args.month}:"
        else:
            transactions = await _client().get_transactions(
                plan_id=plan_id,
                since_date=args.since_date,
                limit=limit,
            )
            header = (
                f"All transactions since {args.since_date}:"
                if args.since_date
                else "All transactions:"
            )

        if not transactions:
            return [types.TextContent(type="text", text="No transactions found.")]

        transaction_list = "\n".join(
            f"- {t.var_date}: {t.payee_name or 'N/A'} | "
            f"{t.category_name or 'N/A'} | {t.amount / 1000:.2f} (ID: {t.id})"
            for t in transactions
        )
        return [
            types.TextContent(
                type="text",
                text=f"{header}\n{transaction_list}",
            )
        ]
    elif name == "list-categories":
        args = ListCategoriesInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())
        category_groups = await _client().get_categories(plan_id=plan_id)

        if not category_groups:
            return [types.TextContent(type="text", text="No categories found for this plan.")]

        output = "Here are the available categories and their status for the current month:\n"
        for group in category_groups:
            if not group.hidden and group.categories:
                output += f"\n--- {group.name} ---\n"
                for cat in group.categories:
                    if not cat.hidden:
                        details = (
                            f"Budgeted: {cat.budgeted / 1000:.2f}, "
                            f"Spent: {abs(cat.activity) / 1000:.2f}, "
                            f"Balance: {cat.balance / 1000:.2f}"
                        )
                        output += f"- {cat.name} (ID: {cat.id})\n  - {details}\n"
                        if cat.goal_type:
                            goal_progress = f"{cat.goal_percentage_complete or 0}%"
                            goal_target = (
                                f"{cat.goal_target / 1000:.2f}" if cat.goal_target else "N/A"
                            )
                            output += (
                                f"  - Goal ({cat.goal_type}): Target {goal_target}, "
                                f"{goal_progress} complete\n"
                            )
        return [types.TextContent(type="text", text=output)]
    elif name == "list-payees":
        args = ListPayeesInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())
        payees = await _client().get_payees(plan_id=plan_id)

        if not payees:
            return [types.TextContent(type="text", text="No payees found for this plan.")]

        total = len(payees)
        if args.search:
            needle = args.search.lower()
            payees = [p for p in payees if needle in (p.name or "").lower()]
        if args.limit is not None:
            payees = payees[: int(args.limit)]

        if not payees:
            return [
                types.TextContent(
                    type="text",
                    text=f"No payees matched '{args.search}' (out of {total} payees).",
                )
            ]

        payee_list = "\n".join(f"- {p.name} (ID: {p.id})" for p in payees)
        return [
            types.TextContent(
                type="text",
                text=(
                    f"Showing {len(payees)} of {total} payees for plan {plan_id} "
                    "(use 'search'/'limit' to narrow):\n" + payee_list
                ),
            )
        ]
    elif name == "manage-payees":
        args = ManagePayeesInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())

        if args.action == "rename":
            await _client().update_payees(
                plan_id=plan_id,
                payee_ids=args.payee_ids,
                name=args.name,
            )
            return [
                types.TextContent(
                    type="text",
                    text=f"Successfully renamed {len(args.payee_ids)} payees to '{args.name}'.",
                )
            ]
    elif name == "manage-budgeted-amount":
        args = ManageBudgetedAmountInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())
        amount = int(args.amount)
        month = args.month

        if args.action == "assign":
            await _client().assign_budget_amount(
                plan_id=plan_id,
                month=month,
                category_id=args.to_category_id,
                amount=amount,
            )
            return [
                types.TextContent(
                    type="text",
                    text=f"Successfully assigned {amount / 1000:.2f} to category {args.to_category_id} for {month}.",
                )
            ]
        elif args.action == "move":
            from_cat = await _client().get_month_category(plan_id, month, args.from_category_id)
            to_cat = await _client().get_month_category(plan_id, month, args.to_category_id)

            new_from_budgeted = from_cat.budgeted - amount
            new_to_budgeted = to_cat.budgeted + amount

            await _client().assign_budget_amount(
                plan_id, month, args.from_category_id, new_from_budgeted
            )
            await _client().assign_budget_amount(
                plan_id, month, args.to_category_id, new_to_budgeted
            )

            return [
                types.TextContent(
                    type="text",
                    text=f"Successfully moved {amount / 1000:.2f} from category {from_cat.name} to {to_cat.name} for {month}.",
                )
            ]
    elif name == "bulk-manage-transactions":
        args = BulkManageTransactionsInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())

        if args.action == "create":
            new_transactions = []
            for tx in args.create_transactions:
                tx_data = {k: v for k, v in tx.model_dump().items() if v is not None}
                if "amount" in tx_data:
                    tx_data["amount"] = int(tx_data["amount"])
                new_transactions.append(NewTransaction(**tx_data))

            result = await _client().create_transactions(
                plan_id=plan_id, transactions=new_transactions
            )

            created_ids = ", ".join(result.transaction_ids)
            duplicate_ids = ", ".join(result.duplicate_import_ids)

            response_text = f"Successfully processed bulk transaction request. Server Knowledge: {result.server_knowledge}\n"
            if created_ids:
                response_text += f"Created transaction IDs: {created_ids}\n"
            if duplicate_ids:
                response_text += f"Duplicate import IDs (skipped): {duplicate_ids}\n"

            return [types.TextContent(type="text", text=response_text.strip())]

        elif args.action == "update":
            updates = []
            for tx in args.update_transactions:
                tx_data = {k: v for k, v in tx.model_dump().items() if v is not None}
                if "amount" in tx_data:
                    tx_data["amount"] = int(tx_data["amount"])
                updates.append(SaveTransactionWithIdOrImportId(**tx_data))
            await _client().update_transactions(plan_id=plan_id, transactions=updates)

            return [
                types.TextContent(
                    type="text",
                    text=f"Successfully updated {len(args.update_transactions)} transactions.",
                )
            ]

        elif args.action == "delete":
            deleted_ids = []
            for tx_id in args.delete_transaction_ids:
                await _client().delete_transaction(plan_id=plan_id, transaction_id=tx_id)
                deleted_ids.append(tx_id)

            return [
                types.TextContent(
                    type="text",
                    text=f"Successfully deleted {len(deleted_ids)} transactions: {', '.join(deleted_ids)}",
                )
            ]
    elif name == "list-scheduled-transactions":
        plan_id = await _get_plan_id(arguments)
        transactions = await _client().get_scheduled_transactions(plan_id=plan_id)

        if not transactions:
            return [types.TextContent(type="text", text="No scheduled transactions found.")]

        scheduled_list = "\n".join(
            f"- Next: {t.date_next}: {t.payee_name or 'N/A'} | "
            f"{t.category_name or 'N/A'} | {t.amount / 1000:.2f} "
            f"(Frequency: {t.frequency}, ID: {t.id})"
            for t in transactions
        )
        return [
            types.TextContent(
                type="text",
                text=f"Here are the scheduled transactions:\n{scheduled_list}",
            )
        ]
    elif name == "manage-scheduled-transaction":
        args = ManageScheduledTransactionInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())

        action = args.action
        if action == "create":
            transaction_data = {
                k: v for k, v in args.transaction_data.model_dump().items() if v is not None
            }
            if "amount" in transaction_data:
                transaction_data["amount"] = int(transaction_data["amount"])

            created = await _client().create_scheduled_transaction(
                plan_id=plan_id, transaction=SaveScheduledTransaction(**transaction_data)
            )
            return [
                types.TextContent(
                    type="text",
                    text=f"Successfully created scheduled transaction (ID: {created.id}).",
                )
            ]
        elif action == "update":
            transaction_data = {
                k: v for k, v in args.transaction_data.model_dump().items() if v is not None
            }
            if "amount" in transaction_data:
                transaction_data["amount"] = int(transaction_data["amount"])

            await _client().update_scheduled_transaction(
                plan_id=plan_id,
                scheduled_transaction_id=args.transaction_id,
                transaction=SaveScheduledTransaction(**transaction_data),
            )
            return [
                types.TextContent(
                    type="text",
                    text=f"Successfully updated scheduled transaction {args.transaction_id}.",
                )
            ]
        elif action == "delete":
            await _client().delete_scheduled_transaction(
                plan_id=plan_id, scheduled_transaction_id=args.transaction_id
            )
            return [
                types.TextContent(
                    type="text",
                    text=f"Successfully deleted scheduled transaction {args.transaction_id}.",
                )
            ]
    elif name == "lookup-entity-by-id":
        args = LookupEntityByIdInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())

        entity = None
        if args.entity_type == "account":
            entity = await _client().get_account_by_id(plan_id, args.entity_id)
        elif args.entity_type == "category":
            entity = await _client().get_category_by_id(plan_id, args.entity_id)
        elif args.entity_type == "payee":
            entity = await _client().get_payee_by_id(plan_id, args.entity_id)

        if not entity:
            return [
                types.TextContent(
                    type="text",
                    text=f"No {args.entity_type.value} found with ID {args.entity_id}.",
                )
            ]

        entity_dict = entity.to_dict()
        return [
            types.TextContent(
                type="text",
                text=f"Found {args.entity_type.value}:\n{json.dumps(entity_dict, indent=2, default=str)}",
            )
        ]
    elif name == "get-month-info":
        args = GetMonthInfoInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())

        if args.month:
            # Get a single month
            month_detail = await _client().get_plan_month(plan_id, args.month)
            result_dict = month_detail.to_dict()
            text_output = (
                f"Details for month {args.month}:\n{json.dumps(result_dict, indent=2, default=str)}"
            )
        else:
            # List all months
            months = await _client().get_plan_months(plan_id)
            month_list = "\n".join(
                f"- Month: {m.month}, Budgeted: {m.budgeted / 1000:.2f}, Activity: {m.activity / 1000:.2f}, To Be Budgeted: {m.to_be_budgeted / 1000:.2f}"
                for m in months
            )
            text_output = f"Available months for plan {plan_id}:\n{month_list}"

        return [types.TextContent(type="text", text=text_output)]
    elif name == "lookup-payee-locations":
        args = LookupPayeeLocationsInput.model_validate(arguments or {})
        plan_id = await _get_plan_id(args.model_dump())
        locations = []
        if args.location_id:
            location = await _client().get_payee_location_by_id(plan_id, args.location_id)
            locations = [location] if location else []
        elif args.payee_id:
            locations = await _client().get_payee_locations_by_payee(plan_id, args.payee_id)
        else:
            locations = await _client().get_payee_locations(plan_id)

        if not locations:
            return [types.TextContent(type="text", text="No payee locations found.")]

        locations_dict = [loc.to_dict() for loc in locations]
        return [
            types.TextContent(
                type="text",
                text=f"Found {len(locations)} payee locations:\n{json.dumps(locations_dict, indent=2, default=str)}",
            )
        ]

    raise ValueError(f"Unknown tool: {name}")


async def main():
    # Run the server using stdin/stdout streams
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="ynab-mcp",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
