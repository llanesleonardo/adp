class AppError(Exception):
    status_code = 400
    detail = "Request failed"

    def __init__(self, detail: str | None = None):
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class Unauthenticated(AppError):
    status_code = 401
    detail = "Authentication required."


class Forbidden(AppError):
    status_code = 403
    detail = "You do not have permission to perform this action."


class NotFound(AppError):
    status_code = 404
    detail = "Not found."


class Conflict(AppError):
    status_code = 409
    detail = "The request conflicts with the current state."


class BadRequest(AppError):
    status_code = 400
    detail = "The request is invalid."


class RateLimited(AppError):
    status_code = 429
    detail = "Too many requests. Try again later."
