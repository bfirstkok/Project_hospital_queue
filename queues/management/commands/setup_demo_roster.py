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


TARGET_STAFF_PER_ROLE = 10
AUTO_ROSTER_MARKER = "AUTO_ROSTER"

ROLE_USERNAME_PREFIX = {
    StaffProfile.Role.DOCTOR: "doctor",
    StaffProfile.Role.NURSE: "nurse",
    StaffProfile.Role.NURSE_ASSISTANT: "nurseassistant",
    StaffProfile.Role.EMERGENCY: "emergency",
    StaffProfile.Role.STAFF: "staff",
    StaffProfile.Role.QUEUE_OPERATOR: "queue",
    StaffProfile.Role.BIOMEDICAL: "biomedical",
    StaffProfile.Role.PHARMACIST: "pharmacist",
    StaffProfile.Role.CASHIER: "cashier",
}

# These names are only used for accounts that are missing from the current
# database. Existing personnel and their names are never overwritten.
DEMO_FIRST_NAMES = [
    "กิตติพงศ์", "พิมพ์ชนก", "ณัฐวุฒิ", "ชลธิชา", "ธนภัทร",
    "สุภาวดี", "วราภรณ์", "ณิชาภัทร", "รัตนา", "กมลชนก",
    "ปวีณา", "ศิริพร", "อรอนงค์", "นันทวัฒน์", "จิราพร",
    "ภัทรพล", "อนุชา", "ธิดารัตน์", "เมธาวี", "วุฒิชัย",
]
DEMO_LAST_NAMES = [
    "วัฒนากุล", "ศรีสวัสดิ์", "เจริญสุข", "รัตนวงศ์", "บุญเรือง",
    "คำแสน", "สุขใจ", "ทองดี", "อินทร์แก้ว", "แสงจันทร์",
    "มั่นคง", "พรหมมา", "ชัยมงคล", "ใจกล้า", "แก้วประเสริฐ",
    "วงศ์คำ", "เดชรักษา", "เพิ่มพูล", "แซ่ตั้ง", "ปานทอง",
]

# Hospital-style 8-hour rotation. The shift_date belongs to the shift start.
SHIFT_TEMPLATES = [
    ("NIGHT", time(0, 0), time(8, 0), "เวรดึก"),
    ("MORNING", time(8, 0), time(16, 0), "เวรเช้า"),
    ("EVENING", time(16, 0), time(0, 0), "เวรบ่าย"),
]

# Coverage is deliberately higher during the daytime OPD peak. Core clinical
# roles remain available around the clock while support services are reduced
# overnight. With 10 people per role this also allows sensible weekly rotation.
SHIFT_ROLE_COVERAGE = {
    "NIGHT": {
        StaffProfile.Role.DOCTOR: 1,
        StaffProfile.Role.NURSE: 2,
        StaffProfile.Role.NURSE_ASSISTANT: 1,
        StaffProfile.Role.EMERGENCY: 1,
        StaffProfile.Role.STAFF: 0,
        StaffProfile.Role.QUEUE_OPERATOR: 1,
        StaffProfile.Role.BIOMEDICAL: 0,
        StaffProfile.Role.PHARMACIST: 1,
        StaffProfile.Role.CASHIER: 1,
    },
    "MORNING": {
        StaffProfile.Role.DOCTOR: 3,
        StaffProfile.Role.NURSE: 4,
        StaffProfile.Role.NURSE_ASSISTANT: 2,
        StaffProfile.Role.EMERGENCY: 2,
        StaffProfile.Role.STAFF: 2,
        StaffProfile.Role.QUEUE_OPERATOR: 2,
        StaffProfile.Role.BIOMEDICAL: 1,
        StaffProfile.Role.PHARMACIST: 2,
        StaffProfile.Role.CASHIER: 2,
    },
    "EVENING": {
        StaffProfile.Role.DOCTOR: 2,
        StaffProfile.Role.NURSE: 3,
        StaffProfile.Role.NURSE_ASSISTANT: 2,
        StaffProfile.Role.EMERGENCY: 2,
        StaffProfile.Role.STAFF: 1,
        StaffProfile.Role.QUEUE_OPERATOR: 1,
        StaffProfile.Role.BIOMEDICAL: 0,
        StaffProfile.Role.PHARMACIST: 1,
        StaffProfile.Role.CASHIER: 1,
    },
}


