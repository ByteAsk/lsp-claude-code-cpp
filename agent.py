"""Harbor's Claude Code agent with one extra step, used by every arm.

Harbor points CLAUDE_CONFIG_DIR at a fresh mount (/logs/agent/sessions), which hides the image's ~/.claude. The LSP
images install the clangd-lsp plugin and the user-level CLAUDE.md there at build time, so this copies ~/.claude into
the session directory before the run. The plain image has no ~/.claude, so nothing is copied."""
from harbor.agents.installed.claude_code import ClaudeCode


class ClaudeCodeWithImageConfig(ClaudeCode):
    async def run(self, instruction, environment, context):
        sessions = (self.environment_logs_dir / "sessions").as_posix()
        await self.exec_as_agent(
            environment,
            command=(f"mkdir -p {sessions} && if [ -d \"$HOME/.claude\" ]; then cp -a \"$HOME/.claude/.\" {sessions}/; "
                     f"fi; ls -A {sessions} > {(self.environment_logs_dir / 'config-copied.txt').as_posix()}"),
        )
        return await super().run(instruction, environment, context)
