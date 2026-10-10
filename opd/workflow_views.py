from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, DateTimeField, IntegerField, Q, When
from django.db.models.functions import Coalesce
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from queues.models import Queue, Visit, VisitWorkflowLog

from .models import (
    Bill,
    BillBalanceTransfer,
    MedicalCertificate,
    PatientCoverage,
    Prescription,
    PrescriptionItem,
    VisitAssessment,
)
from .medication_catalog import MEDICATION_CATALOG


COVERAGE_DEFAULT_PERCENT = {
    PatientCoverage.CoverageType.SELF_PAY: 0,
    PatientCoverage.CoverageType.UCS: 100,
    PatientCoverage.CoverageType.SSS: 100,
    PatientCoverage.CoverageType.CSMBS: 100,
    PatientCoverage.CoverageType.PRIVATE: 80,
    PatientCoverage.CoverageType.OTHER: 0,
}

PHARMACY_QUEUE_STATUSES = (
    Prescription.Status.SENT,
    Prescription.Status.PREPARING,
    Prescription.Status.READY,
)
BILLING_QUEUE_STATUSES = (
    Bill.Status.DRAFT,
    Bill.Status.READY,
    Bill.Status.WAIVED,
)


def _queue_ticket_for_visit(visit):
    try:
        return visit.queue.display_number
    except Queue.DoesNotExist:
        return f"Visit #{visit.pk}"


def _enter_billing_queue(bill, actor):
    if bill.billing_queue_entered_at is not None:
        return False
    entered_at = timezone.now()
    updated = Bill.objects.filter(
        pk=bill.pk,
        billing_queue_entered_at__isnull=True,
        status__in=BILLING_QUEUE_STATUSES,
        paid_at__isnull=True,
    ).update(billing_queue_entered_at=entered_at, updated_at=entered_at)
    if not updated:
        return False
    bill.billing_queue_entered_at = entered_at
    bill.updated_at = entered_at
    VisitWorkflowLog.record(
        visit=bill.visit,
        event_type=VisitWorkflowLog.EventType.BILLING_QUEUE_ENTERED,
        actor=actor,
        description="รายการถูกส่งเข้าคิวการเงิน",
        details={"bill_id": bill.id},
    )
    return True


def _visit_with_patient(visit_id):
    return get_object_or_404(
        Visit.objects.select_related("patient", "queue"),
        pk=visit_id,
    )


def _ensure_bill(visit, actor=None, pharmacy_skipped=False):
    coverage = PatientCoverage.objects.filter(patient=visit.patient, is_active=True).first()
    bill, created = Bill.objects.get_or_create(
        visit=visit,
        defaults={"coverage": coverage, "pharmacy_skipped": pharmacy_skipped},
    )
    decision_changed = pharmacy_skipped and not bill.pharmacy_skipped
    if decision_changed:
        bill.pharmacy_skipped = True
    if coverage and bill.coverage_id != coverage.id and bill.status != Bill.Status.PAID:
        bill.coverage = coverage
    bill.recalculate()
    if created or decision_changed:
        VisitWorkflowLog.record(
            visit=visit,
            event_type=VisitWorkflowLog.EventType.BILL_CREATED,
            actor=actor,
            description=(
                "แพทย์ยืนยันไม่มีรายการยากลับบ้านและส่งเคสให้การเงิน"
                if pharmacy_skipped
                else "สร้างรายการค่าใช้จ่ายหลังการตรวจ OPD"
            ),
            details={"bill_id": bill.id, "pharmacy_skipped": bill.pharmacy_skipped},
        )
    return bill


