from dataclasses import dataclass
import time
from urllib.parse import urlsplit

import requests

from .local import load_private, save_private, state_dir


SITE = "https://www.yuketang.cn"


class RainError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class UnclearReceipt(Exception):
    """The request was sent, but its outcome is not known."""


@dataclass(frozen=True)
class Lesson:
    id: str
    name: str


class RainClient:
    def __init__(self):
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "RainDesk/1.0 (Windows)",
                                  "Accept": "application/json", "Referer": SITE + "/", "xtbz": "ykt"})
        self.uid = ""
        self.name = ""
        self.lesson = None
        self.bearer = ""
        self.lesson_token = ""

    def request(self, method, path, *, lesson=False, timeout=15, receipt=False, **kwargs):
        headers = {}
        if method == "POST":
            headers["Origin"] = SITE
            csrf = requests.utils.dict_from_cookiejar(self.http.cookies).get("csrftoken")
            if csrf:
                headers["X-CSRFToken"] = csrf
        if lesson:
            if not self.bearer:
                raise RainError("请先进入正在上课的课堂。")
            headers["Authorization"] = "Bearer " + self.bearer
        response = self.http.request(method, SITE + path, headers=headers, timeout=(8, timeout),
                                     allow_redirects=False, **kwargs)
        if response.status_code in (301, 302, 401, 403):
            raise RainError("登录已过期或当前账号无权访问，请重新扫码。", response.status_code)
        if receipt and response.status_code >= 500:
            raise UnclearReceipt("雨课堂未确认这次请求。")
        if not response.ok:
            raise RainError("雨课堂返回 HTTP %d。" % response.status_code, response.status_code)
        try:
            obj = response.json()
        except ValueError as exc:
            if receipt:
                raise UnclearReceipt("雨课堂未返回明确的提交回执。") from exc
            raise RainError("雨课堂返回了无法读取的数据。") from exc
        if not isinstance(obj, dict):
            if receipt:
                raise UnclearReceipt("无法识别提交回执。")
            raise RainError("雨课堂返回的数据格式有变化。")
        if receipt and "code" not in obj:
            raise UnclearReceipt("提交响应缺少业务状态码。")
        code = obj.get("code", 0)
        if str(code) != "0" or obj.get("success") is False:
            raise RainError(str(obj.get("msg") or obj.get("message") or "雨课堂请求失败")[:180], code)
        if receipt:
            return {"code": int(code), "http": response.status_code, "time": int(time.time()),
                    "message": str(obj.get("msg") or obj.get("message") or "")[:120]}
        return obj.get("data"), response.headers

    def new_qr(self):
        data, _ = self.request("GET", "/api/v3/user/login/pre-info")
        if not isinstance(data, dict) or not data.get("token"):
            raise RainError("未能获取登录二维码。")
        image = urlsplit(str(data.get("qrImage", "")))
        if image.scheme != "https" or image.hostname not in {"mp.weixin.qq.com", "www.yuketang.cn"}:
            raise RainError("登录二维码图片地址无法识别。")
        with requests.get(image.geturl(), timeout=(8, 12)) as response:
            response.raise_for_status()
            return data["token"], response.content

    def poll_qr(self, token, cancelled=None):
        try:
            self.request("POST", "/api/v3/user/login", json={"token": token}, timeout=38)
        except RainError as exc:
            if str(exc.code) == "50001":
                return False
            raise
        self.profile()
        if cancelled is not None and cancelled.is_set():
            return False
        self.save_session()
        return True

    def profile(self):
        data, _ = self.request("GET", "/v/course_meta/user_info")
        profile = (data or {}).get("user_profile") or {}
        if not profile.get("user_id"):
            raise RainError("尚未登录，请使用本人微信扫码。", 401)
        self.uid = str(profile["user_id"])
        self.name = str(profile.get("name") or "已登录用户")
        return self.name

    def save_session(self):
        save_private("session.dat", [
            {k: getattr(c, k) for k in ("name", "value", "domain", "path", "expires", "secure")}
            for c in self.http.cookies if c.domain.lstrip(".") in {"yuketang.cn", "www.yuketang.cn"}
        ])

    def restore(self):
        cookies = load_private("session.dat")
        if not cookies:
            return False
        for cookie in cookies:
            if cookie["domain"].lstrip(".") in {"yuketang.cn", "www.yuketang.cn"}:
                self.http.cookies.set(**cookie)
        try:
            self.profile()
        except RainError as exc:
            if str(exc.code) in {"401", "403", "302", "301"}:
                self.forget()
                return False
            raise
        self.save_session()
        return True

    def forget(self):
        self.http.cookies.clear()
        self.uid = self.name = self.bearer = self.lesson_token = ""
        self.lesson = None
        (state_dir() / "session.dat").unlink(missing_ok=True)

    def lessons(self):
        data, _ = self.request("GET", "/api/v3/classroom/on-lesson")
        live = (data or {}).get("onLessonClassrooms")
        if not isinstance(live, list):
            raise RainError("无法读取正在上课的课程列表。")
        names = {}
        if live:
            try:
                courses, _ = self.request("GET", "/v/course_meta/learning_list/")
                names = {str(c.get("course_id")): c.get("course_name") for c in courses or []
                         if isinstance(c, dict)}
            except (RainError, requests.RequestException):
                pass  # A lesson ID remains usable if its optional course title cannot be loaded.
        return [Lesson(str(row["lessonId"]), str(row.get("courseName") or names.get(str(row.get("courseId")))
                       or "课堂 " + str(row["lessonId"]))) for row in live if row.get("lessonId")]

    def enter(self, lesson):
        data, headers = self.request("POST", "/api/v3/lesson/checkin", json={
            "source": 21, "lessonId": lesson.id, "joinIfNotIn": False})
        bearer = headers.get("set-auth")
        token = (data or {}).get("lessonToken")
        if not bearer or not token:
            raise RainError("雨课堂未返回本节课的访问凭据。")
        self.lesson, self.bearer, self.lesson_token = lesson, bearer, token

    def presentation(self, presentation_id):
        data, _ = self.request("GET", "/api/v3/lesson/presentation/fetch", lesson=True,
                               params={"presentation_id": presentation_id})
        if not isinstance(data, dict) or not isinstance(data.get("slides"), list):
            raise RainError("课件暂时无法读取。")
        return data

    def submit(self, question, result):
        if self.lesson is None or question.lesson_id != self.lesson.id:
            raise RainError("课堂已切换，请在当前课堂重新确认题目。")
        if question.expired:
            raise RainError("老师已结束本题作答。")
        body = {"problemId": question.id, "problemType": question.kind,
                "dt": int(time.time() * 1000), "result": result}
        if question.kind == 5:
            body["result"] = {"content": result, "pics": [], "videos": []}
        return self.request("POST", "/api/v3/lesson/problem/answer", json=body, lesson=True, receipt=True)
