class RevolveRelateError(Exception):
    """Base error."""


class SchemaError(RevolveRelateError):
    pass


class AskError(RevolveRelateError):
    pass


class EngineError(RevolveRelateError):
    pass


class QueryError(RevolveRelateError):
    pass


class SecurityError(RevolveRelateError):
    pass


class PolicyError(RevolveRelateError):
    pass


class PromoteError(RevolveRelateError):
    pass
