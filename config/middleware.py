from django.utils.deprecation import MiddlewareMixin

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
            response['Vary'] = 'Cookie'

        return response