@login_required
def opd_care_plan(request, visit_id):
    visit = _visit_with_patient(visit_id)
    assessment = get_object_or_404(
        VisitAssessment.objects.select_related("examiner"),
        visit=visit,
    )
    prescription = Prescription.objects.filter(visit=visit).prefetch_related("items").first()
    certificate = MedicalCertificate.objects.filter(visit=visit).first()
    bill = Bill.objects.filter(visit=visit).select_related("coverage").first()

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        if action == "add_medication":
            existing_bill = Bill.objects.filter(visit=visit).first()
            if existing_bill and existing_bill.pharmacy_skipped:
                messages.error(request, "เคสนี้ส่งการเงินในฐานะไม่มีรายการยาแล้ว หากต้องเพิ่มยาให้ติดต่อเจ้าหน้าที่การเงินเพื่อดำเนินการแก้ไข")
                return redirect("opd_care_plan", visit_id=visit.id)
            medication_name = request.POST.get("medication_name", "").strip()
            if not medication_name:
                messages.error(request, "กรุณาระบุชื่อยา/เวชภัณฑ์")
                return redirect("opd_care_plan", visit_id=visit.id)
            try:
                quantity = max(1, int(request.POST.get("quantity") or 1))
                unit_price = Decimal(request.POST.get("unit_price") or "0")
                duration_raw = request.POST.get("duration_days", "").strip()
                duration_days = int(duration_raw) if duration_raw else None
            except (ValueError, InvalidOperation):
                messages.error(request, "จำนวนหรือราคายาไม่ถูกต้อง")
                return redirect("opd_care_plan", visit_id=visit.id)

            prescription, _ = Prescription.objects.get_or_create(
                visit=visit,
                defaults={"prescribed_by": assessment.examiner or request.user},
            )
            if prescription.status != Prescription.Status.DRAFT:
                messages.error(request, "ใบสั่งยาถูกส่งเข้าห้องยาแล้ว ไม่สามารถเพิ่มรายการจากหน้านี้ได้")
                return redirect("opd_care_plan", visit_id=visit.id)

            item = PrescriptionItem.objects.create(
                prescription=prescription,
                medication_name=medication_name[:180],
                strength=request.POST.get("strength", "").strip()[:80],
                dosage=request.POST.get("dosage", "").strip()[:120],
                frequency=request.POST.get("frequency", "").strip()[:120],
                duration_days=duration_days,
                quantity=quantity,
                unit=request.POST.get("unit", "").strip()[:40] or "หน่วย",
                instructions=request.POST.get("instructions", "").strip()[:255],
                unit_price=max(Decimal("0.00"), unit_price),
            )
            VisitWorkflowLog.record(
                visit=visit,
                event_type=VisitWorkflowLog.EventType.PRESCRIPTION_CREATED,
                actor=request.user,
                description=f"เพิ่มรายการยา {item.medication_name} จำนวน {item.quantity} {item.unit}",
                details={"prescription_id": prescription.id, "item_id": item.id},
            )
            _ensure_bill(visit, request.user)
            messages.success(request, "เพิ่มรายการยา/เวชภัณฑ์แล้ว")
            return redirect("opd_care_plan", visit_id=visit.id)

        if action == "send_pharmacy":
            prescription = Prescription.objects.filter(visit=visit).prefetch_related("items").first()
            if not prescription or not prescription.items.exists():
                messages.error(request, "ยังไม่มีรายการยา กรุณาเพิ่มรายการก่อนส่งห้องยา")
                return redirect("opd_care_plan", visit_id=visit.id)
            if prescription.status != Prescription.Status.DRAFT:
                messages.error(request, "ใบสั่งยานี้ถูกส่งเข้าห้องยาแล้ว")
                return redirect("opd_care_plan", visit_id=visit.id)
            prescription.status = Prescription.Status.SENT
            sent_at = timezone.now()
            if prescription.sent_at is None:
                prescription.sent_at = sent_at
            if prescription.pharmacy_queue_entered_at is None:
                prescription.pharmacy_queue_entered_at = sent_at
            prescription.save(update_fields=["status", "sent_at", "pharmacy_queue_entered_at", "updated_at"])
            bill = _ensure_bill(visit, request.user)
            _enter_billing_queue(bill, request.user)
            VisitWorkflowLog.record(
                visit=visit,
                event_type=VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED,
                actor=request.user,
                description="แพทย์ส่งใบสั่งยาไปห้องยา",
                details={"prescription_id": prescription.id, "status": prescription.status},
            )
            messages.success(request, "ส่งใบสั่งยาไปห้องยาแล้ว")
            return redirect("opd_care_plan", visit_id=visit.id)

        if action == "send_billing":
            prescription = Prescription.objects.filter(visit=visit).prefetch_related("items").first()
            if (
                prescription
                and prescription.status == Prescription.Status.DRAFT
                and prescription.items.exists()
            ):
                messages.error(
                    request,
                    "มีรายการยาที่ยังไม่ได้ส่ง กรุณากด “ส่งใบสั่งยาไปห้องยา” ก่อนส่งการเงิน",
                )
                return redirect("opd_care_plan", visit_id=visit.id)

            no_medication_ordered = not (prescription and prescription.items.exists())
            bill = _ensure_bill(
                visit,
                request.user,
                pharmacy_skipped=no_medication_ordered,
            )
            _enter_billing_queue(bill, request.user)
            if prescription and prescription.items.exists():
                messages.success(
                    request,
                    f"รายการการเงินพร้อมแล้ว · ยอดปัจจุบัน {bill.subtotal:.2f} บาท",
                )
            else:
                messages.success(
                    request,
                    f"ยืนยันไม่มีรายการยาและส่งค่าใช้จ่ายไปการเงินแล้ว · ยอดปัจจุบัน {bill.subtotal:.2f} บาท",
                )
            return redirect("opd_care_plan", visit_id=visit.id)

        if action == "issue_certificate":
            recommendation = request.POST.get("recommendation", "").strip()
            rest_from = request.POST.get("rest_from") or None
            rest_to = request.POST.get("rest_to") or None
            certificate, _ = MedicalCertificate.objects.update_or_create(
                visit=visit,
                defaults={
                    "issued_by": assessment.examiner or request.user,
                    "diagnosis_snapshot": assessment.diagnosis or "",
                    "recommendation": recommendation,
                    "rest_from": rest_from,
                    "rest_to": rest_to,
                    "issued_at": timezone.now(),
                },
            )
            VisitWorkflowLog.record(
                visit=visit,
                event_type=VisitWorkflowLog.EventType.MEDICAL_CERTIFICATE_ISSUED,
                actor=request.user,
                description="ออกใบรับรองแพทย์",
                details={"certificate_id": certificate.id},
            )
            messages.success(request, "สร้างใบรับรองแพทย์แล้ว")
            return redirect("opd_care_plan", visit_id=visit.id)

        return HttpResponseBadRequest("Unknown action")

    prescription_has_items = bool(
        prescription and any(True for _ in prescription.items.all())
    )
    billing_done = bool(
        bill and bill.status in {Bill.Status.PAID, Bill.Status.WAIVED}
    )
    pharmacy_done = bool(
        prescription and prescription.status == Prescription.Status.DISPENSED
    )
    pharmacy_skipped = bool(
        billing_done
        and (
            prescription is None
            or not prescription_has_items
            or prescription.status == Prescription.Status.CANCELLED
        )
    )
    no_medication_path = bool(
        bill
        and (
            bill.pharmacy_skipped
            or (
                billing_done
                and (
                    prescription is None
                    or not prescription_has_items
                    or prescription.status == Prescription.Status.CANCELLED
                )
            )
        )
    )
    medication_decision_done = bool(prescription_has_items or no_medication_path)
    handoff_started = bool(
        no_medication_path
        or (
            prescription
            and prescription.status != Prescription.Status.DRAFT
        )
    )
    visit_complete = bool(
        billing_done and (pharmacy_done or pharmacy_skipped)
    )
    prescription_locked = bool(
        billing_done
        and not (
            prescription
            and prescription.status == Prescription.Status.DRAFT
            and prescription_has_items
        )
    )
    can_edit_prescription = bool(
        not billing_done
        and not no_medication_path
        and (
            prescription is None
            or prescription.status == Prescription.Status.DRAFT
        )
    )

    departure_log = VisitWorkflowLog.objects.filter(
        visit=visit,
        event_type=VisitWorkflowLog.EventType.PATIENT_DEPARTED,
    ).first()

    if departure_log:
        next_action_label = "ผู้ป่วยออกจากโรงพยาบาลแล้ว"
        departed_at = timezone.localtime(departure_log.created_at).strftime("%d/%m/%Y %H:%M")
        next_action_detail = f"ยืนยันโดย {departure_log.actor_name or 'บุคลากร'} · {departed_at}"
    elif visit_complete:
        next_action_label = "พร้อมกลับบ้าน · รอเจ้าหน้าที่ปิด Visit"
        next_action_detail = (
            "ไม่มีรายการยาที่ต้องรับและการเงินเสร็จแล้ว · เจ้าหน้าที่จัดคิวยืนยันในหน้า “ปิด Visit” เมื่อผู้ป่วยออกจากโรงพยาบาลจริง"
            if pharmacy_skipped
            else "ห้องยาจ่ายยาและการเงินเสร็จแล้ว · เจ้าหน้าที่จัดคิวยืนยันในหน้า “ปิด Visit” เมื่อผู้ป่วยออกจากโรงพยาบาลจริง"
        )
    elif prescription and prescription.status == Prescription.Status.DRAFT and prescription_has_items:
        next_action_label = "ส่งใบสั่งยาไปห้องยา"
        next_action_detail = "มีรายการยาแล้ว กรุณาส่งใบสั่งยาให้ห้องยาดำเนินการ"
    elif prescription and prescription.status in {
        Prescription.Status.SENT,
        Prescription.Status.PREPARING,
        Prescription.Status.READY,
    }:
        next_action_label = "รอห้องยา"
        next_action_detail = prescription.get_status_display()
    elif not billing_done and not bill:
        next_action_label = "เลือกขั้นตอนหลังตรวจ"
        next_action_detail = "หากมียาให้เพิ่มรายการและส่งห้องยา หากไม่มียาให้ส่งค่าใช้จ่ายไปการเงิน"
    elif not billing_done:
        next_action_label = "รอการเงิน"
        next_action_detail = bill.get_status_display() if bill else "รอดำเนินการ"
    else:
        next_action_label = "ดำเนินการต่อ"
        next_action_detail = "ยังมีขั้นตอนหลังตรวจที่ต้องดำเนินการ"

    return render(request, "opd_care_plan.html", {
        "visit": visit,
        "assessment": assessment,
        "prescription": prescription,
        "certificate": certificate,
        "bill": bill,
        "prescription_has_items": prescription_has_items,
        "billing_done": billing_done,
        "pharmacy_done": pharmacy_done,
        "pharmacy_skipped": pharmacy_skipped,
        "no_medication_path": no_medication_path,
        "medication_decision_done": medication_decision_done,
        "handoff_started": handoff_started,
        "visit_complete": visit_complete,
        "departure_log": departure_log,
        "prescription_locked": prescription_locked,
        "can_edit_prescription": can_edit_prescription,
        "medication_catalog": MEDICATION_CATALOG,
        "next_action_label": next_action_label,
        "next_action_detail": next_action_detail,
    })


