from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model


class CockroachModelBackend(ModelBackend):
    def get_user(self, user_id):
        UserModel = get_user_model()
        db = UserModel._default_manager.db
        table = UserModel._meta.db_table

        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return None

        for user in UserModel._default_manager.raw(
            f"SELECT * FROM {table} WHERE id = %s",
            [user_id],
        ):
            return user if self.user_can_authenticate(user) else None

        return None
