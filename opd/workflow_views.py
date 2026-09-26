from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, IntegerField, When
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from queues.models import Visit, VisitWorkflowLog

from .models import (
    Bill,
    MedicalCertificate,
    PatientCoverage,
    Prescription,
    PrescriptionItem,
    VisitAssessment,
)


COVERAGE_DEFAULT_PERCENT = {
    PatientCoverage.CoverageType.SELF_PAY: 0,
    PatientCoverage.CoverageType.UCS: 100,
    PatientCoverage.CoverageType.SSS: 100,
    PatientCoverage.CoverageType.CSMBS: 100,
    PatientCoverage.CoverageType.PRIVATE: 80,
    PatientCoverage.CoverageType.OTHER: 0,
}


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
            prescription.status = Prescription.Status.SENT
            prescription.sent_at = timezone.now()
            prescription.save(update_fields=["status", "sent_at", "updated_at"])
            _ensure_bill(visit, request.user)
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

    if visit_complete:
        next_action_label = "เสร็จสิ้นการรับบริการ"
        next_action_detail = (
            "ไม่มีรายการยาที่ต้องรับ · การเงินเสร็จแล้ว · ผู้ป่วยสามารถกลับบ้านได้"
            if pharmacy_skipped
            else "จ่ายยาและดำเนินการการเงินเรียบร้อยแล้ว · ผู้ป่วยสามารถกลับบ้านได้"
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
        "prescription_locked": prescription_locked,
        "can_edit_prescription": can_edit_prescription,
        "next_action_label": next_action_label,
        "next_action_detail": next_action_detail,
    })


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
    prescriptions = (
        Prescription.objects
        .select_related("visit", "visit__patient", "prescribed_by")
        .prefetch_related("items")
        .exclude(status=Prescription.Status.DRAFT)
        .order_by(
            Case(
                When(status=Prescription.Status.READY, then=0),
                When(status=Prescription.Status.PREPARING, then=1),
                When(status=Prescription.Status.SENT, then=2),
                When(status=Prescription.Status.DISPENSED, then=3),
                When(status=Prescription.Status.CANCELLED, then=4),
                default=5,
                output_field=IntegerField(),
            ),
            "sent_at",
            "created_at",
        )
    )
    return render(request, "pharmacy_worklist.html", {
        "prescriptions": prescriptions,
        "status_choices": Prescription.Status.choices,
    })


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
    return redirect("pharmacy_worklist")


@login_required
def billing_worklist(request):
    bills = (
        Bill.objects
        .select_related("visit", "visit__patient", "coverage", "received_by")
        .order_by(
            Case(
                When(status=Bill.Status.READY, then=0),
                When(status=Bill.Status.DRAFT, then=1),
                When(status=Bill.Status.PAID, then=2),
                When(status=Bill.Status.WAIVED, then=3),
                When(status=Bill.Status.CANCELLED, then=4),
                default=5,
                output_field=IntegerField(),
            ),
            "created_at",
        )
    )
    return render(request, "billing_worklist.html", {"bills": bills})


@login_required
def billing_detail(request, bill_id):
    bill = get_object_or_404(
        Bill.objects.select_related("visit", "visit__patient", "coverage"),
        pk=bill_id,
    )
    visit = bill.visit
    prescription = Prescription.objects.filter(visit=visit).prefetch_related("items").first()

    if request.method == "POST":
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
        "coverage_choices": PatientCoverage.CoverageType.choices,
        "coverage_defaults": COVERAGE_DEFAULT_PERCENT,
    })


@login_required
@require_POST
def billing_pay(request, bill_id):
    bill = get_object_or_404(Bill.objects.select_related("visit", "coverage"), pk=bill_id)
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
    return render(request, "billing_receipt.html", {
        "bill": bill,
        "visit": bill.visit,
        "prescription": prescription,
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
