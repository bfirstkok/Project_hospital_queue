import csv
import secrets
from collections import defaultdict
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from queues.models import StaffProfile


ROLE_USERNAME_PREFIX = {
    StaffProfile.Role.DOCTOR: "doctor",
    StaffProfile.Role.NURSE: "nurse",
    StaffProfile.Role.NURSE_ASSISTANT: "nurseassistant",
    StaffProfile.Role.QUEUE_OPERATOR: "queue",
    StaffProfile.Role.BIOMEDICAL: "biomedical",
    StaffProfile.Role.EMERGENCY: "emergency",
    StaffProfile.Role.STAFF: "staff",
    StaffProfile.Role.PHARMACIST: "pharmacist",
    StaffProfile.Role.CASHIER: "cashier",
}


class Command(BaseCommand):
    help = (
        "Turn every StaffProfile into an active login account. Existing usable "
        "passwords are preserved; one-time credentials are written to a CSV file."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--credentials-file",
            required=True,
            help="Destination CSV for newly generated one-time passwords.",
        )
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Generate a new password for every personnel account.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        destination = Path(options["credentials_file"]).expanduser()
        if destination.exists():
            raise CommandError(f"Credentials file already exists: {destination}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        user_model = get_user_model()
        counters = defaultdict(int)
        credentials = []
        renamed_count = 0
        activated_count = 0

        profiles = list(
            StaffProfile.objects.select_related("user").order_by("role", "user_id")
        )
        for profile in profiles:
            user = profile.user
            counters[profile.role] += 1

            # Replace the old demo-looking login while preserving the same user ID,
            # duties, care assignments, name, photo and audit relationships.
            if user.username.startswith("staff_demo_"):
                prefix = ROLE_USERNAME_PREFIX.get(profile.role, "staff")
                user.username = self._available_username(
                    user_model,
                    f"{prefix}{counters[profile.role]:03d}",
                    exclude_user_id=user.pk,
                )
                renamed_count += 1

            generated_password = None
            if options["reset_passwords"] or not user.has_usable_password():
                generated_password = secrets.token_urlsafe(14)
                user.set_password(generated_password)

            if not user.is_active:
                activated_count += 1
            user.is_active = True
            user.save()

            if generated_password:
                credentials.append(
                    {
                        "full_name": user.get_full_name() or user.username,
                        "role": profile.get_role_display(),
                        "username": user.username,
                        "temporary_password": generated_password,
                    }
                )

        try:
            with destination.open("x", encoding="utf-8-sig", newline="") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=["full_name", "role", "username", "temporary_password"],
                )
                writer.writeheader()
                writer.writerows(credentials)
        except OSError as exc:
            raise CommandError(f"Unable to write credentials file: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Personnel accounts ready: "
                f"{len(profiles)} total, {renamed_count} renamed, "
                f"{len(credentials)} passwords generated, {activated_count} activated."
            )
        )
        self.stdout.write(f"One-time credentials written to: {destination}")

    @staticmethod
    def _available_username(user_model, base, *, exclude_user_id):
        candidate = base
        suffix = 2
        while user_model.objects.exclude(pk=exclude_user_id).filter(username=candidate).exists():
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate
