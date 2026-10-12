"""Process exit codes (after fallow's exit-code contract).

0 and 1 are both completed runs; a script reading `--format json` output can
treat them alike. 2 and 3 mean the run did not complete — never hide them
with `|| true`.
"""

from __future__ import annotations

SUCCESS = 0
ISSUES = 1
INVALID_INPUT = 2
UNAVAILABLE = 3

EXIT_CODES: dict[int, str] = {
    SUCCESS: "Success: no enforced gate failed (no error-severity findings, audit pass or warn)",
    ISSUES: "An enforced gate failed: error-severity findings (or any finding with "
    "--fail-on-issues), a stale baseline with --fail-on-stale-baseline, an audit "
    "fail verdict, or `baselines prune --check` found entries to prune",
    INVALID_INPUT: "Invalid input or runtime error: unreadable config, unknown format, "
    "refused baseline, git failure. With --format json, an error raised while the command "
    "runs is a JSON document on stdout. Command-line usage errors caught by the argument "
    "parser (unknown option, invalid --boundaries, options before the command) are text "
    "on stderr",
    UNAVAILABLE: "A requested resource is unavailable: the --config file does not exist",
}


class HallowError(Exception):
    """An error that ends the run with a non-zero exit code and a message."""

    def __init__(
        self,
        message: str,
        exit_code: int = INVALID_INPUT,
        code: str | None = None,
        help: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.code = code
        self.help = help
