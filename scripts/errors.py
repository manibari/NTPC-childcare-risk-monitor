"""Pipeline error type: every failure names the problem, the cause, and the fix.

Exit codes (CLI convention, autoplan DX Pass 2):
  0 success · 1 partial failure (stage failed, previous snapshot reused)
  2 aborted (data would be worse than before) · 64 usage error
"""


class PipelineError(Exception):
    exit_code = 2

    def __init__(self, problem: str, cause: str, fix: str, *, stage: str = "", exit_code: int | None = None):
        self.problem, self.cause, self.fix, self.stage = problem, cause, fix, stage
        if exit_code is not None:
            self.exit_code = exit_code
        super().__init__(self.format())

    def format(self) -> str:
        head = f"[{self.stage}] " if self.stage else ""
        return f"{head}{self.problem}\n  原因：{self.cause}\n  修法：{self.fix}"


class PartialFailure(PipelineError):
    """Stage failed but the pipeline continued with the previous snapshot."""

    exit_code = 1


class UsageError(PipelineError):
    exit_code = 64
