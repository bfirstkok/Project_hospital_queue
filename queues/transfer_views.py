from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .models import Queue, VisitWorkflowLog


EXAM_ROOMS = {1, 2, 3}


@require_POST
@transaction.atomic
def transfer_patient(request, visit_id: int):
    """Move a CALLED patient to another exam room or return them to the waiting queue."""
    queue_item = get_object_or_404(
        Queue.objects.select_for_update().select_related("visit", "visit__patient"),
        visit_id=visit_id,
    )
    visit = queue_item.visit
    reason = request.POST.get("reason", "").strip()
    destination = request.POST.get("destination", "").strip()
    next_page = request.POST.get("next", "queue")

    def go_back():
        return redirect("opd_list" if next_page == "opd" else "queue_list")

    if queue_item.status != Queue.Status.CALLED:
        messages.error(request, "ย้ายผู้ป่วยได้เฉพาะผู้ป่วยที่ถูกเรียกเข้าห้องตรวจแล้ว")
        return go_back()

    if len(reason) < 3:
        messages.error(request, "กรุณาระบุหมายเหตุหรือเหตุผลในการย้ายอย่างน้อย 3 ตัวอักษร")
        return go_back()

    old_room = queue_item.exam_room
    if destination == "waiting":
        queue_item.status = Queue.Status.WAITING_QUEUE
        queue_item.exam_room = None
        queue_item.save(update_fields=["status", "exam_room"])
        visit.called_at = None
        visit.save(update_fields=["called_at"])
        description = f"ส่งกลับไปรอเรียกคิวจากห้องตรวจ {old_room or '-'}: {reason}"
        details = {
            "action": "return_to_waiting",
            "from_room": old_room,
            "to_room": None,
            "queue_number": queue_item.display_number,
        }
        success_message = f"ส่ง {queue_item.display_number} กลับไปหน้ารอเรียกคิวแล้ว"
    elif destination.startswith("room_"):
        try:
            new_room = int(destination.split("_", 1)[1])
        except (TypeError, ValueError):
            new_room = None
        if new_room not in EXAM_ROOMS:
            messages.error(request, "ห้องตรวจปลายทางไม่ถูกต้อง")
            return go_back()
        if new_room == old_room:
            messages.error(request, f"ผู้ป่วยอยู่ห้องตรวจ {new_room} อยู่แล้ว กรุณาเลือกห้องอื่น")
            return go_back()

        queue_item.exam_room = new_room
        queue_item.save(update_fields=["exam_room"])
        description = f"ย้ายจากห้องตรวจ {old_room or '-'} ไปห้องตรวจ {new_room}: {reason}"
        details = {
            "action": "room_transfer",
            "from_room": old_room,
            "to_room": new_room,
            "queue_number": queue_item.display_number,
        }
        success_message = f"ย้าย {queue_item.display_number} ไปห้องตรวจ {new_room} แล้ว"
    else:
        messages.error(request, "กรุณาเลือกห้องตรวจปลายทางหรือส่งกลับไปรอเรียกคิว")
        return go_back()

    # Reuse the queue workflow event so the action remains visible in the existing timeline
    # without changing historical database schema. Details clearly mark this as a transfer.
    VisitWorkflowLog.record(
        visit=visit,
        event_type=VisitWorkflowLog.EventType.QUEUE_CALLED,
        actor=request.user,
        description=description,
        details=details,
    )
    messages.success(request, success_message + " และบันทึกหมายเหตุใน Log แล้ว")
    return go_back()