@login_required
def patient_departure_worklist(request):
    """Give queue operators a focused, auditable worklist for closing completed OPD visits."""
    search = request.GET.get("q", "").strip()
    assessments = VisitAssessment.objects.select_related(
        "visit",
        "visit__patient",
        "visit__queue",
        "visit__bill",
        "visit__prescription",
        "examiner",
    ).prefetch_related(
        "visit__prescription__items",
        "visit__workflow_logs",
    ).filter(
        Q(visit__queue__status__in=[Queue.Status.OPD_DONE, Queue.Status.FOLLOWUP])
        | Q(
            visit__queue__status=Queue.Status.DISCHARGED,
            visit__workflow_logs__event_type=VisitWorkflowLog.EventType.PATIENT_DEPARTED,
        )
    )
    if search:
        assessments = assessments.filter(
            Q(visit__patient__first_name__icontains=search)
            | Q(visit__patient__last_name__icontains=search)
            | Q(visit__patient__hn__icontains=search)
            | Q(visit__id__icontains=search)
        )
    paginator = Paginator(assessments.distinct().order_by("-updated_at", "-visit_id"), 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    rows = []
    for assessment in page_obj.object_list:
        visit = assessment.visit
        queue = visit.queue
        try:
            bill = visit.bill
        except Bill.DoesNotExist:
            bill = None
        try:
            prescription = visit.prescription
        except Prescription.DoesNotExist:
            prescription = None

        prescription_has_items = bool(prescription and prescription.items.exists())
        pharmacy_done = bool(prescription and prescription.status == Prescription.Status.DISPENSED)
        billing_done = bool(bill and bill.status in {Bill.Status.PAID, Bill.Status.WAIVED})
        pharmacy_skipped = bool(
            (bill and bill.pharmacy_skipped)
            or (
                billing_done
                and (
                    prescription is None
                    or not prescription_has_items
                    or prescription.status == Prescription.Status.CANCELLED
                )
            )
        )
        ready_to_close = billing_done and (pharmacy_done or pharmacy_skipped)
        departure_log = next(
            (
                log for log in visit.workflow_logs.all()
                if log.event_type == VisitWorkflowLog.EventType.PATIENT_DEPARTED
            ),
            None,
        )

        if departure_log:
            status_label = "ออกจากโรงพยาบาลแล้ว"
            status_detail = (
                f"ปิดโดย {departure_log.actor_name or 'บุคลากร'} · "
                f"{timezone.localtime(departure_log.created_at).strftime('%d/%m/%Y %H:%M')}"
            )
            status_class = "done"
        elif ready_to_close:
            status_label = "พร้อมปิด Visit"
            status_detail = "ห้องยาและการเงินครบแล้ว · ยืนยันเมื่อผู้ป่วยออกจากโรงพยาบาลจริง"
            status_class = "ready"
        elif bill and not billing_done:
            status_label = "รอการเงิน"
            status_detail = bill.get_status_display()
            status_class = "pending"
        elif prescription and prescription_has_items and not pharmacy_done:
            status_label = "รอห้องยา"
            status_detail = prescription.get_status_display()
            status_class = "pending"
        else:
            status_label = "รอแผนหลังตรวจ"
            status_detail = "รอแพทย์ส่งต่อห้องยา/การเงินให้ครบ"
            status_class = "pending"

        rows.append({
            "visit": visit,
            "queue": queue,
            "bill": bill,
            "prescription": prescription,
            "ready_to_close": ready_to_close,
            "departure_log": departure_log,
            "status_label": status_label,
            "status_detail": status_detail,
            "status_class": status_class,
        })

    return render(request, "opd/patient_departure_worklist.html", {
        "rows": rows,
        "page_obj": page_obj,
        "search": search,
        "ready_count": sum(1 for row in rows if row["ready_to_close"] and not row["departure_log"]),
    })


@login_required
@require_POST
def confirm_patient_departure(request, visit_id):
    """Record the queue operator's confirmation that the OPD patient left the facility."""
    visit = _visit_with_patient(visit_id)
    queue = getattr(visit, "queue", None)
    departure_event = VisitWorkflowLog.EventType.PATIENT_DEPARTED

    if VisitWorkflowLog.objects.filter(visit=visit, event_type=departure_event).exists():
        messages.info(request, "Visit นี้ถูกปิดและบันทึกการออกจากโรงพยาบาลไว้ก่อนหน้านี้แล้ว")
        return redirect("patient_departure_worklist")

    if not queue or queue.status not in {Queue.Status.OPD_DONE, Queue.Status.FOLLOWUP}:
        messages.error(request, "Visit นี้ไม่อยู่ในขั้นตอนหลังตรวจที่ปิดได้")
        return redirect("patient_departure_worklist")

    bill = getattr(visit, "bill", None)
    if not bill or bill.status not in {Bill.Status.PAID, Bill.Status.WAIVED}:
        messages.error(request, "ยังปิด Visit ไม่ได้: กรุณาดำเนินการการเงินให้เสร็จก่อน")
        return redirect("patient_departure_worklist")

    prescription = getattr(visit, "prescription", None)
    prescription_has_items = bool(prescription and prescription.items.exists())
    pharmacy_done = bool(
        prescription and prescription.status == Prescription.Status.DISPENSED
    ) or bool(
        not prescription_has_items
        or (bill.pharmacy_skipped and prescription.status == Prescription.Status.CANCELLED)
    )
    if not pharmacy_done:
        messages.error(request, "ยังปิด Visit ไม่ได้: กรุณาให้ห้องยาจ่ายยา หรือยืนยันว่าไม่มีรายการยาก่อน")
        return redirect("patient_departure_worklist")

    with transaction.atomic():
        queue = Queue.objects.select_for_update().get(pk=queue.pk)
        if queue.status not in {Queue.Status.OPD_DONE, Queue.Status.FOLLOWUP}:
            messages.error(request, "สถานะ Visit เปลี่ยนแล้ว กรุณารีเฟรชและตรวจสอบอีกครั้ง")
            return redirect("patient_departure_worklist")
        queue.status = Queue.Status.DISCHARGED
        queue.save(update_fields=["status"])
        VisitWorkflowLog.record(
            visit=visit,
            event_type=departure_event,
            actor=request.user,
            description="ยืนยันว่าผู้ป่วยออกจากโรงพยาบาลและปิด Visit แล้ว",
            details={
                "departure_status": "LEFT_FACILITY",
                "planned_destination": "HOME",
                "queue_status": Queue.Status.DISCHARGED,
            },
        )

    messages.success(request, f"ปิด Visit แล้ว · บันทึกว่าผู้ป่วย {visit.patient.first_name} {visit.patient.last_name} ออกจากโรงพยาบาลแล้ว")
    return redirect("patient_departure_worklist")


@login_required
@require_POST
def delete_prescription_item(request, item_id):
    item = get_object_or_404(
        PrescriptionItem.objects.select_related("prescription", "prescription__visit"),
        pk=item_id,
    )
    prescription = item.prescription
    visit = prescription.visit
    if prescription.status != Prescription.Status.DRAFT:
        messages.error(request, "ใบสั่งยาถูกส่งเข้าห้องยาแล้ว ไม่สามารถลบรายการได้")
        return redirect("opd_care_plan", visit_id=visit.id)
    item.delete()
    _ensure_bill(visit, request.user)
    messages.success(request, "ลบรายการแล้ว")
    return redirect("opd_care_plan", visit_id=visit.id)


@login_required
def pharmacy_worklist(request):
    prescriptions = list(
        Prescription.objects
        .select_related("visit", "visit__patient", "visit__queue", "prescribed_by")
        .prefetch_related("items")
        .filter(status__in=PHARMACY_QUEUE_STATUSES)
        .order_by(
            Case(
                When(pharmacy_queue_skipped_at__isnull=False, status__in=PHARMACY_QUEUE_STATUSES, then=2),
                When(pharmacy_queue_called_at__isnull=False, status__in=PHARMACY_QUEUE_STATUSES, then=0),
                When(status__in=PHARMACY_QUEUE_STATUSES, then=1),
                default=3,
                output_field=IntegerField(),
            ),
            Coalesce("pharmacy_queue_entered_at", "sent_at", "created_at", output_field=DateTimeField()),
            "pk",
        )
    )
    queue_position = 0
    for prescription in prescriptions:
        if prescription.status in PHARMACY_QUEUE_STATUSES:
            prescription.queue_display_number = _queue_ticket_for_visit(prescription.visit)
            prescription.queue_is_skipped = prescription.pharmacy_queue_skipped_at is not None
            prescription.queue_is_called = prescription.pharmacy_queue_called_at is not None and not prescription.queue_is_skipped
            if not prescription.queue_is_called and not prescription.queue_is_skipped:
                queue_position += 1
                prescription.queue_position = queue_position
            else:
                prescription.queue_position = None
        else:
            prescription.queue_position = None
            prescription.queue_display_number = None
            prescription.queue_is_called = False
            prescription.queue_is_skipped = False
        prescription.can_call_queue = False
        prescription.can_skip_queue = (
            prescription.status in PHARMACY_QUEUE_STATUSES
            and not prescription.queue_is_skipped
        )
    waiting = [
        rx for rx in prescriptions
        if rx.status in PHARMACY_QUEUE_STATUSES and not rx.queue_is_called and not rx.queue_is_skipped
    ]
    called_exists = any(
        rx.status in PHARMACY_QUEUE_STATUSES and rx.queue_is_called and not rx.queue_is_skipped
        for rx in prescriptions
    )
    if waiting and not called_exists:
        waiting[0].can_call_queue = True

    requested_id = request.GET.get("prescription_id", "")
    selected = next((rx for rx in prescriptions if str(rx.pk) == requested_id), None)
    if selected is None:
        if request.GET.get("queue_filter") == "SKIPPED":
            selected = next((rx for rx in prescriptions if rx.queue_is_skipped), None)
        else:
            selected = next((rx for rx in prescriptions if rx.queue_is_called), None)
            if selected is None:
                selected = next((rx for rx in prescriptions if not rx.queue_is_skipped), None)

    active_prescriptions = [rx for rx in prescriptions if not rx.queue_is_skipped]
    status_counts = {
        "SENT": sum(rx.status == Prescription.Status.SENT for rx in active_prescriptions),
        "PREPARING": sum(rx.status == Prescription.Status.PREPARING for rx in active_prescriptions),
        "READY": sum(rx.status == Prescription.Status.READY for rx in active_prescriptions),
        "ACTIVE": len(active_prescriptions),
        "SKIPPED": sum(rx.queue_is_skipped for rx in prescriptions),
    }
    dispensed_today = Prescription.objects.filter(
        status=Prescription.Status.DISPENSED,
        dispensed_at__date=timezone.localdate(),
    ).count()
    if selected:
        selected.next_status = {
            Prescription.Status.SENT: (Prescription.Status.PREPARING, "เริ่มจัดยา"),
            Prescription.Status.PREPARING: (Prescription.Status.READY, "ยาพร้อมจ่าย"),
            Prescription.Status.READY: (Prescription.Status.DISPENSED, "ยืนยันจ่ายยาแล้ว"),
        }.get(selected.status)
    return render(request, "pharmacy_worklist.html", {
        "prescriptions": prescriptions,
        "status_choices": Prescription.Status.choices,
        "selected_rx": selected,
        "pharmacy_status_counts": status_counts,
        "pharmacy_dispensed_today": dispensed_today,
    })


@login_required
@require_POST
def pharmacy_queue_action(request, prescription_id):
    return _service_queue_action(
        request,
        model=Prescription,
        record_id=prescription_id,
        service="pharmacy",
        statuses=PHARMACY_QUEUE_STATUSES,
        entered_field="pharmacy_queue_entered_at",
        called_field="pharmacy_queue_called_at",
        skipped_field="pharmacy_queue_skipped_at",
        time_order_fields=("pharmacy_queue_entered_at", "sent_at", "created_at"),
        return_to="pharmacy_worklist",
    )


@login_required
@require_POST
def pharmacy_update_status(request, prescription_id):
    prescription = get_object_or_404(
        Prescription.objects.select_related("visit", "visit__patient"),
        pk=prescription_id,
    )
    status = request.POST.get("status", "").strip()
    allowed = {
        Prescription.Status.SENT,
        Prescription.Status.PREPARING,
        Prescription.Status.READY,
        Prescription.Status.DISPENSED,
        Prescription.Status.CANCELLED,
    }
    if status not in allowed:
        return HttpResponseBadRequest("Invalid pharmacy status")

    prescription.status = status
    update_fields = ["status", "updated_at"]
    if status == Prescription.Status.DISPENSED:
        prescription.dispensed_at = timezone.now()
        prescription.dispensed_by = request.user
        update_fields.extend(["dispensed_at", "dispensed_by"])
    prescription.save(update_fields=update_fields)

    _ensure_bill(prescription.visit, request.user)
    VisitWorkflowLog.record(
        visit=prescription.visit,
        event_type=VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED,
        actor=request.user,
        description=f"ห้องยาเปลี่ยนสถานะเป็น {prescription.get_status_display()}",
        details={"prescription_id": prescription.id, "status": status},
    )
    messages.success(request, f"อัปเดตใบสั่งยา Visit#{prescription.visit_id} เป็น {prescription.get_status_display()}")
    if request.POST.get("return_prescription_id") == str(prescription.pk):
        return redirect(f"{reverse('pharmacy_worklist')}?prescription_id={prescription.pk}")
    return redirect("pharmacy_worklist")


@login_required
def billing_worklist(request):
    active_billing = (
        Q(billing_queue_entered_at__isnull=False)
        & (
            Q(status__in=(Bill.Status.DRAFT, Bill.Status.READY))
            | Q(status=Bill.Status.WAIVED, paid_at__isnull=True)
        )
    )
    billing_history = (
        Q(status__in=(Bill.Status.PAID, Bill.Status.CANCELLED, Bill.Status.TRANSFERRED))
        | Q(status=Bill.Status.WAIVED, paid_at__isnull=False)
    )
    bills = list(
        Bill.objects
        .filter(active_billing | billing_history)
        .select_related("visit", "visit__patient", "visit__queue", "coverage", "received_by")
        .order_by(
            Case(
                When(active_billing & Q(billing_queue_skipped_at__isnull=False), then=2),
                When(active_billing & Q(billing_queue_called_at__isnull=False), then=0),
                When(active_billing, then=1),
                default=3,
                output_field=IntegerField(),
            ),
            Coalesce("billing_queue_entered_at", "created_at", output_field=DateTimeField()),
            "pk",
        )
    )
    queue_position = 0
    for bill in bills:
        if bill.billing_queue_entered_at is not None and bill.status in BILLING_QUEUE_STATUSES and bill.paid_at is None:
            bill.queue_display_number = _queue_ticket_for_visit(bill.visit)
            bill.queue_is_skipped = bill.billing_queue_skipped_at is not None
            bill.queue_is_called = bill.billing_queue_called_at is not None and not bill.queue_is_skipped
            if not bill.queue_is_called and not bill.queue_is_skipped:
                queue_position += 1
                bill.queue_position = queue_position
            else:
                bill.queue_position = None
        else:
            bill.queue_position = None
            bill.queue_display_number = None
            bill.queue_is_called = False
            bill.queue_is_skipped = False
        bill.can_call_queue = False
        bill.can_skip_queue = (
            bill.queue_display_number is not None and not bill.queue_is_skipped
        )
    waiting = [
        bill for bill in bills
        if bill.billing_queue_entered_at is not None
        and bill.status in BILLING_QUEUE_STATUSES
        and bill.paid_at is None
        and not bill.queue_is_called
        and not bill.queue_is_skipped
    ]
    called_exists = any(
        bill.billing_queue_entered_at is not None
        and bill.status in BILLING_QUEUE_STATUSES
        and bill.paid_at is None
        and bill.queue_is_called
        and not bill.queue_is_skipped
        for bill in bills
    )
    if waiting and not called_exists:
        waiting[0].can_call_queue = True

    pending_bills = [
        bill for bill in bills
        if bill.billing_queue_entered_at is not None
        and bill.status in BILLING_QUEUE_STATUSES
        and bill.paid_at is None
    ]
    active_bills = [bill for bill in pending_bills if not bill.queue_is_skipped]
    skipped_bills = [bill for bill in pending_bills if bill.queue_is_skipped]
    today = timezone.localdate()
    paid_today = [
        bill for bill in bills
        if bill.paid_at is not None
        and timezone.localtime(bill.paid_at).date() == today
        and bill.status in (Bill.Status.PAID, Bill.Status.WAIVED)
    ]
    selected_bill = next(
        (bill for bill in bills if str(bill.pk) == request.GET.get("bill_id")),
        None,
    )
    if selected_bill is None:
        default_bills = (
            skipped_bills
            if request.GET.get("queue_filter") == "SKIPPED"
            else active_bills
        )
        selected_bill = next(iter(default_bills), None)
    prescription = (
        Prescription.objects.filter(visit=selected_bill.visit)
        .prefetch_related("items")
        .first()
        if selected_bill else None
    )
    if selected_bill:
        selected_bill.carried_transfers = list(
            BillBalanceTransfer.objects.filter(target_visit=selected_bill.visit)
            .select_related("source_bill", "source_bill__visit")
        )
    billing_summary = {
        "active": len(active_bills),
        "skipped": len(skipped_bills),
        "review": sum(bill.status == Bill.Status.DRAFT for bill in active_bills),
        "awaiting_payment": sum(bill.status in (Bill.Status.READY, Bill.Status.WAIVED) for bill in active_bills),
        "paid_today": len(paid_today),
        "paid_today_total": sum((bill.patient_due for bill in paid_today), Decimal("0.00")),
        "cancelled": sum(bill.status == Bill.Status.CANCELLED for bill in bills),
        "transferred": sum(bill.status == Bill.Status.TRANSFERRED for bill in bills),
    }
    return render(request, "billing_worklist.html", {
        "bills": bills,
        "selected_bill": selected_bill,
        "selected_prescription": prescription,
        "billing_summary": billing_summary,
    })


@login_required
@require_POST
def billing_queue_action(request, bill_id):
    return _service_queue_action(
        request,
        model=Bill,
        record_id=bill_id,
        service="billing",
        statuses=BILLING_QUEUE_STATUSES,
        entered_field="billing_queue_entered_at",
        called_field="billing_queue_called_at",
        skipped_field="billing_queue_skipped_at",
        time_order_fields=("billing_queue_entered_at", "created_at"),
        return_to="billing_worklist",
    )


def _service_queue_action(
    request,
    *,
    model,
    record_id,
    service,
    statuses,
    entered_field,
    called_field,
    skipped_field,
    time_order_fields,
    return_to,
):
    action = request.POST.get("action", "").strip()
    if action not in {"call", "skip", "requeue"}:
        return HttpResponseBadRequest("Invalid queue action")

    now = timezone.now()
    with transaction.atomic():
        active = model.objects.select_for_update().filter(status__in=statuses)
        if model is Bill:
            active = active.filter(billing_queue_entered_at__isnull=False, paid_at__isnull=True)
        ordered = list(
            active.select_related("visit")
            .order_by(
                Coalesce(*time_order_fields, output_field=DateTimeField()),
                "pk",
            )
        )
        target = next((row for row in ordered if row.pk == record_id), None)
        if target is None:
            messages.error(request, "รายการนี้ไม่อยู่ในคิวที่กำลังให้บริการแล้ว")
            return redirect(return_to)

        called = [row for row in ordered if getattr(row, called_field) and not getattr(row, skipped_field)]
        waiting = [
            row for row in ordered
            if not getattr(row, called_field) and not getattr(row, skipped_field)
        ]
        description = ""
        if action == "call":
            if called:
                messages.error(request, "ยังมีคิวที่กำลังเรียกอยู่ กรุณาบันทึกผลก่อนเรียกคิวถัดไป")
                return redirect(return_to)
            if not waiting or waiting[0].pk != target.pk:
                messages.error(request, "เรียกคิวได้ตามลำดับก่อนมาถึงเท่านั้น")
                return redirect(return_to)
            setattr(target, called_field, now)
            description = f"เรียกคิว{('ห้องยา' if service == 'pharmacy' else 'การเงิน')}"
        elif action == "skip":
            if getattr(target, skipped_field):
                messages.error(request, "รายการนี้อยู่ในหมวดค้าง / ไม่มาแล้ว")
                return redirect(return_to)
            setattr(target, called_field, None)
            setattr(target, skipped_field, now)
            description = f"ข้ามคิว{('ห้องยา' if service == 'pharmacy' else 'การเงิน')} · ย้ายผู้ป่วยไปหมวดค้าง / ไม่มา"
        else:
            if not getattr(target, skipped_field):
                messages.error(request, "นำกลับเข้าคิวได้เฉพาะรายการที่ถูกข้ามคิว")
                return redirect(return_to)
            setattr(target, called_field, None)
            setattr(target, skipped_field, None)
            setattr(target, entered_field, now)
            description = f"นำคิว{('ห้องยา' if service == 'pharmacy' else 'การเงิน')}กลับเข้าท้ายแถว"

        update_fields = [called_field, skipped_field, "updated_at"]
        if action == "requeue":
            update_fields.append(entered_field)
        target.save(update_fields=update_fields)
        VisitWorkflowLog.record(
            visit=target.visit,
            event_type=VisitWorkflowLog.EventType.SERVICE_QUEUE_ACTION,
            actor=request.user,
            description=description,
            details={"service": service, "action": action, "record_id": target.pk},
        )

    messages.success(request, {
        "call": "เรียกคิวแล้ว",
        "skip": "ย้ายไปหมวดค้าง / ไม่มาแล้ว · สามารถเรียกคิวถัดไปได้",
        "requeue": "นำกลับเข้าคิวท้ายแถวแล้ว",
    }[action])
    return redirect(return_to)


@require_GET
def service_queue_display(request):
    """Choose a service-counter board without combining separate service queues."""
    return render(request, "service_queue_selector.html")


def _number_public_queue(items, *, called_field, skipped_field, entered_at):
    position = 0
    for item in items:
        item.queue_display_number = _queue_ticket_for_visit(item.visit)
        item.queue_is_called = getattr(item, called_field) is not None and getattr(item, skipped_field) is None
        item.queue_is_skipped = getattr(item, skipped_field) is not None
        item.queue_wait_started = entered_at(item)
        if not item.queue_is_called and not item.queue_is_skipped:
            position += 1
            item.queue_position = position
        else:
            item.queue_position = None
    return items


@require_GET
def pharmacy_queue_display(request):
    """Public pharmacy board: show queue numbers only, never patient details."""
    prescriptions = list(
        Prescription.objects.filter(status__in=PHARMACY_QUEUE_STATUSES)
        .select_related("visit", "visit__queue")
        .order_by(
            Case(
                When(pharmacy_queue_skipped_at__isnull=False, then=2),
                When(pharmacy_queue_called_at__isnull=False, then=0),
                default=1,
                output_field=IntegerField(),
            ),
            Coalesce("pharmacy_queue_entered_at", "sent_at", "created_at", output_field=DateTimeField()),
            "pk",
        )
    )
    _number_public_queue(
        prescriptions,
        called_field="pharmacy_queue_called_at",
        skipped_field="pharmacy_queue_skipped_at",
        entered_at=lambda prescription: prescription.pharmacy_queue_entered_at or prescription.sent_at or prescription.created_at,
    )
    for prescription in prescriptions:
        prescription.queue_status_label = {
            Prescription.Status.SENT: "รอรับใบสั่งยา",
            Prescription.Status.PREPARING: "กำลังจัดยา",
            Prescription.Status.READY: "พร้อมจ่ายยา",
        }.get(prescription.status, "รอรับยา")
    return render(request, "service_queue_display.html", {
        "queue_items": [rx for rx in prescriptions if not rx.queue_is_skipped],
        "skipped_queue_items": [rx for rx in prescriptions if rx.queue_is_skipped],
        "lane_title": "คิวห้องยา",
        "empty_message": "ขณะนี้ยังไม่มีคิวห้องยา",
        "called_message": "กำลังเรียก · เชิญที่ห้องยา",
    })


@require_GET
def billing_queue_display(request):
    """Public billing board: show queue numbers only, never patient details."""
    open_bills = (
        Q(status__in=(Bill.Status.DRAFT, Bill.Status.READY))
        | Q(status=Bill.Status.WAIVED, paid_at__isnull=True)
    )
    bills = list(
        Bill.objects
        .filter(billing_queue_entered_at__isnull=False)
        .filter(open_bills)
        .select_related("visit", "visit__queue")
        .order_by(
            Case(
                When(billing_queue_skipped_at__isnull=False, then=2),
                When(billing_queue_called_at__isnull=False, then=0),
                default=1,
                output_field=IntegerField(),
            ),
            "billing_queue_entered_at",
            "pk",
        )
    )
    _number_public_queue(
        bills,
        called_field="billing_queue_called_at",
        skipped_field="billing_queue_skipped_at",
        entered_at=lambda bill: bill.billing_queue_entered_at,
    )
    for bill in bills:
        bill.queue_status_label = {
            Bill.Status.DRAFT: "รอตรวจยอด",
            Bill.Status.WAIVED: "รอปิดยอด",
            Bill.Status.READY: "รอชำระเงิน",
        }.get(bill.status, "รอชำระเงิน")
    return render(request, "service_queue_display.html", {
        "queue_items": [bill for bill in bills if not bill.queue_is_skipped],
        "skipped_queue_items": [bill for bill in bills if bill.queue_is_skipped],
        "lane_title": "คิวชำระเงิน",
        "empty_message": "ขณะนี้ยังไม่มีคิวการเงิน",
        "called_message": "กำลังเรียก · เชิญที่การเงิน",
    })


@login_required
def billing_detail(request, bill_id):
    bill = get_object_or_404(
        Bill.objects.select_related("visit", "visit__patient", "coverage"),
        pk=bill_id,
    )
    visit = bill.visit
    prescription = Prescription.objects.filter(visit=visit).prefetch_related("items").first()
    carried_transfers = list(
        BillBalanceTransfer.objects.filter(target_visit=visit)
        .select_related("source_bill", "source_bill__visit", "transferred_by")
    )
    source_transfer = (
        BillBalanceTransfer.objects.filter(source_bill=bill)
        .select_related("target_visit", "transferred_by")
        .first()
    )

    if request.method == "POST":
        if bill.status == Bill.Status.TRANSFERRED:
            messages.error(request, "บิลนี้ถูกโอนยอดไปรวมกับ Visit ใหม่แล้ว ไม่สามารถแก้ไขหรือรับชำระซ้ำได้")
            return redirect("billing_detail", bill_id=bill.id)
        coverage_type = request.POST.get("coverage_type", PatientCoverage.CoverageType.SELF_PAY)
        valid_types = set(PatientCoverage.CoverageType.values)
        if coverage_type not in valid_types:
            coverage_type = PatientCoverage.CoverageType.SELF_PAY

        default_percent = COVERAGE_DEFAULT_PERCENT.get(coverage_type, 0)
        try:
            coverage_percent = int(request.POST.get("coverage_percent", default_percent))
            coverage_percent = max(0, min(coverage_percent, 100))
            other_fee = max(Decimal("0.00"), Decimal(request.POST.get("other_fee") or "0"))
        except (ValueError, InvalidOperation):
            messages.error(request, "เปอร์เซ็นต์สิทธิหรือค่าใช้จ่ายเพิ่มเติมไม่ถูกต้อง")
            return redirect("billing_detail", bill_id=bill.id)

        coverage, _ = PatientCoverage.objects.update_or_create(
            patient=visit.patient,
            defaults={
                "coverage_type": coverage_type,
                "member_no": request.POST.get("member_no", "").strip()[:80],
                "coverage_percent": coverage_percent,
                "note": request.POST.get("coverage_note", "").strip()[:255],
                "is_active": True,
            },
        )
        bill.coverage = coverage
        bill.other_fee = other_fee
        bill.recalculate()
        messages.success(request, "คำนวณค่าใช้จ่ายและสิทธิใหม่แล้ว")
        return redirect("billing_detail", bill_id=bill.id)

    bill.recalculate()
    return render(request, "billing_detail.html", {
        "bill": bill,
        "visit": visit,
        "prescription": prescription,
        "carried_transfers": carried_transfers,
        "source_transfer": source_transfer,
        "coverage_choices": PatientCoverage.CoverageType.choices,
        "coverage_defaults": COVERAGE_DEFAULT_PERCENT,
    })


@login_required
@require_POST
@transaction.atomic
def billing_pay(request, bill_id):
    bill = get_object_or_404(
        Bill.objects.select_for_update(of=("self",)).select_related("visit", "coverage"),
        pk=bill_id,
    )
    if bill.status == Bill.Status.TRANSFERRED:
        messages.error(request, "บิลนี้ถูกโอนยอดไปรวมกับบิลของ Visit ใหม่แล้ว ระบบป้องกันการชำระซ้ำ")
        return redirect("billing_worklist")
    if bill.status in {Bill.Status.PAID, Bill.Status.CANCELLED}:
        messages.error(request, "บิลนี้ไม่อยู่ในสถานะที่รับชำระได้")
        return redirect("billing_worklist")
    bill.recalculate()
    bill.received_by = request.user
    bill.paid_at = timezone.now()
    bill.status = Bill.Status.WAIVED if bill.patient_due == 0 else Bill.Status.PAID
    bill.save(update_fields=["received_by", "paid_at", "status", "updated_at"])
    VisitWorkflowLog.record(
        visit=bill.visit,
        event_type=VisitWorkflowLog.EventType.PAYMENT_RECEIVED,
        actor=request.user,
        description=f"ปิดรายการการเงิน ยอดผู้ป่วยชำระ {bill.patient_due:.2f} บาท",
        details={"bill_id": bill.id, "patient_due": str(bill.patient_due), "status": bill.status},
    )
    messages.success(request, "บันทึกการชำระเงินเรียบร้อยแล้ว")
    return redirect("billing_receipt", bill_id=bill.id)


@login_required
def billing_receipt(request, bill_id):
    bill = get_object_or_404(
        Bill.objects.select_related("visit", "visit__patient", "coverage", "received_by"),
        pk=bill_id,
    )
    prescription = Prescription.objects.filter(visit=bill.visit).prefetch_related("items").first()
    carried_transfers = list(
        BillBalanceTransfer.objects.filter(target_visit=bill.visit)
        .select_related("source_bill", "source_bill__visit")
    )
    return render(request, "billing_receipt.html", {
        "bill": bill,
        "visit": bill.visit,
        "prescription": prescription,
        "carried_transfers": carried_transfers,
    })


@login_required
def medical_certificate_print(request, certificate_id):
    certificate = get_object_or_404(
        MedicalCertificate.objects.select_related(
            "visit",
            "visit__patient",
            "issued_by",
            "visit__opd_assessment",
        ),
        pk=certificate_id,
    )
    return render(request, "medical_certificate_print.html", {
        "certificate": certificate,
        "visit": certificate.visit,
    })


@login_required
def clinical_summary_print(request, visit_id):
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "vitals", "queue"),
        pk=visit_id,
    )
    assessment = get_object_or_404(
        VisitAssessment.objects.select_related("examiner"),
        visit=visit,
    )
    prescription = Prescription.objects.filter(visit=visit).prefetch_related("items").first()
    return render(request, "clinical_summary_print.html", {
        "visit": visit,
        "assessment": assessment,
        "prescription": prescription,
    })
