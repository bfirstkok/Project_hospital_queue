from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLDatabaseWrapper
from django.db.backends.postgresql.features import DatabaseFeatures as PostgreSQLDatabaseFeatures


class CockroachDatabaseFeatures(PostgreSQLDatabaseFeatures):
    minimum_database_version = (13,)


class DatabaseWrapper(PostgreSQLDatabaseWrapper):
    features_class = CockroachDatabaseFeatures
