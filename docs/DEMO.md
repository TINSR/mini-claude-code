# Demo checklist

The following sequence demonstrates the main architecture without requiring destructive operations.

1. Ask the Agent to list Python files with `glob` and read one file.
2. Ask it to create a small task plan with `todo_write`.
3. Create two persistent tasks where the second depends on the first.
4. Start a read-only teammate and let it report through the mailbox.
5. Run `Start-Sleep 2; Write-Output 'done'` with `run_in_background=true`.
6. Connect the bundled `baidu` MCP Server and inspect its discovered `search_web` schema. A real search additionally requires `BAIDU_SEARCH_API_KEY`.
7. Create a disposable Git Worktree, make one uncommitted file, and demonstrate that safe removal is refused.

Before recording a demo, use a temporary Git repository and placeholder API credentials. Never show the contents of `.env`.
