import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import DeviceCreateForm, DeviceManagementPairForm
from .models import Device, DeviceAssignment, Queue


def mask_api_key(api_key):
    if not api_key:
        return "-"
    if len(api_key) <= 8:
        return f"{api_key[:2]}***{api_key[-2:]}"
    return f"{api_key[:4]}...{api_key[-4:]}"


@login_required
def device_management(request):
    create_form = DeviceCreateForm(initial={"device_id": Device.suggest_next_device_id()})
    pair_form = DeviceManagementPairForm()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_device":
            create_form = DeviceCreateForm(request.POST)
            if create_form.is_valid():
                try:
                    device = create_form.save(commit=False)
                    if not device.api_key:
                        device.api_key = secrets.token_urlsafe(24)
                    device.save()
                    messages.success(request, f"สร้างอุปกรณ์ {device.device_id} สำเร็จ")
                    return redirect("device_management")
                except IntegrityError:
                    messages.error(request, "สร้างอุปกรณ์ไม่สำเร็จ: device_id ซ้ำหรือข้อมูลชนกับฐานข้อมูล")
            else:
                messages.error(request, "กรุณาตรวจสอบข้อมูลสร้างอุปกรณ์")

        elif action == "pair_device":
            pair_form = DeviceManagementPairForm(request.POST)
            if pair_form.is_valid():
                device = pair_form.cleaned_data["device"]
                visit = pair_form.cleaned_data["visit"]
                now = timezone.now()

                with transaction.atomic():
                    DeviceAssignment.objects.filter(device=device, is_active=True).update(
                        is_active=False,
                        unpaired_at=now,
                    )
                    DeviceAssignment.objects.filter(visit=visit, is_active=True).update(
                        is_active=False,
                        unpaired_at=now,
                    )
                    DeviceAssignment.objects.create(device=device, visit=visit, is_active=True)

                    q = getattr(visit, "queue", None)
                    if q:
                        q.status = Queue.Status.MONITORING
                        q.save(update_fields=["status"])

                messages.success(request, "ผูกอุปกรณ์สำเร็จ")
                return redirect("device_management")
            messages.error(request, "กรุณาเลือกอุปกรณ์และ Visit ให้ถูกต้อง")

        elif action == "toggle_device":
            device = get_object_or_404(Device, id=request.POST.get("device_id"))
            device.is_active = not device.is_active
            device.save(update_fields=["is_active"])
            state = "เปิดใช้งาน" if device.is_active else "ปิดใช้งาน"
            messages.success(request, f"{state} {device.device_id} แล้ว")
            return redirect("device_management")

        elif action == "delete_device":
            device = get_object_or_404(Device, id=request.POST.get("device_id"))
            device_code = device.device_id
            try:
                with transaction.atomic():
                    DeviceAssignment.objects.filter(device=device, is_active=True).update(
                        is_active=False,
                        unpaired_at=timezone.now(),
                    )
                    device.delete()
                messages.success(request, f"Deleted device {device_code}")
            except IntegrityError:
                messages.error(request, f"Cannot delete device {device_code}")
            return redirect("device_management")

        elif action == "unpair_device":
            assignment = get_object_or_404(
                DeviceAssignment.objects.select_related("device", "visit"),
                id=request.POST.get("assignment_id"),
                is_active=True,
            )
            assignment.is_active = False
            assignment.unpaired_at = timezone.now()
            assignment.save(update_fields=["is_active", "unpaired_at"])
            messages.success(request, f"ยกเลิกการผูก {assignment.device.device_id} แล้ว")
            return redirect("device_management")

        else:
            messages.error(request, "คำสั่งไม่ถูกต้อง")

    active_assignments = {
        assignment.device_id: assignment
        for assignment in (
            DeviceAssignment.objects
            .select_related("device", "visit", "visit__patient", "visit__queue")
            .filter(is_active=True)
        )
    }
    devices = Device.objects.order_by("device_id")
    for device in devices:
        device.active_assignment = active_assignments.get(device.id)
        device.masked_api_key = mask_api_key(device.api_key)

    return render(request, "queues/device_management.html", {
        "create_form": create_form,
        "pair_form": pair_form,
        "devices": devices,
    })
