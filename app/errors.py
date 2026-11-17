from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class DomainError(ValueError):
    """A deterministic, user-facing business failure.

    ``code`` is a stable reason code (also used inside receipts), while
    ``status_code`` maps the failure onto the HTTP contract.  Rejecting a stale
    template publication is a conflict (409), not a validation error.
    """

    def __init__(self, code: str, message: str, status_code: int = 422):
        self.code, self.message, self.status_code = code, message, status_code
        super().__init__(message)


def install(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "code": exc.code, "message": exc.message},
        )
