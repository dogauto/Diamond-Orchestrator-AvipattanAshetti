from typing import ClassVar


class DiamondError(Exception):
    code: ClassVar[str] = "INTERNAL_ERROR"
    exit_code: ClassVar[int] = 1


class ValidationError(DiamondError):
    code = "INVALID_REQUEST"
    exit_code = 2


class ConfigError(ValidationError):
    code = "INVALID_CONFIG"


class TransitionError(ValidationError):
    code = "INVALID_TRANSITION"


class ExecutionGuardError(TransitionError):
    code = "EXECUTION_UNAVAILABLE"


class NotFoundError(DiamondError):
    code = "NOT_FOUND"
    exit_code = 3


class ConflictError(DiamondError):
    code = "REVISION_CONFLICT"
    exit_code = 4


class CommandConflictError(ConflictError):
    code = "COMMAND_CONFLICT"


class StorageError(DiamondError):
    code = "STORAGE_ERROR"
    exit_code = 5


class ProviderError(DiamondError):
    code = "PROVIDER_ERROR"
    exit_code = 6


class ProviderAuthError(ProviderError):
    code = "PROVIDER_AUTH_ERROR"
