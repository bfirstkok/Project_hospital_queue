import re
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from .models import ShiftSchedule, StaffProfile


ROOM_PATTERN = re.compile(r"(?:ประจำ\s*)?ห้อง(?:ตรวจ)?\s*([1-3])", re.IGNORECASE)


@dataclass(frozen=True)
class DoctorRoomAssignment:
    room: int
    shift: ShiftSchedule

    @property
    def duty_label(self):
        return (self.shift.note or "").strip() or f"ประจำห้องตรวจ {self.room}"


def room_from_duty_label(value):
    """Extract room 1-3 from roster duty text such as 'ประจำห้องตรวจ 1'."""
    match = ROOM_PATTERN.search((value or "").strip())
    return int(match.group(1)) if match else None


def _shift_contains_local_datetime(shift, local_now):
    local_date = local_now.date()
    local_time = local_now.time().replace(tzinfo=None)
    start = shift.start_time
    end = shift.end_time

    if start < end:
        return shift.shift_date == local_date and start <= local_time < end

    # Overnight shift: e.g. 20:00-08:00 belongs to the date on which it starts.
    if start > end:
        if shift.shift_date == local_date:
            return local_time >= start
        if shift.shift_date == local_date - timedelta(days=1):
            return local_time < end

    return False


def active_doctor_room_assignment(user, at=None):
    """Return the logged-in doctor's active room assignment from ShiftSchedule.

    The current roster already stores explicit duties in ShiftSchedule.note.
    Only a scheduled shift whose current time window is active and whose duty
    contains room 1-3 locks the doctor to that room.
    """
    if not getattr(user, "is_authenticated", False) or getattr(user, "is_superuser", False):
        return None

    profile = getattr(user, "hospital_staff_profile", None)
    if not profile or profile.role != StaffProfile.Role.DOCTOR:
        return None

    local_now = timezone.localtime(at or timezone.now())
    candidate_dates = (local_now.date(), local_now.date() - timedelta(days=1))
    shifts = (
        ShiftSchedule.objects.filter(
            user=user,
            shift_date__in=candidate_dates,
            status=ShiftSchedule.Status.SCHEDULED,
        )
        .order_by("-shift_date", "-start_time")
    )

    for shift in shifts:
        if not _shift_contains_local_datetime(shift, local_now):
            continue
        room = room_from_duty_label(shift.note)
        if room:
            return DoctorRoomAssignment(room=room, shift=shift)

    return None
