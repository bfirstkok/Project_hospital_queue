from django.conf import settings
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from django.utils.cache import patch_vary_headers

from patients.security import audit_patient_api_request


class PatientApiAuditMiddleware:
    """Record patient API events and keep the API response contract JSON-only."""

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _is_patient_api(request):
        return request.path.startswith("/api/patient/")

    @staticmethod
    def _apply_cors(request, response):
        origin = request.headers.get("Origin", "")
        if origin and origin in settings.PATIENT_APP_ORIGINS:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Credentials"] = "true"
            patch_vary_headers(response, ["Origin"])
        response["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
        return response

    def __call__(self, request):
        try:
            response = self.get_response(request)
        except Exception:
            audit_patient_api_request(request, 500)
            if not self._is_patient_api(request):
                raise
            response = JsonResponse(
                {"ok": False, "error": "ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง"},
                status=500,
            )
            return self._apply_cors(request, response)

        if self._is_patient_api(request):
            content_type = response.get("Content-Type", "")
            if "application/json" not in content_type.lower():
                status = response.status_code
                if status == 404:
                    message = "ไม่พบ API ที่ร้องขอ"
                elif status == 403:
                    message = "ไม่มีสิทธิ์ดำเนินการ"
                elif status >= 500:
                    message = "ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง"
                else:
                    message = "ไม่สามารถดำเนินการตามคำขอได้"
                response = JsonResponse({"ok": False, "error": message}, status=status)
            response = self._apply_cors(request, response)

        audit_patient_api_request(request, response.status_code)
        return response

class NoCacheMiddleware(MiddlewareMixin):
    """
    Middleware ที่ป้องกัน browser cache หน้าเว็บทั้งหมด
    แก้ปัญหาการกด back button หลัง logout
    """
    def process_response(self, request, response):
        # ป้องกัน browser cache ทุกรูปแบบ
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '-1'

        # เพิ่ม headers เพิ่มเติมสำหรับ browser บางตัว
        response['Last-Modified'] = 'Thu, 01 Jan 1970 00:00:00 GMT'

        # ป้องกัน bfcache (back-forward cache) ของ Safari และ Firefox
        if hasattr(response, 'status_code') and response.status_code == 200:
            patch_vary_headers(response, ['Cookie'])

        return response