class Command(BaseCommand):
    help = (
        "Ensure every hospital role has at least 10 active demo users and build "
        "a balanced hospital-style weekly roster."
    )

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
        parser.add_argument(
            "--replace-week",
            action="store_true",
            help=(
                "Delete existing SCHEDULED shifts for non-admin personnel in the "
                "selected week before rebuilding. LEAVE/CANCELLED records are preserved."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        selected = self._selected_date(options.get("week"))
        week_start = selected - timedelta(days=selected.weekday())
        week_end = week_start + timedelta(days=6)
        credentials_path = Path(options["credentials_file"]).expanduser()
        if credentials_path.exists():
            raise CommandError(f"Credentials file already exists: {credentials_path}")

        credentials = self._ensure_minimum_staff()
        self._write_credentials(credentials_path, credentials)

        if options.get("replace_week"):
            ShiftSchedule.objects.filter(
                shift_date__range=(week_start, week_end),
                status=ShiftSchedule.Status.SCHEDULED,
                user__is_superuser=False,
            ).delete()

        staff_by_role = self._active_staff_by_role()
        missing_roles = [
            role
            for role in ROLE_USERNAME_PREFIX
            if len(staff_by_role.get(role, [])) < TARGET_STAFF_PER_ROLE
        ]
        if missing_roles:
            raise CommandError(
                "Unable to provision 10 active personnel for roles: "
                + ", ".join(missing_roles)
            )

        created_shifts = self._build_weekly_roster(
            week_start=week_start,
            staff_by_role=staff_by_role,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Roster ready for {week_start:%Y-%m-%d}: "
                f"{len(credentials)} accounts created and "
                f"{created_shifts} shifts created."
            )
        )
        self.stdout.write(
            "Active staff per role: "
            + ", ".join(
                f"{StaffProfile.Role(role).label}={len(staff_by_role[role])}"
                for role in ROLE_USERNAME_PREFIX
            )
        )
        self.stdout.write(f"Credentials written to: {credentials_path}")

    def _ensure_minimum_staff(self):
        user_model = get_user_model()
        credentials = []
        current = self._active_staff_by_role()

        for role_index, role in enumerate(ROLE_USERNAME_PREFIX):
            existing_count = len(current.get(role, []))
            missing = max(0, TARGET_STAFF_PER_ROLE - existing_count)
            if not missing:
                continue

            prefix = ROLE_USERNAME_PREFIX[role]
            next_number = 1
            for offset in range(missing):
                while user_model.objects.filter(
                    username=f"{prefix}{next_number:03d}"
                ).exists():
                    next_number += 1

                username = f"{prefix}{next_number:03d}"
                # Offset each role through the name pools so newly created
                # personnel do not all receive the same visible names.
                name_index = (
                    role_index * TARGET_STAFF_PER_ROLE
                    + existing_count
                    + offset
                ) % len(DEMO_FIRST_NAMES)
                surname_index = (
                    role_index * 3
                    + existing_count
                    + offset
                ) % len(DEMO_LAST_NAMES)

                password = secrets.token_urlsafe(14)
                user = user_model.objects.create_user(
                    username=username,
                    password=password,
                    first_name=DEMO_FIRST_NAMES[name_index],
                    last_name=DEMO_LAST_NAMES[surname_index],
                    is_active=True,
                )
                StaffProfile.objects.create(user=user, role=role)
                credentials.append(
                    {
                        "full_name": user.get_full_name(),
                        "role": StaffProfile.Role(role).label,
                        "username": username,
                        "temporary_password": password,
                    }
                )
                next_number += 1

        return credentials

    @staticmethod
    def _write_credentials(destination, credentials):
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("x", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "full_name",
                        "role",
                        "username",
                        "temporary_password",
                    ],
                )
                writer.writeheader()
                writer.writerows(credentials)
        except OSError as exc:
            raise CommandError(f"Unable to write credentials file: {exc}") from exc

    @staticmethod
    def _active_staff_by_role():
        staff_by_role = defaultdict(list)
        profiles = (
            StaffProfile.objects.select_related("user")
            .filter(user__is_active=True, user__is_superuser=False)
            .order_by("role", "user__first_name", "user__username")
        )
        for profile in profiles:
            staff_by_role[profile.role].append(profile.user)
        return staff_by_role

    def _build_weekly_roster(self, *, week_start, staff_by_role):
        created_shifts = 0
        weekly_assignments = defaultdict(int)
        last_assigned_date = {}
        previous_evening_by_role = defaultdict(set)

        # Existing leave records must be respected even when the scheduled
        # roster is rebuilt.
        leave_by_day = defaultdict(set)
        for user_id, shift_date in ShiftSchedule.objects.filter(
            shift_date__range=(week_start, week_start + timedelta(days=6)),
            status=ShiftSchedule.Status.LEAVE,
        ).values_list("user_id", "shift_date"):
            leave_by_day[shift_date].add(user_id)

        for day_offset in range(7):
            shift_date = week_start + timedelta(days=day_offset)
            assigned_today_by_role = defaultdict(set)
            evening_today_by_role = defaultdict(set)

            for shift_code, start_time, end_time, shift_label in SHIFT_TEMPLATES:
                coverage_map = SHIFT_ROLE_COVERAGE[shift_code]

                for role, coverage in coverage_map.items():
                    if coverage <= 0:
                        continue

                    people = staff_by_role[role]
                    blocked = set(assigned_today_by_role[role])
                    blocked.update(leave_by_day[shift_date])

                    # Prevent a 16:00-00:00 shift from being immediately
                    # followed by a 00:00-08:00 shift the next calendar day.
                    if shift_code == "NIGHT":
                        blocked.update(previous_evening_by_role[role])

                    candidates = [
                        person
                        for person in people
                        if person.id not in blocked
                    ]
                    candidates.sort(
                        key=lambda person: (
                            weekly_assignments[person.id],
                            last_assigned_date.get(person.id, date.min),
                            person.username,
                        )
                    )

                    if len(candidates) < coverage:
                        raise CommandError(
                            f"Not enough available {StaffProfile.Role(role).label} "
                            f"for {shift_date:%Y-%m-%d} {shift_label}: "
                            f"need {coverage}, available {len(candidates)}."
                        )

                    for person in candidates[:coverage]:
                        _shift, created = ShiftSchedule.objects.get_or_create(
                            user=person,
                            shift_date=shift_date,
                            start_time=start_time,
                            defaults={
                                "end_time": end_time,
                                "status": ShiftSchedule.Status.SCHEDULED,
                                "note": f"{shift_label} · {AUTO_ROSTER_MARKER}",
                            },
                        )
                        if created:
                            created_shifts += 1
                            weekly_assignments[person.id] += 1
                            last_assigned_date[person.id] = shift_date
                        assigned_today_by_role[role].add(person.id)
                        if shift_code == "EVENING":
                            evening_today_by_role[role].add(person.id)

            previous_evening_by_role = evening_today_by_role

        return created_shifts

    @staticmethod
    def _selected_date(value):
        if not value:
            return timezone.localdate()
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError("--week must use YYYY-MM-DD") from exc
