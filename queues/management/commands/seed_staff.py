from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from queues.models import StaffProfile


STAFF_MEMBERS = [
    ("กิตติพงศ์", "วัฒนากุล", StaffProfile.Role.DOCTOR),
    ("พิมพ์ชนก", "ศรีสวัสดิ์", StaffProfile.Role.DOCTOR),
    ("ณัฐวุฒิ", "เจริญสุข", StaffProfile.Role.DOCTOR),
    ("ชลธิชา", "รัตนวงศ์", StaffProfile.Role.DOCTOR),
    ("ธนภัทร", "บุญเรือง", StaffProfile.Role.DOCTOR),
    ("สุภาวดี", "คำแสน", StaffProfile.Role.NURSE),
    ("วราภรณ์", "สุขใจ", StaffProfile.Role.NURSE),
    ("ณิชาภัทร", "ทองดี", StaffProfile.Role.NURSE),
    ("รัตนา", "อินทร์แก้ว", StaffProfile.Role.NURSE),
    ("กมลชนก", "แสงจันทร์", StaffProfile.Role.NURSE),
    ("ปวีณา", "มั่นคง", StaffProfile.Role.NURSE),
    ("ศิริพร", "พรหมมา", StaffProfile.Role.NURSE),
    ("อรอนงค์", "ชัยมงคล", StaffProfile.Role.NURSE),
    ("นันทวัฒน์", "ใจกล้า", StaffProfile.Role.NURSE),
    ("จิราพร", "แก้วประเสริฐ", StaffProfile.Role.NURSE_ASSISTANT),
    ("ภัทรพล", "วงศ์คำ", StaffProfile.Role.NURSE_ASSISTANT),
    ("อนุชา", "เดชรักษา", StaffProfile.Role.EMERGENCY),
    ("ธิดารัตน์", "เพิ่มพูล", StaffProfile.Role.EMERGENCY),
    ("เมธาวี", "แซ่ตั้ง", StaffProfile.Role.STAFF),
    ("วุฒิชัย", "ปานทอง", StaffProfile.Role.STAFF),
]


class Command(BaseCommand):
    help = "Create or update 20 safe demo staff directory entries."

    @transaction.atomic
    def handle(self, *args, **options):
        user_model = get_user_model()
        created_count = 0

        for index, (first_name, last_name, role) in enumerate(STAFF_MEMBERS, start=1):
            username = f"staff_demo_{index:03d}"
            user, created = user_model.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_active": True,
                },
            )
            user.first_name = first_name
            user.last_name = last_name
            user.is_active = True
            user.set_unusable_password()
            user.save(update_fields=["first_name", "last_name", "is_active", "password"])
            StaffProfile.objects.update_or_create(user=user, defaults={"role": role})
            created_count += int(created)

        self.stdout.write(self.style.SUCCESS(
            f"Staff directory ready: {len(STAFF_MEMBERS)} entries ({created_count} created)."
        ))
