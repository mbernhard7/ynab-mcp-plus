"""Print a fresh MCP_TOKEN_SECRET. Run: python -m ynab_mcp_server.gen_secret"""

from .crypto import generate_secret


def main() -> None:
    print(generate_secret())


if __name__ == "__main__":
    main()
