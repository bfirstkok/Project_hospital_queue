import csv
import secrets
from collections import defaultdict
from datetime import date, time, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from queues.models import ShiftSchedule, StaffProfile


ADDITIONAL_STAFF = [
    ("nurseassistant003", "ชนากานต์", "มณีวงศ์", StaffProfile.Role.NURSE_ASSISTANT),
    ("nurseassistant004", "ปณิตา", "บุญช่วย", StaffProfile.Role.NURSE_ASSISTANT),
    ("emergency003", "เกรียงไกร", "ภูมิรักษ์", StaffProfile.Role.EMERGENCY),
    ("emergency004", "ศุภชัย", "คงมั่น", StaffProfile.Role.EMERGENCY),
    ("staff004", "อภิญญา", "วงศ์สวัสดิ์", StaffProfile.Role.STAFF),
    ("staff005", "ธนกฤต", "พูนทรัพย์", StaffProfile.Role.STAFF),
    ("pharmacist001", "พิชชาภา", "โอสถดี", StaffProfile.Role.PHARMACIST),
    ("cashier001", "ศิริพร", "การเงิน", StaffProfile.Role.CASHIER),
]

SHIFT_TEMPLATES = [
    (time(0, 0), time(8, 0), "เวรดึก"),
    (time(8, 0), time(16, 0), "เวรเช้า"),
    (time(16, 0), time(0, 0), "เวรบ่าย"),
]

ROLE_COVERAGE = {
    StaffProfile.Role.DOCTOR: 1,
    StaffProfile.Role.NURSE: 2,
    StaffProfile.Role.NURSE_ASSISTANT: 1,
    StaffProfile.Role.EMERGENCY: 1,
    StaffProfile.Role.STAFF: 1,
    StaffProfile.Role.PHARMACIST: 1,
    StaffProfile.Role.CASHIER: 1,
}


class Command(BaseCommand):
    help = "Add missing demo personnel and build a balanced weekly roster."

    def add_arguments(self, parser):
        parser.add_argument(
            "--week",
            help="Any date in the requested week (YYYY-MM-DD); defaults to today.",
        )
        parser.add_argument(
            "--credentials-file",
            required=True,
            help="CSV destination for credentials of accounts created by this run.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        selected = self._selected_date(options.get("week"))
        week_start = selected - timedelta(days=selected.weekday())
        credentials_path = Path(options["credentials_file"]).expanduser()
        if credentials_path.exists():
            raise CommandError(f"Credentials file already exists: {credentials_path}")

        user_model = get_user_model()
        credentials = []
        for username, first_name, last_name, role in ADDITIONAL_STAFF:
            user, created = user_model.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_active": True,
                },
            )
            if created:
                password = secrets.token_urlsafe(14)
                user.set_password(password)
                user.save(update_fields=["password"])
                credentials.append({
                    "full_name": user.get_full_name(),
                    "role": StaffProfile.Role(role).label,
                    "username": username,
                    "temporary_password": password,
                })
            StaffProfile.objects.update_or_create(user=user, defaults={"role": role})

        credentials_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with credentials_path.open("x", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["full_name", "role", "username", "temporary_password"],
                )
                writer.writeheader()
                writer.writerows(credentials)
        except OSError as exc:
            raise CommandError(f"Unable to write credentials file: {exc}") from exc

        staff_by_role = defaultdict(list)
        profiles = (
            StaffProfile.objects.select_related("user")
            .filter(user__is_active=True, user__is_superuser=False)
            .order_by("role", "user__first_name", "user__username")
        )
        for profile in profiles:
            staff_by_role[profile.role].append(profile.user)

        missing_roles = [role for role in ROLE_COVERAGE if not staff_by_role[role]]
        if missing_roles:
            raise CommandError(f"No active personnel for roles: {', '.join(missing_roles)}")

        created_shifts = 0
        assignment_counters = defaultdict(int)
        for day_offset in range(7):
            shift_date = week_start + timedelta(days=day_offset)
            for shift_index, (start_time, end_time, shift_label) in enumerate(SHIFT_TEMPLATES):
                for role, coverage in ROLE_COVERAGE.items():
                    # General staff cover the public-facing morning/evening periods.
                    if role == StaffProfile.Role.STAFF and shift_index == 0:
                        continue
                    people = staff_by_role[role]
                    for _slot in range(coverage):
                        person = people[assignment_counters[role] % len(people)]
                        assignment_counters[role] += 1
                        _shift, created = ShiftSchedule.objects.get_or_create(
                            user=person,
                            shift_date=shift_date,
                            start_time=start_time,
                            defaults={
                                "end_time": end_time,
                                "status": ShiftSchedule.Status.SCHEDULED,
                                "note": shift_label,
                            },
                        )
                        created_shifts += int(created)

        self.stdout.write(self.style.SUCCESS(
            f"Roster ready for {week_start:%Y-%m-%d}: "
            f"{len(credentials)} accounts and {created_shifts} shifts created."
        ))
        self.stdout.write(f"Credentials written to: {credentials_path}")

    @staticmethod
    def _selected_date(value):
        if not value:
            return timezone.localdate()
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError("--week must use YYYY-MM-DD") from exc
