from django.contrib.auth.hashers import PBKDF2PasswordHasher


class PBKDF2PasswordHasher600k(PBKDF2PasswordHasher):
    iterations = 600000
