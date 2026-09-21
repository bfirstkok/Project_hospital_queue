from django.db import models


class PostgresEnumField(models.CharField):
    """
    CharField-compatible field that uses a native PostgreSQL ENUM type in
    PostgreSQL and falls back to VARCHAR on other database backends.

    This keeps local SQLite tests portable while allowing production
    PostgreSQL to enforce the allowed values at the database layer.
    """

    def __init__(self, *args, enum_type, **kwargs):
        self.enum_type = enum_type
        super().__init__(*args, **kwargs)

    def db_type(self, connection):
        if connection.vendor == "postgresql":
            return connection.ops.quote_name(self.enum_type)
        return super().db_type(connection)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["enum_type"] = self.enum_type
        return name, path, args, kwargs
